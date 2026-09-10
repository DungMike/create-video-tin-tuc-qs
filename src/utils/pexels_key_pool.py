"""Pool xoay vong nhieu PEXELS_API_KEY, de tai hang loat khong chet vi quota.

Pexels tinh quota THEO KEY: 200 request/gio va 20.000/thang. Mot luot quet het
mot tu khoa (``iter_all_provider_videos``) ton toi 100 request, nen voi mot key
duy nhat chi hai tu khoa la het quota. Cach xu ly cu -- ngu roi thu lai
(``_RATE_LIMIT_BASE_DELAY * 2**n``, tong cong ~30 giay) -- khong bao gio cuu duoc
tinh huong nay: quota Pexels reset theo GIO, ngu 30 giay xong van 429.

Pool nay giu N key doc tu .env (PEXELS_API_KEY, PEXELS_API_KEY_2, ...) va doi
chien luoc tu "ngu cho" sang "doi key":

- **Bam key (sticky), khong chia deu.** Dung mai mot key cho toi khi no het
  quota roi moi sang key ke tiep. Chia deu (round-robin) cung tong quota nhung
  lam ca 6 key cung can kiet trong cung mot gio; bam key thi moc reset cua chung
  lech nhau, luc nao cung con key vua hoi quota.
- **429 = cho key do nghi, khong phai ngu.** Key dinh 429 bi khoa den dung moc
  ``X-Ratelimit-Reset`` (hoac ``Retry-After``), request ke tiep nhay ngay sang
  key khac -- khong ton mot giay nao.
- **Het quota thi nghi truoc khi bi tu choi.** Header
  ``X-Ratelimit-Remaining`` ve 0 la du de cho key nghi; khong can dot them mot
  request rac chi de nhan 429.
- **Ca pool nghi thi bao loi, khong treo.** Caller chi cho toi
  ``Config.PEXELS_POOL_MAX_WAIT_SECONDS`` roi nhan ``PexelsQuotaExhausted``, de
  job dung co kiem soat va noi ro con bao lau moi tai tiep duoc.

Do that te tren 6 key cua du an nay (2026-09-08) truoc khi viet module:

- Quota la RIENG cho tung key: sau khi key #1 goi vai request, no bao
  ``remaining=24967`` trong khi 5 key con lai bao ``24999``, va moc reset cua
  chung lech nhau. 6 key = 6 lan quota that, khong phai dung chung mot xo.
- Header cua Pexels khong dang tin bang cai 429: xem ``_cooldown_seconds``.
- Response cua Pexels co qua CDN cache. Hai request giong het nhau tren hai key
  khac nhau tra ve y het header cua nhau (ke ca ``remaining``), nen dung dua vao
  ``remaining`` de do quota -- chi dung no khi no bao 0.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from src.config import Config
from src.utils.logger import logger

# Tran cho cooldown suy ra tu header. X-Ratelimit-Reset la unix timestamp cua
# may chu Pexels; neu dong ho may nay lech (hoac header hong) thi mot moc reset
# sai co the khoa key hang ngay troi. Hai gio la du rong cho quota theo gio.
_MAX_COOLDOWN_SECONDS = 2 * 3600

# So luot thu them ngoai "moi key mot luot", danh cho 429 rai rac tu phia CDN /
# proxy ma khong phai do quota key.
_EXTRA_ATTEMPTS = 2


class PexelsQuotaExhausted(RuntimeError):
    """Moi key trong pool deu dang cooldown (hoac chua cau hinh key nao).

    Co y KHONG phai ``requests.HTTPError``: ``iter_all_provider_videos`` coi moi
    loi 4xx la "het ket qua", nen neu 429 di duoi dang HTTPError thi mot luot
    quet chet vi quota se im lang bao la da quet xong.
    """


@dataclass
class _KeyState:
    key: str
    index: int
    cooldown_until: float = 0.0
    remaining: int | None = None
    reset_at: float | None = None
    requests: int = 0
    rate_limit_hits: int = 0


@dataclass(frozen=True)
class PexelsKeyLease:
    """Mot luot dung key. ``index`` de log ("key #3") ma khong lo key ra log."""

    key: str
    index: int

    @property
    def label(self) -> str:
        return f"key #{self.index + 1}"


def _configured_keys() -> list[str]:
    """Danh sach key hien tai cua Config, da bo rong/trung.

    Doc lai moi lan goi thay vi chup mot lan luc import: test monkeypatch
    ``Config.PEXELS_API_KEYS`` va pool phai thay ngay thay doi do.
    """
    raw_keys = getattr(Config, "PEXELS_API_KEYS", None)
    if not raw_keys:
        raw_keys = [getattr(Config, "PEXELS_API_KEY", "")]
    keys: list[str] = []
    seen: set[str] = set()
    for raw in raw_keys:
        key = str(raw or "").strip()
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _parse_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _cooldown_seconds(headers, now: float) -> float:
    """Bao lau nua key nay moi dung lai duoc.

    Do that te tren cac key cua du an nay (2026-09-08): ``X-Ratelimit-Reset`` cua
    Pexels tro toi moc reset THEO THANG (~30 ngay nua), khong phai moc reset cua
    han 200 request/gio -- ma 429 hang loat gan nhu luon la dung han theo GIO.
    Tin thang vao header do se khoa mot key con quota suot hai tieng (tran
    _MAX_COOLDOWN_SECONDS) trong khi no hoi lai sau chua toi mot gio.

    Nen thu tu uu tien la:
      1. ``Retry-After`` -- do chinh Pexels noi cho request nay, luon dung nhat.
      2. Mac dinh mot gio (``PEXELS_KEY_COOLDOWN_SECONDS``) = dung nhip cua han
         theo gio.
      3. ``X-Ratelimit-Reset`` chi duoc dung khi no SOM HON mac dinh, tuc la moc
         thang sap sang trang that -- luc do cho toi do la du.

    Header con co the sai han: cung ngay do, mot response tra ve moc reset da qua
    HAI THANG (do CDN cache lai response cu). Moc nam trong qua khu bi bo qua.
    """
    headers = headers or {}

    retry_after = _parse_int(headers.get("Retry-After"))
    if retry_after is not None and retry_after > 0:
        return min(float(retry_after), _MAX_COOLDOWN_SECONDS)

    cooldown = float(Config.PEXELS_KEY_COOLDOWN_SECONDS)
    reset_at = _parse_int(headers.get("X-Ratelimit-Reset"))
    if reset_at is not None and reset_at > now:
        cooldown = min(cooldown, float(reset_at) - now)

    return min(cooldown, _MAX_COOLDOWN_SECONDS)


class PexelsKeyPool:
    """Thread-safe: cac worker tai hang loat chay song song tren cung pool nay."""

    def __init__(self):
        self._lock = threading.Lock()
        self._states: list[_KeyState] = []
        self._signature: tuple[str, ...] = ()
        self._cursor = 0

    # -- danh sach key ----------------------------------------------------- #
    def _sync_locked(self) -> None:
        keys = tuple(_configured_keys())
        if keys == self._signature:
            return
        # Giu lai cooldown cua nhung key van con trong danh sach moi: reload config
        # khong duoc phep xoa ky uc "key nay dang het quota".
        previous = {state.key: state for state in self._states}
        states: list[_KeyState] = []
        for index, key in enumerate(keys):
            state = previous.get(key) or _KeyState(key=key, index=index)
            state.index = index
            states.append(state)
        self._states = states
        self._signature = keys
        self._cursor = 0

    def _state_for(self, lease: PexelsKeyLease) -> _KeyState | None:
        for state in self._states:
            if state.key == lease.key:
                return state
        return None

    def _advance_locked(self, state: _KeyState) -> None:
        """Chuyen con tro sang key ke tiep sau ``state``."""
        if self._states:
            self._cursor = (state.index + 1) % len(self._states)

    # -- API --------------------------------------------------------------- #
    def size(self) -> int:
        with self._lock:
            self._sync_locked()
            return len(self._states)

    def max_attempts(self) -> int:
        """Du luot de thu het moi key mot lan roi con vai lan backoff."""
        return self.size() + _EXTRA_ATTEMPTS

    def acquire(self) -> PexelsKeyLease | None:
        """Key con quota, hoac ``None`` neu ca pool dang nghi.

        Raise ``PexelsQuotaExhausted`` khi chua cau hinh key nao -- do la loi cau
        hinh, cho bao lau cung khong co key.
        """
        now = time.time()
        with self._lock:
            self._sync_locked()
            if not self._states:
                raise PexelsQuotaExhausted(
                    "Chua cau hinh PEXELS_API_KEY nao trong .env"
                )
            count = len(self._states)
            for offset in range(count):
                state = self._states[(self._cursor + offset) % count]
                if state.cooldown_until <= now:
                    self._cursor = state.index
                    state.requests += 1
                    return PexelsKeyLease(key=state.key, index=state.index)
            return None

    def wait_seconds(self) -> float:
        """Bao lau nua key som nhat trong pool moi tinh lai."""
        now = time.time()
        with self._lock:
            self._sync_locked()
            if not self._states:
                return 0.0
            return max(0.0, min(s.cooldown_until for s in self._states) - now)

    def report(self, lease: PexelsKeyLease, status_code: int, headers=None) -> None:
        """Ghi nhan ket qua mot request de biet key nao con quota."""
        headers = headers or {}
        now = time.time()
        remaining = _parse_int(headers.get("X-Ratelimit-Remaining"))
        reset_at = _parse_int(headers.get("X-Ratelimit-Reset"))

        with self._lock:
            self._sync_locked()
            state = self._state_for(lease)
            if state is None:
                return
            state.remaining = remaining
            state.reset_at = float(reset_at) if reset_at is not None else None

            if status_code == 429:
                state.rate_limit_hits += 1
                state.cooldown_until = now + _cooldown_seconds(headers, now)
                self._advance_locked(state)
                logger.warning(
                    f"[Pexels] {lease.label} dinh 429 — cho nghi "
                    f"{state.cooldown_until - now:.0f}s, chuyen sang key khac "
                    f"({self._available_locked(now)}/{len(self._states)} key con quota)"
                )
            elif remaining is not None and remaining <= 0:
                # Con quota = 0 nhung request nay van duoc phuc vu. Cho nghi ngay
                # thay vi doi request sau an 429 -- tiet kiem dung mot round-trip.
                state.cooldown_until = now + _cooldown_seconds(headers, now)
                self._advance_locked(state)
                logger.info(
                    f"[Pexels] {lease.label} het quota — cho nghi "
                    f"{state.cooldown_until - now:.0f}s, chuyen sang key khac"
                )

    def _available_locked(self, now: float) -> int:
        return sum(1 for s in self._states if s.cooldown_until <= now)

    def status(self) -> dict:
        """Anh chup pool de log / debug. Khong bao gio tra ve key that."""
        now = time.time()
        with self._lock:
            self._sync_locked()
            return {
                "total": len(self._states),
                "available": self._available_locked(now),
                "keys": [
                    {
                        "label": f"key #{s.index + 1}",
                        "requests": s.requests,
                        "rateLimitHits": s.rate_limit_hits,
                        "remaining": s.remaining,
                        "cooldownSeconds": max(0.0, round(s.cooldown_until - now, 1)),
                    }
                    for s in self._states
                ],
            }

    def reset(self) -> None:
        """Xoa sach trang thai. Chi dung cho test."""
        with self._lock:
            self._states = []
            self._signature = ()
            self._cursor = 0


_pool = PexelsKeyPool()


def get_pexels_key_pool() -> PexelsKeyPool:
    return _pool
