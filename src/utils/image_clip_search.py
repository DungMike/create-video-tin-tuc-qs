"""Tim ANH tren Pexels / Pixabay cho thu vien clip tu anh.

Khac ``decor_image_search`` (anh decor khung TV): o day ``per_page`` do con tro
phan trang quyet dinh (``src/db/image_repo.py``), ket qua tra ve ``total`` de tinh
trang cuoi, va bo loc noi hon -- anh duoc cat phu ve 16:9 truoc khi Ken Burns nen
chi can phan cat du 1920px, khong can dung 16:9.

Dung chung ``_get_with_rate_limit_retry`` (key pool Pexels + backoff) voi luong
video, nen search anh Pexels an chung quota theo gio cua cac key.
"""

from __future__ import annotations

import re

import requests

from src.config import Config
from src.utils.decor_image_search import PIXABAY_NO_FULL_ACCESS_NOTICE
from src.utils.pexels_key_pool import get_pexels_key_pool
from src.utils.video_source_downloader import _get_with_rate_limit_retry

PROVIDERS = ("pixabay", "pexels")

# Bo loc gui len API. Doi bo loc -> doi chuoi nay -> con tro cu bi bo qua (tim lai
# tu trang 1), vi thu tu / so ket qua cua truy van da khac.
QUERY_SIGNATURE = "landscape|min1920x1080|v1"

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


def _normalize_pixabay_photo(hit: dict) -> dict | None:
    photo_id = str(hit.get("id") or "").strip()
    if not photo_id:
        return None
    tags_text = str(hit.get("tags") or "").strip()
    return {
        "provider": "pixabay",
        "id": photo_id,
        "title": tags_text or f"Pixabay photo {photo_id}",
        "tags": [tag.strip() for tag in tags_text.split(",") if tag.strip()],
        "thumbnailUrl": hit.get("webformatURL") or hit.get("previewURL") or "",
        # Anh goc truoc (full API access), roi toi ban 1920. largeImageURL
        # (<=1280px) khong dung: thap hon Full HD.
        "downloadUrl": hit.get("imageURL") or hit.get("fullHDURL") or "",
        "pageUrl": hit.get("pageURL") or "",
        "width": int(hit.get("imageWidth") or 0),
        "height": int(hit.get("imageHeight") or 0),
        "author": hit.get("user") or "",
    }


def _request(provider: str, url: str, params: dict, page: int):
    try:
        return _get_with_rate_limit_retry(url, params=params, timeout=30).json()
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


def search_photos(provider: str, query: str, page: int = 1, per_page: int | None = None) -> dict:
    """Mot trang ket qua anh. Raise ``SearchOutOfRange`` / ``ProviderSearchError``.

    ``fullResMissing`` = so hit Pixabay bi bo vi key chua co full API access (chi
    co anh <=1280px); ``notice`` kem theo de UI noi ro.
    """
    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("Provider khong hop le (pixabay/pexels).")
    page = max(1, int(page or 1))
    per_page = _clamp_per_page(provider, per_page if per_page is not None else MAX_PER_PAGE[provider])

    if provider == "pexels":
        if not get_pexels_key_pool().size():
            raise ValueError("PEXELS_API_KEY chua cau hinh trong .env")
        params = {"query": query, "page": page, "per_page": per_page, "orientation": "landscape"}
        data = _request(provider, "https://api.pexels.com/v1/search", params, page)
        photos = [photo for photo in data.get("photos", []) if isinstance(photo, dict)]
        raw_items = [item for item in (_normalize_pexels_photo(photo) for photo in photos) if item]
        total = int(data.get("total_results") or 0)
        has_next = bool(data.get("next_page")) if "next_page" in data else page * per_page < total
        result = _finish(provider, raw_items, page, per_page, total, has_next)
        result["rawCount"] = len(photos)
        result["fullResMissing"] = 0
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
    }
    data = _request(provider, "https://pixabay.com/api/", params, page)
    hits = [hit for hit in data.get("hits", []) if isinstance(hit, dict)]
    raw_items = [item for item in (_normalize_pixabay_photo(hit) for hit in hits) if item]
    total = int(data.get("totalHits") or 0)
    full_res_missing = sum(
        1 for hit in hits if not (hit.get("imageURL") or hit.get("fullHDURL")) and hit.get("largeImageURL")
    )
    result = _finish(provider, raw_items, page, per_page, total, page * per_page < min(total, 500))
    result["rawCount"] = len(hits)
    result["fullResMissing"] = full_res_missing
    if full_res_missing:
        result["notice"] = PIXABAY_NO_FULL_ACCESS_NOTICE
    return result
