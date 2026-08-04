import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from src.config import Config
from src.utils.clip_canonical import (
    canonical_output_args,
    canonical_video_filter,
    keyframe_args,
)
from src.utils.clip_spec_validation import probe_clip_spec
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger
from src.utils.story_library import (
    _clips_dir,
    _index_lock,
    library_clip_duration,
    load_story_library_index,
    save_story_library_index,
)

_TARGET_PROVIDER_VIDEO_HEIGHT = 1080

# Provider APIs (Pexels 200 req/h, Pixabay 100 req/min) trả 429 khi vượt quota.
# Retry với exponential backoff, tôn trọng Retry-After nếu server gửi kèm.
_RATE_LIMIT_MAX_RETRIES = 4
_RATE_LIMIT_BASE_DELAY = 2.0  # giây


def _get_with_rate_limit_retry(url, *, headers=None, params=None, timeout=30):
    """GET có retry/backoff cho HTTP 429. Trả về Response hoặc raise ở lần cuối."""
    for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code != 429 or attempt == _RATE_LIMIT_MAX_RETRIES:
            resp.raise_for_status()
            return resp
        retry_after = resp.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
        except (TypeError, ValueError):
            delay = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
        logger.warning(
            f"Rate limited (429) on {url} — retry {attempt + 1}/{_RATE_LIMIT_MAX_RETRIES} sau {delay:.1f}s"
        )
        time.sleep(delay)
    # Không thể tới đây, nhưng để an toàn kiểu trả về:
    resp.raise_for_status()
    return resp


def _ensure_dirs(library_id=None):
    os.makedirs(Config.STORY_RAW_DIR, exist_ok=True)
    os.makedirs(_clips_dir(library_id), exist_ok=True)


def _target_clip_duration(library_id=None) -> int:
    """Clip length to cut for the library being filled.

    A library that recorded its own `clipDuration` wins; everything else follows
    Config.STORY_CLIP_DURATION. Either way new clips come out the same length as
    the segment the render trims each clip to, so a library that moved from 5s to
    3s doesn't keep accumulating 5s clips.
    """
    return max(1, library_clip_duration(library_id, max(1, int(Config.STORY_CLIP_DURATION))))


def _detect_source(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    if "pixabay" in hostname:
        return "pixabay"
    if "pexels" in hostname:
        return "pexels"
    return "direct"


def _extract_pixabay_id(url: str) -> str | None:
    # Handles: /videos/slug-ID/, /vi/videos/slug-ID/, /de/videos/slug-ID/ etc.
    match = re.search(r"pixabay\.com/(?:[a-z]{2}/)?videos/[^/]*?(\d+)/?$", url)
    if match:
        return match.group(1)
    # Fallback: any path ending with -DIGITS
    match = re.search(r"pixabay\.com/.*-(\d+)/?$", url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=(\d+)", url)
    return match.group(1) if match else None


def _extract_pexels_id(url: str) -> str | None:
    match = re.search(r"pexels\.com/video/[^/]*?(\d+)/?$", url)
    if match:
        return match.group(1)
    match = re.search(r"pexels\.com/video/(\d+)", url)
    return match.group(1) if match else None


def _resolve_pixabay_download_url(video_id: str) -> str | None:
    api_key = Config.PIXABAY_API_KEY
    if not api_key:
        logger.error("PIXABAY_API_KEY not configured")
        return None
    api_url = f"https://pixabay.com/api/videos/?key={api_key}&id={video_id}"
    try:
        resp = _get_with_rate_limit_retry(api_url, timeout=30)
        data = resp.json()
        hits = data.get("hits", [])
        if not hits:
            logger.error(f"Pixabay video not found: {video_id}")
            return None
        selected = _select_pixabay_video_file(hits[0].get("videos", {}))
        download_url = selected.get("url") if selected else None
        if download_url:
            return download_url
        logger.error(f"No downloadable URL in Pixabay response for {video_id}")
        return None
    except Exception as exc:
        logger.error(f"Pixabay API error for video {video_id}: {exc}")
        return None


def _resolve_pexels_download_url(video_id: str) -> str | None:
    api_key = Config.PEXELS_API_KEY
    if not api_key:
        logger.error("PEXELS_API_KEY not configured")
        return None
    api_url = f"https://api.pexels.com/videos/videos/{video_id}"
    try:
        resp = _get_with_rate_limit_retry(api_url, headers={"Authorization": api_key}, timeout=30)
        data = resp.json()
        video_files = data.get("video_files", [])
        if not video_files:
            logger.error(f"Pexels video not found: {video_id}")
            return None
        selected = _select_pexels_video_file(video_files)
        return selected.get("link") if selected else None
    except Exception as exc:
        logger.error(f"Pexels API error for video {video_id}: {exc}")
        return None


def _download_file(url: str, dest_path: str) -> bool:
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as exc:
        logger.error(f"Download failed {url}: {exc}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def _select_height_limited_file(candidates: list[dict], url_key: str) -> dict | None:
    usable = [
        item for item in candidates
        if item.get(url_key) and isinstance(item.get("height"), int) and item.get("height", 0) > 0
    ]
    if not usable:
        return None

    at_or_below = [item for item in usable if item.get("height", 0) <= _TARGET_PROVIDER_VIDEO_HEIGHT]
    if at_or_below:
        return max(at_or_below, key=lambda item: (item.get("height", 0), item.get("width", 0), item.get("size", 0)))
    return min(usable, key=lambda item: (item.get("height", 99999), item.get("width", 99999)))


def _select_pixabay_video_file(videos: dict) -> dict | None:
    if not isinstance(videos, dict):
        return None
    return _select_height_limited_file(
        [entry for entry in videos.values() if isinstance(entry, dict)],
        "url",
    )


def _select_pexels_video_file(video_files: list[dict]) -> dict | None:
    mp4_files = [
        vf for vf in video_files
        if isinstance(vf, dict) and vf.get("link") and str(vf.get("file_type", "")).lower() == "video/mp4"
    ]
    return _select_height_limited_file(mp4_files, "link")


def _normalize_pixabay_video(hit: dict) -> dict | None:
    selected = _select_pixabay_video_file(hit.get("videos", {}))
    if not selected:
        return None
    width = selected.get("width") or hit.get("width") or 0
    height = selected.get("height") or hit.get("height") or 0
    tags = str(hit.get("tags") or "").strip()
    return {
        "provider": "pixabay",
        "id": str(hit.get("id") or ""),
        "title": tags or f"Pixabay video {hit.get('id')}",
        "tags": tags,
        "thumbnailUrl": hit.get("previewURL") or hit.get("picture_url") or "",
        "previewUrl": selected.get("url") or "",
        "pageUrl": hit.get("pageURL") or "",
        "duration": hit.get("duration") or 0,
        "width": width,
        "height": height,
        "author": hit.get("user") or "",
    }


def _normalize_pexels_video(video: dict) -> dict | None:
    selected = _select_pexels_video_file(video.get("video_files", []))
    if not selected:
        return None
    user = video.get("user") if isinstance(video.get("user"), dict) else {}
    width = selected.get("width") or video.get("width") or 0
    height = selected.get("height") or video.get("height") or 0
    return {
        "provider": "pexels",
        "id": str(video.get("id") or ""),
        "title": f"Pexels video {video.get('id')}",
        "tags": "",
        "thumbnailUrl": video.get("image") or "",
        "previewUrl": selected.get("link") or "",
        "pageUrl": video.get("url") or "",
        "duration": video.get("duration") or 0,
        "width": width,
        "height": height,
        "author": user.get("name") or "",
    }


def search_pixabay_videos(query: str, page: int = 1, per_page: int = 20) -> dict:
    api_key = Config.PIXABAY_API_KEY
    if not api_key:
        raise ValueError("PIXABAY_API_KEY is not configured")

    resp = requests.get(
        "https://pixabay.com/api/videos/",
        params={
            "key": api_key,
            "q": query,
            "page": page,
            "per_page": per_page,
            "safesearch": "true",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    items = [
        item for item in (_normalize_pixabay_video(hit) for hit in data.get("hits", []))
        if item and item.get("id") and item.get("previewUrl")
    ]
    return {
        "provider": "pixabay",
        "items": items,
        "total": int(data.get("totalHits") or data.get("total") or len(items)),
        "page": page,
        "perPage": per_page,
    }


def search_pexels_videos(query: str, page: int = 1, per_page: int = 20) -> dict:
    api_key = Config.PEXELS_API_KEY
    if not api_key:
        raise ValueError("PEXELS_API_KEY is not configured")

    resp = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "page": page, "per_page": per_page},
        headers={"Authorization": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    items = [
        item for item in (_normalize_pexels_video(video) for video in data.get("videos", []))
        if item and item.get("id") and item.get("previewUrl")
    ]
    return {
        "provider": "pexels",
        "items": items,
        "total": int(data.get("total_results") or len(items)),
        "page": page,
        "perPage": per_page,
    }


# Maximum results per page each provider's API supports.
PIXABAY_MAX_PER_PAGE = 200  # Pixabay: per_page valid 3-200
PEXELS_MAX_PER_PAGE = 80  # Pexels: per_page valid 1-80


def search_provider_videos(provider: str, query: str, page: int = 1, per_page: int | None = None) -> dict:
    if provider == "pixabay":
        size = PIXABAY_MAX_PER_PAGE if per_page is None else max(3, min(PIXABAY_MAX_PER_PAGE, per_page))
        return search_pixabay_videos(query, page, size)
    if provider == "pexels":
        size = PEXELS_MAX_PER_PAGE if per_page is None else max(1, min(PEXELS_MAX_PER_PAGE, per_page))
        return search_pexels_videos(query, page, size)
    raise ValueError("Unsupported provider")


def _resolve_download_url(link: str, source_type: str) -> str | None:
    if source_type == "pixabay":
        video_id = _extract_pixabay_id(link)
        if not video_id:
            logger.error(f"Cannot extract Pixabay video ID from: {link}")
            return None
        return _resolve_pixabay_download_url(video_id)

    if source_type == "pexels":
        video_id = _extract_pexels_id(link)
        if not video_id:
            logger.error(f"Cannot extract Pexels video ID from: {link}")
            return None
        return _resolve_pexels_download_url(video_id)

    return link


def _resolve_provider_download_url(provider: str, video_id: str) -> str | None:
    if provider == "pixabay":
        return _resolve_pixabay_download_url(video_id)
    if provider == "pexels":
        return _resolve_pexels_download_url(video_id)
    return None


def split_into_clips(video_path: str, clip_duration: int | None = None) -> list[str]:
    """Cut a downloaded/uploaded source video into canonical library clips.

    The source's own resolution/pix_fmt/color tags are probed first so the encode
    can convert them (not just relabel them) to the one spec the render accepts —
    otherwise a Pixabay clip tagged smpte170m or full-range lands in the library
    and is silently excluded from every render. See src/utils/clip_canonical.py.
    """
    duration = clip_duration or Config.STORY_CLIP_DURATION
    output_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    clip_pattern = os.path.join(output_dir, f"{base_name}_clip_%03d.mp4")
    source_spec = probe_clip_spec(video_path)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", canonical_video_filter(source_spec),
        "-an",
        *keyframe_args(duration),
        "-f", "segment",
        "-segment_time", str(duration),
        "-segment_time_delta", "0.05",
        "-reset_timestamps", "1",
    ]
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(canonical_output_args())
    cmd.append(clip_pattern)

    if not FFmpegHelper.run_command(cmd):
        logger.error(f"Failed to split video into clips: {video_path}")
        return []

    clips = sorted([
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.startswith(f"{base_name}_clip_") and f.endswith(".mp4")
    ])

    # The segment muxer leaves a remainder clip shorter than the segment time
    # (e.g. a 28s source yields 5x5s + 3s). Short clips would otherwise flow
    # into the library/bake and pollute it, so drop them here.
    min_duration = duration - 0.1
    kept: list[str] = []
    for clip_path in clips:
        clip_len = FFmpegHelper.probe_duration(clip_path)
        if clip_len >= min_duration:
            kept.append(clip_path)
            continue
        try:
            os.remove(clip_path)
        except OSError as exc:
            logger.warning(f"Could not delete short clip {clip_path}: {exc}")
        logger.info(
            f"Dropped short clip {os.path.basename(clip_path)} "
            f"({clip_len:.2f}s < {min_duration:.2f}s)"
        )

    logger.info(
        f"Split {video_path} into {len(kept)} clips "
        f"(segment={duration}s, dropped {len(clips) - len(kept)} short)"
    )
    return kept


def _ingest_clips(
    clips: list[str],
    source_type: str,
    tags: list[str] | None = None,
    library_id=None,
    session_id: str | None = None,
    progress_callback=None,
    current: int = 0,
    total: int = 0,
) -> list[dict]:
    """Route freshly split clips into the target library.

    Styled/baked libraries re-bake the clips (style + waveform + CTA) with the
    library's own stored metadata so new clips match existing baked units; normal
    libraries store the raw clips as-is.
    """
    from src.utils.story_library import get_library

    record = get_library(library_id) if library_id else None
    if record and record.get("styled"):
        from src.utils.story_library_bake import bake_and_append_clips

        if progress_callback:
            progress_callback({
                "stage": "baking",
                "current": current,
                "total": total,
                "message": f"Đang bake video {current}/{total} theo hiệu ứng thư viện...",
            })
        return bake_and_append_clips(
            library_id,
            clips,
            extra_tags=tags,
            source_type=source_type,
            session_id=session_id,
        )
    return add_clips_to_library(clips, source_type, tags, library_id=library_id)


def add_clips_to_library(
    clips: list[str],
    source_type: str,
    tags: list[str] | None = None,
    library_id=None,
) -> list[dict]:
    _ensure_dirs(library_id)
    clips_dir = _clips_dir(library_id)
    added = []

    with _index_lock(library_id):
        index = load_story_library_index(library_id)

        for clip_path in clips:
            clip_id = str(uuid.uuid4())
            ext = os.path.splitext(clip_path)[1] or ".mp4"
            dest_name = f"{clip_id}{ext}"
            dest_path = os.path.join(clips_dir, dest_name)

            try:
                shutil.copy2(clip_path, dest_path)
            except OSError as exc:
                logger.error(f"Failed to copy clip to library: {exc}")
                continue

            duration = FFmpegHelper.probe_duration(dest_path)
            source_name = os.path.basename(clip_path)

            asset = {
                "id": clip_id,
                "source_type": source_type,
                "source_name": source_name,
                "relative_path": f"clips/{dest_name}",
                "duration": round(duration, 3),
                "tags": tags or [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            index["assets"].append(asset)
            added.append(asset)

        save_story_library_index(index, library_id)

    logger.info(f"Added {len(added)} clips to story library (source: {source_type})")
    return added


def download_from_links(
    links: list[str],
    session_id: str,
    tags: list[str] | None = None,
    progress_callback=None,
    library_id=None,
) -> list[dict]:
    _ensure_dirs(library_id)
    session_dir = os.path.join(Config.STORY_RAW_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    all_added = []
    total = len(links)

    for idx, link in enumerate(links):
        link = link.strip()
        if not link:
            continue

        if progress_callback:
            progress_callback({
                "stage": "downloading",
                "current": idx + 1,
                "total": total,
                "message": f"Downloading {idx + 1}/{total}: {link[:80]}",
            })

        source_type = _detect_source(link)
        download_url = _resolve_download_url(link, source_type)
        if not download_url:
            logger.warning(f"Skipping unresolvable link: {link}")
            continue

        file_ext = ".mp4"
        parsed_path = urlparse(download_url).path
        if parsed_path.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
            file_ext = os.path.splitext(parsed_path)[1]

        dest_filename = f"{source_type}_{idx:03d}_{uuid.uuid4().hex[:8]}{file_ext}"
        dest_path = os.path.join(session_dir, dest_filename)

        if not _download_file(download_url, dest_path):
            continue

        logger.info(f"Downloaded {source_type} video: {dest_path}")

        if progress_callback:
            progress_callback({
                "stage": "splitting",
                "current": idx + 1,
                "total": total,
                "message": f"Splitting {idx + 1}/{total} into clips",
            })

        clips = split_into_clips(dest_path, _target_clip_duration(library_id))
        if clips:
            clip_tags = [source_type, f"session:{session_id}", *(tags or [])]
            added = _ingest_clips(
                clips, source_type, clip_tags, library_id=library_id,
                session_id=session_id, progress_callback=progress_callback,
                current=idx + 1, total=total,
            )
            all_added.extend(added)

    if progress_callback:
        progress_callback({
            "stage": "complete",
            "current": total,
            "total": total,
            "message": f"Finished processing {total} links, {len(all_added)} clips added",
        })

    return all_added


def download_from_provider_items(
    items: list[dict],
    session_id: str,
    tags: list[str] | None = None,
    progress_callback=None,
    library_id=None,
) -> list[dict]:
    _ensure_dirs(library_id)
    session_dir = os.path.join(Config.STORY_RAW_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    all_added = []
    total = len(items)

    for idx, item in enumerate(items):
        provider = str(item.get("provider") or "").strip().lower()
        video_id = str(item.get("id") or "").strip()
        if provider not in {"pixabay", "pexels"} or not video_id:
            logger.warning(f"Skipping invalid provider item: {item}")
            continue

        if progress_callback:
            progress_callback({
                "stage": "downloading",
                "current": idx + 1,
                "total": total,
                "message": f"Downloading {provider} video {idx + 1}/{total}: {video_id}",
            })

        # Ưu tiên URL download đã có sẵn từ kết quả search để tránh gọi lại
        # API resolve từng video (nguyên nhân chính gây 429 rate limit).
        download_url = str(item.get("downloadUrl") or "").strip()
        if not download_url:
            download_url = _resolve_provider_download_url(provider, video_id)
        if not download_url:
            logger.warning(f"Skipping unresolvable {provider} video: {video_id}")
            continue

        dest_filename = f"{provider}_{idx:03d}_{video_id}_{uuid.uuid4().hex[:8]}.mp4"
        dest_path = os.path.join(session_dir, dest_filename)
        if not _download_file(download_url, dest_path):
            continue

        if progress_callback:
            progress_callback({
                "stage": "splitting",
                "current": idx + 1,
                "total": total,
                "message": f"Splitting {provider} video {idx + 1}/{total} into clips",
            })

        clips = split_into_clips(dest_path, _target_clip_duration(library_id))
        if clips:
            clip_tags = [provider, f"session:{session_id}", *(tags or [])]
            added = _ingest_clips(
                clips, provider, clip_tags, library_id=library_id,
                session_id=session_id, progress_callback=progress_callback,
                current=idx + 1, total=total,
            )
            all_added.extend(added)

    if progress_callback:
        progress_callback({
            "stage": "complete",
            "current": total,
            "total": total,
            "message": f"Finished processing {total} provider videos, {len(all_added)} clips added",
        })

    return all_added


def process_local_uploads(
    file_paths: list[str],
    session_id: str,
    tags: list[str] | None = None,
    progress_callback=None,
    library_id=None,
) -> list[dict]:
    _ensure_dirs(library_id)
    session_dir = os.path.join(Config.STORY_RAW_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    all_added = []
    total = len(file_paths)

    for idx, src_path in enumerate(file_paths):
        if not os.path.isfile(src_path):
            logger.warning(f"File not found, skipping: {src_path}")
            continue

        if progress_callback:
            progress_callback({
                "stage": "copying",
                "current": idx + 1,
                "total": total,
                "message": f"Processing upload {idx + 1}/{total}: {os.path.basename(src_path)}",
            })

        dest_filename = f"local_{idx:03d}_{uuid.uuid4().hex[:8]}{os.path.splitext(src_path)[1]}"
        dest_path = os.path.join(session_dir, dest_filename)

        try:
            shutil.copy2(src_path, dest_path)
        except OSError as exc:
            logger.error(f"Failed to copy uploaded file {src_path}: {exc}")
            continue

        if progress_callback:
            progress_callback({
                "stage": "splitting",
                "current": idx + 1,
                "total": total,
                "message": f"Splitting upload {idx + 1}/{total} into clips",
            })

        clips = split_into_clips(dest_path, _target_clip_duration(library_id))
        if clips:
            clip_tags = ["local_upload", f"session:{session_id}", *(tags or [])]
            added = _ingest_clips(
                clips, "local_upload", clip_tags, library_id=library_id,
                session_id=session_id, progress_callback=progress_callback,
                current=idx + 1, total=total,
            )
            all_added.extend(added)

    if progress_callback:
        progress_callback({
            "stage": "complete",
            "current": total,
            "total": total,
            "message": f"Finished processing {total} uploads, {len(all_added)} clips added",
        })

    return all_added
