"""Tim ANH tren Pexels / Pixabay cho thu vien clip tu anh.

Khac ``decor_image_search`` (anh decor khung TV): o day ``per_page`` do con tro
phan trang quyet dinh (``src/db/image_repo.py``), ket qua tra ve ``total`` de tinh
trang cuoi, va bo loc noi hon -- anh duoc cat phu ve 16:9 truoc khi Ken Burns nen
chi can phan cat du 1920px, khong can dung 16:9.

Dung chung ``_get_with_rate_limit_retry`` (key pool Pexels + backoff) voi luong
video, nen search anh Pexels an chung quota theo gio cua cac key.

**Bien the truy van** (``query_variants``): moi truy van chi truy cap duoc vai tram
ket qua dau -- Pexels 480 (``total_results`` bao 8000 nhung tu vi tri 481 tro di
trang rong; do that 2026-09-29 voi per_page 20/50/80), Pixabay 500-600
(``totalHits``). Cung tu khoa them bo loc mau / thu tu / chu de thi ra mot tap anh
KHAC. Do that "forest" (anh dung duoc, da bo trung):

- Pexels: goc 465 -> them 12 mau 2509 (x5.4). ``size``/``locale``/ma mau hex gan
  nhu khong them gi (hex bi quy ve mau co ten gan nhat).
- Pixabay: goc 595 -> them order/mau/chu de 13277 (x22).
"""

from __future__ import annotations

import re
import time

import requests

from src.config import Config
from src.utils.decor_image_search import PIXABAY_NO_FULL_ACCESS_NOTICE
from src.utils.pexels_key_pool import get_pexels_key_pool
from src.utils.video_source_downloader import _get_with_rate_limit_retry

PROVIDERS = ("pixabay", "pexels")

# Bo loc gui len API. Doi bo loc -> doi chuoi nay -> con tro cu bi bo qua (tim lai
# tu trang 1), vi thu tu / so ket qua cua truy van da khac.
QUERY_SIGNATURE = "landscape|min1920x1080|v1"

# Thu tu quet bien the: mau cho nhieu anh moi nhat (do tren "forest") len truoc.
PEXELS_COLORS = (
    "green", "blue", "brown", "turquoise", "red", "violet", "pink", "white", "orange", "yellow", "black", "gray",
)
PIXABAY_COLORS = (
    "green", "blue", "brown", "gray", "black", "white", "grayscale", "turquoise", "orange", "yellow", "red",
    "pink", "lilac",
)
PIXABAY_CATEGORIES = (
    "backgrounds", "nature", "places", "animals", "travel", "buildings", "feelings", "science", "religion",
    "people", "transportation", "industry", "health", "food", "education", "sports", "business", "fashion",
    "computer", "music",
)
# Bo loc duoc phep gui kem (chan gia tri la tu request/DB lot vao URL).
_VARIANT_PARAMS = {
    "pexels": {"color": set(PEXELS_COLORS), "size": {"large", "medium", "small"}},
    "pixabay": {"colors": set(PIXABAY_COLORS), "category": set(PIXABAY_CATEGORIES), "order": {"popular", "latest"}},
}

PIXABAY_LOW_RES_NOTICE = (
    "Key Pixabay chưa có full API access — đang nhận bản 1280px (clip gắn tag lowres, mờ hơn ảnh ≥1920). "
    "Xin full access tại pixabay.com/api/docs để lấy ảnh gốc."
)
# Pixabay: 100 request / 60 giay moi key (header X-RateLimit-*).
PIXABAY_WINDOW_SECONDS = 60
PIXABAY_429_RETRIES = 3

MAX_PER_PAGE = {"pexels": 80, "pixabay": 200}
MIN_PER_PAGE = {"pexels": 1, "pixabay": 3}

MIN_CROP_WIDTH = 1920
MIN_RATIO = 1.3
MAX_RATIO = 2.4
# Pexels CDN tu thu nho anh goc (6000px+, 10MB+); 2880 du cho zoom 1.3 tren 1920.
PEXELS_DOWNLOAD_WIDTH = 2880

_PIXABAY_OUT_OF_RANGE = "out of valid range"


class SearchOutOfRange(Exception):
    """Trang vuot pham vi ket qua (Pixabay tra 400 "page is out of valid range")."""


class ProviderSearchError(Exception):
    """Search that bai (key sai, 4xx/5xx, mang). Thong diep da xoa API key."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def variant_id(filters: dict | None) -> str:
    """Ten on dinh cua mot bien the (``""`` = truy van goc), vd ``colors=green&order=latest``."""
    return "&".join(f"{key}={value}" for key, value in sorted((filters or {}).items()))


def query_variants(provider: str) -> list[dict]:
    """Bo loc them cho moi lan quet mot tu khoa, theo thu tu quet. Phan tu dau ``{}``
    la truy van goc (dung con tro cu cua tu khoa)."""
    provider = str(provider or "").strip().lower()
    if provider == "pexels":
        return [{}] + [{"color": color} for color in PEXELS_COLORS] + [{"size": "large"}]
    if provider == "pixabay":
        variants: list[dict] = [{}, {"order": "latest"}]
        slices = [{"colors": color} for color in PIXABAY_COLORS]
        slices += [{"category": category} for category in PIXABAY_CATEGORIES]
        for extra in slices:
            variants += [extra, {**extra, "order": "latest"}]
        return variants
    raise ValueError("Provider khong hop le (pixabay/pexels).")


def coverage_key(filters: dict | None) -> frozenset:
    """Tap bo loc gioi han ket qua (``order`` chi doi thu tu, khong doi tap anh)."""
    return frozenset((key, value) for key, value in (filters or {}).items() if key != "order")


def _clean_filters(provider: str, filters: dict | None) -> dict:
    allowed = _VARIANT_PARAMS[provider]
    clean = {}
    for key, value in (filters or {}).items():
        if key not in allowed or str(value) not in allowed[key]:
            raise ValueError(f"Bo loc {key}={value} khong hop le cho {provider}.")
        clean[key] = str(value)
    return clean


def redact(text) -> str:
    """Xoa API key khoi thong diep (URL Pixabay chua ``key=...``)."""
    value = re.sub(r"(?i)(key=)[^&\s'\"]+", r"\1***", str(text or ""))
    key = str(getattr(Config, "PIXABAY_API_KEY", "") or "")
    if len(key) >= 8:
        value = value.replace(key, "***")
    return value


def photo_rejection_reason(width, height, download_url) -> str | None:
    """Ly do loai anh, hoac None neu dung duoc."""
    if not download_url:
        return "no_url"
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return "too_small"
    if w <= 0 or h <= 0:
        return "too_small"
    ratio = w / h
    if ratio < MIN_RATIO or ratio > MAX_RATIO:
        return "bad_ratio"
    crop_width = w if ratio <= 16 / 9 else h * 16 / 9
    if crop_width < MIN_CROP_WIDTH:
        return "too_small"
    return None


def _clamp_per_page(provider: str, per_page) -> int:
    try:
        value = int(per_page)
    except (TypeError, ValueError):
        value = MAX_PER_PAGE[provider]
    return max(MIN_PER_PAGE[provider], min(MAX_PER_PAGE[provider], value))


def _normalize_pexels_photo(photo: dict) -> dict | None:
    src = photo.get("src") if isinstance(photo.get("src"), dict) else {}
    original = str(src.get("original") or "")
    photo_id = str(photo.get("id") or "").strip()
    if not photo_id:
        return None
    return {
        "provider": "pexels",
        "id": photo_id,
        "title": str(photo.get("alt") or "").strip() or f"Pexels photo {photo_id}",
        "tags": [],
        "thumbnailUrl": src.get("medium") or src.get("large") or original,
        "downloadUrl": (
            f"{original}?auto=compress&cs=tinysrgb&w={PEXELS_DOWNLOAD_WIDTH}" if original else ""
        ),
        "pageUrl": photo.get("url") or "",
        "width": int(photo.get("width") or 0),
        "height": int(photo.get("height") or 0),
        "author": photo.get("photographer") or "",
    }


def _normalize_pixabay_photo(hit: dict, allow_low_res: bool = False) -> dict | None:
    photo_id = str(hit.get("id") or "").strip()
    if not photo_id:
        return None
    tags_text = str(hit.get("tags") or "").strip()
    # Anh goc truoc (full API access), roi toi ban 1920. largeImageURL (<=1280px)
    # thap hon Full HD: chi dung khi job cho phep (key chua co full access).
    download_url = hit.get("imageURL") or hit.get("fullHDURL") or ""
    low_res = False
    if not download_url and allow_low_res and hit.get("largeImageURL"):
        download_url, low_res = hit["largeImageURL"], True
    item = {
        "provider": "pixabay",
        "id": photo_id,
        "title": tags_text or f"Pixabay photo {photo_id}",
        "tags": [tag.strip() for tag in tags_text.split(",") if tag.strip()],
        "thumbnailUrl": hit.get("webformatURL") or hit.get("previewURL") or "",
        "downloadUrl": download_url,
        "pageUrl": hit.get("pageURL") or "",
        "width": int(hit.get("imageWidth") or 0),
        "height": int(hit.get("imageHeight") or 0),
        "author": hit.get("user") or "",
    }
    if low_res:
        item["lowRes"] = True
    return item


def _parse_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _pixabay_wait_seconds(headers) -> float:
    reset = _parse_int((headers or {}).get("X-RateLimit-Reset"))
    return float(min(max(reset if reset is not None else PIXABAY_WINDOW_SECONDS, 1), PIXABAY_WINDOW_SECONDS)) + 0.5


def _get(provider: str, url: str, params: dict):
    """GET + giu nhip Pixabay: het luot trong cua so 60 giay thi ngu toi moc reset
    (backoff chung chi ~30 giay, khong du cho mot cua so Pixabay)."""
    for attempt in range(PIXABAY_429_RETRIES + 1):
        try:
            response = _get_with_rate_limit_retry(url, params=params, timeout=30)
        except requests.HTTPError as exc:
            response = exc.response
            if (provider == "pixabay" and response is not None and response.status_code == 429
                    and attempt < PIXABAY_429_RETRIES):
                time.sleep(_pixabay_wait_seconds(response.headers))
                continue
            raise
        if provider == "pixabay":
            remaining = _parse_int((getattr(response, "headers", None) or {}).get("X-RateLimit-Remaining"))
            if remaining is not None and remaining <= 0:
                time.sleep(_pixabay_wait_seconds(response.headers))
        return response
    raise AssertionError("unreachable")


def _request(provider: str, url: str, params: dict, page: int):
    try:
        return _get(provider, url, params).json()
    except requests.HTTPError as exc:
        response = exc.response
        status = response.status_code if response is not None else None
        body = ""
        try:
            body = response.text if response is not None else ""
        except Exception:
            body = ""
        if provider == "pixabay" and status == 400 and page > 1 and _PIXABAY_OUT_OF_RANGE in body.lower():
            raise SearchOutOfRange(f"{provider} trang {page} vuot pham vi ket qua") from None
        raise ProviderSearchError(
            redact(f"{provider} tra HTTP {status}: {body[:200] or exc}"), status
        ) from None
    except requests.RequestException as exc:
        raise ProviderSearchError(redact(f"{provider} loi mang: {exc}")) from None
    except ValueError as exc:  # JSON hong
        raise ProviderSearchError(redact(f"{provider} tra du lieu khong doc duoc: {exc}")) from None


def _finish(provider: str, raw_items: list, page: int, per_page: int, total: int, has_next: bool) -> dict:
    items, rejected = [], {}
    for item in raw_items:
        reason = photo_rejection_reason(item.get("width"), item.get("height"), item.get("downloadUrl"))
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        items.append(item)
    return {
        "provider": provider,
        "items": items,
        "page": page,
        "perPage": per_page,
        "total": total,
        "rawCount": len(raw_items),
        "rejected": rejected,
        "hasMore": has_next,
    }


def search_photos(
    provider: str,
    query: str,
    page: int = 1,
    per_page: int | None = None,
    *,
    filters: dict | None = None,
    allow_low_res: bool = False,
) -> dict:
    """Mot trang ket qua anh. Raise ``SearchOutOfRange`` / ``ProviderSearchError``.

    ``filters`` = bo loc cua mot bien the (``query_variants``) gui kem truy van.
    ``fullResMissing`` = so hit Pixabay khong co ban >=1920 vi key chua co full API
    access (chi co anh <=1280px); ``notice`` kem theo de UI noi ro. ``allow_low_res``
    thi van nhan cac hit do voi ban 1280px (``lowRes``). ``totalMatches`` = tong so
    anh khop bo loc (Pixabay ``total``, khong bi chan 500 nhu ``totalHits``).
    """
    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("Provider khong hop le (pixabay/pexels).")
    page = max(1, int(page or 1))
    per_page = _clamp_per_page(provider, per_page if per_page is not None else MAX_PER_PAGE[provider])
    extra = _clean_filters(provider, filters)

    if provider == "pexels":
        if not get_pexels_key_pool().size():
            raise ValueError("PEXELS_API_KEY chua cau hinh trong .env")
        params = {"query": query, "page": page, "per_page": per_page, "orientation": "landscape", **extra}
        data = _request(provider, "https://api.pexels.com/v1/search", params, page)
        photos = [photo for photo in data.get("photos", []) if isinstance(photo, dict)]
        raw_items = [item for item in (_normalize_pexels_photo(photo) for photo in photos) if item]
        total = int(data.get("total_results") or 0)
        has_next = bool(data.get("next_page")) if "next_page" in data else page * per_page < total
        result = _finish(provider, raw_items, page, per_page, total, has_next)
        result["rawCount"] = len(photos)
        result["fullResMissing"] = 0
        result["totalMatches"] = total
        return result

    api_key = Config.PIXABAY_API_KEY
    if not api_key:
        raise ValueError("PIXABAY_API_KEY chua cau hinh trong .env")
    params = {
        "key": api_key,
        "q": query,
        "page": page,
        "per_page": per_page,
        "image_type": "photo",
        "orientation": "horizontal",
        "min_width": 1920,
        "min_height": 1080,
        "safesearch": "true",
        **extra,
    }
    data = _request(provider, "https://pixabay.com/api/", params, page)
    hits = [hit for hit in data.get("hits", []) if isinstance(hit, dict)]
    raw_items = [item for item in (_normalize_pixabay_photo(hit, allow_low_res) for hit in hits) if item]
    total = int(data.get("totalHits") or 0)
    full_res_missing = sum(
        1 for hit in hits if not (hit.get("imageURL") or hit.get("fullHDURL")) and hit.get("largeImageURL")
    )
    result = _finish(provider, raw_items, page, per_page, total, page * per_page < min(total, 500))
    result["rawCount"] = len(hits)
    result["fullResMissing"] = full_res_missing
    result["totalMatches"] = int(data.get("total") or total)
    if full_res_missing:
        result["notice"] = PIXABAY_LOW_RES_NOTICE if allow_low_res else PIXABAY_NO_FULL_ACCESS_NOTICE
    return result
