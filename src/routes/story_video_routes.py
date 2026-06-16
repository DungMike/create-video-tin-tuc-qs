"""Flask Blueprint for Story Video pipeline API routes.

Provides endpoints for:
- Story library management (CRUD, download from links, upload, pagination)
- CRT effect presets and demo generation
- Waveform overlay management
- Single story video creation and progress tracking
- Batch story video creation, progress, and retry
"""

import json
import os
import shutil
import threading
import uuid

from flask import Blueprint, jsonify, request, send_file
from werkzeug.utils import secure_filename

from src.config import Config
from src.utils.logger import logger
from src.utils.story_library import (
    LibraryError,
    count_library_clips,
    create_library,
    delete_library,
    delete_story_library_assets,
    ensure_libraries_registry,
    get_default_library_id,
    get_library as get_library_record,
    load_libraries,
    load_story_library_index as _load_library_index,
    rename_library,
    resolve_library_id,
    save_story_library_index as _save_library_index,
    story_library_root,
)
from src.utils.story_library import _index_lock as _library_index_lock

story_video_bp = Blueprint("story_video", __name__)

# In-memory tracking for download/upload sessions
_download_sessions: dict[str, dict] = {}
_download_sessions_lock = threading.Lock()
_tv_noise_jobs: dict[str, dict] = {}
_tv_noise_jobs_lock = threading.Lock()


def _error(message: str, code: str = "bad_request", status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _has_active_library_session(library_id=None) -> bool:
    """True if a download/upload/import session is still running.

    When ``library_id`` is given, only sessions targeting that library count.
    """
    target = resolve_library_id(library_id) if library_id else None
    with _download_sessions_lock:
        for session in _download_sessions.values():
            if session.get("status") in {"completed", "failed"}:
                continue
            if target is not None and resolve_library_id(session.get("libraryId")) != target:
                continue
            return True
    return False


def _library_id_from_request():
    """Resolve the target library id from query (GET) or JSON/form body."""
    raw = request.args.get("libraryId")
    if raw is None:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            raw = body.get("libraryId")
    if raw is None and request.form:
        raw = request.form.get("libraryId")
    return str(raw or "").strip()


def _resolve_or_404(raw_library_id):
    """Validate a library id. Blank/"default" -> Default. Unknown -> (None, 404)."""
    ensure_libraries_registry()
    raw = str(raw_library_id or "").strip()
    if not raw or raw == get_default_library_id():
        return get_default_library_id(), None
    if get_library_record(raw) is None:
        return None, _error("Thư viện không tồn tại.", code="library_not_found", status=404)
    return raw, None


def _new_tv_noise_job(action: str, overlay_id: str = "") -> str:
    session_id = f"tvn-{str(uuid.uuid4())[:8]}"
    with _tv_noise_jobs_lock:
        _tv_noise_jobs[session_id] = {
            "sessionId": session_id,
            "status": "processing",
            "action": action,
            "current": 0,
            "total": 1,
            "message": "Dang xu ly TV noise overlay...",
            "overlayId": overlay_id,
            "error": None,
        }
    return session_id


def _update_tv_noise_job(session_id: str, **updates):
    with _tv_noise_jobs_lock:
        if session_id in _tv_noise_jobs:
            _tv_noise_jobs[session_id].update(updates)


def _download_tv_noise_youtube(link: str, overlay_id: str) -> str:
    import yt_dlp

    os.makedirs(Config.STORY_TV_NOISE_OVERLAY_DIR, exist_ok=True)
    before_files = set(os.listdir(Config.STORY_TV_NOISE_OVERLAY_DIR))
    output_template = os.path.join(Config.STORY_TV_NOISE_OVERLAY_DIR, f"{overlay_id}_youtube_%(id)s.%(ext)s")
    options = {
        "format": (
            "bestvideo[vcodec^=avc1][ext=mp4][height>=720]+bestaudio[ext=m4a]/"
            "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[ext=mp4]+bestaudio/"
            "best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        },
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([link])

    after_files = set(os.listdir(Config.STORY_TV_NOISE_OVERLAY_DIR))
    new_files = sorted(after_files - before_files)
    for filename in new_files:
        if filename.lower().endswith(".mp4"):
            return os.path.join(Config.STORY_TV_NOISE_OVERLAY_DIR, filename)
    raise RuntimeError("Khong tim thay file MP4 sau khi tai YouTube.")


def _run_tv_noise_preprocess_async(overlay_id: str, session_id: str | None = None):
    from src.utils.story_tv_noise_overlays import run_tv_noise_preprocess

    def _worker():
        try:
            if session_id:
                _update_tv_noise_job(session_id, status="processing", current=0, message="Dang tao alpha MOV...")
            record = run_tv_noise_preprocess(overlay_id)
            if record and record.get("status") == "ready":
                if session_id:
                    _update_tv_noise_job(
                        session_id,
                        status="completed",
                        current=1,
                        message="TV noise overlay da san sang.",
                        overlayId=overlay_id,
                    )
            else:
                error = (record or {}).get("error") or "TV noise preprocess failed."
                if session_id:
                    _update_tv_noise_job(
                        session_id,
                        status="failed",
                        current=1,
                        message=error,
                        error=error,
                        overlayId=overlay_id,
                    )
        except Exception as exc:
            logger.error(f"[StoryVideo] TV noise preprocess job failed: {exc}", exc_info=True)
            if session_id:
                _update_tv_noise_job(
                    session_id,
                    status="failed",
                    current=1,
                    message=str(exc),
                    error=str(exc),
                    overlayId=overlay_id,
                )

    threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# 1. GET /api/story-video/library — paginated clip list
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library", methods=["GET"])
def get_library():
    library_id, err = _resolve_or_404(request.args.get("libraryId"))
    if err:
        return err

    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", Config.STORY_LIBRARY_PAGE_SIZE))))
    tag_filter = request.args.get("tags", "").strip()
    filter_tags = [t.strip() for t in tag_filter.split(",") if t.strip()] if tag_filter else []

    index = _load_library_index(library_id)
    assets = index.get("assets", [])

    # Filter by tags if provided
    if filter_tags:
        assets = [a for a in assets if any(t in a.get("tags", []) for t in filter_tags)]

    # Sort by created_at desc
    assets.sort(key=lambda a: a.get("created_at", ""), reverse=True)

    total = len(assets)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    end = start + per_page
    page_assets = assets[start:end]

    return jsonify({
        "clips": page_assets,
        "total": total,
        "page": page,
        "perPage": per_page,
        "totalPages": total_pages,
    })


# ---------------------------------------------------------------------------
# 2. POST /api/story-video/library/download — download from links
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/download", methods=["POST"])
def download_library_videos():
    data = request.get_json(silent=True) or {}
    links = data.get("links", [])
    tags = data.get("tags", [])

    library_id, err = _resolve_or_404(data.get("libraryId"))
    if err:
        return err

    if not links or not isinstance(links, list):
        return _error("Cần ít nhất 1 link.", code="no_links")

    # Filter empty/invalid
    links = [l.strip() for l in links if isinstance(l, str) and l.strip()]
    if not links:
        return _error("Không có link hợp lệ.", code="no_valid_links")

    session_id = f"dl-{str(uuid.uuid4())[:8]}"

    # Initialize progress
    with _download_sessions_lock:
        _download_sessions[session_id] = {
            "sessionId": session_id,
            "status": "downloading",
            "current": 0,
            "total": len(links),
            "message": "Bắt đầu tải...",
            "addedClips": 0,
            "libraryId": library_id,
        }

    def _run_download():
        from src.utils.video_source_downloader import download_from_links

        def progress_cb(data):
            with _download_sessions_lock:
                if session_id in _download_sessions:
                    _download_sessions[session_id].update(data)

        try:
            result = download_from_links(links, session_id, tags, progress_cb, library_id=library_id)
            added_count = len(result) if isinstance(result, list) else 0
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "completed",
                    "current": len(links),
                    "total": len(links),
                    "message": f"Hoàn tất! Đã thêm {added_count} clips.",
                    "addedClips": added_count,
                })
        except Exception as e:
            logger.error(f"[StoryVideo] Download error: {e}")
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "failed",
                    "message": f"Lỗi: {str(e)}",
                })

    threading.Thread(target=_run_download, daemon=True).start()
    return jsonify({"sessionId": session_id}), 202


# ---------------------------------------------------------------------------
# 3. POST /api/story-video/library/upload — upload local files
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/upload", methods=["POST"])
def upload_library_videos():
    files = request.files.getlist("files")
    if not files:
        return _error("Chưa chọn file nào.", code="no_files")

    library_id, err = _resolve_or_404(request.form.get("libraryId"))
    if err:
        return err

    tags_raw = request.form.get("tags", "[]")
    try:
        tags = json.loads(tags_raw) if tags_raw.startswith("[") else [t.strip() for t in tags_raw.split(",") if t.strip()]
    except Exception:
        tags = []

    session_id = f"up-{str(uuid.uuid4())[:8]}"

    # Save files to temp dir
    upload_dir = os.path.join(Config.STORY_RAW_DIR, session_id)
    os.makedirs(upload_dir, exist_ok=True)

    saved_paths = []
    for f in files:
        if f.filename:
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            if ext in Config.ALLOWED_VIDEO_EXTENSIONS:
                safe_name = secure_filename(f.filename)
                dest = os.path.join(upload_dir, safe_name)
                f.save(dest)
                saved_paths.append(dest)

    if not saved_paths:
        return _error("Không có file video hợp lệ.", code="no_valid_files")

    # Initialize progress
    with _download_sessions_lock:
        _download_sessions[session_id] = {
            "sessionId": session_id,
            "status": "splitting",
            "current": 0,
            "total": len(saved_paths),
            "message": "Đang xử lý file...",
            "addedClips": 0,
            "libraryId": library_id,
        }

    def _run_upload():
        from src.utils.video_source_downloader import process_local_uploads

        def progress_cb(data):
            with _download_sessions_lock:
                if session_id in _download_sessions:
                    _download_sessions[session_id].update(data)

        try:
            result = process_local_uploads(saved_paths, session_id, tags, progress_cb, library_id=library_id)
            added_count = len(result) if isinstance(result, list) else 0
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "completed",
                    "current": len(saved_paths),
                    "total": len(saved_paths),
                    "message": f"Hoàn tất! Đã thêm {added_count} clips.",
                    "addedClips": added_count,
                })
        except Exception as e:
            logger.error(f"[StoryVideo] Upload processing error: {e}")
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "failed",
                    "message": f"Lỗi: {str(e)}",
                })

    threading.Thread(target=_run_upload, daemon=True).start()
    return jsonify({"sessionId": session_id}), 202


# ---------------------------------------------------------------------------
# 4. GET /api/story-video/library/download-progress/<session_id>
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/download-progress/<session_id>", methods=["GET"])
def get_download_progress(session_id: str):
    with _download_sessions_lock:
        progress = _download_sessions.get(session_id)
    if not progress:
        return _error("Session không tồn tại.", code="session_not_found", status=404)
    return jsonify(progress)


# ---------------------------------------------------------------------------
# 4b. GET /api/story-video/video-search/<provider> - proxy Pixabay/Pexels search
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/video-search/<provider>", methods=["GET"])
def search_story_provider_videos(provider: str):
    from requests import HTTPError, RequestException

    from src.utils.video_source_downloader import search_provider_videos

    provider = provider.strip().lower()
    if provider not in {"pixabay", "pexels"}:
        return _error("Provider khong hop le.", code="invalid_provider", status=404)

    query = request.args.get("q", "").strip()
    if not query:
        return _error("Can nhap keyword de search video.", code="missing_query")

    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = max(1, min(80, int(request.args.get("per_page", 20))))
    except ValueError:
        return _error("page/per_page khong hop le.", code="invalid_pagination")

    try:
        return jsonify(search_provider_videos(provider, query, page, per_page))
    except ValueError as exc:
        return _error(str(exc), code="provider_api_key_missing", status=400)
    except HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        logger.error(f"[StoryVideo] {provider} search HTTP error: {exc}")
        return _error(
            f"{provider} search API error.",
            code="provider_search_failed",
            status=502 if status_code >= 500 else 400,
        )
    except RequestException as exc:
        logger.error(f"[StoryVideo] {provider} search request error: {exc}")
        return _error(f"Khong the goi {provider} API.", code="provider_search_failed", status=502)


# ---------------------------------------------------------------------------
# 4c. POST /api/story-video/library/import-selected - import selected provider videos
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/import-selected", methods=["POST"])
def import_selected_story_videos():
    data = request.get_json(silent=True) or {}
    raw_items = data.get("items", [])
    tags = data.get("tags", [])

    library_id, err = _resolve_or_404(data.get("libraryId"))
    if err:
        return err

    if not isinstance(raw_items, list) or not raw_items:
        return _error("Can chon it nhat 1 video.", code="no_items")
    if tags is not None and not isinstance(tags, list):
        return _error("tags phai la array.", code="invalid_tags")

    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip().lower()
        video_id = str(item.get("id") or "").strip()
        page_url = str(item.get("pageUrl") or "").strip()
        if provider in {"pixabay", "pexels"} and video_id:
            items.append({"provider": provider, "id": video_id, "pageUrl": page_url})

    if not items:
        return _error("Khong co video hop le de import.", code="no_valid_items")

    clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()] if isinstance(tags, list) else []
    session_id = f"imp-{str(uuid.uuid4())[:8]}"

    with _download_sessions_lock:
        _download_sessions[session_id] = {
            "sessionId": session_id,
            "status": "downloading",
            "current": 0,
            "total": len(items),
            "message": "Bat dau import video...",
            "addedClips": 0,
            "libraryId": library_id,
        }

    def _run_import():
        from src.utils.video_source_downloader import download_from_provider_items

        def progress_cb(progress_data):
            with _download_sessions_lock:
                if session_id in _download_sessions:
                    _download_sessions[session_id].update(progress_data)

        try:
            result = download_from_provider_items(items, session_id, clean_tags, progress_cb, library_id=library_id)
            added_count = len(result) if isinstance(result, list) else 0
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "completed",
                    "current": len(items),
                    "total": len(items),
                    "message": f"Hoan tat! Da them {added_count} clips.",
                    "addedClips": added_count,
                })
        except Exception as exc:
            logger.error(f"[StoryVideo] Import selected videos failed: {exc}", exc_info=True)
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "failed",
                    "message": f"Loi: {str(exc)}",
                })

    threading.Thread(target=_run_import, daemon=True).start()
    return jsonify({"sessionId": session_id}), 202


# ---------------------------------------------------------------------------
# 5. DELETE /api/story-video/library/<clip_id>
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/<clip_id>", methods=["DELETE"])
def delete_library_clip(clip_id: str):
    library_id, err = _resolve_or_404(_library_id_from_request())
    if err:
        return err
    result = delete_story_library_assets([clip_id], library_id=library_id)

    if result["missingClipIds"]:
        return _error("Clip không tồn tại.", code="clip_not_found", status=404)

    if result["failedClipIds"]:
        return _error("Khong the xoa file clip.", code="clip_delete_failed", status=409)

    return jsonify({"deleted": True, "clipId": clip_id})


# ---------------------------------------------------------------------------
# 5b. POST /api/story-video/library/bulk-delete
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/bulk-delete", methods=["POST"])
def bulk_delete_library_clips():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Payload phai la JSON object.", code="invalid_payload")

    library_id, err = _resolve_or_404(data.get("libraryId"))
    if err:
        return err

    scope = str(data.get("scope") or "").strip().lower()
    if scope == "ids":
        raw_clip_ids = data.get("clipIds")
        if not isinstance(raw_clip_ids, list):
            return _error("clipIds phai la array.", code="invalid_clip_ids")
        clip_ids = [clip_id.strip() for clip_id in raw_clip_ids if isinstance(clip_id, str) and clip_id.strip()]
        if not clip_ids:
            return _error("Can it nhat 1 clipId de xoa.", code="invalid_clip_ids")
        result = delete_story_library_assets(clip_ids, library_id=library_id)
    elif scope == "all":
        if _has_active_library_session(library_id):
            return _error(
                "Thu vien dang duoc import hoac upload. Hay doi tac vu hoan tat roi xoa lai.",
                code="library_busy",
                status=409,
            )
        result = delete_story_library_assets(delete_all=True, clean_orphans=True, library_id=library_id)
    else:
        return _error("scope phai la 'ids' hoac 'all'.", code="invalid_scope")

    return jsonify({"scope": scope, **result})


# ---------------------------------------------------------------------------
# 6. PATCH /api/story-video/library/<clip_id>/tags
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/<clip_id>/tags", methods=["PATCH"])
def update_clip_tags(clip_id: str):
    data = request.get_json(silent=True) or {}
    new_tags = data.get("tags", [])
    if not isinstance(new_tags, list):
        return _error("tags phải là array.", code="invalid_tags")

    library_id, err = _resolve_or_404(data.get("libraryId"))
    if err:
        return err

    with _library_index_lock(library_id):
        index = _load_library_index(library_id)
        found = False
        for asset in index.get("assets", []):
            if asset.get("id") == clip_id:
                asset["tags"] = new_tags
                found = True
                break

        if not found:
            return _error("Clip không tồn tại.", code="clip_not_found", status=404)

        _save_library_index(index, library_id)
    return jsonify({"updated": True, "clipId": clip_id, "tags": new_tags})


# ---------------------------------------------------------------------------
# 7. GET /api/story-video/library/stats
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/stats", methods=["GET"])
def get_library_stats():
    library_id, err = _resolve_or_404(request.args.get("libraryId"))
    if err:
        return err
    index = _load_library_index(library_id)
    assets = index.get("assets", [])

    total_duration = sum(a.get("duration", 0) for a in assets)
    by_source: dict[str, int] = {}
    for a in assets:
        src = a.get("source_type", "unknown")
        by_source[src] = by_source.get(src, 0) + 1

    return jsonify({
        "totalClips": len(assets),
        "totalDuration": round(total_duration, 2),
        "bySource": by_source,
    })


# ---------------------------------------------------------------------------
# 7b. Library registry CRUD (multiple named clip libraries / "folders")
# ---------------------------------------------------------------------------
def _serialize_library(record: dict, *, with_count: bool = True) -> dict:
    payload = {
        "id": record.get("id"),
        "name": record.get("name"),
        "isDefault": bool(record.get("isDefault")),
        "createdAt": record.get("createdAt"),
    }
    if record.get("updatedAt"):
        payload["updatedAt"] = record["updatedAt"]
    if with_count:
        payload["clipCount"] = count_library_clips(record.get("id"))
    return payload


@story_video_bp.route("/api/story-video/libraries", methods=["GET"])
def list_libraries():
    libraries = [_serialize_library(lib) for lib in load_libraries()]
    return jsonify({
        "libraries": libraries,
        "defaultLibraryId": get_default_library_id(),
    })


@story_video_bp.route("/api/story-video/libraries", methods=["POST"])
def create_library_route():
    data = request.get_json(silent=True) or {}
    try:
        record = create_library(data.get("name", ""))
    except LibraryError as exc:
        status = 409 if exc.code == "duplicate_library_name" else 400
        return _error(exc.message, code=exc.code, status=status)
    return jsonify({"library": _serialize_library(record)}), 201


@story_video_bp.route("/api/story-video/libraries/<library_id>", methods=["PATCH"])
def rename_library_route(library_id: str):
    data = request.get_json(silent=True) or {}
    try:
        record = rename_library(library_id, data.get("name", ""))
    except LibraryError as exc:
        status = {
            "library_not_found": 404,
            "duplicate_library_name": 409,
        }.get(exc.code, 400)
        return _error(exc.message, code=exc.code, status=status)
    return jsonify({"library": _serialize_library(record)})


@story_video_bp.route("/api/story-video/libraries/<library_id>", methods=["DELETE"])
def delete_library_route(library_id: str):
    body = request.get_json(silent=True) or {}
    delete_clips = body.get("deleteClips", True)
    if _has_active_library_session(library_id):
        return _error(
            "Thu vien dang duoc import hoac upload. Hay doi tac vu hoan tat roi xoa lai.",
            code="library_in_use",
            status=409,
        )
    try:
        result = delete_library(library_id, delete_clips=bool(delete_clips))
    except LibraryError as exc:
        status = {
            "library_not_found": 404,
            "cannot_delete_default": 409,
        }.get(exc.code, 400)
        return _error(exc.message, code=exc.code, status=status)
    return jsonify(result)


# ---------------------------------------------------------------------------
# 8. GET /api/story-video/crt-presets
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/crt-presets", methods=["GET"])
def get_crt_presets():
    from src.processors.crt_effect_processor import CRTEffectProcessor

    presets = []
    labels = {"subtle": "Subtle", "light": "Light", "medium": "Medium", "heavy": "Heavy"}
    for name, settings in CRTEffectProcessor.PRESETS.items():
        presets.append({
            "name": name,
            "label": labels.get(name, name.title()),
            "settings": {
                "noiseStrength": settings.get("noise_strength", 15),
                "scanlineOpacity": settings.get("scan_opacity", 0.06),
                "vignetteAngle": settings.get("vignette", "PI/5"),
                "colorBleed": settings.get("color_bleed", True),
                "flickerIntensity": settings.get("flicker", 0.02),
            },
        })

    current_config = {
        "noiseStrength": Config.CRT_NOISE_STRENGTH,
        "scanlineOpacity": Config.CRT_SCANLINE_OPACITY,
        "vignetteAngle": Config.CRT_VIGNETTE,
        "colorBleed": Config.CRT_COLOR_BLEED,
        "flickerIntensity": Config.CRT_FLICKER,
    }

    return jsonify({"presets": presets, "currentConfig": current_config})


# ---------------------------------------------------------------------------
# 9. POST /api/story-video/crt-demo
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/crt-demo", methods=["POST"])
def generate_crt_demo():
    from src.processors.crt_effect_processor import CRTEffectProcessor

    data = request.get_json(silent=True) or {}
    settings = data.get("settings", {})
    sample_clip_id = data.get("sampleClipId")

    # Find sample image/video for demo
    sample_path = None
    if sample_clip_id:
        index = _load_library_index()
        clip = next((a for a in index.get("assets", []) if a.get("id") == sample_clip_id), None)
        if clip:
            sample_path = os.path.join(Config.STORY_LIBRARY_DIR, clip.get("relative_path", ""))

    # If no sample found, use first clip in library or generate a test pattern
    if not sample_path or not os.path.isfile(sample_path):
        index = _load_library_index()
        assets = index.get("assets", [])
        for a in assets:
            p = os.path.join(Config.STORY_LIBRARY_DIR, a.get("relative_path", ""))
            if os.path.isfile(p):
                sample_path = p
                break

    if not sample_path or not os.path.isfile(sample_path):
        return _error("Không có clip mẫu trong thư viện. Hãy thêm clip trước.", code="no_sample", status=404)

    # Convert settings keys from camelCase to snake_case for processor
    proc_settings = {
        "noise_strength": settings.get("noiseStrength", Config.CRT_NOISE_STRENGTH),
        "scan_opacity": settings.get("scanlineOpacity", Config.CRT_SCANLINE_OPACITY),
        "vignette": settings.get("vignetteAngle", Config.CRT_VIGNETTE),
        "color_bleed": settings.get("colorBleed", Config.CRT_COLOR_BLEED),
        "flicker": settings.get("flickerIntensity", Config.CRT_FLICKER),
    }

    os.makedirs(Config.CRT_EFFECT_DIR, exist_ok=True)
    demo_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(Config.CRT_EFFECT_DIR, f"demo_{demo_id}.jpg")

    try:
        processor = CRTEffectProcessor()

        # If sample is a video, extract a single frame first
        actual_sample = sample_path
        ext = os.path.splitext(sample_path)[1].lower()
        if ext in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
            from src.utils.ffmpeg_helper import FFmpegHelper
            frame_path = os.path.join(Config.CRT_EFFECT_DIR, f"frame_{demo_id}.jpg")
            extract_cmd = [
                "ffmpeg", "-y", "-i", sample_path,
                "-ss", "1", "-frames:v", "1", "-q:v", "2", frame_path,
            ]
            if FFmpegHelper.run_command(extract_cmd) and os.path.isfile(frame_path):
                actual_sample = frame_path

        result_path = processor.generate_demo_image(actual_sample, proc_settings, output_path)
        if not result_path or not os.path.isfile(result_path):
            return _error("Không thể tạo demo CRT. FFmpeg trả về lỗi.", code="demo_failed", status=500)

        # Return relative path for /media/ serving
        rel = os.path.relpath(result_path, Config.STORAGE_DIR).replace("\\", "/")
        return jsonify({"demoPath": rel})
    except Exception as e:
        logger.error(f"[StoryVideo] CRT demo error: {e}")
        return _error(f"Lỗi tạo demo: {str(e)}", code="demo_error", status=500)


# ---------------------------------------------------------------------------
# 9b. TV effect styles (1990s looks) — list / select / preview
# ---------------------------------------------------------------------------
def _find_sample_clip(sample_clip_id: str | None = None, library_id=None) -> str | None:
    """Resolve a sample clip path from a story library (specific id or first available).

    Searches the requested library first; if it yields nothing and the library is
    not the Default, falls back to the Default library so previews still work.
    """
    candidates = [resolve_library_id(library_id)]
    if candidates[0] != get_default_library_id():
        candidates.append(get_default_library_id())

    for lib_id in candidates:
        root = story_library_root(lib_id)
        assets = _load_library_index(lib_id).get("assets", [])
        if sample_clip_id:
            clip = next((a for a in assets if a.get("id") == sample_clip_id), None)
            if clip:
                path = os.path.join(root, clip.get("relative_path", ""))
                if os.path.isfile(path):
                    return path
        for asset in assets:
            path = os.path.join(root, asset.get("relative_path", ""))
            if os.path.isfile(path):
                return path
    return None


def _tv_effect_preview_rel(style_id: str) -> str | None:
    from src.processors.crt_effect_processor import tv_effect_previews_dir

    preview_path = os.path.join(tv_effect_previews_dir(), f"{style_id}.mp4")
    if os.path.isfile(preview_path):
        return os.path.relpath(preview_path, Config.STORAGE_DIR).replace("\\", "/")
    return None


@story_video_bp.route("/api/story-video/tv-effects", methods=["GET"])
def get_tv_effect_styles():
    from src.processors.crt_effect_processor import (
        TV_EFFECT_PARAM_SPEC,
        TV_EFFECT_STYLES,
        get_custom_tv_effect_params,
        get_selected_tv_effect_style_id,
        sanitize_tv_effect_params,
    )

    styles = []
    for style in TV_EFFECT_STYLES:
        styles.append({
            "id": style["id"],
            "name": style["name"],
            "description": style["description"],
            "previewPath": _tv_effect_preview_rel(style["id"]),
            "params": sanitize_tv_effect_params(style["params"]),
        })
    return jsonify({
        "styles": styles,
        "selectedId": get_selected_tv_effect_style_id(),
        "customParams": get_custom_tv_effect_params(),
        "customPreviewPath": _tv_effect_preview_rel("custom"),
        "paramSpec": TV_EFFECT_PARAM_SPEC,
    })


@story_video_bp.route("/api/story-video/tv-effects/select", methods=["POST"])
def select_tv_effect_style():
    from src.processors.crt_effect_processor import set_selected_tv_effect_style_id

    data = request.get_json(silent=True) or {}
    style_id = str(data.get("styleId", "")).strip()
    if not set_selected_tv_effect_style_id(style_id):
        return _error("Style không hợp lệ.", code="invalid_style", status=404)
    return jsonify({"selectedId": style_id})


@story_video_bp.route("/api/story-video/tv-effects/custom", methods=["POST"])
def save_custom_tv_effect():
    """Save custom effect params and select the custom style."""
    from src.processors.crt_effect_processor import (
        CUSTOM_STYLE_ID,
        set_custom_tv_effect_params,
        set_selected_tv_effect_style_id,
    )

    data = request.get_json(silent=True) or {}
    params = data.get("params")
    if not isinstance(params, dict):
        return _error("Thiếu params.", code="missing_params")
    cleaned = set_custom_tv_effect_params(params)
    set_selected_tv_effect_style_id(CUSTOM_STYLE_ID)
    return jsonify({"selectedId": CUSTOM_STYLE_ID, "customParams": cleaned})


@story_video_bp.route("/api/story-video/tv-effects/preview", methods=["POST"])
def generate_tv_effect_style_preview():
    """Render a 3-5s preview. Body: {styleId} for a builtin style, or {params} for custom."""
    from src.processors.crt_effect_processor import (
        generate_tv_effect_style_preview as render_style_preview,
        get_tv_effect_style,
        sanitize_tv_effect_params,
    )

    data = request.get_json(silent=True) or {}
    custom_params = data.get("params")
    style_id = str(data.get("styleId", "")).strip()
    if custom_params is not None:
        if not isinstance(custom_params, dict):
            return _error("params không hợp lệ.", code="invalid_params")
        custom_params = sanitize_tv_effect_params(custom_params)
        style_id = "custom"
    elif not get_tv_effect_style(style_id):
        return _error("Style không hợp lệ.", code="invalid_style", status=404)

    sample_path = _find_sample_clip(data.get("sampleClipId"), data.get("libraryId"))
    if not sample_path:
        return _error("Không có clip mẫu trong thư viện. Hãy thêm clip trước.", code="no_sample", status=404)

    try:
        duration = float(data.get("duration", 4.0))
    except (TypeError, ValueError):
        duration = 4.0

    output_path = render_style_preview(style_id, sample_path, duration, custom_params=custom_params)
    if not output_path:
        return _error("Không thể render preview hiệu ứng.", code="preview_failed", status=500)

    rel = os.path.relpath(output_path, Config.STORAGE_DIR).replace("\\", "/")
    return jsonify({"previewPath": rel})


# ---------------------------------------------------------------------------
# 9c. Subtitle fonts / presets / preview
# ---------------------------------------------------------------------------
def _subtitle_config_from_payload(payload: dict) -> dict:
    """Map camelCase subtitle payload keys to pipeline config keys (without subtitle_path)."""

    def _positive_int(value, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    return {
        "subtitle_font": str(payload.get("subtitleFont", "")).strip(),
        "subtitle_preset": str(payload.get("subtitlePreset", "")).strip() or "clean",
        "subtitle_max_chars_per_line": _positive_int(
            payload.get("subtitleMaxCharsPerLine"), Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE
        ),
        "subtitle_max_lines": _positive_int(
            payload.get("subtitleMaxLines"), Config.STORY_SUBTITLE_MAX_LINES
        ),
    }


@story_video_bp.route("/api/story-video/subtitle-fonts", methods=["GET"])
def get_subtitle_fonts():
    from src.utils.story_subtitles import scan_fonts

    return jsonify({
        "fonts": scan_fonts(),
        "defaultFamily": Config.STORY_SUBTITLE_DEFAULT_FONT,
    })


@story_video_bp.route("/api/story-video/subtitle-fonts", methods=["POST"])
def upload_subtitle_font():
    from src.utils.story_subtitles import scan_fonts

    font_file = request.files.get("font")
    if not font_file or not font_file.filename:
        return _error("Chưa chọn file font.", code="no_file")

    ext = font_file.filename.rsplit(".", 1)[-1].lower() if "." in font_file.filename else ""
    if ext not in {"ttf", "otf", "ttc"}:
        return _error("Font phải có định dạng .ttf, .otf hoặc .ttc.", code="invalid_font_format")

    font_file.stream.seek(0, os.SEEK_END)
    font_size = font_file.stream.tell()
    font_file.stream.seek(0)
    if font_size > 40 * 1024 * 1024:
        return _error("File font vượt quá 40MB.", code="font_too_large")

    safe_name = secure_filename(font_file.filename)
    if not safe_name.lower().endswith(f".{ext}"):
        safe_name = f"font_{str(uuid.uuid4())[:8]}.{ext}"

    os.makedirs(Config.STORY_FONTS_DIR, exist_ok=True)
    font_file.save(os.path.join(Config.STORY_FONTS_DIR, safe_name))
    logger.info(f"[StoryVideo] Subtitle font uploaded: {safe_name}")

    return jsonify({
        "fonts": scan_fonts(force_refresh=True),
        "defaultFamily": Config.STORY_SUBTITLE_DEFAULT_FONT,
    }), 201


@story_video_bp.route("/api/story-video/subtitle-presets", methods=["GET"])
def get_subtitle_presets():
    from src.utils.story_subtitles import SUBTITLE_PRESETS

    return jsonify({"presets": SUBTITLE_PRESETS})


@story_video_bp.route("/api/story-video/subtitle-preview", methods=["POST"])
def generate_subtitle_preview():
    """Render a short subtitle preview. Body: {font, presetId, maxCharsPerLine, maxLines, sampleClipId?}."""
    from src.utils.story_subtitles import get_subtitle_preset, render_subtitle_preview

    data = request.get_json(silent=True) or {}
    font_family = str(data.get("font", "")).strip() or Config.STORY_SUBTITLE_DEFAULT_FONT
    preset_id = str(data.get("presetId", "")).strip() or "clean"
    if not get_subtitle_preset(preset_id):
        return _error("Preset không hợp lệ.", code="invalid_preset", status=404)

    try:
        max_chars = int(data.get("maxCharsPerLine", Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE))
        max_lines = int(data.get("maxLines", Config.STORY_SUBTITLE_MAX_LINES))
    except (TypeError, ValueError):
        return _error("maxCharsPerLine/maxLines không hợp lệ.", code="invalid_subtitle_config")
    if max_chars < 1 or max_lines < 1:
        return _error("maxCharsPerLine/maxLines không hợp lệ.", code="invalid_subtitle_config")

    style_overrides = data.get("styleOverrides")
    if not isinstance(style_overrides, dict):
        style_overrides = None

    # Empty library is fine: render_subtitle_preview falls back to a lavfi background.
    sample_path = _find_sample_clip(data.get("sampleClipId"), data.get("libraryId"))

    output_path = render_subtitle_preview(
        font_family,
        preset_id,
        max_chars,
        max_lines,
        sample_clip_path=sample_path,
        style_overrides=style_overrides,
    )
    if not output_path:
        return _error("Không thể render preview phụ đề.", code="preview_failed", status=500)

    rel = os.path.relpath(output_path, Config.STORAGE_DIR).replace("\\", "/")
    return jsonify({"previewPath": rel})


# ---------------------------------------------------------------------------
# 10. POST /api/story-video/create — single story video
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/create", methods=["POST"])
def create_story_video():
    from src.utils.story_video_pipeline import StoryVideoPipelineRunner

    # Handle both JSON and multipart/form-data
    if request.content_type and "multipart" in request.content_type:
        payload_str = request.form.get("payload", "{}")
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError:
            return _error("payload khong phai JSON hop le.", code="invalid_payload")
        audio_file = request.files.get("audio")
        subtitle_file = request.files.get("subtitle")
    else:
        data = request.get_json(silent=True) or {}
        audio_file = None
        subtitle_file = None

    input_type = str(data.get("inputType", "")).strip()
    if input_type not in ("audio_file", "script_url"):
        return _error("inputType phải là 'audio_file' hoặc 'script_url'.", code="invalid_input_type")

    output_name = str(data.get("outputName", "")).strip()
    if not output_name:
        return _error("outputName không được để trống.", code="missing_output_name")

    if subtitle_file and subtitle_file.filename:
        sub_ext = subtitle_file.filename.rsplit(".", 1)[-1].lower() if "." in subtitle_file.filename else ""
        if sub_ext != "srt":
            return _error("File subtitle phải có định dạng .srt.", code="invalid_subtitle_format")

    story_id = f"sv-{str(uuid.uuid4())[:8]}"
    story_dir = os.path.join(Config.STORY_VIDEO_DIR, story_id)
    os.makedirs(story_dir, exist_ok=True)

    # Handle audio file upload
    input_value = str(data.get("inputValue", "")).strip()
    if audio_file and audio_file.filename:
        safe_name = secure_filename(audio_file.filename)
        audio_path = os.path.join(story_dir, safe_name)
        audio_file.save(audio_path)
        input_value = audio_path
        input_type = "audio_file"

    # Handle subtitle file upload (.srt)
    subtitle_path = ""
    if subtitle_file and subtitle_file.filename:
        safe_sub_name = secure_filename(subtitle_file.filename)
        if not safe_sub_name.lower().endswith(".srt"):
            safe_sub_name = "subtitle.srt"
        subtitle_path = os.path.join(story_dir, safe_sub_name)
        subtitle_file.save(subtitle_path)

    if not input_value:
        return _error("inputValue không được để trống.", code="missing_input_value")

    config_dict = {
        "input_type": input_type,
        "input_value": input_value,
        "output_name": output_name,
        "clip_tags": data.get("clipTags", []),
        "library_id": str(data.get("libraryId", "") or "").strip(),
        "crt_settings": data.get("crtSettings", {}),
        "tv_effect_style_id": str(data.get("tvEffectStyleId", "")).strip(),
        "waveform_overlay_id": str(data.get("waveformOverlayId", "")).strip(),
        "voice_id": str(data.get("voiceId", "")).strip(),
        "subtitle_path": subtitle_path,
        **_subtitle_config_from_payload(data),
    }

    runner = StoryVideoPipelineRunner(story_id, config_dict)
    threading.Thread(target=runner.run, daemon=True).start()

    logger.info(f"[StoryVideo] Started single pipeline: story_id={story_id}")
    return jsonify({"storyId": story_id}), 202


# ---------------------------------------------------------------------------
# 11. GET /api/story-video/<story_id>/progress
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/<story_id>/progress", methods=["GET"])
def get_story_progress(story_id: str):
    from src.utils.story_video_pipeline import load_story_progress

    progress = load_story_progress(story_id)
    if not progress:
        return _error("Story không tìm thấy.", code="story_not_found", status=404)
    return jsonify(progress)


# ---------------------------------------------------------------------------
# 12. GET /api/story-video/<story_id>/result
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/<story_id>/result", methods=["GET"])
def get_story_result(story_id: str):
    from src.utils.story_video_pipeline import load_story_progress

    progress = load_story_progress(story_id)
    if not progress:
        return _error("Story không tìm thấy.", code="story_not_found", status=404)

    result = progress.get("result", {})
    video_path = (result.get("videoPath") or result.get("video_path")) if result else None
    if not video_path:
        return _error("Video chưa sẵn sàng.", code="video_not_ready", status=404)

    abs_path = video_path if os.path.isabs(video_path) else os.path.join(Config.STORAGE_DIR, video_path)
    if not os.path.isfile(abs_path):
        return _error("Video chua san sang.", code="video_not_ready", status=404)

    rel = video_path.replace("\\", "/") if not os.path.isabs(video_path) else os.path.relpath(abs_path, Config.STORAGE_DIR).replace("\\", "/")
    return jsonify({"videoPath": rel})


# ---------------------------------------------------------------------------
# 12b. POST /api/story-video/<story_id>/cancel
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/<story_id>/cancel", methods=["POST"])
def cancel_story_video(story_id: str):
    from src.utils.story_video_pipeline import load_story_progress, request_story_cancel

    progress = load_story_progress(story_id)
    if not progress:
        return _error("Story khong tim thay.", code="story_not_found", status=404)
    if progress.get("status") not in {"completed", "failed", "cancelled"}:
        request_story_cancel(story_id)
    return jsonify({"storyId": story_id, "status": progress.get("status", "pending")})


# ---------------------------------------------------------------------------
# 13. POST/GET /api/story-video/batch/drive-audio-imports - stage Drive folder audio
# ---------------------------------------------------------------------------
# Stage public Google Drive folder audio before adding items to a story batch.
@story_video_bp.route("/api/story-video/batch/drive-audio-imports", methods=["POST"])
def create_drive_audio_import():
    from src.utils.google_drive_audio import (
        DriveAudioImportError,
        cleanup_expired_drive_audio_imports,
        create_drive_audio_import_session,
        run_drive_audio_import,
    )

    data = request.get_json(silent=True) or {}
    folder_url = str(data.get("folderUrl", "")).strip()
    if not folder_url:
        return _error("folderUrl khong duoc de trong.", code="missing_folder_url")

    cleanup_expired_drive_audio_imports()
    try:
        manifest = create_drive_audio_import_session(folder_url)
    except DriveAudioImportError as exc:
        return _error(str(exc), code=exc.code)

    session_id = manifest["sessionId"]

    def _run_import():
        try:
            run_drive_audio_import(session_id, folder_url)
        except Exception as exc:
            logger.error(f"[StoryVideo] Drive audio import failed: session_id={session_id}, error={exc}")

    threading.Thread(target=_run_import, daemon=True).start()
    return jsonify({"sessionId": session_id}), 202


@story_video_bp.route("/api/story-video/batch/drive-audio-imports/<session_id>", methods=["GET"])
def get_drive_audio_import(session_id: str):
    from src.utils.google_drive_audio import load_drive_audio_import, public_drive_audio_import

    manifest = load_drive_audio_import(session_id)
    if not manifest:
        return _error("Drive audio import khong tim thay.", code="drive_audio_session_not_found", status=404)
    return jsonify(public_drive_audio_import(manifest))


# ---------------------------------------------------------------------------
# 14. POST /api/story-video/batch/create - batch of stories
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/batch/create", methods=["POST"])
def create_story_batch():
    from src.utils.google_drive_audio import (
        DriveAudioImportError,
        cleanup_expired_drive_audio_imports,
        copy_staged_audio_to_batch,
    )
    from src.utils.story_video_batch import StoryVideoBatchRunner

    cleanup_expired_drive_audio_imports()

    # Handle multipart/form-data
    payload_str = request.form.get("payload", "{}")
    try:
        data = json.loads(payload_str)
    except Exception:
        data = request.get_json(silent=True) or {}

    items = data.get("items", [])
    shared_config = data.get("sharedConfig", {})

    if not items or not isinstance(items, list):
        return _error("Cần ít nhất 1 item.", code="empty_items")

    batch_id = f"sb-{str(uuid.uuid4())[:8]}"
    batch_dir = os.path.join(Config.STORY_VIDEO_DIR, "batches", batch_id)
    os.makedirs(batch_dir, exist_ok=True)

    # Handle uploaded audio files
    audio_files = request.files.getlist("audio_files")
    audio_file_map: dict[int, str] = {}
    audio_name_map: dict[str, str] = {}
    for i, af in enumerate(audio_files):
        if af.filename:
            safe_name = secure_filename(af.filename)
            audio_path = os.path.join(batch_dir, f"audio_{i}_{safe_name}")
            af.save(audio_path)
            audio_file_map[i] = audio_path
            audio_name_map[safe_name] = audio_path
            audio_name_map[str(af.filename)] = audio_path

    # Handle uploaded subtitle files (mirrors the audio file mapping)
    subtitle_files = request.files.getlist("subtitle_files")
    subtitle_file_map: dict[int, str] = {}
    subtitle_name_map: dict[str, str] = {}
    for i, sf in enumerate(subtitle_files):
        if sf.filename:
            sub_ext = sf.filename.rsplit(".", 1)[-1].lower() if "." in sf.filename else ""
            if sub_ext != "srt":
                shutil.rmtree(batch_dir, ignore_errors=True)
                return _error("File subtitle phải có định dạng .srt.", code="invalid_subtitle_format")
            safe_name = secure_filename(sf.filename)
            if not safe_name.lower().endswith(".srt"):
                safe_name = "subtitle.srt"
            saved_subtitle_path = os.path.join(batch_dir, f"subtitle_{i}_{safe_name}")
            sf.save(saved_subtitle_path)
            subtitle_file_map[i] = saved_subtitle_path
            subtitle_name_map[safe_name] = saved_subtitle_path
            subtitle_name_map[str(sf.filename)] = saved_subtitle_path

    shared_subtitle_config = _subtitle_config_from_payload(shared_config)

    # Build story configs
    story_configs = []
    for idx, item in enumerate(items):
        input_type = str(item.get("inputType", "")).strip()
        input_value = str(item.get("inputValue", "")).strip()
        output_name = str(item.get("outputName", f"story_{idx + 1}")).strip()

        # Map local upload indexes or Drive staging tokens to durable batch files.
        if input_type == "audio_file":
            if input_value.isdigit():
                file_idx = int(input_value)
                if file_idx in audio_file_map:
                    input_value = audio_file_map[file_idx]
            elif input_value in audio_name_map:
                input_value = audio_name_map[input_value]
            elif secure_filename(input_value) in audio_name_map:
                input_value = audio_name_map[secure_filename(input_value)]
        elif input_type == "drive_audio":
            try:
                input_value, _original_name = copy_staged_audio_to_batch(input_value, batch_dir, idx)
                input_type = "audio_file"
            except DriveAudioImportError as exc:
                shutil.rmtree(batch_dir, ignore_errors=True)
                return _error(str(exc), code=exc.code)

        # Map subtitle upload indexes or filenames to durable batch files.
        subtitle_ref = str(item.get("subtitleFile", "")).strip()
        subtitle_path = ""
        if subtitle_ref:
            if subtitle_ref.isdigit():
                file_idx = int(subtitle_ref)
                if file_idx in subtitle_file_map:
                    subtitle_path = subtitle_file_map[file_idx]
            elif subtitle_ref in subtitle_name_map:
                subtitle_path = subtitle_name_map[subtitle_ref]
            elif secure_filename(subtitle_ref) in subtitle_name_map:
                subtitle_path = subtitle_name_map[secure_filename(subtitle_ref)]

        story_configs.append({
            "story_id": f"sv-{str(uuid.uuid4())[:8]}",
            "input_type": input_type,
            "input_value": input_value,
            "output_name": output_name,
            "clip_tags": shared_config.get("clipTags", []),
            "library_id": str(shared_config.get("libraryId", "") or "").strip(),
            "crt_settings": shared_config.get("crtSettings", {}),
            "tv_effect_style_id": str(shared_config.get("tvEffectStyleId", "")).strip(),
            "waveform_overlay_id": str(shared_config.get("waveformOverlayId", "")).strip(),
            "voice_id": str(shared_config.get("voiceId", "")).strip(),
            "subtitle_path": subtitle_path,
            **shared_subtitle_config,
        })

    runner = StoryVideoBatchRunner(batch_id, story_configs)
    runner.start_async()

    logger.info(f"[StoryVideo] Started batch: batch_id={batch_id}, items={len(story_configs)}")
    return jsonify({"batchId": batch_id}), 202


# ---------------------------------------------------------------------------
# 15. GET /api/story-video/batch/<batch_id>/progress
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/batch/<batch_id>/progress", methods=["GET"])
def get_batch_progress(batch_id: str):
    from src.utils.story_video_batch import load_batch_progress
    from src.utils.story_video_pipeline import load_story_progress

    progress = load_batch_progress(batch_id)
    if not progress:
        return _error("Batch không tìm thấy.", code="batch_not_found", status=404)
    stories = progress.get("stories", [])
    items = []
    for story in stories:
        story_id = story.get("storyId") or story.get("story_id") or ""
        story_progress = load_story_progress(story_id) if story_id else None
        source = story_progress or story
        item_status = source.get("status", "pending")
        if item_status == "running":
            item_status = "processing"
        items.append({
            "id": story_id,
            "outputName": story.get("outputName", ""),
            "status": item_status,
            "stage": source.get("stage"),
            "percent": source.get("percent", 0),
            "message": source.get("message", ""),
            "result": source.get("result"),
            "error": source.get("error"),
        })

    batch_status = progress.get("status", "pending")
    if batch_status == "running":
        batch_status = "processing"
    elif batch_status == "partial":
        batch_status = "completed"

    return jsonify({
        "batchId": progress.get("batchId", batch_id),
        "status": batch_status,
        "totalItems": progress.get("total", len(items)),
        "completedItems": progress.get("completed", 0),
        "failedItems": progress.get("failed", 0),
        "cancelledItems": progress.get("cancelled", 0),
        "currentIndex": progress.get("current", 0),
        "items": items,
    })


# ---------------------------------------------------------------------------
# 16. POST /api/story-video/batch/<batch_id>/items/<story_id>/cancel
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/batch/<batch_id>/items/<story_id>/cancel", methods=["POST"])
def cancel_story_batch_item(batch_id: str, story_id: str):
    from src.utils.story_video_batch import request_batch_story_cancel

    story = request_batch_story_cancel(batch_id, story_id)
    if not story:
        return _error("Batch item khong tim thay.", code="batch_item_not_found", status=404)
    return jsonify({"batchId": batch_id, "storyId": story_id, "status": story.get("status", "pending")})


# ---------------------------------------------------------------------------
# 17. POST /api/story-video/batch/<batch_id>/cancel
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/batch/<batch_id>/cancel", methods=["POST"])
def cancel_story_batch(batch_id: str):
    from src.utils.story_video_batch import request_batch_cancel

    progress = request_batch_cancel(batch_id)
    if not progress:
        return _error("Batch khong tim thay.", code="batch_not_found", status=404)
    return jsonify({"batchId": batch_id, "status": progress.get("status", "pending")})


# ---------------------------------------------------------------------------
# 18. POST /api/story-video/batch/<batch_id>/retry-failed
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/batch/<batch_id>/retry-failed", methods=["POST"])
def retry_batch_failed(batch_id: str):
    from src.utils.story_video_batch import StoryVideoBatchRunner, load_batch_progress

    progress = load_batch_progress(batch_id)
    if not progress:
        return _error("Batch không tìm thấy.", code="batch_not_found", status=404)

    # Collect failed story configs and re-run
    failed_stories = [
        s for s in progress.get("stories", [])
        if s.get("status") == "failed"
    ]
    if not failed_stories:
        return _error("Không có item nào thất bại.", code="no_failed_items")

    # Rebuild story configs from progress data
    retry_configs = []
    for s in failed_stories:
        input_type = s.get("input_type", "")
        input_value = s.get("input_value", "")
        if input_type == "audio_file" and input_value and not os.path.isabs(input_value):
            batch_dir = os.path.join(Config.STORY_VIDEO_DIR, "batches", batch_id)
            safe_name = secure_filename(str(input_value))
            candidates = []
            if os.path.isdir(batch_dir):
                candidates = [
                    os.path.join(batch_dir, filename)
                    for filename in os.listdir(batch_dir)
                    if filename.endswith(safe_name) or filename == safe_name
                ]
            if candidates:
                input_value = candidates[0]

        retry_configs.append({
            "story_id": s.get("storyId") or s.get("story_id") or f"sv-{str(uuid.uuid4())[:8]}",
            "input_type": input_type,
            "input_value": input_value,
            "output_name": s.get("output_name", ""),
            "clip_tags": s.get("clip_tags", []),
            "library_id": s.get("library_id", ""),
            "voice_id": s.get("voice_id", ""),
            "subtitle_path": s.get("subtitle_path", ""),
            "subtitle_font": s.get("subtitle_font", ""),
            "subtitle_preset": s.get("subtitle_preset", "clean"),
            "subtitle_max_chars_per_line": s.get(
                "subtitle_max_chars_per_line", Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE
            ),
            "subtitle_max_lines": s.get(
                "subtitle_max_lines", Config.STORY_SUBTITLE_MAX_LINES
            ),
        })

    retry_batch_id = f"{batch_id}-retry"
    runner = StoryVideoBatchRunner(retry_batch_id, retry_configs)
    runner.start_async()

    logger.info(f"[StoryVideo] Retrying {len(retry_configs)} failed items from batch {batch_id}")
    return jsonify({"batchId": retry_batch_id, "retryCount": len(retry_configs)}), 202


# ---------------------------------------------------------------------------
# 16. GET /api/story-video/waveform-overlays
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/waveform-overlays", methods=["GET"])
def get_waveform_overlays():
    from src.utils.waveform_overlays import load_waveform_index

    data = load_waveform_index()
    return jsonify({"overlays": data.get("overlays", [])})


# ---------------------------------------------------------------------------
# 17. POST /api/story-video/waveform-overlays — upload waveform video
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/waveform-overlays", methods=["POST"])
def upload_waveform_overlay():
    from src.utils.waveform_overlays import create_waveform_overlay

    upload_file = request.files.get("file")
    if not upload_file or not upload_file.filename:
        return _error("Chua chon file.", code="no_file")

    ext = upload_file.filename.rsplit(".", 1)[-1].lower() if "." in upload_file.filename else ""
    if ext not in Config.ALLOWED_VIDEO_EXTENSIONS:
        return _error("Dinh dang khong ho tro.", code="invalid_format")

    try:
        overlay_record = create_waveform_overlay(upload_file)
    except Exception as exc:
        logger.error(f"[StoryVideo] Waveform preprocessing error: {exc}", exc_info=True)
        return _error(f"Khong the xu ly waveform overlay: {exc}", code="waveform_preprocess_failed", status=500)

    logger.info(f"[StoryVideo] Waveform overlay uploaded: {overlay_record.get('filename')}")
    return jsonify({"overlay": overlay_record}), 201

    file = request.files.get("file")
    if not file or not file.filename:
        return _error("Chưa chọn file.", code="no_file")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in Config.ALLOWED_VIDEO_EXTENSIONS:
        return _error("Định dạng không hỗ trợ.", code="invalid_format")

    os.makedirs(Config.WAVEFORM_OVERLAY_DIR, exist_ok=True)

    overlay_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(file.filename)
    filename = f"{overlay_id}_{safe_name}"
    filepath = os.path.join(Config.WAVEFORM_OVERLAY_DIR, filename)
    file.save(filepath)

    # Get duration via FFprobe
    duration = 0.0
    try:
        from src.utils.ffmpeg_helper import FFmpegHelper
        duration = FFmpegHelper.probe_duration(filepath)
    except Exception:
        pass

    overlay_record = {
        "id": overlay_id,
        "name": safe_name,
        "filename": filename,
        "relativePath": f"waveform_overlays/{filename}",
        "durationSeconds": round(duration, 2),
        "createdAt": __import__("datetime").datetime.now().isoformat(),
    }

    # Update index.json
    index_path = os.path.join(Config.WAVEFORM_OVERLAY_DIR, "index.json")
    if os.path.isfile(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {"overlays": []}

    index["overlays"].append(overlay_record)
    tmp = index_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    shutil.move(tmp, index_path)

    logger.info(f"[StoryVideo] Waveform overlay uploaded: {filename}")
    return jsonify({"overlay": overlay_record}), 201


# ---------------------------------------------------------------------------
# 18. PATCH /api/story-video/waveform-overlays/<overlay_id>
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/waveform-overlays/<overlay_id>", methods=["PATCH"])
def update_waveform_overlay_config(overlay_id: str):
    from src.utils.waveform_overlays import update_waveform_overlay

    data = request.get_json(silent=True) or {}
    updates = {}

    try:
        if "isDefault" in data:
            updates["isDefault"] = bool(data.get("isDefault"))
        if "keyColor" in data:
            updates["keyColor"] = str(data.get("keyColor") or Config.WAVEFORM_OVERLAY_KEY_COLOR).strip()
        if "similarity" in data:
            updates["similarity"] = max(0.0, min(1.0, float(data.get("similarity"))))
        if "blend" in data:
            updates["blend"] = max(0.0, min(1.0, float(data.get("blend"))))
        if "scaleWidth" in data:
            updates["scaleWidth"] = max(64, int(data.get("scaleWidth")))
        if "position" in data:
            position = str(data.get("position") or Config.WAVEFORM_OVERLAY_POSITION)
            if position not in {"top_left", "top_right", "bottom_left", "bottom_right"}:
                return _error("position khong hop le.", code="invalid_position")
            updates["position"] = position
        if "margin" in data:
            updates["margin"] = max(0, int(data.get("margin")))
    except (TypeError, ValueError):
        return _error("Cau hinh waveform khong hop le.", code="invalid_waveform_config")

    try:
        overlay = update_waveform_overlay(overlay_id, updates)
    except Exception as exc:
        logger.error(f"[StoryVideo] Waveform update error: {exc}", exc_info=True)
        return _error(f"Khong the cap nhat waveform overlay: {exc}", code="waveform_update_failed", status=500)

    if not overlay:
        return _error("Overlay khong ton tai.", code="overlay_not_found", status=404)
    return jsonify({"overlay": overlay})


# ---------------------------------------------------------------------------
# 19. DELETE /api/story-video/waveform-overlays/<overlay_id>
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/waveform-overlays/<overlay_id>", methods=["DELETE"])
def delete_waveform_overlay(overlay_id: str):
    from src.utils.waveform_overlays import delete_waveform_overlay_record

    if not delete_waveform_overlay_record(overlay_id):
        return _error("Overlay khong ton tai.", code="overlay_not_found", status=404)

    logger.info(f"[StoryVideo] Waveform overlay deleted: {overlay_id}")
    return jsonify({"deleted": True, "overlayId": overlay_id})


# ---------------------------------------------------------------------------
# 19b. Story CTA overlay management (Like/Subscribe/Notification corner)
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/cta-overlays", methods=["GET"])
def get_cta_overlays():
    from src.utils.story_cta_overlay import ensure_default_cta_overlay, load_cta_index

    ensure_default_cta_overlay()  # seed-on-first-load so the UI always shows the default
    data = load_cta_index()
    return jsonify({"overlays": data.get("overlays", [])})


@story_video_bp.route("/api/story-video/cta-overlays", methods=["POST"])
def upload_cta_overlay():
    from src.utils.story_cta_overlay import create_cta_overlay

    upload_file = request.files.get("file")
    if not upload_file or not upload_file.filename:
        return _error("Chua chon file.", code="no_file")

    ext = upload_file.filename.rsplit(".", 1)[-1].lower() if "." in upload_file.filename else ""
    if ext not in Config.ALLOWED_VIDEO_EXTENSIONS:
        return _error("Dinh dang khong ho tro.", code="invalid_format")

    try:
        overlay_record = create_cta_overlay(upload_file)
    except Exception as exc:
        logger.error(f"[StoryVideo] CTA preprocessing error: {exc}", exc_info=True)
        return _error(f"Khong the xu ly CTA overlay: {exc}", code="cta_preprocess_failed", status=500)

    logger.info(f"[StoryVideo] CTA overlay uploaded: {overlay_record.get('filename')}")
    return jsonify({"overlay": overlay_record}), 201


@story_video_bp.route("/api/story-video/cta-overlays/<overlay_id>", methods=["PATCH"])
def update_cta_overlay_config(overlay_id: str):
    from src.utils.story_cta_overlay import update_cta_overlay

    data = request.get_json(silent=True) or {}
    updates = {}

    try:
        if "isDefault" in data:
            updates["isDefault"] = bool(data.get("isDefault"))
        if "enabled" in data:
            updates["enabled"] = bool(data.get("enabled"))
        if "keyColor" in data:
            updates["keyColor"] = str(data.get("keyColor") or Config.STORY_CTA_OVERLAY_KEY_COLOR).strip()
        if "similarity" in data:
            updates["similarity"] = max(0.0, min(1.0, float(data.get("similarity"))))
        if "blend" in data:
            updates["blend"] = max(0.0, min(1.0, float(data.get("blend"))))
        if "scaleWidth" in data:
            updates["scaleWidth"] = max(64, int(data.get("scaleWidth")))
        if "position" in data:
            position = str(data.get("position") or Config.STORY_CTA_OVERLAY_POSITION)
            if position not in {"top_left", "top_right", "bottom_left", "bottom_right"}:
                return _error("position khong hop le.", code="invalid_position")
            updates["position"] = position
        if "margin" in data:
            updates["margin"] = max(0, int(data.get("margin")))
    except (TypeError, ValueError):
        return _error("Cau hinh CTA khong hop le.", code="invalid_cta_config")

    try:
        overlay = update_cta_overlay(overlay_id, updates)
    except Exception as exc:
        logger.error(f"[StoryVideo] CTA update error: {exc}", exc_info=True)
        return _error(f"Khong the cap nhat CTA overlay: {exc}", code="cta_update_failed", status=500)

    if not overlay:
        return _error("Overlay khong ton tai.", code="overlay_not_found", status=404)
    return jsonify({"overlay": overlay})


@story_video_bp.route("/api/story-video/cta-overlays/<overlay_id>", methods=["DELETE"])
def delete_cta_overlay(overlay_id: str):
    from src.utils.story_cta_overlay import delete_cta_overlay_record

    if not delete_cta_overlay_record(overlay_id):
        return _error("Overlay khong ton tai.", code="overlay_not_found", status=404)

    logger.info(f"[StoryVideo] CTA overlay deleted: {overlay_id}")
    return jsonify({"deleted": True, "overlayId": overlay_id})


# ---------------------------------------------------------------------------
# 20. TV noise overlay management
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/tv-noise-overlays", methods=["GET"])
def get_tv_noise_overlays():
    from src.utils.story_tv_noise_overlays import load_tv_noise_index

    data = load_tv_noise_index()
    overlays = data.get("overlays", [])
    overlays.sort(key=lambda item: (int(item.get("order") or 0), str(item.get("createdAt") or "")))
    return jsonify({"overlays": overlays})


@story_video_bp.route("/api/story-video/tv-noise-overlays", methods=["POST"])
def upload_tv_noise_overlay():
    from src.utils.story_tv_noise_overlays import create_tv_noise_overlay

    upload_file = request.files.get("file")
    if not upload_file or not upload_file.filename:
        return _error("Chua chon file.", code="no_file")

    ext = upload_file.filename.rsplit(".", 1)[-1].lower() if "." in upload_file.filename else ""
    if ext not in Config.ALLOWED_VIDEO_EXTENSIONS:
        return _error("Dinh dang video khong ho tro.", code="invalid_format")

    try:
        overlay = create_tv_noise_overlay(upload_file)
    except Exception as exc:
        logger.error(f"[StoryVideo] TV noise upload error: {exc}", exc_info=True)
        return _error(f"Khong the luu TV noise overlay: {exc}", code="tv_noise_upload_failed", status=500)

    session_id = _new_tv_noise_job("upload", overlay.get("id", ""))
    _run_tv_noise_preprocess_async(str(overlay["id"]), session_id)
    return jsonify({"sessionId": session_id, "overlay": overlay}), 202


@story_video_bp.route("/api/story-video/tv-noise-overlays/import-youtube", methods=["POST"])
def import_tv_noise_from_youtube():
    from src.utils.story_tv_noise_overlays import (
        attach_tv_noise_source_file,
        create_tv_noise_placeholder,
        mark_tv_noise_failed,
        run_tv_noise_preprocess,
    )

    data = request.get_json(silent=True) or {}
    link = str(data.get("url") or "").strip()
    display_name = str(data.get("name") or "").strip()
    if not link:
        return _error("Can nhap YouTube URL.", code="missing_url")

    overlay = create_tv_noise_placeholder(display_name or "youtube_tv_noise", link)
    overlay_id = str(overlay["id"])
    session_id = _new_tv_noise_job("import_youtube", overlay_id)

    def _worker():
        try:
            _update_tv_noise_job(session_id, status="downloading", current=0, message="Dang tai TV noise tu YouTube...")
            downloaded_path = _download_tv_noise_youtube(link, overlay_id)
            filename = os.path.basename(downloaded_path)
            attach_tv_noise_source_file(
                overlay_id,
                downloaded_path,
                display_name or os.path.splitext(filename)[0],
            )
            _update_tv_noise_job(session_id, status="processing", current=0, message="Dang tao alpha MOV...")
            record = run_tv_noise_preprocess(overlay_id)
            if record and record.get("status") == "ready":
                _update_tv_noise_job(
                    session_id,
                    status="completed",
                    current=1,
                    message="TV noise overlay da san sang.",
                    overlayId=overlay_id,
                )
            else:
                error = (record or {}).get("error") or "TV noise preprocess failed."
                _update_tv_noise_job(
                    session_id,
                    status="failed",
                    current=1,
                    message=error,
                    error=error,
                    overlayId=overlay_id,
                )
        except Exception as exc:
            logger.error(f"[StoryVideo] TV noise YouTube import error: {exc}", exc_info=True)
            mark_tv_noise_failed(overlay_id, str(exc))
            _update_tv_noise_job(
                session_id,
                status="failed",
                current=1,
                message=str(exc),
                error=str(exc),
                overlayId=overlay_id,
            )

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"sessionId": session_id, "overlay": overlay}), 202


@story_video_bp.route("/api/story-video/tv-noise-overlays/jobs/<session_id>", methods=["GET"])
def get_tv_noise_job(session_id: str):
    with _tv_noise_jobs_lock:
        job = _tv_noise_jobs.get(session_id)
    if not job:
        return _error("TV noise job khong ton tai.", code="job_not_found", status=404)
    return jsonify(job)


@story_video_bp.route("/api/story-video/tv-noise-overlays/<overlay_id>", methods=["PATCH"])
def update_tv_noise_overlay_config(overlay_id: str):
    from src.utils.story_tv_noise_overlays import update_tv_noise_overlay

    data = request.get_json(silent=True) or {}
    updates = {}
    try:
        for key in ("enabled", "name", "order", "opacity", "tolerance", "softness"):
            if key in data:
                updates[key] = data.get(key)
        overlay, should_regenerate = update_tv_noise_overlay(overlay_id, updates)
    except (TypeError, ValueError):
        return _error("Cau hinh TV noise khong hop le.", code="invalid_tv_noise_config")
    except Exception as exc:
        logger.error(f"[StoryVideo] TV noise update error: {exc}", exc_info=True)
        return _error(f"Khong the cap nhat TV noise overlay: {exc}", code="tv_noise_update_failed", status=500)

    if not overlay:
        return _error("TV noise overlay khong ton tai.", code="overlay_not_found", status=404)
    if should_regenerate:
        _run_tv_noise_preprocess_async(overlay_id)
    return jsonify({"overlay": overlay})


@story_video_bp.route("/api/story-video/tv-noise-overlays/<overlay_id>", methods=["DELETE"])
def delete_tv_noise_overlay(overlay_id: str):
    from src.utils.story_tv_noise_overlays import delete_tv_noise_overlay_record

    if not delete_tv_noise_overlay_record(overlay_id):
        return _error("TV noise overlay khong ton tai.", code="overlay_not_found", status=404)

    logger.info(f"[StoryVideo] TV noise overlay deleted: {overlay_id}")
    return jsonify({"deleted": True, "overlayId": overlay_id})


@story_video_bp.route("/api/story-video/tv-noise-demo", methods=["POST"])
def generate_tv_noise_demo():
    from src.utils.ffmpeg_helper import FFmpegHelper
    from src.utils.story_tv_noise_overlays import get_active_tv_noise_overlays, get_tv_noise_overlay, processed_abs_path

    data = request.get_json(silent=True) or {}
    overlay_id = str(data.get("overlayId") or "").strip()
    sample_clip_id = str(data.get("sampleClipId") or "").strip()

    if overlay_id:
        record = get_tv_noise_overlay(overlay_id)
        overlays = [record] if record else []
    else:
        overlays = get_active_tv_noise_overlays()

    ready_overlays = [item for item in overlays if item and item.get("status") == "ready" and processed_abs_path(item)]
    if not ready_overlays:
        return _error("Chua co TV noise overlay san sang de tao demo.", code="no_ready_tv_noise", status=404)

    sample_path = None
    if sample_clip_id:
        index = _load_library_index()
        clip = next((a for a in index.get("assets", []) if a.get("id") == sample_clip_id), None)
        if clip:
            sample_path = os.path.join(Config.STORY_LIBRARY_DIR, clip.get("relative_path", ""))

    if not sample_path or not os.path.isfile(sample_path):
        index = _load_library_index()
        for asset in index.get("assets", []):
            candidate = os.path.join(Config.STORY_LIBRARY_DIR, asset.get("relative_path", ""))
            if os.path.isfile(candidate):
                sample_path = candidate
                break

    if not sample_path or not os.path.isfile(sample_path):
        return _error("Khong co clip mau trong thu vien Story Video.", code="no_sample", status=404)

    os.makedirs(Config.STORY_TV_NOISE_OVERLAY_DIR, exist_ok=True)
    demo_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(Config.STORY_TV_NOISE_OVERLAY_DIR, f"demo_{demo_id}.mp4")
    width, height = Config.TARGET_RESOLUTION.split("x", 1)

    cmd = ["ffmpeg", "-y", "-i", sample_path]
    for item in ready_overlays:
        cmd.extend(["-stream_loop", "-1", "-i", processed_abs_path(item)])

    filter_parts = [
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={Config.TARGET_FPS},setsar=1[base]"
    ]
    chain_label = "[base]"
    for index, _item in enumerate(ready_overlays):
        input_idx = index + 1
        noise_label = f"noise{index}"
        out_label = f"tvn{index}"
        filter_parts.append(f"[{input_idx}:v]setpts=PTS-STARTPTS[{noise_label}]")
        filter_parts.append(
            f"{chain_label}[{noise_label}]overlay=0:0:format=auto:eof_action=repeat:eval=init[{out_label}]"
        )
        chain_label = f"[{out_label}]"
    filter_parts.append(f"{chain_label}format=yuv420p[v]")

    cmd.extend([
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[v]",
        "-an",
        "-t",
        str(Config.STORY_TV_NOISE_DEMO_SECONDS),
    ])
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(["-movflags", "+faststart", output_path])

    if not FFmpegHelper.run_command(cmd):
        return _error("Khong the tao demo TV noise.", code="demo_failed", status=500)
    rel = os.path.relpath(output_path, Config.STORAGE_DIR).replace("\\", "/")
    return jsonify({"demoPath": rel})
