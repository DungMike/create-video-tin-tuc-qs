"""Bulk harvest: tai TRUOC toan bo video theo tu khoa, chon loc SAU.

Luong cu (search -> preview tung ket qua tren luoi -> chon -> import) mo mot
stream toi CDN provider cho *moi* ket qua, va goi lai `search` moi lan lat trang.
Voi 200 ket qua/trang thi vua cham vua hao quota.

Luong nay dao thu tu lai. Diem mau chot: `previewUrl` tra ve tu search DA LA URL
file mp4 <=1080p (xem `_normalize_pixabay_video` / `_normalize_pexels_video`),
nen tai file **khong ton them mot API request nao** - chi `search` moi ton quota:

    search (1 request/trang) -> tai het ve staging -> preview tu o cung (mien phi)
    -> xoa video thua -> chi video giu lai moi cat clip + nhap thu vien

Video goc nam duoi STORY_RAW_DIR, ma route `/media/<path>` da biet map qua
prefix `story_raw/`, nen preview serve duoc ngay - khong can endpoint serve rieng.

Job state theo dung pattern cua story_library_bake.py: mot thu muc job chua
progress.json + manifest.json + marker file de dung.

Module nay KHONG sua doi luong import cu - no chi tai su dung cac ham cap thap
cua video_source_downloader.
"""

import os
import shutil
import threading
import uuid
from datetime import datetime

from src.config import Config
from src.utils.file_manager import remove_file_with_retries, storage_relative_path
from src.utils.logger import logger
from src.utils.story_video_pipeline import _load_json, _save_json
from src.utils.video_source_downloader import (
    _download_file,
    _ingest_clips,
    _target_clip_duration,
    search_provider_videos,
    split_into_clips,
)

SUPPORTED_PROVIDERS = ("pixabay", "pexels")

# Trang thai job khong the dieu khien tiep.
TERMINAL_HARVEST_STATUSES = {"completed", "failed", "cancelled", "stopped_disk"}

# Ty le toi thieu de coi la landscape 16:9. Nguong 1.5 tach 16:9 (1.78) khoi
# 4:3 (1.33) va vuong (1.0) - video 4:3 se bi pillarbox khi render 16:9.
_LANDSCAPE_MIN_RATIO = 1.5

# Chan an toan cho truong hop "tai het": Pixabay tu gioi han totalHits o 500,
# nhung Pexels co the tra ve hang nghin ket qua.
_MAX_PAGES_PER_QUERY = 200

# Ghi manifest theo checkpoint thay vi sau moi video - manifest duoc ghi de
# nguyen file, checkpoint giu so lan ghi o muc hop ly ma van chiu duoc crash.
_MANIFEST_CHECKPOINT_EVERY = 10

_job_locks_guard = threading.Lock()
_job_locks: dict[str, threading.RLock] = {}


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _job_lock(job_id: str) -> threading.RLock:
    with _job_locks_guard:
        lock = _job_locks.get(job_id)
        if lock is None:
            lock = threading.RLock()
            _job_locks[job_id] = lock
        return lock


# --------------------------------------------------------------------------- #
# Duong dan
# --------------------------------------------------------------------------- #
def _jobs_root() -> str:
    return os.path.join(Config.STORY_RAW_DIR, "_harvest_jobs")


def _job_dir(job_id: str) -> str:
    path = os.path.join(_jobs_root(), job_id)
    os.makedirs(path, exist_ok=True)
    return path


def staging_dir(job_id: str) -> str:
    path = os.path.join(Config.STORY_RAW_DIR, "harvest", job_id)
    os.makedirs(path, exist_ok=True)
    return path


def _staging_path(job_id: str, filename: str) -> str:
    """Duong dan file staging, KHONG tao thu muc (dung khi chi doc manifest)."""
    return os.path.join(Config.STORY_RAW_DIR, "harvest", job_id, filename)


def _progress_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "progress.json")


def _manifest_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "manifest.json")


def _cancel_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "cancel.requested")


# --------------------------------------------------------------------------- #
# Doc / ghi state
# --------------------------------------------------------------------------- #
def load_harvest_progress(job_id: str) -> dict | None:
    return _load_json(_progress_path(job_id))


def load_harvest_manifest(job_id: str) -> dict:
    data = _load_json(_manifest_path(job_id))
    if not isinstance(data, dict):
        return {"jobId": job_id, "items": []}
    data.setdefault("items", [])
    # Manifest cu (truoc khi storage_relative_path biet toi STORY_RAW_DIR) luu
    # duong dan tuyet doi khi STORY_RAW_DIR nam ngoai STORAGE_DIR, khien
    # previewPath thanh "/media/D:/..." va <video> khong phat duoc. Tinh lai theo
    # filename moi lan doc de job da tai xong tu lanh, khong phai tai lai.
    for item in data["items"]:
        filename = item.get("filename")
        if not filename:
            continue
        relative_path = storage_relative_path(_staging_path(job_id, filename))
        item["relativePath"] = relative_path
        item["previewPath"] = f"/media/{relative_path}"
    return data


def _save_progress(job_id: str, progress: dict):
    progress["updatedAt"] = _utc_now()
    _save_json(_progress_path(job_id), progress)


def _save_manifest(job_id: str, manifest: dict):
    _save_json(_manifest_path(job_id), manifest)


def is_harvest_cancel_requested(job_id: str) -> bool:
    return os.path.isfile(_cancel_path(job_id))


def request_harvest_cancel(job_id: str) -> dict | None:
    progress = load_harvest_progress(job_id)
    if not progress:
        return None
    if progress.get("status") not in TERMINAL_HARVEST_STATUSES:
        with open(_cancel_path(job_id), "w", encoding="utf-8") as file_obj:
            file_obj.write(_utc_now())
        progress["status"] = "cancelling"
        progress["message"] = "Dang huy, cho video hien tai tai xong..."
        _save_progress(job_id, progress)
    return progress


def list_harvest_jobs() -> list[dict]:
    """Moi job kem so video dang cho chon loc, moi nhat truoc."""
    root = _jobs_root()
    if not os.path.isdir(root):
        return []

    jobs = []
    for job_id in os.listdir(root):
        progress = load_harvest_progress(job_id)
        if not progress:
            continue
        items = load_harvest_manifest(job_id).get("items", [])
        progress["keptItems"] = sum(1 for item in items if item.get("status") == "kept")
        progress["totalItems"] = len(items)
        jobs.append(progress)

    jobs.sort(key=lambda job: job.get("startedAt", ""), reverse=True)
    return jobs


# --------------------------------------------------------------------------- #
# Loc
# --------------------------------------------------------------------------- #
def _is_landscape(item: dict) -> bool:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    return width / height >= _LANDSCAPE_MIN_RATIO


def _free_bytes(path: str) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


class _HarvestCancelled(RuntimeError):
    """Raised khi thay marker huy."""


# --------------------------------------------------------------------------- #
# Harvest job
# --------------------------------------------------------------------------- #
def start_harvest_job(
    *,
    keywords: list[str],
    providers: list[str],
    library_id: str,
    tags: list[str] | None = None,
    landscape_only: bool = True,
    max_per_keyword: int = 0,
    min_free_gb: float = 10.0,
) -> dict:
    """Tao job va chay o daemon thread. Tra ve progress dict ban dau."""
    clean_keywords = []
    seen = set()
    for keyword in keywords:
        text = str(keyword or "").strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            clean_keywords.append(text)
    if not clean_keywords:
        raise ValueError("Can it nhat 1 tu khoa.")

    requested = {str(item).strip().lower() for item in providers}
    clean_providers = [name for name in SUPPORTED_PROVIDERS if name in requested]
    if not clean_providers:
        raise ValueError("Can chon it nhat 1 provider (pixabay/pexels).")

    job_id = f"hv-{str(uuid.uuid4())[:8]}"
    staging_dir(job_id)

    progress = {
        "jobId": job_id,
        "status": "running",
        "libraryId": library_id,
        "keywords": clean_keywords,
        "providers": clean_providers,
        "tags": [str(tag).strip() for tag in (tags or []) if str(tag).strip()],
        "landscapeOnly": bool(landscape_only),
        "maxPerKeyword": max(0, int(max_per_keyword or 0)),
        "keywordIndex": 0,
        "keywordTotal": len(clean_keywords),
        "currentKeyword": clean_keywords[0],
        "currentProvider": clean_providers[0],
        "searchRequests": 0,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "bytesDownloaded": 0,
        "message": "Bat dau tai hang loat...",
        "startedAt": _utc_now(),
        "updatedAt": _utc_now(),
        "error": None,
    }
    _save_progress(job_id, progress)
    _save_manifest(job_id, {"jobId": job_id, "items": []})

    threading.Thread(target=_run_harvest, args=(job_id, float(min_free_gb)), daemon=True).start()
    return progress


def _run_harvest(job_id: str, min_free_gb: float):
    progress = load_harvest_progress(job_id) or {}
    manifest = load_harvest_manifest(job_id)
    items = manifest["items"]
    seen_keys = {f"{item['provider']}:{item['videoId']}" for item in items}
    dest_dir = staging_dir(job_id)
    min_free_bytes = int(max(0.0, min_free_gb) * 1024 ** 3)
    pending_writes = 0

    def flush(force: bool = False):
        nonlocal pending_writes
        if force or pending_writes >= _MANIFEST_CHECKPOINT_EVERY:
            _save_manifest(job_id, manifest)
            pending_writes = 0
        _save_progress(job_id, progress)

    try:
        for keyword_index, keyword in enumerate(progress["keywords"]):
            progress["keywordIndex"] = keyword_index
            progress["currentKeyword"] = keyword

            for provider in progress["providers"]:
                progress["currentProvider"] = provider
                taken = 0
                page = 1

                while page <= _MAX_PAGES_PER_QUERY:
                    if is_harvest_cancel_requested(job_id):
                        raise _HarvestCancelled()

                    progress["message"] = f"Dang search '{keyword}' tren {provider} (trang {page})..."
                    flush()

                    response = search_provider_videos(
                        provider,
                        keyword,
                        page,
                        per_page=None,
                        orientation="landscape" if progress.get("landscapeOnly") else None,
                    )
                    progress["searchRequests"] += 1

                    results = response.get("items") or []
                    per_page = int(response.get("perPage") or len(results) or 1)
                    total = int(response.get("total") or 0)

                    for item in results:
                        if is_harvest_cancel_requested(job_id):
                            raise _HarvestCancelled()
                        if progress["maxPerKeyword"] and taken >= progress["maxPerKeyword"]:
                            break

                        video_id = str(item.get("id") or "").strip()
                        download_url = str(item.get("previewUrl") or "").strip()
                        if not video_id or not download_url:
                            progress["skipped"] += 1
                            continue

                        # Pixabay khong co tham so orientation nen phai loc o day;
                        # Pexels da loc o API nhung van kiem tra lai cho chac.
                        if progress.get("landscapeOnly") and not _is_landscape(item):
                            progress["skipped"] += 1
                            continue

                        key = f"{provider}:{video_id}"
                        if key in seen_keys:
                            progress["skipped"] += 1
                            continue

                        if _free_bytes(dest_dir) < min_free_bytes:
                            progress["status"] = "stopped_disk"
                            progress["message"] = (
                                f"Dung lai: o dia con duoi {min_free_gb:.0f}GB trong. "
                                f"Da tai {progress['downloaded']} video."
                            )
                            flush(force=True)
                            logger.warning(f"[Harvest] {job_id} stopped: low disk space")
                            return

                        filename = f"{provider}_{video_id}_{uuid.uuid4().hex[:8]}.mp4"
                        dest_path = os.path.join(dest_dir, filename)
                        progress["message"] = (
                            f"'{keyword}' | {provider} | dang tai video "
                            f"{progress['downloaded'] + 1}: {video_id}"
                        )
                        flush()

                        if not _download_file(download_url, dest_path):
                            progress["failed"] += 1
                            continue

                        try:
                            size_bytes = os.path.getsize(dest_path)
                        except OSError:
                            size_bytes = 0

                        seen_keys.add(key)
                        taken += 1
                        relative_path = storage_relative_path(dest_path)
                        items.append({
                            "itemId": str(uuid.uuid4()),
                            "provider": provider,
                            "videoId": video_id,
                            "keyword": keyword,
                            "title": item.get("title") or "",
                            "filename": filename,
                            "relativePath": relative_path,
                            "previewPath": f"/media/{relative_path}",
                            "duration": item.get("duration") or 0,
                            "width": item.get("width") or 0,
                            "height": item.get("height") or 0,
                            "pageUrl": item.get("pageUrl") or "",
                            "author": item.get("author") or "",
                            "bytes": size_bytes,
                            "status": "kept",
                            "createdAt": _utc_now(),
                        })
                        progress["downloaded"] += 1
                        progress["bytesDownloaded"] += size_bytes
                        pending_writes += 1
                        flush()

                    if progress["maxPerKeyword"] and taken >= progress["maxPerKeyword"]:
                        break
                    if not results or page * per_page >= total:
                        break
                    page += 1

                flush(force=True)

        progress["keywordIndex"] = progress["keywordTotal"]
        progress["status"] = "completed"
        progress["message"] = (
            f"Hoan tat! Da tai {progress['downloaded']} video "
            f"({progress['searchRequests']} request search). Chuyen sang buoc chon loc."
        )
        flush(force=True)

    except _HarvestCancelled:
        progress["status"] = "cancelled"
        progress["message"] = f"Da huy. Giu lai {progress['downloaded']} video da tai."
        flush(force=True)
    except Exception as exc:
        logger.error(f"[Harvest] {job_id} failed: {exc}", exc_info=True)
        progress["status"] = "failed"
        progress["error"] = str(exc)
        progress["message"] = f"Loi: {exc}"
        flush(force=True)


# --------------------------------------------------------------------------- #
# Chon loc
# --------------------------------------------------------------------------- #
def delete_harvest_items(job_id: str, item_ids: list[str] | None = None, *, delete_all: bool = False) -> dict:
    """Xoa file staging va danh dau item la 'deleted' trong manifest."""
    targets = set(item_ids or [])
    deleted = 0
    failed: list[str] = []

    with _job_lock(job_id):
        manifest = load_harvest_manifest(job_id)
        for item in manifest["items"]:
            if item.get("status") != "kept":
                continue
            if not delete_all and item.get("itemId") not in targets:
                continue

            path = os.path.join(staging_dir(job_id), item.get("filename") or "")
            if remove_file_with_retries(path):
                item["status"] = "deleted"
                deleted += 1
            else:
                failed.append(item.get("itemId"))
        _save_manifest(job_id, manifest)
        remaining = sum(1 for item in manifest["items"] if item.get("status") == "kept")

    return {"deletedCount": deleted, "failedItemIds": failed, "remainingCount": remaining}


def commit_harvest_job(
    job_id: str,
    *,
    library_id: str,
    session_id: str,
    progress_callback=None,
    delete_staging: bool = True,
) -> list[dict]:
    """Cat clip + nhap thu vien cho cac video con giu lai.

    Tai su dung nguyen `split_into_clips` + `_ingest_clips` cua luong import cu,
    nen thu vien da bake van tu dong bake lai clip moi.
    """
    with _job_lock(job_id):
        manifest = load_harvest_manifest(job_id)
        pending = [item for item in manifest["items"] if item.get("status") == "kept"]

    job_tags = (load_harvest_progress(job_id) or {}).get("tags") or []
    clip_duration = _target_clip_duration(library_id)
    all_added: list[dict] = []
    total = len(pending)

    for index, item in enumerate(pending):
        video_path = os.path.join(staging_dir(job_id), item.get("filename") or "")
        provider = item.get("provider") or "direct"
        keyword = item.get("keyword") or ""

        if not os.path.isfile(video_path):
            logger.warning(f"[Harvest] {job_id}: missing staged file {video_path}")
            continue

        if progress_callback:
            progress_callback({
                "stage": "splitting",
                "current": index + 1,
                "total": total,
                "message": f"Cat clip {index + 1}/{total}: {keyword} | {provider} {item.get('videoId')}",
            })

        clips = split_into_clips(video_path, clip_duration)
        if clips:
            clip_tags = [provider, f"session:{session_id}", f"keyword:{keyword}", *job_tags]
            added = _ingest_clips(
                clips, provider, clip_tags, library_id=library_id,
                session_id=session_id, progress_callback=progress_callback,
                current=index + 1, total=total,
            )
            all_added.extend(added)

        with _job_lock(job_id):
            manifest = load_harvest_manifest(job_id)
            for entry in manifest["items"]:
                if entry.get("itemId") == item.get("itemId"):
                    entry["status"] = "committed"
                    break
            _save_manifest(job_id, manifest)

    if delete_staging:
        _clear_staging(job_id)

    if progress_callback:
        progress_callback({
            "stage": "complete",
            "current": total,
            "total": total,
            "message": f"Hoan tat! Da them {len(all_added)} clip tu {total} video.",
        })

    return all_added


def _clear_staging(job_id: str):
    """Xoa ca video goc lan file clip tam do split_into_clips sinh ra."""
    path = os.path.join(Config.STORY_RAW_DIR, "harvest", job_id)
    if not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logger.warning(f"[Harvest] Could not remove staging dir {path}: {exc}")


def delete_harvest_job(job_id: str) -> bool:
    """Xoa job va toan bo file staging cua no."""
    _clear_staging(job_id)
    job_path = os.path.join(_jobs_root(), job_id)
    if not os.path.isdir(job_path):
        return False
    try:
        shutil.rmtree(job_path)
    except OSError as exc:
        logger.warning(f"[Harvest] Could not remove job dir {job_path}: {exc}")
        return False
    return True
