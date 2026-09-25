"""Che do "moi clip 1 lan" (clip_usage_mode = "once") cho render story video.

Chi duoc goi khi mot render bat che do once; che do mac dinh ("reuse") van di
``SharedClipBag`` / xao ngau nhien nhu cu va khong bao gio cham toi module nay.

Thu tu chon: clip chua dung truoc, het thi toi clip dung 1 lan, 2 lan... (trong
cung muc la ngau nhien). ``use_count`` nam o Mongo (``src/db/media_repo.py``) va
chi tang khi video render THANH CONG.

Nhieu video render song song (batch nhieu worker, render le chay cung batch), nen
clip vua duoc mot video chon ma chua commit se bi "lease": video khac thay no nhu
da dung them 1 lan va chon clip khac. Lease chi nam trong RAM -- app chet giua
chung thi lease mat theo, dung y: video do chua xong nen khong duoc tinh.

Commit loi (Mongo tat giua chung) thi ghi vao outbox JSONL canh story_video/ va
phat lai o lan chon/commit sau; event co ``_id`` co dinh nen phat lai khong dem 2 lan.
"""

from __future__ import annotations

import json
import os
import random
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field

from src.config import Config
from src.utils.logger import logger

CLIP_USAGE_REUSE = "reuse"
CLIP_USAGE_ONCE = "once"

_OUTBOX_NAME = "_clip_usage_outbox.jsonl"


def normalize_clip_usage_mode(value) -> str:
    """Chi dung chu "once" moi bat che do moi; moi gia tri khac (ke ca thieu) = reuse."""
    return CLIP_USAGE_ONCE if str(value or "").strip().lower() == CLIP_USAGE_ONCE else CLIP_USAGE_REUSE


@dataclass
class ClipSelection:
    paths: list[str]
    run_starts: list[int] = field(default_factory=list)
    runs: list[int] = field(default_factory=list)
    fresh: int = 0
    reused: int = 0
    max_count: int = 0

    def stats(self) -> dict:
        return {
            "mode": CLIP_USAGE_ONCE,
            "clips": len(self.paths),
            "fresh": self.fresh,
            "reused": self.reused,
            "maxUseCount": self.max_count,
        }


def _outbox_path() -> str:
    return os.path.join(Config.STORY_VIDEO_DIR, _OUTBOX_NAME)


class ClipUsageLedger:
    def __init__(self):
        self._lock = threading.Lock()
        self._outbox_lock = threading.Lock()
        # story_id -> clip keys that story picked (leased until commit/release).
        self._leases: dict[str, set[str]] = {}
        self._leased: Counter = Counter()

    # ------------------------------------------------------------------ select
    def select(
        self,
        story_id: str,
        pool: list[tuple[str, float]],
        key_of,
        target_duration: float,
        unit: float | None,
        *,
        run_length=None,
        successor=None,
    ) -> ClipSelection:
        """Chon clip cho mot video. Raise neu khong doc duoc so dem tu Mongo.

        ``unit`` = do dai toi da tinh cho moi clip; ``None`` = tinh du do dai that
        (render phat nguyen clip, khong cat ve mot do dai chung).

        ``run_length()`` + ``successor(path)`` bat che do long takes: moi lan lay mot
        clip "seed" theo thu tu tren roi noi them clip lien sau cua cung video goc,
        chi khi clip do chua bi lay va khong bi dung nhieu hon seed.
        """
        from src.db import media_repo

        if not pool:
            return ClipSelection(paths=[])
        self.flush_outbox()
        keys = {key_of(path) for path, _dur in pool}
        counts = media_repo.get_clip_use_counts(keys)

        with self._lock:
            self._release_locked(story_id)
            leased = self._leased

            def effective(path: str) -> int:
                key = key_of(path)
                return counts.get(key, 0) + leased.get(key, 0)

            order = list(pool)
            random.shuffle(order)
            order.sort(key=lambda item: effective(item[0]))  # stable: random within a tier
            durations = dict(pool)

            selection = ClipSelection(paths=[])
            taken: set[str] = set()  # clip keys already in this video
            picked_keys: set[str] = set()
            total = 0.0
            cursor = 0

            def next_seed():
                nonlocal cursor, taken
                while True:
                    while cursor < len(order) and key_of(order[cursor][0]) in taken:
                        cursor += 1
                    if cursor < len(order):
                        item = order[cursor]
                        cursor += 1
                        return item
                    # Pool nho hon nhu cau: duyet lai tu dau, chap nhan lap nhu cu.
                    cursor = 0
                    taken = set()

            while total < target_duration:
                seed_path, seed_dur = next_seed()
                run = [(seed_path, seed_dur)]
                if run_length is not None and successor is not None:
                    want = max(1, int(run_length()))
                    seed_level = effective(seed_path)
                    run_keys = {key_of(seed_path)}
                    nxt = successor(seed_path)
                    while len(run) < want and nxt and nxt in durations:
                        nxt_key = key_of(nxt)
                        if nxt_key in taken or nxt_key in run_keys or effective(nxt) > seed_level:
                            break
                        run.append((nxt, durations[nxt]))
                        run_keys.add(nxt_key)
                        nxt = successor(nxt)
                selection.run_starts.append(len(selection.paths))
                selection.runs.append(len(run))
                for path, dur in run:
                    key = key_of(path)
                    taken.add(key)
                    picked_keys.add(key)
                    selection.paths.append(path)
                    total += float(dur) if unit is None else min(float(dur), float(unit))

            self._leases[story_id] = picked_keys
            leased.update(picked_keys)

        for key in picked_keys:
            if counts.get(key, 0):
                selection.reused += 1
            else:
                selection.fresh += 1
        selection.max_count = max((counts.get(key, 0) for key in picked_keys), default=0)
        return selection

    # ----------------------------------------------------------- commit/release
    def commit(self, story_id: str, meta: dict | None = None) -> dict:
        """Tang ``use_count`` cho cac clip cua video vua xong roi tra lease. Khong raise."""
        with self._lock:
            keys = sorted(self._leases.get(story_id) or ())
        if not keys:
            return {"committed": 0}
        usage_id = f"{story_id}:{uuid.uuid4().hex[:12]}"
        try:
            from src.db import media_repo

            self.flush_outbox()
            media_repo.commit_clip_usage(story_id, keys, meta, usage_id=usage_id)
            return {"committed": len(keys), "usageId": usage_id}
        except Exception as exc:
            logger.warning(
                f"[ClipUsage:{story_id}] Commit {len(keys)} clip vao Mongo that bai ({exc}); "
                f"ghi outbox de phat lai sau."
            )
            self._append_outbox({
                "usage_id": usage_id,
                "story_id": story_id,
                "clip_keys": keys,
                "meta": meta or {},
            })
            return {"committed": 0, "queued": len(keys), "usageId": usage_id}
        finally:
            self.release(story_id)

    def release(self, story_id: str) -> None:
        with self._lock:
            self._release_locked(story_id)

    def _release_locked(self, story_id: str) -> None:
        keys = self._leases.pop(story_id, None)
        if keys:
            self._leased.subtract(keys)
            for key in keys:
                if self._leased[key] <= 0:
                    del self._leased[key]

    def leased_keys(self) -> set[str]:
        with self._lock:
            return set(self._leased)

    # ------------------------------------------------------------------ outbox
    def _append_outbox(self, entry: dict) -> None:
        try:
            with self._outbox_lock:
                os.makedirs(os.path.dirname(_outbox_path()), exist_ok=True)
                with open(_outbox_path(), "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error(f"[ClipUsage] Khong ghi duoc outbox {_outbox_path()}: {exc}")

    def flush_outbox(self) -> int:
        """Phat lai cac commit dang cho. Tra ve so muc da ghi xong. Khong raise."""
        path = _outbox_path()
        if not os.path.exists(path):
            return 0
        from src.db import media_repo

        with self._outbox_lock:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    lines = [line for line in handle.read().splitlines() if line.strip()]
            except OSError:
                return 0
            done = 0
            remaining: list[str] = []
            for index, line in enumerate(lines):
                try:
                    entry = json.loads(line)
                    if not (entry.get("story_id") and entry.get("usage_id") and entry.get("clip_keys")):
                        raise ValueError("thieu truong")
                except (ValueError, AttributeError):
                    logger.warning(f"[ClipUsage] Bo dong outbox hong: {line[:120]}")
                    continue
                try:
                    media_repo.commit_clip_usage(
                        entry["story_id"], entry["clip_keys"], entry.get("meta"),
                        usage_id=entry["usage_id"],
                    )
                    done += 1
                except Exception as exc:
                    logger.warning(f"[ClipUsage] Outbox con {len(lines) - index} muc chua ghi duoc: {exc}")
                    remaining = lines[index:]
                    break
            try:
                if remaining:
                    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
                    with open(tmp, "w", encoding="utf-8") as handle:
                        handle.write("\n".join(remaining) + "\n")
                    os.replace(tmp, path)
                else:
                    os.remove(path)
            except OSError as exc:
                logger.warning(f"[ClipUsage] Khong cap nhat duoc outbox: {exc}")
            if done:
                logger.info(f"[ClipUsage] Da phat lai {done} commit tu outbox.")
            return done


_ledger = ClipUsageLedger()


def get_clip_usage_ledger() -> ClipUsageLedger:
    return _ledger
