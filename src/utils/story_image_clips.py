"""Thu vien clip tu anh: tim anh -> tai ve staging -> duyet -> 1 anh = 1 clip Ken Burns.

Cung khuon voi ``story_bulk_harvest`` (thu muc job + progress.json + manifest.json
+ marker huy), nhung khac o ba cho:

- **Con tro phan trang** (``src/db/image_repo.py``): moi cap (provider, tu khoa) tim
  tiep tu trang lan truoc dung lai, voi dung ``per_page`` da dung. Ghi sau MOI
  trang, nen huy / restart giua chung chi mat toi da mot trang (lan sau tai lai
  trang do va bo id da biet).
- **Chong trung toan he thong**: anh da staged/rejected/committed o bat ky job nao
  khong bao gio duoc tai lai (``source_images``).
- **Commit render Ken Burns** (``image_clip_effects``) roi them vao thu vien THEO LO:
  ``index.json`` that nang 5-24 MB, ghi tung clip thi 1000 clip ton ~10 phut lock.

Tinh nang nay can MongoDB (con tro + chong trung). Luong video cu khong goi toi
module nay.

Chi mot job search chay mot luc (hai job cung ghi mot con tro se de len nhau).
Guard dua vao registry thread trong process, KHONG dua vao dong ho "stale": mot
job con song co the im lang lau vi retry mang / cho quota.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from src.config import Config
from src.db import image_repo
from src.db.keyword_repo import normalize_keyword
from src.utils import image_clip_effects as effects
from src.utils import image_clip_search as search
from src.utils import video_source_downloader as vsd
from src.utils.file_manager import remove_file_with_retries, storage_relative_path
from src.utils.logger import logger
from src.utils.pexels_key_pool import PexelsQuotaExhausted
from src.utils.story_video_pipeline import _load_json, _save_json

SUPPORTED_PROVIDERS = search.PROVIDERS
RUNNING_STATUSES = {"running", "cancelling"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "stopped_disk"}

DEFAULT_MAX_PER_KEYWORD = 100
# Chan an toan cho "0 = tai toi khi het": Pexels co the tra hang nghin trang.
MAX_PAGES_PER_RUN = 50
INGEST_BATCH = 25
MIN_FREE_GB = 10.0

_JOB_ID_RE = re.compile(r"^ic-[0-9a-f]{8}$")

_job_locks_guard = threading.Lock()
_job_locks: dict[str, threading.RLock] = {}

# "search:<job>" / "commit:<job>" -> thread dang chay trong process nay.
_registry_lock = threading.Lock()
_live: dict[str, threading.Thread] = {}


class ImageClipError(ValueError):
    """Request khong hop le (route tra 400)."""


class ImageClipBusy(RuntimeError):
    """Dang co job/commit chay (route tra 409)."""


class LibraryHasMotion(ImageClipError):
    """Thu vien dich bake san chuyen dong: clip Ken Burns se bi chong hai lan."""


class _Cancelled(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _job_lock(job_id: str) -> threading.RLock:
    with _job_locks_guard:
        lock = _job_locks.get(job_id)
        if lock is None:
            lock = threading.RLock()
            _job_locks[job_id] = lock
        return lock


# --------------------------------------------------------------------------- #
# Registry thread
# --------------------------------------------------------------------------- #
def is_live(kind: str, job_id: str) -> bool:
    with _registry_lock:
        thread = _live.get(f"{kind}:{job_id}")
        return bool(thread and thread.is_alive())


def active_search_job() -> str | None:
    with _registry_lock:
        for name, thread in _live.items():
            if name.startswith("search:") and thread.is_alive():
                return name.split(":", 1)[1]
    return None


def _start_thread(kind: str, job_id: str, target, *args) -> None:
    """Chay ``target`` o daemon thread va giu no trong registry toi khi xong.

    Goi khi DANG giu ``_registry_lock`` (kiem tra ban + dang ky la mot buoc).
    """
    name = f"{kind}:{job_id}"

    def runner():
        try:
            target(*args)
        finally:
            with _registry_lock:
                if _live.get(name) is threading.current_thread():
                    _live.pop(name, None)

    thread = threading.Thread(target=runner, name=f"image-clip-{kind}-{job_id}", daemon=True)
    _live[name] = thread
    thread.start()


def wait_for(kind: str, job_id: str, timeout: float = 30.0) -> bool:
    """Cho thread ``kind`` cua job xong (dung cho test). True neu xong kip."""
    with _registry_lock:
        thread = _live.get(f"{kind}:{job_id}")
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


# --------------------------------------------------------------------------- #
# Duong dan + state
# --------------------------------------------------------------------------- #
def is_valid_job_id(job_id) -> bool:
    return bool(_JOB_ID_RE.match(str(job_id or "")))


def _jobs_root() -> str:
    return os.path.join(Config.STORY_RAW_DIR, "_image_clip_jobs")


def _job_dir(job_id: str) -> str:
    """Thu muc job, KHONG tao (doc manifest cua id la khong duoc de lai thu muc rac;
    ``_save_json`` tu tao khi ghi)."""
    return os.path.join(_jobs_root(), job_id)


def _staging_root(job_id: str) -> str:
    return os.path.join(Config.STORY_RAW_DIR, "image_clips", job_id)


def staging_dir(job_id: str) -> str:
    path = _staging_root(job_id)
    os.makedirs(path, exist_ok=True)
    return path


def _staging_path(job_id: str, filename: str) -> str:
    return os.path.join(_staging_root(job_id), filename)


def _progress_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "progress.json")


def _manifest_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "manifest.json")


def _cancel_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "cancel.requested")


def _commit_cancel_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "commit_cancel.requested")


def load_progress(job_id: str) -> dict | None:
    if not is_valid_job_id(job_id):
        return None
    data = _load_json(_progress_path(job_id))
    return data if isinstance(data, dict) else None


def load_manifest(job_id: str) -> dict:
    data = _load_json(_manifest_path(job_id)) if is_valid_job_id(job_id) else None
    if not isinstance(data, dict):
        data = {"jobId": job_id, "items": []}
    data.setdefault("items", [])
    # Duong dan preview tinh lai moi lan doc (giong harvest): khong luu cung
    # duong dan tuyet doi vao manifest.
    for item in data["items"]:
        for field, target in (("filename", "previewPath"), ("thumbFilename", "thumbPath")):
            filename = item.get(field)
            if filename:
                item[target] = f"/media/{storage_relative_path(_staging_path(job_id, filename))}"
    return data


def _save_progress(job_id: str, progress: dict) -> None:
    progress["updatedAt"] = _utc_now()
    _save_json(_progress_path(job_id), progress)


def _save_manifest(job_id: str, manifest: dict) -> None:
    items = []
    for item in manifest.get("items", []):
        clean = dict(item)
        clean.pop("previewPath", None)
        clean.pop("thumbPath", None)
        items.append(clean)
    _save_json(_manifest_path(job_id), {**manifest, "items": items})


def describe_job(progress: dict) -> dict:
    """Progress + co ``live`` / ``stale`` cho UI."""
    job_id = progress.get("jobId") or ""
    search_live = is_live("search", job_id)
    commit_live = is_live("commit", job_id)
    described = dict(progress)
    described["searchLive"] = search_live
    described["commitLive"] = commit_live
    # Ghi "running" ma khong con thread: web app da restart giua chung.
    described["stale"] = progress.get("status") in RUNNING_STATUSES and not search_live
    commit = progress.get("commit")
    if isinstance(commit, dict) and commit.get("status") == "running" and not commit_live:
        described["commit"] = {**commit, "stale": True}
    return described


def list_jobs() -> list[dict]:
    root = _jobs_root()
    if not os.path.isdir(root):
        return []
    jobs = []
    for job_id in os.listdir(root):
        progress = load_progress(job_id)
        if not progress:
            continue
        items = load_manifest(job_id)["items"]
        described = describe_job(progress)
        described["keptItems"] = sum(1 for item in items if item.get("status") == "kept")
        described["totalItems"] = len(items)
        described["committedItems"] = sum(1 for item in items if item.get("status") == "committed")
        jobs.append(described)
    jobs.sort(key=lambda job: job.get("startedAt", ""), reverse=True)
    return jobs


def is_cancel_requested(job_id: str) -> bool:
    return os.path.isfile(_cancel_path(job_id))


def is_commit_cancel_requested(job_id: str) -> bool:
    return os.path.isfile(_commit_cancel_path(job_id))


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _free_bytes(path: str) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def library_motion_blocked(library_id) -> bool:
    """Thu vien styled co motion pan/zoom -> ``_ingest_clips`` se bake them Ken Burns."""
    from src.utils.story_library import get_library, resolve_library_id

    record = get_library(resolve_library_id(library_id))
    if not record or not record.get("styled"):
        return False
    from src.utils.story_library_bake import motion_filter

    try:
        zoom = float(record.get("motionZoom") or 1)
    except (TypeError, ValueError):
        zoom = 1.0
    return bool(motion_filter(str(record.get("motion") or "off"), zoom, 3, "x"))


def _ensure_not_busy(job_id: str) -> None:
    if is_live("search", job_id):
        raise ImageClipBusy("Job đang tìm ảnh. Hãy đợi xong hoặc huỷ trước.")
    if is_live("commit", job_id):
        raise ImageClipBusy("Job đang tạo clip. Hãy đợi xong hoặc huỷ trước.")


# --------------------------------------------------------------------------- #
# Bat dau job search
# --------------------------------------------------------------------------- #
def _clean_keywords(keywords) -> list[str]:
    clean, seen = [], set()
    for keyword in keywords or []:
        text = " ".join(str(keyword or "").split())
        normalized = normalize_keyword(text)
        if normalized and normalized not in seen:
            seen.add(normalized)
            clean.append(text)
    return clean


def start_image_clip_job(
    *,
    keywords,
    providers,
    library_id: str,
    tags=None,
    max_per_keyword: int = DEFAULT_MAX_PER_KEYWORD,
    effects_enabled=None,
    zoom=effects.DEFAULT_ZOOM,
    rescan_exhausted: bool = False,
    background: bool = True,
) -> dict:
    """Tao job va chay search o daemon thread. Tra ve progress ban dau.

    Cap (tu khoa, provider) ma con tro da ``exhausted`` bi bo qua (ghi vao
    ``exhaustedPairs``), tru khi ``rescan_exhausted`` -- khi do con tro duoc reset
    ve trang 1 truoc.
    """
    clean_keywords = _clean_keywords(keywords)
    if not clean_keywords:
        raise ImageClipError("Cần ít nhất 1 từ khóa.")
    requested = {str(item).strip().lower() for item in providers or []}
    clean_providers = [name for name in SUPPORTED_PROVIDERS if name in requested]
    if not clean_providers:
        raise ImageClipError("Cần chọn ít nhất 1 nguồn (pixabay/pexels).")
    try:
        max_per_keyword = max(0, int(max_per_keyword or 0))
    except (TypeError, ValueError):
        raise ImageClipError("Số ảnh tối đa mỗi từ khóa không hợp lệ.") from None
    if library_motion_blocked(library_id):
        raise LibraryHasMotion(
            "Thư viện này đã bake chuyển động (motion pan/zoom): clip ảnh sẽ bị Ken Burns hai lần. "
            "Hãy chọn thư viện khác."
        )

    with _registry_lock:
        running = next(
            (name.split(":", 1)[1] for name, thread in _live.items()
             if name.startswith("search:") and thread.is_alive()),
            None,
        )
        if running:
            raise ImageClipBusy(f"Job {running} đang tìm ảnh. Mỗi lúc chỉ chạy được 1 job.")

        exhausted_pairs: list[dict] = []
        reset_pairs: list[dict] = []
        for provider in clean_providers:
            docs = image_repo.get_cursors(provider, clean_keywords)
            for keyword in clean_keywords:
                doc = docs.get(normalize_keyword(keyword))
                position = image_repo.start_position(doc, provider, search.QUERY_SIGNATURE)
                if not position["exhausted"]:
                    continue
                if rescan_exhausted:
                    image_repo.reset_cursor(provider, keyword)
                    reset_pairs.append({"keyword": keyword, "provider": provider})
                else:
                    exhausted_pairs.append({
                        "keyword": keyword,
                        "provider": provider,
                        "reason": doc.get("exhausted_reason") if doc else None,
                        "totalResults": doc.get("total_results") if doc else None,
                    })
        if len(exhausted_pairs) >= len(clean_keywords) * len(clean_providers):
            raise ImageClipError(
                "Tất cả từ khóa đã quét hết kết quả trên nguồn đã chọn. "
                "Tick 'Quét lại từ đầu' nếu vẫn muốn tìm lại."
            )

        job_id = f"ic-{uuid.uuid4().hex[:8]}"
        staging_dir(job_id)
        progress = {
            "jobId": job_id,
            "status": "running",
            "libraryId": library_id,
            "keywords": clean_keywords,
            "providers": clean_providers,
            "tags": [str(tag).strip() for tag in (tags or []) if str(tag).strip()],
            "maxPerKeyword": max_per_keyword,
            "effects": effects.clean_effects(effects_enabled),
            "zoom": effects.clamp_zoom(zoom),
            "rescanExhausted": bool(rescan_exhausted),
            # Do dai clip tu anh: ngau nhien moi anh, doc lap voi do dai cat video.
            "durationRange": list(effects.duration_range()),
            "exhaustedPairs": exhausted_pairs,
            "resetPairs": reset_pairs,
            "keywordIndex": 0,
            "keywordTotal": len(clean_keywords),
            "currentKeyword": clean_keywords[0],
            "currentProvider": clean_providers[0],
            "currentPage": None,
            "currentMaxPage": None,
            "searchRequests": 0,
            "downloaded": 0,
            "rejected": 0,
            "duplicates": 0,
            "failed": 0,
            "bytesDownloaded": 0,
            "keywordStats": [],
            "notices": {},
            "quotaStopped": {},
            "providerErrors": [],
            "message": "Bắt đầu tìm ảnh...",
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
            "error": None,
        }
        _save_progress(job_id, progress)
        _save_manifest(job_id, {"jobId": job_id, "items": []})
        if background:
            _start_thread("search", job_id, _run_search, job_id)
    if not background:
        _run_search(job_id)
        return load_progress(job_id) or progress
    return progress


def request_cancel(job_id: str) -> dict | None:
    progress = load_progress(job_id)
    if not progress:
        return None
    if progress.get("status") in RUNNING_STATUSES:
        if is_live("search", job_id):
            with open(_cancel_path(job_id), "w", encoding="utf-8") as handle:
                handle.write(_utc_now())
            progress["status"] = "cancelling"
            progress["message"] = "Đang huỷ, chờ ảnh đang tải xong..."
        else:
            # Thread da chet (restart): khong ai doc marker nua, chot luon.
            progress["status"] = "cancelled"
            progress["message"] = "Đã huỷ (job bị dừng khi web app khởi động lại)."
        _save_progress(job_id, progress)
    return describe_job(progress)


# --------------------------------------------------------------------------- #
# Search worker
# --------------------------------------------------------------------------- #
def _fetch_photo(item: dict, dest_dir: str) -> dict:
    """Tai 1 anh + chuan hoa JPEG + thumbnail. Chay o pool tai (khong dung Mongo)."""
    from src.utils.decor_image_search import download_provider_image

    base = f"{item['provider']}_{item['id']}_{uuid.uuid4().hex[:8]}"
    raw_path = download_provider_image(item["downloadUrl"], dest_dir, f"{base}_src")
    filename, thumb_filename = f"{base}.jpg", f"{base}.thumb.jpg"
    try:
        width, height = effects.normalize_photo(
            raw_path, os.path.join(dest_dir, filename), os.path.join(dest_dir, thumb_filename)
        )
    finally:
        if os.path.abspath(raw_path) != os.path.abspath(os.path.join(dest_dir, filename)):
            _remove(raw_path)
    return {
        "filename": filename,
        "thumbFilename": thumb_filename,
        "bytes": os.path.getsize(os.path.join(dest_dir, filename)),
        "stagedWidth": width,
        "stagedHeight": height,
    }


def _download_batch(job_id, progress, batch, dest_dir, on_progress) -> tuple[list[tuple[dict, dict]], bool]:
    """Tai song song ``batch``. Tra ve ``([(item, file_info)], da_thu_het)``."""
    results: list[tuple[dict, dict]] = []
    if not batch:
        return results, True
    workers = max(1, int(Config.STORY_PREFETCH_DOWNLOAD_WORKERS or 1))
    attempted_all = True
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"image-dl-{job_id}")
    try:
        futures = {pool.submit(_fetch_photo, item, dest_dir): item for item in batch}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results.append((item, future.result()))
            except Exception as exc:
                progress["failed"] += 1
                logger.warning(f"[ImageClip] {job_id} tai anh {item.get('provider')}:{item.get('id')} loi: {exc}")
            on_progress()
            if is_cancel_requested(job_id):
                attempted_all = False
                break
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return results, attempted_all


def _sweep_pair(job_id, progress, manifest, keyword, provider, rotation, duration_rng, seen_keys,
                stopped_providers) -> str:
    """Quet mot cap (tu khoa, provider) tu con tro. Tra ve "ok" hoac "disk"."""
    doc = image_repo.get_cursor(provider, keyword)
    position = image_repo.start_position(doc, provider, search.QUERY_SIGNATURE)
    page, per_page = position["page"], position["per_page"]
    stat = {
        "keyword": keyword, "provider": provider,
        "startPage": page, "endPage": None, "perPage": per_page,
        "total": doc.get("total_results") if doc and not position["fresh"] else None,
        "maxPage": doc.get("max_page") if doc and not position["fresh"] else None,
        "accepted": 0, "rejected": 0, "duplicates": 0, "failed": 0,
        "exhausted": False, "reason": None, "stoppedBy": None,
    }
    progress["keywordStats"].append(stat)
    cap = int(progress.get("maxPerKeyword") or 0)
    taken, pages_this_run, new_run = 0, 0, True
    dest_dir = staging_dir(job_id)
    min_free = int(MIN_FREE_GB * 1024 ** 3)

    def save():
        _save_progress(job_id, progress)

    while True:
        if is_cancel_requested(job_id):
            raise _Cancelled()
        if pages_this_run >= MAX_PAGES_PER_RUN:
            stat["stoppedBy"] = "page_limit"
            break
        if _free_bytes(dest_dir) < min_free:
            stat["stoppedBy"] = "disk"
            return "disk"

        progress["currentPage"] = page
        progress["currentMaxPage"] = stat["maxPage"]
        progress["message"] = f"Đang tìm '{keyword}' trên {provider} (trang {page}, {per_page}/trang)..."
        save()

        try:
            result = search.search_photos(provider, keyword, page, per_page)
        except PexelsQuotaExhausted as exc:
            stopped_providers[provider] = str(exc)
            progress["quotaStopped"][provider] = str(exc)
            stat["stoppedBy"] = "quota"
            break
        except search.SearchOutOfRange:
            image_repo.record_out_of_range(
                provider, keyword, signature=search.QUERY_SIGNATURE, page=page, per_page=per_page, job_id=job_id
            )
            progress["searchRequests"] += 1
            stat.update({"exhausted": True, "reason": "out_of_range", "endPage": page})
            break
        except search.ProviderSearchError as exc:
            message = search.redact(exc)
            progress["providerErrors"].append({"provider": provider, "keyword": keyword, "message": message})
            stat["stoppedBy"] = "error"
            if exc.status in (401, 403):
                stopped_providers[provider] = message
            break

        progress["searchRequests"] += 1
        pages_this_run += 1
        per_page = int(result.get("perPage") or per_page)
        stat["perPage"] = per_page
        stat["total"] = int(result.get("total") or 0)
        stat["maxPage"] = image_repo.compute_max_page(provider, stat["total"], per_page)
        progress["currentMaxPage"] = stat["maxPage"]
        if result.get("notice"):
            progress["notices"][provider] = result["notice"]

        raw_count = int(result.get("rawCount") or 0)
        if raw_count and int(result.get("fullResMissing") or 0) >= raw_count:
            # Key Pixabay chua co full access: ca trang deu <=1280px. Khong ghi tien
            # con tro de khi co full access van con nhung trang nay.
            stopped_providers[provider] = result.get("notice") or "no_full_access"
            stat["stoppedBy"] = "no_full_access"
            break

        items = list(result.get("items") or [])
        rejected = sum(int(value) for value in (result.get("rejected") or {}).values())
        fresh = [item for item in items if image_repo.photo_key(provider, item["id"]) not in seen_keys]
        known = image_repo.find_known_photo_keys(image_repo.photo_key(provider, item["id"]) for item in fresh)
        candidates = [item for item in fresh if image_repo.photo_key(provider, item["id"]) not in known]
        duplicates = len(items) - len(candidates)
        remaining = max(0, cap - taken) if cap else len(candidates)
        batch = candidates[:remaining]

        progress["message"] = f"'{keyword}' | {provider} | trang {page}: tải {len(batch)} ảnh..."
        save()
        downloaded, attempted_all = _download_batch(job_id, progress, batch, dest_dir, save)

        new_items = []
        for item, info in downloaded:
            key = image_repo.photo_key(provider, item["id"])
            new_items.append({
                "itemId": uuid.uuid4().hex,
                "key": key,
                "provider": provider,
                "photoId": str(item["id"]),
                "keyword": keyword,
                "page": page,
                "title": item.get("title") or "",
                "tags": item.get("tags") or [],
                "width": item.get("width") or 0,
                "height": item.get("height") or 0,
                "pageUrl": item.get("pageUrl") or "",
                "author": item.get("author") or "",
                "effect": rotation.next(),
                "duration": effects.pick_duration(duration_rng),
                "status": "kept",
                "createdAt": _utc_now(),
                **info,
            })
        # Thu tu ghi: manifest truoc, Mongo sau -- restart giua chung khong bao gio
        # de lai anh "staged" ma khong manifest nao chua.
        if new_items:
            with _job_lock(job_id):
                manifest["items"].extend(new_items)
                _save_manifest(job_id, manifest)
            for entry, (item, _info) in zip(new_items, downloaded):
                image_repo.record_photo_staged(job_id, provider, entry["photoId"], keyword, item, entry["effect"])
                seen_keys.add(entry["key"])

        taken += len(new_items)
        page_done = attempted_all and len(batch) == len(candidates)
        recorded = image_repo.record_page(
            provider, keyword,
            signature=search.QUERY_SIGNATURE, page=page, per_page=per_page,
            total=stat["total"], raw_count=raw_count, accepted=len(new_items), rejected=rejected,
            page_done=page_done, has_next=result.get("hasMore"), new_run=new_run, job_id=job_id,
        )
        new_run = False

        progress["downloaded"] += len(new_items)
        progress["bytesDownloaded"] += sum(int(entry.get("bytes") or 0) for entry in new_items)
        progress["rejected"] += rejected
        progress["duplicates"] += duplicates
        stat["accepted"] += len(new_items)
        stat["rejected"] += rejected
        stat["duplicates"] += duplicates
        stat["failed"] += len(batch) - len(downloaded) if attempted_all else 0
        stat["endPage"] = page
        stat["exhausted"] = bool(recorded["exhausted"])
        stat["reason"] = recorded["reason"]
        save()

        if not attempted_all:
            raise _Cancelled()
        if recorded["exhausted"]:
            break
        if cap and taken >= cap:
            stat["stoppedBy"] = "limit"
            break
        page = recorded["next_page"]
    return "ok"


def _run_search(job_id: str) -> None:
    progress = load_progress(job_id) or {}
    manifest = load_manifest(job_id)
    seen_keys = {item.get("key") for item in manifest["items"] if item.get("key")}
    rotation = effects.EffectRotation(progress.get("effects"), job_id)
    duration_rng = random.Random(f"{job_id}:duration")
    skip_pairs = {
        (normalize_keyword(pair.get("keyword")), str(pair.get("provider") or ""))
        for pair in progress.get("exhaustedPairs") or []
    }
    stopped_providers: dict[str, str] = {}

    try:
        keywords = progress["keywords"]
        for keyword_index, keyword in enumerate(keywords):
            progress["keywordIndex"] = keyword_index
            progress["currentKeyword"] = keyword
            for provider in progress["providers"]:
                if provider in stopped_providers or (normalize_keyword(keyword), provider) in skip_pairs:
                    continue
                progress["currentProvider"] = provider
                swept = _sweep_pair(job_id, progress, manifest, keyword, provider, rotation, duration_rng,
                                    seen_keys, stopped_providers)
                if swept == "disk":
                    progress["status"] = "stopped_disk"
                    progress["message"] = (
                        f"Dừng: ổ đĩa còn dưới {MIN_FREE_GB:.0f}GB trống. Đã tải {progress['downloaded']} ảnh."
                    )
                    _save_progress(job_id, progress)
                    return

        progress["keywordIndex"] = progress["keywordTotal"]
        progress["status"] = "completed"
        progress["message"] = (
            f"Xong! Đã tải {progress['downloaded']} ảnh ({progress['searchRequests']} request tìm kiếm). "
            "Chuyển sang bước duyệt ảnh."
        )
        if stopped_providers:
            progress["message"] += " CẢNH BÁO: " + " ".join(
                f"[{provider}] {search.redact(reason)}" for provider, reason in stopped_providers.items()
            )
        _save_progress(job_id, progress)
    except _Cancelled:
        progress["status"] = "cancelled"
        progress["message"] = f"Đã huỷ. Giữ lại {progress.get('downloaded', 0)} ảnh đã tải."
        _save_progress(job_id, progress)
    except Exception as exc:
        logger.error(f"[ImageClip] {job_id} search failed: {search.redact(exc)}", exc_info=True)
        progress["status"] = "failed"
        progress["error"] = search.redact(exc)
        progress["message"] = f"Lỗi: {search.redact(exc)}"
        _save_progress(job_id, progress)
    finally:
        _remove(_cancel_path(job_id))


# --------------------------------------------------------------------------- #
# Duyet
# --------------------------------------------------------------------------- #
def delete_items(job_id: str, item_ids=None, *, keyword: str | None = None, delete_all: bool = False) -> dict:
    """Xoa anh khoi buoc duyet (file + manifest) va danh dau ``rejected`` trong Mongo:
    anh bi loai khong bao gio duoc tai lai."""
    _ensure_not_busy(job_id)
    targets = {str(value) for value in (item_ids or [])}
    deleted, failed, keys = 0, [], []
    with _job_lock(job_id):
        manifest = load_manifest(job_id)
        for item in manifest["items"]:
            if item.get("status") != "kept":
                continue
            if not delete_all:
                if keyword is not None:
                    if item.get("keyword") != keyword:
                        continue
                elif item.get("itemId") not in targets:
                    continue
            path = _staging_path(job_id, item.get("filename") or "")
            if remove_file_with_retries(path):
                _remove(_staging_path(job_id, item.get("thumbFilename") or ""))
                item["status"] = "deleted"
                deleted += 1
                if item.get("key"):
                    keys.append(item["key"])
            else:
                failed.append(item.get("itemId"))
        _save_manifest(job_id, manifest)
        remaining = sum(1 for item in manifest["items"] if item.get("status") == "kept")

    mongo_synced = True
    try:
        image_repo.mark_photos(keys, "rejected")
    except Exception as exc:
        mongo_synced = False
        logger.warning(f"[ImageClip] {job_id}: khong danh dau duoc anh bi loai: {exc}")
    return {"deletedCount": deleted, "failedItemIds": failed, "remainingCount": remaining, "mongoSynced": mongo_synced}


def set_item_effect(job_id: str, item_id: str, effect: str) -> dict:
    if effect not in effects.EFFECTS:
        raise ImageClipError("Hiệu ứng không hợp lệ.")
    _ensure_not_busy(job_id)
    with _job_lock(job_id):
        manifest = load_manifest(job_id)
        for item in manifest["items"]:
            if item.get("itemId") == item_id and item.get("status") == "kept":
                item["effect"] = effect
                _save_manifest(job_id, manifest)
                return item
    raise KeyError(item_id)


# --------------------------------------------------------------------------- #
# Commit: render Ken Burns + them vao thu vien
# --------------------------------------------------------------------------- #
def pending_items(job_id: str) -> list[dict]:
    return [item for item in load_manifest(job_id)["items"] if item.get("status") == "kept"]


def _clip_tags(item: dict, session_id: str, job_tags: list[str]) -> list[str]:
    source = f"{item['provider']}-photo"
    return [
        source, "image-clip", f"session:{session_id}", f"src:{source}:{item['photoId']}",
        f"keyword:{item.get('keyword') or ''}", f"effect:{item.get('effect') or ''}", *job_tags,
    ]


def item_seconds(item: dict) -> float:
    """Do dai clip cua mot anh: gan ngau nhien luc tai ve (``duration``), trong khoang
    STORY_IMAGE_CLIP_DURATION_MIN/MAX. Anh cu chua co thi boc ngay theo id cua no."""
    try:
        seconds = float(item.get("duration") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds > 0:
        return seconds
    return effects.pick_duration(random.Random(str(item.get("key") or item.get("itemId") or "")))


def _render_item(job_id: str, item: dict, clip_dir: str, zoom: float, cancel_cb) -> str | None:
    seconds = item_seconds(item)
    image_path = _staging_path(job_id, item.get("filename") or "")
    if not item.get("filename") or not os.path.isfile(image_path):
        logger.warning(f"[ImageClip] {job_id}: thieu anh staging {image_path}")
        return None
    effect = item.get("effect") if item.get("effect") in effects.EFFECTS else effects.EFFECT_IDS[0]
    name = (
        f"{item['provider']}-photo_{item['photoId']}_{effect.replace('_', '-')}"
        f"_{uuid.uuid4().hex[:8]}_clip_000.mp4"
    )
    out_path = os.path.join(clip_dir, name)
    if effects.render_image_clip(image_path, out_path, effect, seconds, zoom, cancel_cb=cancel_cb):
        return out_path
    return None


def start_commit(
    job_id: str,
    *,
    library_id: str,
    session_id: str,
    progress_callback,
    delete_staging: bool = True,
    background: bool = True,
) -> int:
    """Kiem tra + chay commit o daemon thread. Tra ve so anh se tao clip.

    ``progress_callback`` nhan ca cap nhat giua chung lan trang thai cuoi
    (``completed`` -- kem ``cancelled`` khi huy -- hoac ``failed``), nen session
    tai (``_download_sessions``) cua route luon ket thuc dung.
    """
    if library_motion_blocked(library_id):
        raise LibraryHasMotion(
            "Thư viện này đã bake chuyển động (motion pan/zoom): clip ảnh sẽ bị Ken Burns hai lần. "
            "Hãy chọn thư viện khác."
        )
    with _registry_lock:
        for kind in ("search", "commit"):
            thread = _live.get(f"{kind}:{job_id}")
            if thread and thread.is_alive():
                raise ImageClipBusy(
                    "Job đang tìm ảnh, hãy đợi xong." if kind == "search" else "Job đang tạo clip rồi."
                )
        total = len(pending_items(job_id))
        if not total:
            raise ImageClipError("Không còn ảnh nào để tạo clip.")
        _remove(_commit_cancel_path(job_id))
        args = (job_id, library_id, session_id, progress_callback, delete_staging)
        if background:
            _start_thread("commit", job_id, _run_commit, *args)
    if not background:
        _run_commit(*args)
    return total


def request_commit_cancel(job_id: str) -> bool:
    if not is_live("commit", job_id):
        return False
    with open(_commit_cancel_path(job_id), "w", encoding="utf-8") as handle:
        handle.write(_utc_now())
    return True


def _run_commit(job_id, library_id, session_id, progress_callback, delete_staging) -> None:
    progress = load_progress(job_id) or {}
    commit_state = {
        "sessionId": session_id, "libraryId": library_id, "status": "running",
        "startedAt": _utc_now(), "added": 0, "failed": 0, "skipped": 0, "cancelled": False,
    }
    progress["commit"] = commit_state
    _save_progress(job_id, progress)
    try:
        result = commit_image_clip_job(
            job_id, library_id=library_id, session_id=session_id,
            progress_callback=progress_callback, delete_staging=delete_staging,
        )
        commit_state.update(result, status="completed", finishedAt=_utc_now())
        message = f"Xong! Đã thêm {result['added']} clip vào thư viện."
        if result["failed"]:
            message += f" {result['failed']} ảnh lỗi khi tạo clip."
        if result["skipped"]:
            message += f" Bỏ qua {result['skipped']} ảnh đã có clip."
        if result["cancelled"]:
            message = f"Đã huỷ. {message} Ảnh chưa tạo clip vẫn còn để tạo tiếp."
        progress_callback({
            "status": "completed", "current": result["processed"], "total": result["total"],
            "addedClips": result["added"], "cancelled": result["cancelled"], "message": message,
        })
    except Exception as exc:
        logger.error(f"[ImageClip] {job_id} commit failed: {exc}", exc_info=True)
        commit_state.update(status="failed", error=search.redact(exc), finishedAt=_utc_now())
        progress_callback({"status": "failed", "message": f"Lỗi: {search.redact(exc)}"})
    finally:
        progress = load_progress(job_id) or progress
        progress["commit"] = commit_state
        _save_progress(job_id, progress)
        _remove(_commit_cancel_path(job_id))


def commit_image_clip_job(
    job_id: str,
    *,
    library_id: str,
    session_id: str,
    progress_callback=None,
    delete_staging: bool = True,
) -> dict:
    """Render moi anh ``kept`` thanh 1 clip roi them vao thu vien theo lo."""
    from src.utils.story_library import get_library, resolve_library_id

    progress = load_progress(job_id) or {}
    job_tags = list(progress.get("tags") or [])
    zoom = effects.clamp_zoom(progress.get("zoom"))
    record = get_library(resolve_library_id(library_id))
    styled = bool(record and record.get("styled"))

    pending = pending_items(job_id)
    total = len(pending)
    statuses = image_repo.get_photo_statuses(item.get("key") for item in pending)
    duplicates = [item for item in pending if statuses.get(item.get("key")) == "committed"]
    to_render = [item for item in pending if statuses.get(item.get("key")) != "committed"]

    counts = {"added": 0, "failed": 0, "skipped": len(duplicates), "processed": len(duplicates)}
    updates: dict[str, dict] = {item["itemId"]: {"status": "skipped", "reason": "already_committed"} for item in duplicates}
    clip_dir = os.path.join(staging_dir(job_id), "clips")
    os.makedirs(clip_dir, exist_ok=True)

    def cancel_cb() -> bool:
        return is_commit_cancel_requested(job_id)

    def report(message: str):
        if progress_callback:
            progress_callback({
                "stage": "rendering", "current": counts["processed"], "total": total, "message": message,
            })

    def cleanup_item(item: dict, clip_path: str | None):
        _remove(_staging_path(job_id, item.get("filename") or ""))
        _remove(_staging_path(job_id, item.get("thumbFilename") or ""))
        if clip_path:
            _remove(clip_path)

    def finish(item: dict, clip_path: str | None, asset: dict | None):
        if asset:
            counts["added"] += 1
            updates[item["itemId"]] = {"status": "committed", "assetId": asset.get("id"), "libraryId": library_id}
            try:
                image_repo.mark_photo_committed(item["key"], library_id, asset.get("id"), item.get("effect"))
            except Exception as exc:  # clip da vao thu vien; Mongo chi la ban ghi
                logger.warning(f"[ImageClip] {job_id}: khong ghi duoc anh da commit {item['key']}: {exc}")
            cleanup_item(item, clip_path)
            return
        # Loi render/ingest (vd NVENC het VRAM khi batch render dang chay): GIU anh o
        # trang thai "kept" de lan tao clip sau thu lai, thay vi mat anh da duyet.
        counts["failed"] += 1
        updates[item["itemId"]] = {
            "lastError": "ingest" if clip_path else "render",
            "failedAttempts": int(item.get("failedAttempts") or 0) + 1,
        }
        if clip_path:
            _remove(clip_path)

    def flush(buffer: list[tuple[dict, str]]):
        if not buffer:
            return
        if styled:
            # Thu vien styled: _ingest_clips bake lai tung clip theo style thu vien.
            for item, clip_path in buffer:
                ref = vsd.provider_item_source_ref(
                    f"{item['provider']}-photo", item["photoId"], item, "image-clip", keyword=item.get("keyword"),
                )
                try:
                    added = vsd._ingest_clips(
                        [clip_path], f"{item['provider']}-photo", _clip_tags(item, session_id, job_tags),
                        library_id=library_id, session_id=session_id, source_ref=ref,
                    )
                except Exception as exc:
                    logger.warning(f"[ImageClip] {job_id}: ingest {item['key']} loi: {exc}")
                    added = []
                finish(item, clip_path, added[0] if added else None)
        else:
            groups: dict[str, list[tuple[dict, str]]] = {}
            for item, clip_path in buffer:
                groups.setdefault(f"{item['provider']}-photo", []).append((item, clip_path))
            for source_type, group in groups.items():
                clips = [clip_path for _item, clip_path in group]
                added = vsd.add_clips_to_library(
                    clips, source_type, None, library_id=library_id,
                    tags_per_clip=[_clip_tags(item, session_id, job_tags) for item, _path in group],
                )
                by_name = {asset.get("source_name"): asset for asset in added}
                for item, clip_path in group:
                    asset = by_name.get(os.path.basename(clip_path))
                    if asset:
                        vsd._report_ingest(library_id, vsd.provider_item_source_ref(
                            source_type, item["photoId"], item, "image-clip", keyword=item.get("keyword"),
                        ), [asset])
                    finish(item, clip_path, asset)
        buffer.clear()
        _apply_updates(job_id, updates)

    def result_of(future) -> str | None:
        try:
            return future.result()
        except Exception as exc:
            logger.warning(f"[ImageClip] {job_id}: render {futures[future].get('key')} loi: {exc}")
            return None

    report(f"Bắt đầu tạo {len(to_render)} clip...")
    buffer: list[tuple[dict, str]] = []
    cancelled = False
    futures: dict = {}
    collected: set = set()
    workers = max(1, int(Config.STORY_IMAGE_CLIP_WORKERS or 1))
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"image-render-{job_id}")
    try:
        futures = {
            pool.submit(_render_item, job_id, item, clip_dir, zoom, cancel_cb): item
            for item in to_render
        }
        for future in as_completed(futures):
            collected.add(future)
            item = futures[future]
            clip_path = result_of(future)
            if clip_path:
                counts["processed"] += 1
                buffer.append((item, clip_path))
                if len(buffer) >= INGEST_BATCH and not cancel_cb():
                    flush(buffer)
            elif not cancel_cb():
                counts["processed"] += 1
                finish(item, None, None)
            # else: bi huy giua chung -> anh giu "kept", lan sau tao tiep.
            if cancel_cb():
                cancelled = True
                break
            report(f"Đã tạo {counts['processed']}/{total} clip ({counts['added']} đã vào thư viện)...")
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if cancelled:
        # Clip nao da render xong truoc khi huy thi van nhan vao thu vien.
        for future, item in futures.items():
            if future in collected or future.cancelled() or not future.done():
                continue
            clip_path = result_of(future)
            if clip_path:
                counts["processed"] += 1
                buffer.append((item, clip_path))
    flush(buffer)
    _apply_updates(job_id, updates)

    remaining = len(pending_items(job_id))
    if delete_staging and remaining == 0:
        shutil.rmtree(_staging_root(job_id), ignore_errors=True)
    else:
        shutil.rmtree(clip_dir, ignore_errors=True)
    return {**counts, "total": total, "cancelled": cancelled, "remaining": remaining}


def _apply_updates(job_id: str, updates: dict[str, dict]) -> None:
    if not updates:
        return
    with _job_lock(job_id):
        manifest = load_manifest(job_id)
        for item in manifest["items"]:
            change = updates.get(item.get("itemId"))
            if change:
                item.update(change)
        _save_manifest(job_id, manifest)


# --------------------------------------------------------------------------- #
# Xoa job
# --------------------------------------------------------------------------- #
def delete_job(job_id: str) -> bool:
    """Xoa job + staging. Anh con ``kept`` (chua duyet xong) -> ``discarded``:
    duoc phep tai lai neu sau nay reset con tro."""
    _ensure_not_busy(job_id)
    if not load_progress(job_id):
        return False
    keys = [item.get("key") for item in pending_items(job_id) if item.get("key")]
    try:
        image_repo.mark_photos(keys, "discarded")
    except Exception as exc:
        logger.warning(f"[ImageClip] {job_id}: khong danh dau duoc anh bi bo: {exc}")
    shutil.rmtree(_staging_root(job_id), ignore_errors=True)
    shutil.rmtree(os.path.join(_jobs_root(), job_id), ignore_errors=True)
    return True
