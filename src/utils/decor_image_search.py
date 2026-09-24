"""Search Pexels / Pixabay *photos* for decor images (khung TV).

Mirrors the video search in :mod:`src.utils.video_source_downloader` — same
rate-limit/network retry and Pexels key pool — but for stills, with a hard
filter every hit must pass: at least 1920x1080 and a landscape ratio within
±3% of 16:9. Providers can only pre-filter part of that (orientation, min size)
so the ratio check runs here, which is why a page can come back with fewer
items than the provider sent.
"""

import mimetypes
import os
from urllib.parse import urlparse

from src.config import Config
from src.utils.pexels_key_pool import get_pexels_key_pool
from src.utils.video_source_downloader import _download_file, _get_with_rate_limit_retry

MIN_WIDTH = 1920
MIN_HEIGHT = 1080
RATIO = 16 / 9
RATIO_TOLERANCE = 0.03

PEXELS_PER_PAGE = 80  # Pexels max
PIXABAY_PER_PAGE = 200  # Pixabay max

# Only these CDNs may be downloaded from: the import endpoint takes the URL from
# the client, so it must not become a way to make the server fetch anything.
_ALLOWED_DOWNLOAD_HOSTS = ("images.pexels.com", "pixabay.com", "cdn.pixabay.com")

PIXABAY_NO_FULL_ACCESS_NOTICE = (
    "Key Pixabay chưa có full API access — API chỉ trả ảnh tối đa 1280px nên "
    "các ảnh này đã bị ẩn. Xin full access tại pixabay.com/api/docs để dùng Pixabay."
)


def is_fullhd_16_9(width, height) -> bool:
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return False
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return False
    return abs(w / h - RATIO) / RATIO <= RATIO_TOLERANCE


def _normalize_pexels_photo(photo: dict) -> dict | None:
    src = photo.get("src") if isinstance(photo.get("src"), dict) else {}
    original = str(src.get("original") or "")
    if not original:
        return None
    return {
        "provider": "pexels",
        "id": str(photo.get("id") or ""),
        "title": str(photo.get("alt") or "").strip() or f"Pexels photo {photo.get('id')}",
        "thumbnailUrl": src.get("medium") or src.get("large") or original,
        # Originals run 6000px+ and 10MB+; the decor is scaled to the output
        # resolution anyway, so let the Pexels CDN resize before sending.
        "downloadUrl": f"{original}?auto=compress&cs=tinysrgb&w=1920",
        "pageUrl": photo.get("url") or "",
        "width": int(photo.get("width") or 0),
        "height": int(photo.get("height") or 0),
        "author": photo.get("photographer") or "",
    }


def search_pexels_images(query: str, page: int = 1) -> dict:
    if not get_pexels_key_pool().size():
        raise ValueError("PEXELS_API_KEY is not configured")

    # No `size` param: "large" means 24MP, which throws away most 1920x1080+ photos.
    params = {"query": query, "page": page, "per_page": PEXELS_PER_PAGE, "orientation": "landscape"}
    data = _get_with_rate_limit_retry("https://api.pexels.com/v1/search", params=params, timeout=30).json()

    photos = data.get("photos", [])
    items = [
        item for item in (_normalize_pexels_photo(p) for p in photos if isinstance(p, dict))
        if item and item["id"] and is_fullhd_16_9(item["width"], item["height"])
    ]
    total = int(data.get("total_results") or 0)
    return {
        "provider": "pexels",
        "items": items,
        "page": page,
        "rawCount": len(photos),
        "filteredOut": len(photos) - len(items),
        "hasMore": bool(data.get("next_page")) or page * PEXELS_PER_PAGE < total,
    }


def search_pixabay_images(query: str, page: int = 1) -> dict:
    api_key = Config.PIXABAY_API_KEY
    if not api_key:
        raise ValueError("PIXABAY_API_KEY is not configured")

    params = {
        "key": api_key,
        "q": query,
        "page": page,
        "per_page": PIXABAY_PER_PAGE,
        "image_type": "photo",
        "orientation": "horizontal",
        "min_width": MIN_WIDTH,
        "min_height": MIN_HEIGHT,
        "safesearch": "true",
    }
    data = _get_with_rate_limit_retry("https://pixabay.com/api/", params=params, timeout=30).json()

    hits = [hit for hit in data.get("hits", []) if isinstance(hit, dict)]
    items = []
    missing_full_res = 0
    for hit in hits:
        width, height = hit.get("imageWidth"), hit.get("imageHeight")
        if not is_fullhd_16_9(width, height):
            continue
        # Without full API access only largeImageURL (<=1280px) comes back;
        # that is below Full HD, so such hits are dropped rather than upscaled.
        download_url = hit.get("fullHDURL") or hit.get("imageURL")
        if not download_url:
            missing_full_res += 1
            continue
        tags = str(hit.get("tags") or "").strip()
        items.append({
            "provider": "pixabay",
            "id": str(hit.get("id") or ""),
            "title": tags or f"Pixabay photo {hit.get('id')}",
            "thumbnailUrl": hit.get("webformatURL") or hit.get("previewURL") or "",
            "downloadUrl": download_url,
            "pageUrl": hit.get("pageURL") or "",
            "width": int(width),
            "height": int(height),
            "author": hit.get("user") or "",
        })

    total = int(data.get("totalHits") or 0)
    result = {
        "provider": "pixabay",
        "items": [item for item in items if item["id"]],
        "page": page,
        "rawCount": len(hits),
        "filteredOut": len(hits) - len(items),
        "hasMore": page * PIXABAY_PER_PAGE < total,
    }
    if missing_full_res:
        result["notice"] = PIXABAY_NO_FULL_ACCESS_NOTICE
    return result


def search_provider_images(provider: str, query: str, page: int = 1) -> dict:
    if provider == "pexels":
        return search_pexels_images(query, page)
    if provider == "pixabay":
        return search_pixabay_images(query, page)
    raise ValueError("Unsupported provider")


def _is_allowed_download_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == allowed or host.endswith(f".{allowed}") for allowed in _ALLOWED_DOWNLOAD_HOSTS
    )


def download_provider_image(download_url: str, dest_dir: str, basename: str) -> str:
    """Download one searched photo into ``dest_dir``; returns the file path."""
    if not _is_allowed_download_url(download_url):
        raise ValueError("URL anh khong thuoc Pexels/Pixabay.")

    ext = os.path.splitext(urlparse(download_url).path)[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = mimetypes.guess_extension(mimetypes.guess_type(download_url)[0] or "") or ".jpg"
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, f"{basename}{ext}")
    if not _download_file(download_url, dest_path):
        raise RuntimeError("Khong tai duoc anh tu provider.")
    return dest_path
