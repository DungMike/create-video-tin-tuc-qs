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
    library_clip_duration,
    load_libraries,
    load_story_library_index as _load_library_index,
    rename_library,
    resolve_library_id,
    save_story_library_index as _save_library_index,
    story_library_root,
)
from src.utils.story_library import _index_lock as _library_index_lock
from src.utils.story_intro_library import (
    IntroLibraryError,
    add_intro,
    delete_intro,
    list_intros,
    rename_intro,
    resolve_intro_path,
)

story_video_bp = Blueprint("story_video", __name__)

# In-memory tracking for download/upload sessions
_download_sessions: dict[str, dict] = {}
_download_sessions_lock = threading.Lock()
_tv_noise_jobs: dict[str, dict] = {}
_tv_noise_jobs_lock = threading.Lock()
_effect_preview_jobs: dict[str, dict] = {}
_effect_preview_jobs_lock = threading.Lock()


def _error(message: str, code: str = "bad_request", status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _clip_usage_mode_or_503(raw_mode):
    """``clipUsageMode`` cua request -> (mode, error_response).

    Mac dinh "reuse" (luong chon clip cu, khong can Mongo). Chi "once" moi kiem tra
    Mongo truoc, de batch khong xep hang roi moi video deu failed vi thieu DB.
    """
    from src.utils.clip_usage import CLIP_USAGE_ONCE, normalize_clip_usage_mode

    mode = normalize_clip_usage_mode(raw_mode)
    if mode == CLIP_USAGE_ONCE:
        from src.db import mongo

        if not mongo.is_available(force=True):
            return None, _error(mongo.unavailable_message(), code="mongo_unavailable", status=503)
    return mode, None


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


def _resolve_library_ids_or_404(raw_library_ids, raw_library_id=None):
    """Validate a multi-library render selection -> (ids, error_response).

    Accepts ``libraryIds`` (list) and falls back to the single ``libraryId`` sent
    by clients from before multi-select. Duplicates are dropped keeping the click
    order, and an unknown id is a hard 404 rather than a silent fallback so the
    user never renders from a library they did not pick.
    """
    ensure_libraries_registry()
    raw_values = raw_library_ids if isinstance(raw_library_ids, (list, tuple)) else []
    candidates = [str(value or "").strip() for value in raw_values]
    candidates = [value for value in candidates if value]
    if not candidates:
        candidates = [str(raw_library_id or "").strip()]

    resolved: list[str] = []
    for raw in candidates:
        library_id, err = _resolve_or_404(raw)
        if err is not None:
            return None, err
        if library_id and library_id not in resolved:
            resolved.append(library_id)

    if not resolved:
        return None, _error("Chưa chọn thư viện clip nguồn.", code="missing_library")
    return resolved, None


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
        # Upper-bound at the highest provider max (Pixabay 200); the per-provider
        # clamp in search_provider_videos narrows Pexels to its own 80 limit.
        per_page = max(1, min(200, int(request.args.get("per_page", 200))))
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
    # Tu khoa da dung de tim cac video nay (tuy chon): chi de ghi DB tu khoa.
    # ``queries`` = {provider: keyword} (moi tab search mot tu khoa); ``query`` = chung.
    shared_query = str(data.get("query") or "").strip()
    raw_queries = data.get("queries") if isinstance(data.get("queries"), dict) else {}
    search_queries = {
        name: str(raw_queries.get(name) or shared_query).strip() for name in ("pixabay", "pexels")
    }

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
        # Download URL từ kết quả search (previewUrl) để bỏ qua bước resolve
        # từng video — giảm mạnh số API call, tránh 429 rate limit.
        download_url = str(item.get("downloadUrl") or item.get("previewUrl") or "").strip()
        if provider in {"pixabay", "pexels"} and video_id:
            entry = {
                "provider": provider,
                "id": video_id,
                "pageUrl": page_url,
                "downloadUrl": download_url,
            }
            # Metadata cua ket qua search, chi de ghi DB video goc (src/db/).
            for field in ("title", "tags", "author", "duration", "width", "height", "thumbnailUrl"):
                if item.get(field) not in (None, ""):
                    entry[field] = item.get(field)
            if search_queries.get(provider):
                entry["keyword"] = search_queries[provider]
            items.append(entry)

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
            if added_count:
                from src.db.keyword_repo import report_keyword_sweep

                for provider_name in sorted({entry["provider"] for entry in items}):
                    if search_queries.get(provider_name):
                        report_keyword_sweep(
                            provider_name, search_queries[provider_name], "manual", "manual",
                            videos_downloaded=sum(1 for entry in items if entry["provider"] == provider_name),
                        )
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
# 4d. Prefetch branch — download every search hit first, review locally, then cut.
#
# The alternative to 4b+4c: instead of previewing candidates over the provider CDN
# and importing the picked ones, a sweep downloads the whole keyword into staging
# so the review happens on local files. Deliberately a separate set of routes —
# the flow above stays untouched. See src/utils/story_video_prefetch.py.
# ---------------------------------------------------------------------------
_PREFETCH_ORIENTATIONS = {"landscape", "portrait", "square"}


@story_video_bp.route("/api/story-video/library/prefetch", methods=["POST"])
def start_story_prefetch():
    from src.utils import story_video_prefetch as prefetch

    data = request.get_json(silent=True) or {}

    library_id, err = _resolve_or_404(data.get("libraryId"))
    if err:
        return err

    provider = str(data.get("provider") or "").strip().lower()
    if provider not in {"pixabay", "pexels"}:
        return _error("Provider khong hop le.", code="invalid_provider")

    query = str(data.get("query") or "").strip()
    if not query:
        return _error("Can nhap keyword de tai video.", code="missing_query")

    tags = data.get("tags", [])
    if tags is not None and not isinstance(tags, list):
        return _error("tags phai la array.", code="invalid_tags")
    clean_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]

    orientation = str(data.get("orientation") or "").strip().lower() or None
    if orientation and orientation not in _PREFETCH_ORIENTATIONS:
        return _error("orientation phai la landscape/portrait/square.", code="invalid_orientation")

    try:
        min_width = int(data.get("minWidth") or 0) or None
        min_height = int(data.get("minHeight") or 0) or None
    except (TypeError, ValueError):
        return _error("minWidth/minHeight khong hop le.", code="invalid_dimensions")

    # Opt-in (mac dinh tat = luong cu): tu choi tu khoa DB ghi la da dung. Mongo
    # tat/chua cau hinh thi khong chan -- chay nhu binh thuong.
    if bool(data.get("skipUsedKeywords", False)):
        from src.db.keyword_repo import find_used_keyword_pairs

        used_pairs, _check = find_used_keyword_pairs([provider], [query])
        if used_pairs:
            used = used_pairs[0]
            return jsonify({"error": {
                "code": "keyword_used",
                "message": (
                    f"Tu khoa '{query}' da duoc dung tren {provider} "
                    f"(trang thai: {used['status']}). Bo tick 'Bo qua tu khoa da dung' neu van muon tai lai."
                ),
                "keyword": query,
                "provider": provider,
                "status": used["status"],
                "lastSearchedAt": used["lastSearchedAt"],
            }}), 409

    # One sweep at a time per library: two concurrent sweeps would race on the
    # library index during their commits and make the progress UI meaningless.
    if prefetch.has_active_session(library_id):
        return _error(
            "Thu vien dang co mot luot tai truoc dang chay. Hay doi hoac huy luot do.",
            code="prefetch_busy",
            status=409,
        )

    prefetch.cleanup_expired_sessions()

    session = prefetch.create_session(
        library_id=library_id,
        provider=provider,
        query=query,
        tags=clean_tags,
        orientation=orientation,
        min_width=min_width,
        min_height=min_height,
        skip_imported=bool(data.get("skipImported", True)),
    )
    session_id = session["sessionId"]

    threading.Thread(target=prefetch.run_prefetch, args=(session_id,), daemon=True).start()
    return jsonify(session), 202


@story_video_bp.route("/api/story-video/library/prefetch", methods=["GET"])
def list_story_prefetch_sessions():
    from src.utils import story_video_prefetch as prefetch

    library_id, err = _resolve_or_404(request.args.get("libraryId"))
    if err:
        return err

    prefetch.cleanup_expired_sessions()
    include_completed = str(request.args.get("includeCompleted", "")).lower() in {"1", "true", "yes"}
    sessions = prefetch.list_sessions(library_id, include_completed=include_completed)
    return jsonify({"sessions": sessions})


@story_video_bp.route("/api/story-video/library/prefetch/<session_id>", methods=["GET"])
def get_story_prefetch_session(session_id: str):
    from src.utils import story_video_prefetch as prefetch

    session = prefetch.load_manifest(session_id)
    if session is None:
        return _error("Prefetch session khong ton tai.", code="prefetch_session_not_found", status=404)
    return jsonify(session)


@story_video_bp.route(
    "/api/story-video/library/prefetch/<session_id>/items/<item_id>/poster",
    methods=["GET"],
)
def story_prefetch_item_poster(session_id: str, item_id: str):
    """Poster frame for one staged video, cut from the local file and cached.

    The review grid cannot rely on the provider thumbnail: Pixabay sessions have
    none recorded, so without this every card renders black.
    """
    from src.utils import story_video_prefetch as prefetch

    try:
        poster_path = prefetch.ensure_item_poster(session_id, item_id)
    except prefetch.PrefetchError as exc:
        missing = {
            "prefetch_session_not_found",
            "prefetch_item_not_found",
            "prefetch_item_missing_file",
            "invalid_prefetch_session",
        }
        return _error(str(exc), code=exc.code, status=404 if exc.code in missing else 500)

    response = send_file(poster_path, mimetype="image/jpeg", conditional=True)
    # Immutable once cut: the staged file never changes under a given item id.
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@story_video_bp.route("/api/story-video/library/prefetch/<session_id>/cancel", methods=["POST"])
def cancel_story_prefetch(session_id: str):
    from src.utils import story_video_prefetch as prefetch

    session = prefetch.request_cancel(session_id)
    if session is None:
        return _error("Prefetch session khong ton tai.", code="prefetch_session_not_found", status=404)
    return jsonify(session)


@story_video_bp.route("/api/story-video/library/prefetch/<session_id>/discard-items", methods=["POST"])
def discard_story_prefetch_items(session_id: str):
    from src.utils import story_video_prefetch as prefetch

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Payload phai la JSON object.", code="invalid_payload")

    raw_ids = data.get("itemIds")
    if not isinstance(raw_ids, list) or not raw_ids:
        return _error("Can it nhat 1 itemId de xoa.", code="invalid_item_ids")

    try:
        return jsonify(prefetch.discard_items(session_id, [str(item) for item in raw_ids]))
    except prefetch.PrefetchError as exc:
        status = 404 if exc.code == "prefetch_session_not_found" else 400
        return _error(str(exc), code=exc.code, status=status)


@story_video_bp.route("/api/story-video/library/prefetch/<session_id>/commit", methods=["POST"])
def commit_story_prefetch(session_id: str):
    from src.utils import story_video_prefetch as prefetch

    data = request.get_json(silent=True) or {}

    session = prefetch.load_manifest(session_id)
    if session is None:
        return _error("Prefetch session khong ton tai.", code="prefetch_session_not_found", status=404)
    # "failed" is allowed so a commit that died part-way can be resumed: run_commit
    # only picks up items still marked "downloaded", so already-ingested videos are
    # not cut twice and the staged files do not have to be thrown away.
    if session.get("status") not in {prefetch.REVIEW_STATUS, "cancelled", "failed"}:
        return _error(
            "Chi co the cat clip khi luot tai da hoan tat.",
            code="prefetch_not_ready",
            status=409,
        )

    tags = data.get("tags", [])
    if tags is not None and not isinstance(tags, list):
        return _error("tags phai la array.", code="invalid_tags")
    clean_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]

    kept = [item for item in session.get("items", []) if item.get("status") == "downloaded"]
    if not kept:
        return _error("Khong con video nao de cat clip.", code="prefetch_nothing_to_commit")

    library_id = session.get("libraryId")
    delete_raw_after = bool(data.get("deleteRawAfter"))

    # Mirror the commit into the in-memory session map so _has_active_library_session
    # keeps rejecting "delete the whole library" while clips are being ingested,
    # exactly as it does for the import-selected flow.
    with _download_sessions_lock:
        _download_sessions[session_id] = {
            "sessionId": session_id,
            "status": "splitting",
            "current": 0,
            "total": len(kept),
            "message": "Bat dau cat clip...",
            "addedClips": 0,
            "libraryId": library_id,
        }

    def _run_commit():
        def progress_cb(progress_data):
            with _download_sessions_lock:
                if session_id in _download_sessions:
                    _download_sessions[session_id].update(progress_data)

        try:
            prefetch.run_commit(
                session_id,
                tags=clean_tags,
                delete_raw_after=delete_raw_after,
                progress_callback=progress_cb,
            )
            final = prefetch.load_manifest(session_id) or {}
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "completed" if final.get("status") == "completed" else "failed",
                    "current": final.get("total", len(kept)),
                    "total": final.get("total", len(kept)),
                    "message": final.get("message", ""),
                    "addedClips": final.get("addedClips", 0),
                })
        except Exception as exc:
            logger.error(f"[StoryVideo] Prefetch commit failed: {exc}", exc_info=True)
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "failed",
                    "message": f"Loi: {exc}",
                })

    threading.Thread(target=_run_commit, daemon=True).start()
    return jsonify({"sessionId": session_id, "total": len(kept)}), 202


@story_video_bp.route("/api/story-video/library/prefetch/<session_id>", methods=["DELETE"])
def delete_story_prefetch_session(session_id: str):
    from src.utils import story_video_prefetch as prefetch

    session = prefetch.load_manifest(session_id)
    if session is None:
        return _error("Prefetch session khong ton tai.", code="prefetch_session_not_found", status=404)
    if session.get("status") == "committing":
        return _error(
            "Luot nay dang cat clip, khong the xoa.",
            code="prefetch_busy",
            status=409,
        )

    # A sweep still in flight has to be told to stop before its dir disappears,
    # or its workers keep writing files into a directory nobody will clean up.
    if session.get("status") in {"searching", "downloading", "cancelling"}:
        prefetch.request_cancel(session_id)

    prefetch.discard_session(session_id)
    return jsonify({"deleted": True, "sessionId": session_id})


# ---------------------------------------------------------------------------
# 4e. Bulk harvest — tai het video theo tu khoa TRUOC, chon loc SAU.
#
# Luong doc lap voi 4b/4c o tren: thay vi preview tung ket qua tu CDN provider
# roi moi tai, o day search -> tai het ve staging -> preview tu o cung -> xoa
# video thua -> chi video con lai moi cat clip va vao thu vien.
# Xem src/utils/story_bulk_harvest.py.
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/harvest", methods=["POST"])
def start_story_harvest():
    from src.utils.story_bulk_harvest import start_harvest_job

    data = request.get_json(silent=True) or {}
    library_id, err = _resolve_or_404(data.get("libraryId"))
    if err:
        return err

    raw_keywords = data.get("keywords")
    if isinstance(raw_keywords, str):
        raw_keywords = raw_keywords.replace(",", "\n").split("\n")
    if not isinstance(raw_keywords, list):
        return _error("keywords phai la array hoac chuoi.", code="invalid_keywords")

    providers = data.get("providers")
    if not isinstance(providers, list):
        return _error("providers phai la array.", code="invalid_providers")

    tags = data.get("tags") or []
    if not isinstance(tags, list):
        return _error("tags phai la array.", code="invalid_tags")

    try:
        max_per_keyword = max(0, int(data.get("maxPerKeyword") or 0))
    except (TypeError, ValueError):
        return _error("maxPerKeyword khong hop le.", code="invalid_max_per_keyword")

    try:
        progress = start_harvest_job(
            keywords=raw_keywords,
            providers=providers,
            library_id=library_id,
            tags=tags,
            landscape_only=bool(data.get("landscapeOnly", True)),
            max_per_keyword=max_per_keyword,
            skip_used_keywords=bool(data.get("skipUsedKeywords", False)),
        )
    except ValueError as exc:
        return _error(str(exc), code="invalid_harvest_request")

    return jsonify(progress), 202


# ---------------------------------------------------------------------------
# 4f. MongoDB: tu khoa da tim + luot dung clip (che do "moi clip 1 lan").
# Chi doc/ghi Mongo; khong route nao o tren phu thuoc vao cac route nay.
# ---------------------------------------------------------------------------
def _mongo_or_503():
    from src.db import mongo

    if not mongo.is_available():
        return _error(mongo.unavailable_message(), code="mongo_unavailable", status=503)
    return None


@story_video_bp.route("/api/story-video/search-keywords", methods=["GET"])
def list_search_keywords():
    from src.db.keyword_repo import list_keywords

    err = _mongo_or_503()
    if err is not None:
        return err
    provider = str(request.args.get("provider") or "").strip().lower() or None
    if provider and provider not in {"pixabay", "pexels"}:
        return _error("Provider khong hop le.", code="invalid_provider")
    try:
        limit = max(1, min(20000, int(request.args.get("limit", 5000))))
    except ValueError:
        return _error("limit khong hop le.", code="invalid_limit")
    return jsonify({"items": list_keywords(provider, limit)})


@story_video_bp.route("/api/story-video/search-keywords/lookup", methods=["GET"])
def lookup_search_keyword():
    """Lich su mot tu khoa tren mot provider -> ``{record|null}``. Mongo tat -> null."""
    from src.db import mongo
    from src.db.keyword_repo import get_keyword_records, normalize_keyword, serialize_keyword

    provider = str(request.args.get("provider") or "").strip().lower()
    query = str(request.args.get("q") or "").strip()
    if provider not in {"pixabay", "pexels"} or not query:
        return _error("Can provider (pixabay/pexels) va q.", code="invalid_lookup")
    if not mongo.is_available():
        return jsonify({"record": None, "mongoAvailable": False})
    try:
        doc = get_keyword_records(provider, [query]).get(normalize_keyword(query))
    except Exception as exc:
        logger.warning(f"[Keywords] lookup {provider}:{query!r} failed: {exc}")
        return jsonify({"record": None, "mongoAvailable": False})
    return jsonify({"record": serialize_keyword(doc), "mongoAvailable": True})


@story_video_bp.route("/api/story-video/search-keywords/<provider>", methods=["DELETE"])
def delete_search_keyword(provider: str):
    """Xoa mot tu khoa (``?keyword=``) de tim/tai lai duoc."""
    from src.db.keyword_repo import delete_keyword

    provider = provider.strip().lower()
    keyword = str(request.args.get("keyword") or "").strip()
    if provider not in {"pixabay", "pexels"} or not keyword:
        return _error("Can provider (pixabay/pexels) va keyword.", code="invalid_keyword")
    err = _mongo_or_503()
    if err is not None:
        return err
    if not delete_keyword(provider, keyword):
        return _error("Khong tim thay tu khoa.", code="keyword_not_found", status=404)
    return jsonify({"deleted": True, "provider": provider, "keyword": keyword})


@story_video_bp.route("/api/story-video/clip-usage/summary", methods=["GET"])
def clip_usage_summary():
    """So clip chua dung / da dung N lan cua cac thu vien (``?libraryIds=a,b``)."""
    from src.db.media_repo import usage_summary
    from src.utils.clip_identity import clip_identity

    raw_ids = [part.strip() for part in str(request.args.get("libraryIds") or "").split(",") if part.strip()]
    library_ids, library_error = _resolve_library_ids_or_404(raw_ids, request.args.get("libraryId"))
    if library_error is not None:
        return library_error
    err = _mongo_or_503()
    if err is not None:
        return err

    keys: set[str] = set()
    for library_id in library_ids:
        for asset in _load_library_index(library_id).get("assets", []):
            if asset.get("relative_path"):
                keys.add(clip_identity(library_id, asset)[1])
    try:
        summary = usage_summary(keys)
    except Exception as exc:
        logger.warning(f"[ClipUsage] summary failed: {exc}")
        return _error(f"Khong doc duoc luot dung clip: {exc}", code="mongo_unavailable", status=503)
    summary["libraryIds"] = library_ids
    return jsonify(summary)


@story_video_bp.route("/api/story-video/library/harvest", methods=["GET"])
def list_story_harvest_jobs():
    from src.utils.story_bulk_harvest import list_harvest_jobs

    return jsonify({"jobs": list_harvest_jobs()})


@story_video_bp.route("/api/story-video/library/harvest/<job_id>", methods=["GET"])
def get_story_harvest_job(job_id: str):
    from src.utils.story_bulk_harvest import load_harvest_progress

    progress = load_harvest_progress(job_id)
    if not progress:
        return _error("Harvest job khong ton tai.", code="harvest_not_found", status=404)
    return jsonify(progress)


@story_video_bp.route("/api/story-video/library/harvest/<job_id>/items", methods=["GET"])
def get_story_harvest_items(job_id: str):
    from src.utils.story_bulk_harvest import load_harvest_manifest, load_harvest_progress

    if not load_harvest_progress(job_id):
        return _error("Harvest job khong ton tai.", code="harvest_not_found", status=404)

    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 24))))
    except ValueError:
        return _error("page/per_page khong hop le.", code="invalid_pagination")

    all_items = [
        item for item in load_harvest_manifest(job_id).get("items", [])
        if item.get("status") == "kept"
    ]
    keywords = sorted({item.get("keyword") or "" for item in all_items if item.get("keyword")})

    keyword = request.args.get("keyword", "").strip()
    items = [item for item in all_items if item.get("keyword") == keyword] if keyword else all_items

    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page

    return jsonify({
        "items": items[start:start + per_page],
        "total": total,
        "page": page,
        "perPage": per_page,
        "totalPages": total_pages,
        "keywords": keywords,
        "keptTotal": len(all_items),
    })


@story_video_bp.route("/api/story-video/library/harvest/<job_id>/cancel", methods=["POST"])
def cancel_story_harvest(job_id: str):
    from src.utils.story_bulk_harvest import request_harvest_cancel

    progress = request_harvest_cancel(job_id)
    if not progress:
        return _error("Harvest job khong ton tai.", code="harvest_not_found", status=404)
    return jsonify(progress)


@story_video_bp.route("/api/story-video/library/harvest/<job_id>/resume", methods=["POST"])
def resume_story_harvest(job_id: str):
    """Chay tiep job bi dut (thuong la do restart web app giet daemon thread)."""
    from src.utils.story_bulk_harvest import resume_harvest_job

    try:
        progress = resume_harvest_job(job_id)
    except ValueError as exc:
        return _error(str(exc), code="harvest_resume_failed")
    if not progress:
        return _error("Harvest job khong ton tai.", code="harvest_not_found", status=404)

    logger.info(f"[Harvest] Resumed job={job_id}")
    return jsonify(progress), 202


@story_video_bp.route("/api/story-video/library/harvest/<job_id>/items/delete", methods=["POST"])
def delete_story_harvest_items(job_id: str):
    from src.utils.story_bulk_harvest import (
        delete_harvest_items,
        load_harvest_manifest,
        load_harvest_progress,
    )

    progress = load_harvest_progress(job_id)
    if not progress:
        return _error("Harvest job khong ton tai.", code="harvest_not_found", status=404)

    # Worker ghi de nguyen file manifest, nen xoa item khi job dang chay se bi
    # ghi de mat. Doi job dung han (hoac huy) roi moi chon loc.
    if progress.get("status") in {"running", "cancelling"}:
        return _error(
            "Job dang tai video. Hay doi tai xong hoac huy truoc khi xoa.",
            code="harvest_running",
            status=409,
        )

    data = request.get_json(silent=True) or {}
    scope = str(data.get("scope") or "ids").strip().lower()

    if scope == "all":
        result = delete_harvest_items(job_id, delete_all=True)
    elif scope == "keyword":
        keyword = str(data.get("keyword") or "").strip()
        if not keyword:
            return _error("Can truyen keyword khi scope='keyword'.", code="invalid_keyword")
        item_ids = [
            item["itemId"] for item in load_harvest_manifest(job_id).get("items", [])
            if item.get("status") == "kept" and item.get("keyword") == keyword
        ]
        result = delete_harvest_items(job_id, item_ids)
    elif scope == "ids":
        raw_ids = data.get("itemIds")
        if not isinstance(raw_ids, list):
            return _error("itemIds phai la array.", code="invalid_item_ids")
        item_ids = [str(value).strip() for value in raw_ids if str(value).strip()]
        if not item_ids:
            return _error("Can it nhat 1 itemId de xoa.", code="invalid_item_ids")
        result = delete_harvest_items(job_id, item_ids)
    else:
        return _error("scope phai la 'ids', 'keyword' hoac 'all'.", code="invalid_scope")

    return jsonify({"scope": scope, **result})


@story_video_bp.route("/api/story-video/library/harvest/<job_id>/commit", methods=["POST"])
def commit_story_harvest(job_id: str):
    from src.utils.story_bulk_harvest import load_harvest_manifest, load_harvest_progress

    progress = load_harvest_progress(job_id)
    if not progress:
        return _error("Harvest job khong ton tai.", code="harvest_not_found", status=404)

    if progress.get("status") in {"running", "cancelling"}:
        return _error(
            "Job dang tai video. Hay doi tai xong hoac huy truoc khi nhap thu vien.",
            code="harvest_running",
            status=409,
        )

    data = request.get_json(silent=True) or {}
    library_id, err = _resolve_or_404(data.get("libraryId") or progress.get("libraryId"))
    if err:
        return err

    pending = [
        item for item in load_harvest_manifest(job_id).get("items", [])
        if item.get("status") == "kept"
    ]
    if not pending:
        return _error("Khong con video nao de nhap thu vien.", code="no_harvest_items")

    delete_staging = bool(data.get("deleteStaging", True))
    session_id = f"hvc-{str(uuid.uuid4())[:8]}"

    # Dung chung _download_sessions + GET /download-progress/<id> co san nen
    # frontend poll bang dung mot ham getStoryDownloadProgress.
    with _download_sessions_lock:
        _download_sessions[session_id] = {
            "sessionId": session_id,
            "status": "splitting",
            "current": 0,
            "total": len(pending),
            "message": "Bat dau cat clip va nhap thu vien...",
            "addedClips": 0,
            "libraryId": library_id,
        }

    def _run_commit():
        from src.utils.story_bulk_harvest import commit_harvest_job

        def progress_cb(progress_data):
            with _download_sessions_lock:
                if session_id in _download_sessions:
                    _download_sessions[session_id].update(progress_data)

        try:
            result = commit_harvest_job(
                job_id,
                library_id=library_id,
                session_id=session_id,
                progress_callback=progress_cb,
                delete_staging=delete_staging,
            )
            added_count = len(result) if isinstance(result, list) else 0
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "completed",
                    "current": len(pending),
                    "total": len(pending),
                    "message": f"Hoan tat! Da them {added_count} clips.",
                    "addedClips": added_count,
                })
        except Exception as exc:
            logger.error(f"[StoryVideo] Harvest commit failed: {exc}", exc_info=True)
            with _download_sessions_lock:
                _download_sessions[session_id].update({
                    "status": "failed",
                    "message": f"Loi: {str(exc)}",
                })

    threading.Thread(target=_run_commit, daemon=True).start()
    return jsonify({"sessionId": session_id, "total": len(pending)}), 202


@story_video_bp.route("/api/story-video/library/harvest/<job_id>", methods=["DELETE"])
def delete_story_harvest_job(job_id: str):
    from src.utils.story_bulk_harvest import delete_harvest_job, load_harvest_progress

    progress = load_harvest_progress(job_id)
    if not progress:
        return _error("Harvest job khong ton tai.", code="harvest_not_found", status=404)
    if progress.get("status") in {"running", "cancelling"}:
        return _error(
            "Job dang chay. Hay huy truoc khi xoa.",
            code="harvest_running",
            status=409,
        )

    delete_harvest_job(job_id)
    return jsonify({"deleted": True, "jobId": job_id})


# ---------------------------------------------------------------------------
# 4g. Thu vien clip tu anh: tim anh Pexels/Pixabay -> duyet -> 1 anh = 1 clip Ken
# Burns vao thu vien. Can MongoDB (con tro phan trang theo tu khoa + chong trung
# anh). Xem src/utils/story_image_clips.py. Khong route nao o tren dung toi day.
# ---------------------------------------------------------------------------
def _image_clip_mongo_or_503():
    from src.db import mongo

    if not mongo.is_available(force=True):
        return _error(mongo.unavailable_message(), code="mongo_unavailable", status=503)
    return None


def _image_clip_job_or_404(job_id: str):
    from src.utils.story_image_clips import load_progress

    progress = load_progress(job_id)
    if not progress:
        return None, _error("Job ảnh không tồn tại.", code="image_clip_not_found", status=404)
    return progress, None


def _is_mongo_error(exc: Exception) -> bool:
    from src.db.mongo import MongoUnavailable

    if isinstance(exc, MongoUnavailable):
        return True
    try:
        from pymongo.errors import PyMongoError
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, PyMongoError)


@story_video_bp.route("/api/story-video/image-clips/effects", methods=["GET"])
def list_image_clip_effects():
    from src.utils import image_clip_effects as effects

    duration_min, duration_max = effects.duration_range()
    return jsonify({
        "effects": effects.list_effects(),
        "defaultZoom": effects.DEFAULT_ZOOM,
        "zoomMin": effects.ZOOM_MIN,
        "zoomMax": effects.ZOOM_MAX,
        # STORY_IMAGE_CLIP_DURATION_MIN/MAX: moi anh dai ngau nhien trong khoang nay.
        "durationMin": duration_min,
        "durationMax": duration_max,
    })


@story_video_bp.route("/api/story-video/image-clips/jobs", methods=["POST"])
def start_image_clip_job_route():
    from src.utils.story_image_clips import (
        ImageClipBusy,
        ImageClipError,
        LibraryHasMotion,
        describe_job,
        start_image_clip_job,
    )

    data = request.get_json(silent=True) or {}
    library_id, err = _resolve_or_404(data.get("libraryId"))
    if err:
        return err

    raw_keywords = data.get("keywords")
    if isinstance(raw_keywords, str):
        raw_keywords = raw_keywords.replace(",", "\n").split("\n")
    if not isinstance(raw_keywords, list):
        return _error("keywords phải là array hoặc chuỗi.", code="invalid_keywords")
    providers = data.get("providers")
    if not isinstance(providers, list):
        return _error("providers phải là array.", code="invalid_providers")
    tags = data.get("tags") or []
    effects_enabled = data.get("effects") or []
    if not isinstance(tags, list) or not isinstance(effects_enabled, list):
        return _error("tags/effects phải là array.", code="invalid_image_clip_request")
    try:
        max_per_keyword = max(0, int(data.get("maxPerKeyword", 100) or 0))
        zoom = float(data.get("zoom") or 0) or None
    except (TypeError, ValueError):
        return _error("maxPerKeyword/zoom không hợp lệ.", code="invalid_image_clip_request")

    err = _image_clip_mongo_or_503()
    if err is not None:
        return err
    try:
        kwargs = {"zoom": zoom} if zoom else {}
        progress = start_image_clip_job(
            keywords=raw_keywords,
            providers=providers,
            library_id=library_id,
            tags=tags,
            max_per_keyword=max_per_keyword,
            effects_enabled=effects_enabled,
            rescan_exhausted=bool(data.get("rescanExhausted", False)),
            **kwargs,
        )
    except ImageClipBusy as exc:
        return _error(str(exc), code="image_clip_busy", status=409)
    except LibraryHasMotion as exc:
        return _error(str(exc), code="library_has_motion", status=409)
    except ImageClipError as exc:
        return _error(str(exc), code="invalid_image_clip_request")
    except Exception as exc:
        if _is_mongo_error(exc):
            from src.db import mongo

            return _error(mongo.unavailable_message(), code="mongo_unavailable", status=503)
        raise
    return jsonify(describe_job(progress)), 202


@story_video_bp.route("/api/story-video/image-clips/jobs", methods=["GET"])
def list_image_clip_jobs_route():
    from src.utils.story_image_clips import list_jobs

    return jsonify({"jobs": list_jobs()})


@story_video_bp.route("/api/story-video/image-clips/jobs/<job_id>", methods=["GET"])
def get_image_clip_job_route(job_id: str):
    from src.utils.story_image_clips import describe_job

    progress, err = _image_clip_job_or_404(job_id)
    if err:
        return err
    return jsonify(describe_job(progress))


@story_video_bp.route("/api/story-video/image-clips/jobs/<job_id>", methods=["DELETE"])
def delete_image_clip_job_route(job_id: str):
    from src.utils.story_image_clips import ImageClipBusy, delete_job

    _progress, err = _image_clip_job_or_404(job_id)
    if err:
        return err
    try:
        delete_job(job_id)
    except ImageClipBusy as exc:
        return _error(str(exc), code="image_clip_busy", status=409)
    return jsonify({"deleted": True, "jobId": job_id})


@story_video_bp.route("/api/story-video/image-clips/jobs/<job_id>/items", methods=["GET"])
def get_image_clip_items_route(job_id: str):
    from src.utils.story_image_clips import load_manifest

    _progress, err = _image_clip_job_or_404(job_id)
    if err:
        return err
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 24))))
    except ValueError:
        return _error("page/per_page không hợp lệ.", code="invalid_pagination")

    all_items = [item for item in load_manifest(job_id)["items"] if item.get("status") == "kept"]
    keywords = sorted({item.get("keyword") or "" for item in all_items if item.get("keyword")})
    keyword = request.args.get("keyword", "").strip()
    items = [item for item in all_items if item.get("keyword") == keyword] if keyword else all_items
    total = len(items)
    start = (page - 1) * per_page
    return jsonify({
        "items": items[start:start + per_page],
        "total": total,
        "page": page,
        "perPage": per_page,
        "totalPages": max(1, (total + per_page - 1) // per_page),
        "keywords": keywords,
        "keptTotal": len(all_items),
        "failedTotal": sum(1 for item in all_items if item.get("lastError")),
    })


@story_video_bp.route("/api/story-video/image-clips/jobs/<job_id>/cancel", methods=["POST"])
def cancel_image_clip_job_route(job_id: str):
    from src.utils.story_image_clips import request_cancel

    progress = request_cancel(job_id)
    if not progress:
        return _error("Job ảnh không tồn tại.", code="image_clip_not_found", status=404)
    return jsonify(progress)


@story_video_bp.route("/api/story-video/image-clips/jobs/<job_id>/items/delete", methods=["POST"])
def delete_image_clip_items_route(job_id: str):
    from src.utils.story_image_clips import ImageClipBusy, delete_items

    _progress, err = _image_clip_job_or_404(job_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    scope = str(data.get("scope") or "ids").strip().lower()
    try:
        if scope == "all":
            result = delete_items(job_id, delete_all=True)
        elif scope == "keyword":
            keyword = str(data.get("keyword") or "").strip()
            if not keyword:
                return _error("Cần truyền keyword khi scope='keyword'.", code="invalid_keyword")
            result = delete_items(job_id, keyword=keyword)
        elif scope == "ids":
            raw_ids = data.get("itemIds")
            if not isinstance(raw_ids, list):
                return _error("itemIds phải là array.", code="invalid_item_ids")
            item_ids = [str(value).strip() for value in raw_ids if str(value).strip()]
            if not item_ids:
                return _error("Cần ít nhất 1 itemId để xoá.", code="invalid_item_ids")
            result = delete_items(job_id, item_ids)
        else:
            return _error("scope phải là 'ids', 'keyword' hoặc 'all'.", code="invalid_scope")
    except ImageClipBusy as exc:
        return _error(str(exc), code="image_clip_busy", status=409)
    return jsonify({"scope": scope, **result})


@story_video_bp.route("/api/story-video/image-clips/jobs/<job_id>/items/<item_id>", methods=["PATCH"])
def update_image_clip_item_route(job_id: str, item_id: str):
    from src.utils.story_image_clips import ImageClipBusy, ImageClipError, set_item_effect

    _progress, err = _image_clip_job_or_404(job_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        item = set_item_effect(job_id, item_id, str(data.get("effect") or "").strip())
    except ImageClipBusy as exc:
        return _error(str(exc), code="image_clip_busy", status=409)
    except ImageClipError as exc:
        return _error(str(exc), code="invalid_effect")
    except KeyError:
        return _error("Ảnh không tồn tại hoặc đã tạo clip.", code="image_clip_item_not_found", status=404)
    return jsonify({"item": item})


@story_video_bp.route("/api/story-video/image-clips/jobs/<job_id>/commit", methods=["POST"])
def commit_image_clip_job_route(job_id: str):
    from src.utils.story_image_clips import ImageClipBusy, ImageClipError, LibraryHasMotion, start_commit

    progress, err = _image_clip_job_or_404(job_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    library_id, err = _resolve_or_404(data.get("libraryId") or progress.get("libraryId"))
    if err:
        return err
    err = _image_clip_mongo_or_503()
    if err is not None:
        return err

    session_id = f"icc-{uuid.uuid4().hex[:8]}"
    # Dung chung _download_sessions + GET /library/download-progress/<id>: frontend
    # poll bang getStoryDownloadProgress, va bake/xoa thu vien biet dang co ingest.
    with _download_sessions_lock:
        _download_sessions[session_id] = {
            "sessionId": session_id,
            "status": "rendering",
            "current": 0,
            "total": 0,
            "message": "Bắt đầu tạo clip từ ảnh...",
            "addedClips": 0,
            "libraryId": library_id,
            "jobId": job_id,
        }

    def progress_cb(progress_data):
        with _download_sessions_lock:
            if session_id in _download_sessions:
                _download_sessions[session_id].update(progress_data)

    try:
        total = start_commit(
            job_id,
            library_id=library_id,
            session_id=session_id,
            progress_callback=progress_cb,
            delete_staging=bool(data.get("deleteStaging", True)),
        )
    except (ImageClipBusy, LibraryHasMotion, ImageClipError) as exc:
        with _download_sessions_lock:
            _download_sessions.pop(session_id, None)
        if isinstance(exc, ImageClipBusy):
            return _error(str(exc), code="image_clip_busy", status=409)
        if isinstance(exc, LibraryHasMotion):
            return _error(str(exc), code="library_has_motion", status=409)
        return _error(str(exc), code="no_image_clip_items")
    with _download_sessions_lock:
        if session_id in _download_sessions and not _download_sessions[session_id].get("total"):
            _download_sessions[session_id]["total"] = total
    return jsonify({"sessionId": session_id, "total": total}), 202


@story_video_bp.route("/api/story-video/image-clips/jobs/<job_id>/commit/cancel", methods=["POST"])
def cancel_image_clip_commit_route(job_id: str):
    from src.utils.story_image_clips import request_commit_cancel

    _progress, err = _image_clip_job_or_404(job_id)
    if err:
        return err
    return jsonify({"cancelRequested": request_commit_cancel(job_id)})


@story_video_bp.route("/api/story-video/image-clips/cursors", methods=["GET"])
def list_image_search_cursors_route():
    from src.db import image_repo
    from src.utils.image_clip_search import QUERY_SIGNATURE

    err = _mongo_or_503()
    if err is not None:
        return err
    provider = str(request.args.get("provider") or "").strip().lower() or None
    if provider and provider not in image_repo.PROVIDERS:
        return _error("Provider không hợp lệ.", code="invalid_provider")
    try:
        limit = max(1, min(5000, int(request.args.get("limit", 500))))
    except ValueError:
        return _error("limit không hợp lệ.", code="invalid_limit")
    try:
        docs = image_repo.list_cursors(provider, request.args.get("q"), limit)
        photo_counts = image_repo.photo_status_counts()
    except Exception as exc:
        if _is_mongo_error(exc):
            from src.db import mongo

            return _error(mongo.unavailable_message(), code="mongo_unavailable", status=503)
        raise
    return jsonify({
        "items": [image_repo.serialize_cursor(doc, QUERY_SIGNATURE) for doc in docs],
        "photoCounts": photo_counts,
        "querySignature": QUERY_SIGNATURE,
    })


@story_video_bp.route("/api/story-video/image-clips/cursors/<provider>/reset", methods=["POST"])
def reset_image_search_cursor_route(provider: str):
    from src.db import image_repo
    from src.utils.story_image_clips import active_search_job

    provider = provider.strip().lower()
    keyword = str(request.args.get("keyword") or (request.get_json(silent=True) or {}).get("keyword") or "").strip()
    if provider not in image_repo.PROVIDERS or not keyword:
        return _error("Cần provider (pixabay/pexels) và keyword.", code="invalid_keyword")
    err = _mongo_or_503()
    if err is not None:
        return err
    if active_search_job():
        return _error("Đang có job tìm ảnh chạy. Hãy đợi xong rồi reset.", code="image_clip_busy", status=409)
    if not image_repo.reset_cursor(provider, keyword):
        return _error("Không tìm thấy từ khóa.", code="cursor_not_found", status=404)
    return jsonify({"reset": True, "provider": provider, "keyword": keyword})


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
        # Length new clips are cut to (and the render trims each clip to) for this
        # library, so the UI never hardcodes "5 giay".
        "clipDurationSeconds": library_clip_duration(
            library_id, max(1, int(Config.STORY_CLIP_DURATION))
        ),
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
    if record.get("styled"):
        payload["styled"] = True
        payload["styleId"] = record.get("styleId")
        payload["styleLabel"] = record.get("styleLabel")
        payload["sourceLibraryId"] = record.get("sourceLibraryId")
    if record.get("fullyBaked"):
        payload["fullyBaked"] = True
        payload["clipDuration"] = record.get("clipDuration")
        payload["waveformLabel"] = record.get("waveformLabel")
        payload["ctaLabel"] = record.get("ctaLabel")
    # Bake state travels on the library record so a paused job can be resumed from
    # the library list alone — the runner is gone after an app restart.
    if record.get("bakeStatus"):
        payload["bakeStatus"] = record.get("bakeStatus")
        payload["bakeJobId"] = record.get("bakeJobId")
        payload["bakeCompleted"] = record.get("bakeCompleted")
        payload["bakeTotal"] = record.get("bakeTotal")
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
        }.get(exc.code, 400)
        return _error(exc.message, code=exc.code, status=status)
    return jsonify(result)


# ---------------------------------------------------------------------------
# 7c. Intro library CRUD (short opening clips prepended to each batch video)
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/intros", methods=["GET"])
def list_intros_route():
    return jsonify({"intros": list_intros()})


@story_video_bp.route("/api/story-video/intros", methods=["POST"])
def create_intro_route():
    file = request.files.get("file")
    if file is None or not file.filename:
        return _error("Chưa chọn file intro.", code="no_file")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in Config.ALLOWED_VIDEO_EXTENSIONS:
        return _error("File intro phải là video (mp4/mov/mkv/webm).", code="invalid_intro_format")

    name = str(request.form.get("name", "")).strip()
    if not name:
        name = os.path.splitext(file.filename)[0].strip() or "intro"

    upload_dir = os.path.join(Config.STORY_RAW_DIR, f"intro-{str(uuid.uuid4())[:8]}")
    os.makedirs(upload_dir, exist_ok=True)
    src_path = os.path.join(upload_dir, secure_filename(file.filename))
    try:
        file.save(src_path)
        record = add_intro(name, src_path)
    except IntroLibraryError as exc:
        status = 400 if exc.code in {"missing_name", "invalid_intro_format"} else 422
        return _error(exc.message, code=exc.code, status=status)
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    return jsonify({"intro": record}), 201


@story_video_bp.route("/api/story-video/intros/<intro_id>", methods=["PATCH"])
def rename_intro_route(intro_id: str):
    data = request.get_json(silent=True) or {}
    try:
        record = rename_intro(intro_id, data.get("name", ""))
    except IntroLibraryError as exc:
        status = 404 if exc.code == "intro_not_found" else 400
        return _error(exc.message, code=exc.code, status=status)
    return jsonify({"intro": record})


@story_video_bp.route("/api/story-video/intros/<intro_id>", methods=["DELETE"])
def delete_intro_route(intro_id: str):
    try:
        result = delete_intro(intro_id)
    except IntroLibraryError as exc:
        status = 404 if exc.code == "intro_not_found" else 400
        return _error(exc.message, code=exc.code, status=status)
    return jsonify(result)


# ---------------------------------------------------------------------------
# 7c. Bake a TV style into a clip library -> new pre-styled library
# ---------------------------------------------------------------------------
def _resolve_bake_style(data: dict):
    """Resolve the bake style from the request body.

    Returns ``(style_id, style_label, style_params, style_filter)`` or an
    ``(error_response, status)`` tuple wrapped via ``_error`` on failure.
    """
    from src.processors.crt_effect_processor import (
        _target_resolution,
        build_custom_tv_effect_filter,
        build_tv_effect_filter,
        get_tv_effect_style,
        sanitize_tv_effect_params,
    )

    width, height = _target_resolution()
    custom_params = data.get("params")
    style_id = str(data.get("styleId", "")).strip()

    if custom_params is not None:
        if not isinstance(custom_params, dict):
            return _error("params không hợp lệ.", code="invalid_params")
        params = sanitize_tv_effect_params(custom_params)
        style_filter = build_custom_tv_effect_filter(params, width, height)
        style_id, style_label = "custom", "Custom"
    else:
        style = get_tv_effect_style(style_id)
        if not style:
            return _error("Style không hợp lệ.", code="invalid_style", status=404)
        params = sanitize_tv_effect_params(style.get("params"))
        style_filter = build_tv_effect_filter(style_id, width, height)
        style_label = style.get("name", style_id)

    if not style_filter:
        return _error(
            "Hiệu ứng rỗng — chọn một style có hiệu ứng để bake.",
            code="empty_style",
        )
    return style_id, style_label, params, style_filter


@story_video_bp.route("/api/story-video/library/bake", methods=["POST"])
def bake_story_library():
    """Bake a TV style (style-only) into a new library.

    Only the TV effect is burned into each 5s clip; waveform + CTA stay runtime
    overlays added at the render step (no clip pairing / 10s units).
    """
    from src.utils.story_library import _utc_now_iso, set_library_metadata
    from src.utils.story_library_bake import StoryLibraryBakeRunner

    data = request.get_json(silent=True) or {}
    source_library_id = str(data.get("sourceLibraryId", "") or "").strip()
    target_name = str(data.get("name", "") or "").strip()

    source = get_library_record(source_library_id) or (
        get_library_record(get_default_library_id())
        if not source_library_id or source_library_id == get_default_library_id()
        else None
    )
    if not source:
        return _error("Không tìm thấy thư viện nguồn.", code="library_not_found", status=404)
    source_library_id = source.get("id")

    if source.get("styled"):
        return _error("Không thể bake từ một thư viện đã được style.", code="source_already_styled")
    if count_library_clips(source_library_id) <= 0:
        return _error("Thư viện nguồn không có clip nào.", code="empty_source_library")

    resolved = _resolve_bake_style(data)
    if not isinstance(resolved, tuple) or len(resolved) != 4:
        return resolved  # already an _error() response tuple
    style_id, style_label, style_params, style_filter = resolved

    if _has_active_library_session(source_library_id):
        return _error(
            "Thư viện nguồn đang được import/upload. Hãy đợi tác vụ hoàn tất.",
            code="library_in_use",
            status=409,
        )

    try:
        target = create_library(target_name)
    except LibraryError as exc:
        status = 409 if exc.code == "duplicate_library_name" else 400
        return _error(exc.message, code=exc.code, status=status)

    target_library_id = target.get("id")
    # Ken Burns is baked per clip here, so the render pays nothing for it.
    motion = str(data.get("motion") or "off").strip()
    motion = motion if motion in ("pan", "zoom") else "off"
    try:
        motion_zoom = max(1.0, min(1.4, float(data.get("motionZoom") or 1.1)))
    except (TypeError, ValueError):
        motion_zoom = 1.1
    clip_seconds = float(source.get("clipDuration") or Config.STORY_CLIP_DURATION or 3)

    set_library_metadata(
        target_library_id,
        styled=True,
        styleId=style_id,
        styleLabel=style_label,
        styleParams=style_params,
        sourceLibraryId=source_library_id,
        motion=motion,
        motionZoom=motion_zoom,
        bakedAt=_utc_now_iso(),
    )

    job_id = f"bake-{str(uuid.uuid4())[:8]}"
    runner = StoryLibraryBakeRunner(
        job_id,
        source_library_id=source_library_id,
        target_library_id=target_library_id,
        target_name=target_name,
        style_filter=style_filter,
        style_id=style_id,
        style_label=style_label,
        motion=motion,
        motion_zoom=motion_zoom,
        clip_seconds=clip_seconds,
    )
    runner.start_async()
    logger.info(
        f"[StoryBake] Started bake job={job_id} (style-only) -> library={target_library_id}"
    )
    return jsonify({"jobId": job_id, "targetLibraryId": target_library_id}), 202


@story_video_bp.route("/api/story-video/library/bake/<job_id>", methods=["GET"])
def get_story_library_bake_job(job_id: str):
    from src.utils.story_library_bake import load_bake_progress

    progress = load_bake_progress(job_id)
    if not progress:
        return _error("Không tìm thấy tác vụ bake.", code="bake_job_not_found", status=404)
    return jsonify(progress)


@story_video_bp.route("/api/story-video/library/bake/<job_id>/cancel", methods=["POST"])
def cancel_story_library_bake_job(job_id: str):
    """Abandon a bake: the partial target library is deleted (see /pause to keep it)."""
    from src.utils.story_library_bake import load_bake_progress, request_bake_cancel

    progress = request_bake_cancel(job_id)
    if not progress:
        if not load_bake_progress(job_id):
            return _error("Không tìm thấy tác vụ bake.", code="bake_job_not_found", status=404)
    return jsonify({"jobId": job_id, "status": (progress or {}).get("status", "unknown")})


@story_video_bp.route("/api/story-video/library/bake/<job_id>/pause", methods=["POST"])
def pause_story_library_bake_job(job_id: str):
    """Park a bake, keeping everything baked so far.

    The target library stays registered and renderable with the clips it already
    has; /resume later bakes only the missing ones.
    """
    from src.utils.story_library_bake import load_bake_progress, request_bake_pause

    progress = request_bake_pause(job_id)
    if not progress:
        if not load_bake_progress(job_id):
            return _error("Không tìm thấy tác vụ bake.", code="bake_job_not_found", status=404)
        return _error("Tác vụ bake đã kết thúc.", code="bake_job_finished", status=409)
    return jsonify({
        "jobId": job_id,
        "status": progress.get("status", "unknown"),
        "completed": progress.get("completed", 0),
        "total": progress.get("total", 0),
    })


@story_video_bp.route("/api/story-video/library/bake/<job_id>/resume", methods=["POST"])
def resume_story_library_bake_job(job_id: str):
    from src.utils.story_library_bake import (
        RESUMABLE_BAKE_STATUSES,
        load_bake_progress,
        resume_bake_job,
    )

    progress = load_bake_progress(job_id)
    if not progress:
        return _error("Không tìm thấy tác vụ bake.", code="bake_job_not_found", status=404)
    if progress.get("status") not in RESUMABLE_BAKE_STATUSES:
        return _error(
            f"Tác vụ đang ở trạng thái '{progress.get('status')}', không thể tiếp tục.",
            code="bake_not_resumable",
            status=409,
        )

    runner = resume_bake_job(job_id)
    if runner is None:
        return _error(
            "Không thể tiếp tục bake (thiếu thư viện đích hoặc thông tin hiệu ứng).",
            code="bake_resume_failed",
            status=409,
        )
    logger.info(
        f"[StoryBake] Resumed job={job_id} -> {runner.progress.get('remaining')} clip(s) remaining"
    )
    return jsonify({
        "jobId": job_id,
        "status": "running",
        "completed": runner.progress.get("completed", 0),
        "total": runner.progress.get("total", 0),
        "remaining": runner.progress.get("remaining", 0),
    }), 202


# ---------------------------------------------------------------------------
# 7d. Normalize library clips to the canonical spec (fixes the clips every
#     render logs as "Excluded ... mismatched resolution/pix_fmt/color tags")
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/library/normalize/scan", methods=["GET"])
def scan_story_library_normalize():
    from src.utils.story_library_normalize import scan_libraries

    raw_library_id = request.args.get("libraryId")
    if str(request.args.get("scope", "")).strip() == "all" or raw_library_id is None:
        return jsonify(scan_libraries())

    library_id, err = _resolve_or_404(raw_library_id)
    if err:
        return err
    return jsonify(scan_libraries([library_id]))


@story_video_bp.route("/api/story-video/library/normalize", methods=["POST"])
def normalize_story_library():
    from src.utils.story_library_normalize import start_normalize_job

    data = request.get_json(silent=True) or {}
    include_all = bool(data.get("includeAll"))

    if str(data.get("scope", "")).strip() == "all":
        library_ids = [lib.get("id") for lib in load_libraries() if lib.get("id")]
    else:
        library_id, err = _resolve_or_404(data.get("libraryId"))
        if err:
            return err
        library_ids = [library_id]

    if not library_ids:
        return _error("Không có thư viện nào để chuẩn hóa.", code="no_libraries")

    for library_id in library_ids:
        if _has_active_library_session(library_id):
            return _error(
                "Thư viện đang được import/upload. Hãy đợi tác vụ hoàn tất rồi chuẩn hóa.",
                code="library_in_use",
                status=409,
            )

    runner = start_normalize_job(library_ids, include_all=include_all)
    logger.info(
        f"[StoryNormalize] Started job={runner.job_id} libraries={library_ids} "
        f"clips={runner.progress.get('total')}"
    )
    return jsonify({"jobId": runner.job_id, "total": runner.progress.get("total", 0)}), 202


@story_video_bp.route("/api/story-video/library/normalize/<job_id>", methods=["GET"])
def get_story_library_normalize_job(job_id: str):
    from src.utils.story_library_normalize import load_normalize_progress

    progress = load_normalize_progress(job_id)
    if not progress:
        return _error("Không tìm thấy tác vụ chuẩn hóa.", code="normalize_job_not_found", status=404)
    return jsonify(progress)


@story_video_bp.route("/api/story-video/library/normalize/<job_id>/cancel", methods=["POST"])
def cancel_story_library_normalize_job(job_id: str):
    from src.utils.story_library_normalize import (
        load_normalize_progress,
        request_normalize_cancel,
    )

    progress = request_normalize_cancel(job_id)
    if not progress and not load_normalize_progress(job_id):
        return _error("Không tìm thấy tác vụ chuẩn hóa.", code="normalize_job_not_found", status=404)
    return jsonify({"jobId": job_id, "status": (progress or {}).get("status", "unknown")})


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

    # Find a sample video for the demo. _find_sample_clip resolves the clip's own
    # library root (default lib now lives under default/, not the shared top-level dir).
    sample_path = _find_sample_clip(sample_clip_id, data.get("libraryId"))
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
def _resolve_decor_selection(image_ids, library_ids, count):
    """Validate a decor selection and deal it out across ``count`` videos.

    Returns ``(assignments, error_response)``. A fully-baked library already has
    the waveform and CTA burned into its clips, which would end up shrunk inside
    the TV screen instead of on top of the photo — so that combination is refused
    here rather than silently rendering something wrong. One fully-baked library
    anywhere in the selection is enough: its clips land in the same pool.

    Decor images are one-shot (see ``story_decor_images.claim_decor_images``), so
    a selection smaller than ``count`` is padded with ``""``: those videos render
    with no decor frame at all, which on this path — no layout chosen — is
    exactly the plain edit they would have had before decor existed.
    """
    from src.utils.story_decor_images import claim_decor_images
    from src.utils.story_library import any_fully_baked_library

    wanted = [str(item or "").strip() for item in (image_ids or []) if str(item or "").strip()]
    if not wanted:
        return [], None

    if any_fully_baked_library(library_ids):
        return None, _error(
            "Thu vien da bake san hieu ung/song am/CTA nen khong dung duoc anh decor. "
            "Hay chon thu vien chua bake.",
            code="decor_baked_library_conflict",
        )

    assignments, usable_total = claim_decor_images(wanted, count)
    if not usable_total:
        return None, _error(
            "Khong co anh decor nao dung duoc (chua tach nen hoac da bi xoa).",
            code="decor_unusable",
        )
    if len(assignments) < count:
        logger.info(
            f"[StoryVideo] Decor images ran out: {len(assignments)}/{count} video(s) get a TV "
            f"frame, the rest render without one."
        )
        assignments = assignments + ["" for _ in range(count - len(assignments))]
    return assignments, None


def _clean_ids(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item or "").strip()]


def _abandon_batch_dir(batch_dir: str, decor_assignments=()):
    """Undo a half-built batch: drop its directory and un-spend its decor images.

    Decor images are marked used the moment they are dealt, so a batch that
    never makes it to the queue has to hand them back — otherwise a failed
    create burns a photo on a video that will never exist.
    """
    from src.utils.story_decor_images import release_decor_images

    shutil.rmtree(batch_dir, ignore_errors=True)
    release_decor_images(decor_assignments)


def _decor_free_layouts(layout_ids: list[str], count: int) -> list[str]:
    """``count`` layouts drawn from the same selection that need no decor image.

    Where a video drew a TV-frame layout but the decor library ran dry, this is
    what it falls back to. ``""`` = no layout at all (clip fills the frame): the
    user picked nothing but TV-frame layouts, so a plain frame is all that is
    left — still better than refusing the whole batch over a spent photo.
    """
    from src.utils.edit_styles import store

    free = [lid for lid in layout_ids if not store.type_flag(lid, "requiresDecor")]
    dealt = store.build_layout_rotation(free, count) if free else []
    return dealt + ["" for _ in range(count - len(dealt))]


def _resolve_edit_selection(shared_config: dict, library_ids, count: int):
    """Deal layouts across ``count`` videos and decide which of them get a decor image.

    Returns ``((layout_per_item, decor_per_item, modifiers_per_item), error_response)``.

    - No ``layoutIds``: the old behaviour. ``decorImageIds`` alone puts every video
      in the TV frame (``_resolve_decor_selection``), so old payloads and retries of
      old batches render exactly as before.
    - With ``layoutIds``: one enabled layout per video, dealt from a shuffled deck.
      Decor images are dealt only to the videos that drew a layout needing one.
    - ``modifierIds`` apply to every video, one record per modifier type; several
      records of one type are dealt across the videos (``deal_modifier_rotation``).

    Each decor image is spent on one video and never dealt again, so a batch can
    ask for more TV frames than the library still holds. The videos left over
    are moved onto a decor-free layout rather than rendered with an empty TV
    frame or refused outright.
    """
    from src.utils.edit_styles import store
    from src.utils.story_decor_images import claim_decor_images
    from src.utils.story_library import any_fully_baked_library

    layout_ids = _clean_ids(shared_config.get("layoutIds"))
    decor_ids = _clean_ids(shared_config.get("decorImageIds"))
    modifier_ids = _clean_ids(shared_config.get("modifierIds"))

    modifiers = store.resolve_modifiers(modifier_ids)
    if modifier_ids and not modifiers:
        return None, _error("Khong co hieu ung bo tro nao dung duoc (da tat hoac da xoa).",
                            code="modifier_unusable")
    modifier_out = store.deal_modifier_rotation(modifier_ids, count)

    if not layout_ids:
        decor_assignments, decor_error = _resolve_decor_selection(decor_ids, library_ids, count)
        if decor_error is not None:
            return None, decor_error
        return (["" for _ in range(count)], decor_assignments or ["" for _ in range(count)], modifier_out), None

    layouts = store.build_layout_rotation(layout_ids, count)
    if not layouts:
        return None, _error("Khong co bo cuc nao dung duoc (da tat hoac da xoa).", code="layout_unusable")

    if any_fully_baked_library(library_ids) and any(store.type_flag(lid, "shrinksFrame") for lid in set(layouts)):
        return None, _error(
            "Thu vien da bake san song am/CTA: cac bo cuc thu nho khung hinh se thu nho ca song am/CTA. "
            "Bo chon cac bo cuc do hoac chon thu vien chua bake.",
            code="layout_baked_library_conflict",
        )

    decor_slots = [i for i, lid in enumerate(layouts) if store.type_flag(lid, "requiresDecor")]
    decor_per_item = ["" for _ in range(count)]
    if decor_slots:
        if not decor_ids:
            return None, _error("Bo cuc khung TV can it nhat 1 anh decor. Hay chon anh decor.",
                                code="layout_needs_decor")
        dealt, usable_total = claim_decor_images(decor_ids, len(decor_slots))
        # Nothing usable at all is a broken selection (deleted, or never keyed)
        # and worth stopping for; merely running out of *unused* images is not.
        if not usable_total:
            return None, _error("Khong co anh decor nao dung duoc (chua tach nen hoac da bi xoa).",
                                code="decor_unusable")
        for slot, image_id in zip(decor_slots, dealt):
            decor_per_item[slot] = image_id

        starved = decor_slots[len(dealt):]
        if starved:
            for slot, layout_id in zip(starved, _decor_free_layouts(layout_ids, len(starved))):
                layouts[slot] = layout_id
            logger.info(
                f"[StoryVideo] Decor images ran out: {len(dealt)}/{len(decor_slots)} TV-frame "
                f"video(s) got an image; {len(starved)} moved to a decor-free layout."
            )
    return (layouts, decor_per_item, modifier_out), None


def _resolve_overlay_rotation(overlay_ids, count, build_rotation, empty_message, empty_code):
    """Validate a waveform/CTA selection and deal it out across ``count`` videos.

    Returns ``(assignments, error_response)``. An empty selection is not an
    error: it means "keep the current behaviour" — the default waveform and the
    CTA enabled on the settings page. A non-empty selection where nothing
    resolves is a hard error rather than a silent fallback.
    """
    wanted = [str(item or "").strip() for item in (overlay_ids or []) if str(item or "").strip()]
    if not wanted:
        return [], None

    assignments = build_rotation(wanted, count)
    if not assignments:
        return None, _error(empty_message, code=empty_code)
    return assignments, None


def _subtitle_config_from_payload(payload: dict) -> dict:
    """Map camelCase subtitle payload keys to pipeline config keys (without subtitle_path)."""
    from src.utils.story_subtitles import get_subtitle_preset

    def _positive_int(value, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    preset_id = str(payload.get("subtitlePreset", "")).strip() or "clean"
    if not get_subtitle_preset(preset_id):
        preset_id = "clean"

    style_overrides: dict = {}
    font_scale_raw = payload.get("subtitleFontScale")
    if font_scale_raw is not None:
        try:
            font_scale = float(font_scale_raw)
        except (TypeError, ValueError):
            font_scale = 0.0
        if font_scale > 0:
            style_overrides["fontScale"] = max(0.3, min(4.0, font_scale))

    # Colours are passed through as the raw "#RRGGBB" the picker produced;
    # build_ass validates and converts them, and drops anything malformed. Only
    # keys the user actually set are forwarded, so an untouched form still renders
    # exactly what the chosen preset always rendered.
    for payload_key, override_key in (
        ("subtitleTextColor", "textColor"),
        ("subtitleOutlineColor", "outlineColor"),
        ("subtitleBackColor", "backColor"),
    ):
        value = str(payload.get(payload_key, "") or "").strip()
        if value:
            style_overrides[override_key] = value

    outline_width_raw = payload.get("subtitleOutlineWidth")
    if outline_width_raw is not None and str(outline_width_raw).strip() != "":
        try:
            style_overrides["outlineWidth"] = max(0, min(20, int(outline_width_raw)))
        except (TypeError, ValueError):
            pass

    if payload.get("subtitleBackgroundEnabled") is not None:
        style_overrides["backgroundEnabled"] = bool(payload.get("subtitleBackgroundEnabled"))

    back_opacity_raw = payload.get("subtitleBackOpacity")
    if back_opacity_raw is not None and str(back_opacity_raw).strip() != "":
        try:
            style_overrides["backOpacity"] = max(0.0, min(1.0, float(back_opacity_raw)))
        except (TypeError, ValueError):
            pass

    return {
        "subtitle_font": str(payload.get("subtitleFont", "")).strip(),
        "subtitle_preset": preset_id,
        "subtitle_max_chars_per_line": _positive_int(
            payload.get("subtitleMaxCharsPerLine"), Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE
        ),
        "subtitle_max_lines": _positive_int(
            payload.get("subtitleMaxLines"), Config.STORY_SUBTITLE_MAX_LINES
        ),
        "subtitle_style_overrides": style_overrides,
    }


def _subtitle_config_for_style(style: dict, shared_config: dict) -> dict:
    """Pipeline subtitle config for one saved subtitle style.

    Font and size fall back to the batch form when the style has none (every
    built-in preset), so a Thai batch keeps its Thai font. Colours never fall back:
    a style is a finished look, and a colour set in the form must not repaint it.
    Line limits always come from the form — they follow the language, not the look.
    """
    from src.utils.subtitle_styles import STYLE_FIELD_KEYS

    payload = {key: style[key] for key in STYLE_FIELD_KEYS if style.get(key) is not None}
    for key in ("subtitleFont", "subtitleFontScale", "subtitleMaxCharsPerLine", "subtitleMaxLines"):
        if not payload.get(key) and shared_config.get(key) is not None:
            payload[key] = shared_config[key]
    return _subtitle_config_from_payload(payload)


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
    from src.utils.subtitle_styles import hidden_preset_ids

    # A preset the user deleted from the subtitle-style list is flagged rather than
    # dropped, so a form still set to it can keep showing its name.
    hidden = hidden_preset_ids()
    presets = [
        {
            "id": item["id"],
            "name": item["name"],
            "description": item["description"],
            "hidden": item["id"] in hidden,
        }
        for item in SUBTITLE_PRESETS
    ]
    return jsonify({"presets": presets})


def _subtitle_styles_response():
    from src.utils.subtitle_styles import hidden_preset_ids, list_subtitle_styles

    return {"styles": list_subtitle_styles(), "hiddenBuiltinCount": len(hidden_preset_ids())}


@story_video_bp.route("/api/story-video/subtitle-styles", methods=["GET"])
def get_subtitle_styles():
    return jsonify(_subtitle_styles_response())


@story_video_bp.route("/api/story-video/subtitle-styles", methods=["POST"])
def create_subtitle_style_route():
    """Save the subtitle form as a named style. Body: {name, subtitleFont, subtitlePreset, ...}."""
    from src.utils.subtitle_styles import create_subtitle_style

    data = request.get_json(silent=True) or {}
    try:
        style = create_subtitle_style(data)
    except ValueError as exc:
        return _error(str(exc), code="invalid_subtitle_style")

    logger.info(f"[StoryVideo] Subtitle style saved: {style['id']} ({style['name']})")
    return jsonify({"style": style}), 201


@story_video_bp.route("/api/story-video/subtitle-styles/restore-builtin", methods=["POST"])
def restore_subtitle_styles_route():
    from src.utils.subtitle_styles import restore_builtin_styles

    restored = restore_builtin_styles()
    logger.info(f"[StoryVideo] Restored {restored} built-in subtitle style(s)")
    return jsonify(_subtitle_styles_response())


@story_video_bp.route("/api/story-video/subtitle-styles/<style_id>", methods=["DELETE"])
def delete_subtitle_style_route(style_id: str):
    """Delete a custom style; a built-in (``preset:<id>``) is hidden instead."""
    from src.utils.subtitle_styles import delete_subtitle_style

    if not delete_subtitle_style(style_id):
        return _error("Cau hinh phu de khong ton tai.", code="subtitle_style_not_found", status=404)

    logger.info(f"[StoryVideo] Subtitle style deleted: {style_id}")
    return jsonify({"deleted": True, "styleId": style_id})


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
        chapters_file = request.files.get("chapters")
    else:
        data = request.get_json(silent=True) or {}
        audio_file = None
        subtitle_file = None
        chapters_file = None

    input_type = str(data.get("inputType", "")).strip()
    if input_type not in ("audio_file", "script_url"):
        return _error("inputType phải là 'audio_file' hoặc 'script_url'.", code="invalid_input_type")

    output_name = str(data.get("outputName", "")).strip()
    if not output_name:
        return _error("outputName không được để trống.", code="missing_output_name")

    clip_usage_mode, clip_usage_error = _clip_usage_mode_or_503(data.get("clipUsageMode"))
    if clip_usage_error is not None:
        return clip_usage_error

    if subtitle_file and subtitle_file.filename:
        sub_ext = subtitle_file.filename.rsplit(".", 1)[-1].lower() if "." in subtitle_file.filename else ""
        if sub_ext != "srt":
            return _error("File subtitle phải có định dạng .srt.", code="invalid_subtitle_format")

    # Validated before anything is written to disk, so a bad selection never
    # leaves an orphan story dir behind.
    library_ids, library_error = _resolve_library_ids_or_404(
        data.get("libraryIds"), data.get("libraryId")
    )
    if library_error is not None:
        return library_error

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

    chapters_path = ""
    if chapters_file and chapters_file.filename:
        chapters_path = os.path.join(story_dir, "chapters.txt")
        chapters_file.save(chapters_path)

    # A single render takes one layout and one decor image; rotation only applies
    # to batches. Without a layout, a decor image means the TV frame, as before.
    layout_id = str(data.get("layoutId") or "").strip()
    edit_selection, edit_error = _resolve_edit_selection(
        {
            "layoutIds": [layout_id] if layout_id else [],
            "decorImageIds": [str(data.get("decorImageId") or "").strip()],
            "modifierIds": data.get("modifierIds") or [],
        },
        library_ids,
        1,
    )
    if edit_error is not None:
        return edit_error
    layouts, decors, modifier_assignments = edit_selection
    decor_image_id = decors[0] if decors else ""
    modifier_ids = modifier_assignments[0] if modifier_assignments else []

    config_dict = {
        "input_type": input_type,
        "input_value": input_value,
        "output_name": output_name,
        "clip_tags": data.get("clipTags", []),
        "library_ids": library_ids,
        "crt_settings": data.get("crtSettings", {}),
        "tv_effect_style_id": str(data.get("tvEffectStyleId", "")).strip(),
        "skip_tv_effect": bool(data.get("skipTvEffect", False)),
        "clip_usage_mode": clip_usage_mode,
        "waveform_overlay_id": str(data.get("waveformOverlayId", "")).strip(),
        "decor_image_id": decor_image_id,
        "layout_id": layouts[0] if layouts else "",
        "modifier_ids": modifier_ids,
        "chapters_path": chapters_path,
        "voice_id": str(data.get("voiceId", "")).strip(),
        "subtitle_path": subtitle_path,
        **_subtitle_config_from_payload(data),
    }

    runner = StoryVideoPipelineRunner(story_id, config_dict)

    # A single render has no batch file on disk, so it holds its decor image here
    # to keep purge-used from deleting the PNG before the render reads it.
    from src.utils.story_decor_images import hold_decor_image, release_decor_hold

    def run_holding_decor():
        try:
            runner.run()
        finally:
            release_decor_hold(decor_image_id)

    hold_decor_image(decor_image_id)
    threading.Thread(target=run_holding_decor, daemon=True).start()

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

    from src.utils.file_manager import storage_absolute_path, storage_relative_path

    abs_path = storage_absolute_path(video_path)
    if not os.path.isfile(abs_path):
        return _error("Video chua san sang.", code="video_not_ready", status=404)

    rel = storage_relative_path(abs_path)
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
# 13b. POST /api/story-video/batch/local-folder - scan a folder on this machine
# ---------------------------------------------------------------------------
LOCAL_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
# "<audio stem>.chapters.txt": chapter titles/labels and quote marks for edit styles.
CHAPTERS_SUFFIX = ".chapters.txt"


@story_video_bp.route("/api/story-video/batch/local-folder", methods=["POST"])
def scan_local_audio_folder():
    """List audio (+ paired .srt) inside a folder on the machine running this server.

    Backend and browser share the same filesystem here, so a batch can reference the
    originals by absolute path instead of re-uploading gigabytes through the form.
    """
    data = request.get_json(silent=True) or {}
    raw_path = str(data.get("path", "") or "").strip().strip('"').strip("'")
    if not raw_path:
        return _error("Nhap duong dan thu muc.", code="empty_path")

    folder = os.path.abspath(os.path.expandvars(os.path.expanduser(raw_path)))
    if not os.path.isdir(folder):
        return _error(f"Thu muc khong ton tai: {folder}", code="folder_not_found", status=404)

    recursive = bool(data.get("recursive", True))

    # Collect audio and subtitles per directory so an .srt only pairs with an audio
    # file of the same stem sitting next to it (same rule the browser picker uses).
    items: list[dict] = []
    total_bytes = 0
    paired_count = 0
    orphan_subtitles = 0

    walker = os.walk(folder) if recursive else [(folder, [], os.listdir(folder))]
    for dir_path, _dirs, filenames in walker:
        subtitles: dict[str, str] = {}
        chapter_files: dict[str, str] = {}
        audio_names: list[str] = []
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if name.lower().endswith(CHAPTERS_SUFFIX):
                chapter_files[name[: -len(CHAPTERS_SUFFIX)].lower()] = os.path.join(dir_path, name)
            elif ext == ".srt":
                subtitles[os.path.splitext(name)[0].lower()] = os.path.join(dir_path, name)
            elif ext in LOCAL_AUDIO_EXTENSIONS:
                audio_names.append(name)

        used_stems: set[str] = set()
        for name in sorted(audio_names, key=str.lower):
            audio_path = os.path.join(dir_path, name)
            stem = os.path.splitext(name)[0]
            subtitle_path = subtitles.get(stem.lower(), "")
            if subtitle_path:
                paired_count += 1
                used_stems.add(stem.lower())
            try:
                size_bytes = os.path.getsize(audio_path)
            except OSError:
                size_bytes = 0
            total_bytes += size_bytes
            chapters_path = chapter_files.get(stem.lower(), "")
            items.append({
                "audioPath": audio_path,
                "audioName": name,
                "outputName": stem,
                "subtitlePath": subtitle_path,
                "subtitleName": os.path.basename(subtitle_path) if subtitle_path else "",
                "chaptersPath": chapters_path,
                "sizeMb": round(size_bytes / (1024 * 1024), 2),
            })
        orphan_subtitles += len(set(subtitles) - used_stems)

    if not items:
        return _error(f"Khong tim thay file audio nao trong: {folder}", code="no_audio_found", status=404)

    return jsonify({
        "path": folder,
        "items": items,
        "totalSizeMb": round(total_bytes / (1024 * 1024), 1),
        "pairedCount": paired_count,
        "orphanSubtitles": orphan_subtitles,
    })


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

    clip_usage_mode, clip_usage_error = _clip_usage_mode_or_503(shared_config.get("clipUsageMode"))
    if clip_usage_error is not None:
        return clip_usage_error

    # Resolved once for the whole batch, before the batch dir exists, so a bad
    # selection fails without leaving files behind.
    library_ids, library_error = _resolve_library_ids_or_404(
        shared_config.get("libraryIds"), shared_config.get("libraryId")
    )
    if library_error is not None:
        return library_error

    batch_id = f"sb-{str(uuid.uuid4())[:8]}"
    batch_dir = os.path.join(Config.STORY_VIDEO_DIR, "batches", batch_id)
    os.makedirs(batch_dir, exist_ok=True)
    # Filled in by _resolve_edit_selection below; empty until then, so the abort
    # paths above that point release nothing.
    decor_assignments: list[str] = []

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
                _abandon_batch_dir(batch_dir, decor_assignments)
                return _error("File subtitle phải có định dạng .srt.", code="invalid_subtitle_format")
            safe_name = secure_filename(sf.filename)
            if not safe_name.lower().endswith(".srt"):
                safe_name = "subtitle.srt"
            saved_subtitle_path = os.path.join(batch_dir, f"subtitle_{i}_{safe_name}")
            sf.save(saved_subtitle_path)
            subtitle_file_map[i] = saved_subtitle_path
            subtitle_name_map[safe_name] = saved_subtitle_path
            subtitle_name_map[str(sf.filename)] = saved_subtitle_path

    # Chapter files (<audio stem>.chapters.txt), mapped exactly like subtitles.
    chapter_files = request.files.getlist("chapter_files")
    chapter_file_map: dict[int, str] = {}
    chapter_name_map: dict[str, str] = {}
    for i, cf in enumerate(chapter_files):
        if cf.filename:
            saved_chapter_path = os.path.join(batch_dir, f"chapters_{i}.txt")
            cf.save(saved_chapter_path)
            chapter_file_map[i] = saved_chapter_path
            chapter_name_map[secure_filename(cf.filename)] = saved_chapter_path
            chapter_name_map[str(cf.filename)] = saved_chapter_path

    shared_subtitle_config = _subtitle_config_from_payload(shared_config)

    # Resolve the (optional) shared intro once for the whole batch. Empty id means
    # "no intro"; a non-empty id that doesn't resolve is a hard error.
    intro_id = str(shared_config.get("introId", "") or "").strip()
    intro_video_path = ""
    if intro_id:
        intro_video_path = resolve_intro_path(intro_id)
        if not intro_video_path:
            _abandon_batch_dir(batch_dir, decor_assignments)
            return _error("Intro không tồn tại.", code="intro_not_found", status=404)

    # Layouts ("bo cuc") rotate across the batch: one shuffled deck dealt out so
    # every run of N videos uses all N layouts in a different order. Decor images
    # go only to the videos that drew a TV layout (or to every video when the
    # payload has decor ids and no layouts: the old "use decor" behaviour).
    edit_selection, edit_error = _resolve_edit_selection(shared_config, library_ids, len(items))
    if edit_error is not None:
        _abandon_batch_dir(batch_dir, decor_assignments)
        return edit_error
    layout_assignments, decor_assignments, modifier_assignments = edit_selection

    # Song am va CTA cung xoay vong theo cach do: chon nhieu cau hinh thi moi N
    # video lien tiep dung du N cau hinh, thu tu ngau nhien. Khong chon = giu
    # nguyen hanh vi cu (waveform mac dinh + CTA dang bat o trang cau hinh).
    from src.utils.story_cta_overlay import build_cta_rotation
    from src.utils.waveform_overlays import build_waveform_rotation

    waveform_assignments, waveform_error = _resolve_overlay_rotation(
        shared_config.get("waveformOverlayIds"),
        len(items),
        build_waveform_rotation,
        "Khong co song am nao dung duoc (chua xu ly xong hoac da bi xoa).",
        "waveform_unusable",
    )
    if waveform_error is not None:
        _abandon_batch_dir(batch_dir, decor_assignments)
        return waveform_error

    cta_assignments, cta_error = _resolve_overlay_rotation(
        shared_config.get("ctaOverlayIds"),
        len(items),
        build_cta_rotation,
        "Khong co CTA overlay nao dung duoc (chua xu ly xong hoac da bi xoa).",
        "cta_unusable",
    )
    if cta_error is not None:
        _abandon_batch_dir(batch_dir, decor_assignments)
        return cta_error

    # Cau hinh phu de cung xoay vong nhu vay. Khong chon = ca batch dung chung
    # cau hinh o form phu de (hanh vi cu).
    from src.utils.subtitle_styles import build_subtitle_style_rotation, get_subtitle_style

    style_assignments, style_error = _resolve_overlay_rotation(
        shared_config.get("subtitleStyleIds"),
        len(items),
        build_subtitle_style_rotation,
        "Khong co cau hinh phu de nao dung duoc (da bi xoa).",
        "subtitle_style_unusable",
    )
    if style_error is not None:
        _abandon_batch_dir(batch_dir, decor_assignments)
        return style_error
    # Moi cau hinh chi giai mot lan, du no roi vao bao nhieu video.
    style_subtitle_configs = {
        style_id: _subtitle_config_for_style(get_subtitle_style(style_id) or {}, shared_config)
        for style_id in dict.fromkeys(style_assignments)
    }

    # Build story configs
    story_configs = []
    for idx, item in enumerate(items):
        input_type = str(item.get("inputType", "")).strip()
        input_value = str(item.get("inputValue", "")).strip()
        output_name = str(item.get("outputName", f"story_{idx + 1}")).strip()

        # Map local upload indexes or Drive staging tokens to durable batch files.
        # An absolute path is taken as-is: the file already lives on this machine, so
        # the pipeline reads the original instead of a re-uploaded copy.
        if input_type == "audio_file":
            if input_value.isdigit():
                file_idx = int(input_value)
                if file_idx in audio_file_map:
                    input_value = audio_file_map[file_idx]
            elif input_value in audio_name_map:
                input_value = audio_name_map[input_value]
            elif secure_filename(input_value) in audio_name_map:
                input_value = audio_name_map[secure_filename(input_value)]
            elif os.path.isabs(input_value):
                if not os.path.isfile(input_value):
                    _abandon_batch_dir(batch_dir, decor_assignments)
                    return _error(
                        f"Khong tim thay file audio local: {input_value}",
                        code="local_audio_not_found",
                        status=404,
                    )
        elif input_type == "drive_audio":
            try:
                input_value, _original_name = copy_staged_audio_to_batch(input_value, batch_dir, idx)
                input_type = "audio_file"
            except DriveAudioImportError as exc:
                _abandon_batch_dir(batch_dir, decor_assignments)
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
            elif os.path.isabs(subtitle_ref):
                if not subtitle_ref.lower().endswith(".srt"):
                    _abandon_batch_dir(batch_dir, decor_assignments)
                    return _error("File subtitle phải có định dạng .srt.", code="invalid_subtitle_format")
                if not os.path.isfile(subtitle_ref):
                    _abandon_batch_dir(batch_dir, decor_assignments)
                    return _error(
                        f"Khong tim thay file subtitle local: {subtitle_ref}",
                        code="local_subtitle_not_found",
                        status=404,
                    )
                subtitle_path = subtitle_ref

        chapters_ref = str(item.get("chaptersFile", "")).strip()
        chapters_path = ""
        if chapters_ref:
            if chapters_ref.isdigit() and int(chapters_ref) in chapter_file_map:
                chapters_path = chapter_file_map[int(chapters_ref)]
            elif chapters_ref in chapter_name_map:
                chapters_path = chapter_name_map[chapters_ref]
            elif os.path.isabs(chapters_ref) and os.path.isfile(chapters_ref):
                chapters_path = chapters_ref

        subtitle_style_id = style_assignments[idx] if style_assignments else ""
        story_configs.append({
            "story_id": f"sv-{str(uuid.uuid4())[:8]}",
            "input_type": input_type,
            "input_value": input_value,
            "output_name": output_name,
            "clip_tags": shared_config.get("clipTags", []),
            "library_ids": library_ids,
            "crt_settings": shared_config.get("crtSettings", {}),
            "tv_effect_style_id": str(shared_config.get("tvEffectStyleId", "")).strip(),
            "skip_tv_effect": bool(shared_config.get("skipTvEffect", False)),
            "clip_usage_mode": clip_usage_mode,
            # `waveformOverlayId` (so it) van duoc doc cho client cu.
            "waveform_overlay_id": (
                waveform_assignments[idx]
                if waveform_assignments
                else str(shared_config.get("waveformOverlayId", "")).strip()
            ),
            "cta_overlay_id": cta_assignments[idx] if cta_assignments else "",
            "decor_image_id": decor_assignments[idx] if decor_assignments else "",
            "layout_id": layout_assignments[idx] if layout_assignments else "",
            "modifier_ids": list(modifier_assignments[idx]) if modifier_assignments else [],
            "chapters_path": chapters_path,
            "voice_id": str(shared_config.get("voiceId", "")).strip(),
            "subtitle_path": subtitle_path,
            "intro_video_path": intro_video_path,
            "subtitle_style_id": subtitle_style_id,
            **(
                style_subtitle_configs[subtitle_style_id]
                if subtitle_style_id
                else shared_subtitle_config
            ),
        })

    # Optimize mode (per-batch): suspend configured competing apps + boost ffmpeg for
    # this render. Off by default so batches don't freeze other apps unless requested.
    optimize_mode = bool(shared_config.get("optimizeMode", False))

    # Batch vao hang doi: moi luc chi render 1 batch, batch nay xong moi toi batch sau.
    # Video ra thu muc output rieng OUTPUT_DIR/story-video/<batch_id>/.
    runner = StoryVideoBatchRunner(batch_id, story_configs, optimize_mode=optimize_mode)
    queue_position = runner.enqueue()

    logger.info(
        f"[StoryVideo] Queued batch: batch_id={batch_id}, items={len(story_configs)}, "
        f"queue_position={queue_position}, optimize_mode={optimize_mode}, "
        f"layouts={len({lid for lid in layout_assignments if lid})}, "
        f"modifiers={len({mid for ids in modifier_assignments for mid in ids})}, "
        f"decor_images={len({d for d in decor_assignments if d})}, "
        f"waveforms={len(set(waveform_assignments)) if waveform_assignments else 0}, "
        f"cta_overlays={len(set(cta_assignments)) if cta_assignments else 0}, "
        f"subtitle_styles={len(style_subtitle_configs)}"
    )
    return jsonify({"batchId": batch_id, "queuePosition": queue_position}), 202


def _public_batch_status(status: str) -> str:
    """Trang thai batch theo tu vung cua UI: running -> processing, partial -> completed."""
    if status == "running":
        return "processing"
    if status == "partial":
        return "completed"
    return status


# ---------------------------------------------------------------------------
# 14b. GET /api/story-video/batch/queue - hang doi batch (dang chay + dang cho + vua xong)
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/batch/queue", methods=["GET"])
def get_batch_queue():
    from src.utils.story_video_batch import batch_output_dir, load_batch_progress, queue_snapshot

    snapshot = queue_snapshot()
    queued_ids = snapshot["queuedIds"]
    batches = []
    for batch_id in snapshot["historyIds"]:
        progress = load_batch_progress(batch_id)
        if not progress:
            continue
        batches.append({
            "batchId": batch_id,
            "status": _public_batch_status(progress.get("status", "pending")),
            "queuePosition": queued_ids.index(batch_id) + 1 if batch_id in queued_ids else 0,
            "totalItems": progress.get("total", 0),
            "completedItems": progress.get("completed", 0),
            "failedItems": progress.get("failed", 0),
            "cancelledItems": progress.get("cancelled", 0),
            "outputDir": batch_output_dir(progress),
            "queuedAt": progress.get("queuedAt", ""),
        })
    return jsonify({"activeBatchId": snapshot["activeBatchId"], "batches": batches})


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
            # Which decor image / waveform / CTA / subtitle style this item drew from the batch rotation.
            "decorImageName": story.get("decor_image_name", ""),
            "waveformName": story.get("waveform_overlay_name", ""),
            "ctaOverlayName": story.get("cta_overlay_name", ""),
            "subtitleStyleName": story.get("subtitle_style_name", ""),
            "layoutName": story.get("layout_name", ""),
            "modifierNames": story.get("modifier_names") or [],
        })

    from src.utils.story_video_batch import batch_output_dir, queue_position

    return jsonify({
        "batchId": progress.get("batchId", batch_id),
        "status": _public_batch_status(progress.get("status", "pending")),
        "totalItems": progress.get("total", len(items)),
        "completedItems": progress.get("completed", 0),
        "failedItems": progress.get("failed", 0),
        "cancelledItems": progress.get("cancelled", 0),
        "currentIndex": progress.get("current", 0),
        # Vi tri trong hang doi (1 = batch ke tiep); 0 = dang chay hoac da xong.
        "queuePosition": queue_position(batch_id),
        "outputDir": batch_output_dir(progress),
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
    from src.utils.story_video_batch import (
        StoryVideoBatchRunner,
        is_batch_queued_or_active,
        load_batch_progress,
    )

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
            # Batches rendered before multi-select only carry the singular key.
            "library_ids": s.get("library_ids") or [s.get("library_id", "")],
            "skip_tv_effect": bool(s.get("skip_tv_effect", False)),
            # Item cu khong co key -> "reuse", dung nhu luc no chay lan dau.
            "clip_usage_mode": s.get("clip_usage_mode", "reuse"),
            "decor_image_id": s.get("decor_image_id", ""),
            # Retry giu dung bo cuc / hieu ung bo tro ma item da boc o lan chay truoc.
            "layout_id": s.get("layout_id", ""),
            "modifier_ids": s.get("modifier_ids") or [],
            "chapters_path": s.get("chapters_path", ""),
            # Retry giu dung song am / CTA ma item da boc o lan chay truoc.
            "waveform_overlay_id": s.get("waveform_overlay_id", ""),
            "cta_overlay_id": s.get("cta_overlay_id", ""),
            "voice_id": s.get("voice_id", ""),
            "intro_video_path": s.get("intro_video_path", ""),
            "subtitle_path": s.get("subtitle_path", ""),
            "subtitle_font": s.get("subtitle_font", ""),
            "subtitle_preset": s.get("subtitle_preset", "clean"),
            "subtitle_max_chars_per_line": s.get(
                "subtitle_max_chars_per_line", Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE
            ),
            "subtitle_max_lines": s.get(
                "subtitle_max_lines", Config.STORY_SUBTITLE_MAX_LINES
            ),
            # Without this a retry silently re-renders at the default size and
            # colours, so the replacement video does not match the rest of the batch.
            "subtitle_style_overrides": s.get("subtitle_style_overrides") or {},
            # Only for display: the resolved fields above already fix the look, even
            # if the style has since been deleted.
            "subtitle_style_id": s.get("subtitle_style_id", ""),
        })

    for retry_config in retry_configs:
        _mode, clip_usage_error = _clip_usage_mode_or_503(retry_config.get("clip_usage_mode"))
        if clip_usage_error is not None:
            return clip_usage_error

    retry_batch_id = f"{batch_id}-retry"
    if is_batch_queued_or_active(retry_batch_id):
        # Tao lai runner se ghi de progress cua batch retry dang cho/dang chay.
        return _error("Batch retry dang cho hoac dang chay.", code="retry_in_progress", status=409)
    runner = StoryVideoBatchRunner(
        retry_batch_id,
        retry_configs,
        optimize_mode=bool(progress.get("optimizeMode", False)),
        # Video retry nam chung thu muc output voi batch goc.
        output_subdir=progress.get("outputSubdir") or batch_id,
    )
    queue_position = runner.enqueue()

    logger.info(
        f"[StoryVideo] Queued retry of {len(retry_configs)} failed items from batch {batch_id} "
        f"(queue_position={queue_position})"
    )
    return jsonify({
        "batchId": retry_batch_id,
        "retryCount": len(retry_configs),
        "queuePosition": queue_position,
    }), 202


# ---------------------------------------------------------------------------
# 16. GET /api/story-video/waveform-overlays
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/waveform-overlays", methods=["GET"])
def get_waveform_overlays():
    from src.utils.overlay_placement import backfill_processed_sizes
    from src.utils.waveform_overlays import (
        load_waveform_index,
        processed_abs_path,
        save_waveform_index,
    )

    data = load_waveform_index()
    if backfill_processed_sizes(data.get("overlays", []), processed_abs_path):
        save_waveform_index(data)
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
        # Free placement. Sent as a pair; either both or neither, since half a
        # coordinate would silently fall back to the corner. The utils clamp
        # them to the frame, so no range check belongs here.
        for key in ("x", "y"):
            if key in data:
                updates[key] = None if data.get(key) is None else int(data.get(key))
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
    from src.utils.overlay_placement import backfill_processed_sizes
    from src.utils.story_cta_overlay import (
        ensure_default_cta_overlay,
        load_cta_index,
        processed_abs_path,
        save_cta_index,
    )

    ensure_default_cta_overlay()  # seed-on-first-load so the UI always shows the default
    data = load_cta_index()
    if backfill_processed_sizes(data.get("overlays", []), processed_abs_path):
        save_cta_index(data)
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
        # Free placement; null clears it back to the corner. The utils clamp the
        # coordinates to the frame, so no range check belongs here.
        for key in ("x", "y"):
            if key in data:
                updates[key] = None if data.get(key) is None else int(data.get(key))
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
# 19a. Edit styles ("kieu dung"): layouts rotated per video + batch-wide modifiers.
#      Each record is one variant of a type with its own params (src/utils/edit_styles).
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/edit-styles", methods=["GET"])
def get_edit_styles():
    from src.utils.edit_styles.spec import public_types
    from src.utils.edit_styles.store import list_edit_styles

    # Pictures are served by /media relative to STORAGE_DIR: `/media/<imageBase>/<ref>`.
    image_base = os.path.relpath(Config.STORY_EDIT_STYLE_DIR, Config.STORAGE_DIR).replace("\\", "/")
    return jsonify({"types": public_types(), "styles": list_edit_styles(), "imageBase": image_base})


@story_video_bp.route("/api/story-video/edit-styles/<style_id>/images/<key>", methods=["POST"])
def upload_edit_style_image(style_id: str, key: str):
    from src.utils.edit_styles.store import add_image

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return _error("Chua chon file anh.", code="missing_file")
    try:
        record = add_image(style_id, key, upload.stream, upload.filename)
    except ValueError as exc:
        return _error(str(exc), code="invalid_edit_style_image")
    if not record:
        return _error("Kieu dung khong ton tai.", code="edit_style_not_found", status=404)
    return jsonify({"style": record})


@story_video_bp.route("/api/story-video/edit-styles/<style_id>/images/<key>", methods=["DELETE"])
def delete_edit_style_image(style_id: str, key: str):
    from src.utils.edit_styles.store import remove_image

    try:
        record = remove_image(style_id, key, request.args.get("ref") or None)
    except ValueError as exc:
        return _error(str(exc), code="invalid_edit_style_image")
    if not record:
        return _error("Kieu dung khong ton tai.", code="edit_style_not_found", status=404)
    return jsonify({"style": record})


@story_video_bp.route("/api/story-video/edit-styles", methods=["POST"])
def create_edit_style_route():
    from src.utils.edit_styles.store import create_edit_style

    data = request.get_json(silent=True) or {}
    try:
        record = create_edit_style(
            str(data.get("type") or "").strip(),
            name=data.get("name"),
            params=data.get("params") if isinstance(data.get("params"), dict) else None,
            copy_from=str(data.get("copyFrom") or "").strip() or None,
        )
    except ValueError as exc:
        return _error(str(exc), code="invalid_edit_style")
    return jsonify({"style": record}), 201


@story_video_bp.route("/api/story-video/edit-styles/<style_id>", methods=["PATCH"])
def patch_edit_style(style_id: str):
    from src.utils.edit_styles.store import update_edit_style

    data = request.get_json(silent=True) or {}
    updates = {key: data[key] for key in ("name", "enabled", "params") if key in data}
    record = update_edit_style(style_id, updates)
    if not record:
        return _error("Kieu dung khong ton tai.", code="edit_style_not_found", status=404)
    return jsonify({"style": record})


@story_video_bp.route("/api/story-video/edit-styles/<style_id>", methods=["DELETE"])
def delete_edit_style_route(style_id: str):
    from src.utils.edit_styles.store import delete_edit_style

    if not delete_edit_style(style_id):
        return _error("Kieu dung khong ton tai.", code="edit_style_not_found", status=404)
    return jsonify({"deleted": True, "styleId": style_id})


@story_video_bp.route("/api/story-video/edit-styles/<style_id>/preview", methods=["POST"])
def preview_edit_style(style_id: str):
    """Render a ~10s clip of this style with the real overlay pass (synchronous)."""
    from src.utils.edit_styles.preview import render_preview

    data = request.get_json(silent=True) or {}
    library_id = str(data.get("libraryId") or "").strip()
    sample = _find_sample_clip(data.get("sampleClipId"), library_id or None)
    if not sample:
        return _error("Chua co clip mau trong thu vien de xem thu.", code="no_sample", status=404)
    try:
        preview_path = render_preview(
            style_id, sample, library_id=library_id,
            decor_image_id=str(data.get("decorImageId") or "").strip(),
            modifier_ids=_clean_ids(data.get("modifierIds")),
        )
    except ValueError as exc:
        return _error(str(exc), code="edit_style_not_found", status=404)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[StoryVideo] Edit style preview failed: {exc}", exc_info=True)
        return _error(f"Khong render duoc ban xem thu: {exc}", code="edit_style_preview_failed", status=500)
    return jsonify({"previewPath": preview_path})


# ---------------------------------------------------------------------------
# 19b. Decor image management ("khung TV": a full-frame photo whose green screen
#      the story video plays inside). CRUD mirrors the CTA overlay endpoints; the
#      two extras are green-region auto-detection and a still alignment preview.
# ---------------------------------------------------------------------------
@story_video_bp.route("/api/story-video/decor-images", methods=["GET"])
def get_decor_images():
    from src.utils.story_decor_images import (
        list_decor_groups,
        load_decor_index,
        load_decor_settings,
    )

    index = load_decor_index()
    images = index.get("images", [])
    images.sort(key=lambda item: str(item.get("createdAt") or ""))
    # Groups are derived from the records, so the UI never has to reconcile a
    # separate list against the images it is showing.
    return jsonify({
        "images": images,
        "groups": list_decor_groups(),
        "settings": load_decor_settings(index),
    })


@story_video_bp.route("/api/story-video/decor-images", methods=["POST"])
def upload_decor_image():
    from src.utils.story_decor_images import create_decor_image

    upload_file = request.files.get("file") or request.files.get("image")
    if not upload_file or not upload_file.filename:
        return _error("Chua chon file anh decor.", code="no_file")

    try:
        record = create_decor_image(
            upload_file,
            group=request.form.get("group") or "",
            mode=request.form.get("mode") or "",
        )
    except ValueError as exc:
        return _error(str(exc), code="invalid_decor_image")
    except Exception as exc:
        logger.error(f"[StoryVideo] Decor image upload failed: {exc}", exc_info=True)
        return _error(f"Khong the xu ly anh decor: {exc}", code="decor_upload_failed", status=500)

    logger.info(f"[StoryVideo] Decor image uploaded: {record['id']}")
    return jsonify({"image": record}), 201


@story_video_bp.route("/api/story-video/decor-image-search/<provider>", methods=["GET"])
def search_decor_provider_images(provider: str):
    """Proxy Pexels/Pixabay photo search, pre-filtered to >=1920x1080 and ~16:9."""
    from requests import HTTPError, RequestException

    from src.utils.decor_image_search import search_provider_images
    from src.utils.pexels_key_pool import PexelsQuotaExhausted
    from src.utils.story_decor_images import imported_source_keys

    provider = provider.strip().lower()
    if provider not in {"pixabay", "pexels"}:
        return _error("Provider khong hop le.", code="invalid_provider", status=404)

    query = request.args.get("q", "").strip()
    if not query:
        return _error("Can nhap keyword de search anh.", code="missing_query")

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        return _error("page khong hop le.", code="invalid_pagination")

    try:
        result = search_provider_images(provider, query, page)
    except ValueError as exc:
        return _error(str(exc), code="provider_api_key_missing", status=400)
    except PexelsQuotaExhausted as exc:
        return _error(str(exc), code="provider_quota_exhausted", status=429)
    except HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        logger.error(f"[StoryVideo] {provider} image search HTTP error: {exc}")
        return _error(
            f"{provider} search API error.",
            code="provider_search_failed",
            status=502 if status_code >= 500 else 400,
        )
    except RequestException as exc:
        logger.error(f"[StoryVideo] {provider} image search request error: {exc}")
        return _error(f"Khong the goi {provider} API.", code="provider_search_failed", status=502)

    result["importedKeys"] = imported_source_keys()
    return jsonify(result)


@story_video_bp.route("/api/story-video/decor-images/import", methods=["POST"])
def import_decor_provider_image():
    """Import one searched photo as a decor image (one per request, like upload)."""
    from src.utils.story_decor_images import import_decor_image

    data = request.get_json(silent=True) or {}
    item = data.get("item")
    if not isinstance(item, dict):
        return _error("Thieu thong tin anh can import.", code="no_item")

    try:
        record = import_decor_image(item, group=str(data.get("group") or ""))
    except FileExistsError as exc:
        return _error(str(exc), code="decor_already_imported", status=409)
    except ValueError as exc:
        return _error(str(exc), code="invalid_decor_image")
    except Exception as exc:
        logger.error(f"[StoryVideo] Decor image import failed: {exc}", exc_info=True)
        return _error(f"Khong the import anh decor: {exc}", code="decor_import_failed", status=500)

    logger.info(f"[StoryVideo] Decor image imported from {item.get('provider')} {item.get('id')}: {record['id']}")
    return jsonify({"image": record}), 201


@story_video_bp.route("/api/story-video/decor-images/<image_id>", methods=["PATCH"])
def patch_decor_image(image_id: str):
    from src.utils.story_decor_images import update_decor_image

    payload = request.get_json(silent=True) or {}
    updates = {}
    for key in ("name", "keyColor", "enabled", "frame", "group", "maskMode", "used"):
        if key in payload:
            updates[key] = payload[key]
    for key in ("similarity", "blend", "overscan"):
        if key in payload and payload[key] is not None:
            try:
                updates[key] = float(payload[key])
            except (TypeError, ValueError):
                return _error(f"Gia tri {key} khong hop le.", code="invalid_value")
    for key in ("cornerRadius",):
        if key in payload and payload[key] is not None:
            try:
                updates[key] = int(payload[key])
            except (TypeError, ValueError):
                return _error(f"Gia tri {key} khong hop le.", code="invalid_value")

    # Rieng mot nhanh: ``null`` o day co nghia ("theo mac dinh chung"), nen no
    # khong the di nho hai vong tren -- ca hai deu bo qua None. Van phai ep
    # kieu tai cho de mot chuoi rac tra ve 400 chu khong vo thanh 500 duoi kia.
    if "blurRadius" in payload:
        raw = payload["blurRadius"]
        if raw is None:
            updates["blurRadius"] = None
        else:
            try:
                updates["blurRadius"] = float(raw)
            except (TypeError, ValueError):
                return _error("Gia tri blurRadius khong hop le.", code="invalid_value")

    # Vien cung luat voi blurRadius: ``null`` = theo mac dinh chung.
    if "border" in payload:
        raw = payload["border"]
        if raw is None:
            updates["border"] = None
        elif isinstance(raw, dict):
            try:
                updates["border"] = {
                    "width": int(raw.get("width") or 0),
                    "color": str(raw.get("color") or ""),
                    "shadow": bool(raw.get("shadow")),
                }
            except (TypeError, ValueError):
                return _error("Gia tri border khong hop le.", code="invalid_value")
        else:
            return _error("Gia tri border khong hop le.", code="invalid_value")

    try:
        record = update_decor_image(image_id, updates)
    except Exception as exc:
        logger.error(f"[StoryVideo] Decor image update failed: {exc}", exc_info=True)
        return _error(f"Khong the cap nhat anh decor: {exc}", code="decor_update_failed", status=500)
    if not record:
        return _error("Anh decor khong ton tai.", code="decor_not_found", status=404)
    return jsonify({"image": record})


@story_video_bp.route("/api/story-video/decor-images/settings", methods=["PATCH"])
def patch_decor_image_settings():
    """Mac dinh chung (do mo, vien cua so) cho moi anh decor tu ve vung nen.

    Gui mot hoac nhieu khoa trong ``backgroundBlur``, ``borderWidth``,
    ``borderColor``, ``borderShadow``; khoa khong gui thi giu nguyen.
    Anh nao dang theo mac dinh chung se duoc ve lai PNG ngay trong request --
    khoang 0,6s moi anh, nen mot thu vien vai chuc anh mat vai giay. Doi lai,
    khi request tra ve thi moi thu tren dia da dung, khong con trang thai nua
    voi nao de render boc phai. Anh da tu dat rieng va anh che do chroma khong
    bi dong toi.

    Nhu ``/group`` o duoi, segment tinh "settings" khong the bi sibling
    ``<image_id>`` nuot mat: Werkzeug xep rule khong tham so len truoc.
    """
    import re

    from src.utils.story_decor_images import apply_decor_settings

    payload = request.get_json(silent=True) or {}
    updates = {}
    if "backgroundBlur" in payload:
        try:
            updates["backgroundBlur"] = float(payload["backgroundBlur"])
        except (TypeError, ValueError):
            return _error("Gia tri backgroundBlur khong hop le.", code="invalid_value")
    if "borderWidth" in payload:
        try:
            updates["borderWidth"] = int(payload["borderWidth"])
        except (TypeError, ValueError):
            return _error("Gia tri borderWidth khong hop le.", code="invalid_value")
    if "borderColor" in payload:
        color = payload["borderColor"]
        if not isinstance(color, str) or not re.match(r"^#?[0-9a-fA-F]{6}$", color.strip()):
            return _error("Mau vien phai dang #RRGGBB.", code="invalid_value")
        updates["borderColor"] = color
    if "borderShadow" in payload:
        updates["borderShadow"] = bool(payload["borderShadow"])
    if not updates:
        return _error("Thieu backgroundBlur / borderWidth / borderColor / borderShadow.", code="invalid_value")

    try:
        settings, images = apply_decor_settings(updates)
    except Exception as exc:
        logger.error(f"[StoryVideo] Decor settings update failed: {exc}", exc_info=True)
        return _error(f"Khong the cap nhat cai dat: {exc}", code="decor_settings_failed", status=500)

    images = sorted(images, key=lambda item: str(item.get("createdAt") or ""))
    return jsonify({"settings": settings, "images": images})


@story_video_bp.route("/api/story-video/decor-images/group", methods=["PATCH"])
def rename_decor_image_group():
    """Rename a theme group across every image that carries it.

    The static "group" segment cannot be shadowed by the sibling
    ``<image_id>`` PATCH rule: Werkzeug ranks argument-free rules above ones
    with a converter, whatever order they were registered in.
    """
    from src.utils.story_decor_images import list_decor_groups, rename_decor_group

    payload = request.get_json(silent=True) or {}
    old_group = str(payload.get("from") or "")
    new_group = str(payload.get("to") or "")

    try:
        moved = rename_decor_group(old_group, new_group)
    except Exception as exc:
        logger.error(f"[StoryVideo] Decor group rename failed: {exc}", exc_info=True)
        return _error(f"Khong the doi ten nhom: {exc}", code="decor_group_rename_failed", status=500)

    logger.info(f"[StoryVideo] Decor group renamed: '{old_group}' -> '{new_group}' ({moved} anh)")
    return jsonify({"moved": moved, "groups": list_decor_groups()})


@story_video_bp.route("/api/story-video/decor-images/reset-used", methods=["POST"])
def reset_decor_images_used():
    """Un-spend decor images so they can be dealt again.

    Each image is used by exactly one video, so a themed set runs out after N
    videos. This is how the user starts that set over: by ``imageIds``, by
    ``group``, or — with neither — across the whole library.
    """
    from src.utils.story_decor_images import reset_decor_used

    payload = request.get_json(silent=True) or {}
    image_ids = payload.get("imageIds")
    group = payload.get("group")

    cleared = reset_decor_used(
        group=None if group is None else str(group),
        image_ids=image_ids if isinstance(image_ids, list) else None,
    )
    logger.info(f"[StoryVideo] Decor used-mark reset: {cleared} anh (group={group!r})")
    return jsonify({"cleared": cleared})


@story_video_bp.route("/api/story-video/decor-images/purge-used", methods=["POST"])
def purge_used_decor_images_route():
    """Delete used decor images from disk: ``group`` for one theme, none = whole library.

    Images a batch still has to render (queued, running, or failed and waiting
    for retry) and single renders in flight are kept and reported as ``kept``.
    """
    from src.utils.story_decor_images import held_decor_ids, purge_used_decor_images
    from src.utils.story_video_batch import decor_ids_still_needed

    payload = request.get_json(silent=True) or {}
    group = payload.get("group")

    try:
        result = purge_used_decor_images(
            group=None if group is None else str(group),
            keep_ids=decor_ids_still_needed() | held_decor_ids(),
        )
    except Exception as exc:
        logger.error(f"[StoryVideo] Decor purge failed: {exc}", exc_info=True)
        return _error(f"Khong the don anh decor da dung: {exc}", code="decor_purge_failed", status=500)
    return jsonify(result)


@story_video_bp.route("/api/story-video/decor-images/<image_id>", methods=["DELETE"])
def delete_decor_image(image_id: str):
    from src.utils.story_decor_images import delete_decor_image_record

    if not delete_decor_image_record(image_id):
        return _error("Anh decor khong ton tai.", code="decor_not_found", status=404)

    logger.info(f"[StoryVideo] Decor image deleted: {image_id}")
    return jsonify({"deleted": True, "imageId": image_id})


@story_video_bp.route("/api/story-video/decor-images/<image_id>/detect-frame", methods=["POST"])
def detect_decor_image_frame(image_id: str):
    """Re-run green-region detection on the original upload."""
    from src.utils.story_decor_images import (
        decor_source_abs_path,
        detect_green_frame,
        get_decor_image,
    )

    record = get_decor_image(image_id)
    if not record:
        return _error("Anh decor khong ton tai.", code="decor_not_found", status=404)

    source_path = decor_source_abs_path(record)
    if not source_path:
        return _error("File anh goc khong con tren dia.", code="decor_source_missing", status=404)

    detected = detect_green_frame(source_path)
    if not detected:
        return _error(
            "Khong tim thay vung mau xanh trong anh. Hay keo khung thu cong.",
            code="decor_no_green",
        )
    frame, key_color = detected
    return jsonify({"frame": frame, "keyColor": key_color})


@story_video_bp.route("/api/story-video/decor-images/<image_id>/frame-preview", methods=["POST"])
def preview_decor_image_frame(image_id: str):
    """Compose one still: a library frame fitted into the decor frame, PNG on top.

    Synchronous and sub-second (one frame, no clip encode), so the canvas editor
    can show what the current alignment actually produces without waiting on a
    video render. Mirrors the crt-demo endpoint, and reuses the exact filter the
    render builds so the still cannot disagree with it.
    """
    from src.utils.ffmpeg_helper import FFmpegHelper
    from src.utils.story_decor_images import (
        decor_filter_parts,
        get_decor_image,
        processed_abs_path,
    )

    data = request.get_json(silent=True) or {}
    record = get_decor_image(image_id)
    if not record:
        return _error("Anh decor khong ton tai.", code="decor_not_found", status=404)

    decor_path = processed_abs_path(record)
    if not decor_path:
        return _error("Anh decor chua duoc tach nen.", code="decor_not_processed", status=409)

    sample_path = _find_sample_clip(data.get("sampleClipId"), data.get("libraryId"))
    if not sample_path or not os.path.isfile(sample_path):
        return _error(
            "Khong co clip mau trong thu vien. Hay them clip truoc.",
            code="no_sample",
            status=404,
        )

    preview_dir = os.path.join(Config.STORY_DECOR_DIR, "previews")
    os.makedirs(preview_dir, exist_ok=True)
    # A new filename each time so the browser cannot serve a stale cached still.
    output_path = os.path.join(preview_dir, f"{image_id}_{str(uuid.uuid4())[:8]}.jpg")

    target = Config.TARGET_RESOLUTION.replace("x", ":")
    parts = [f"[0:v]scale={target},setsar=1[src]"]
    decor_parts, chain = decor_filter_parts("[src]", record, 1)
    parts.extend(decor_parts)
    parts.append(f"{chain}format=yuv420p[v]")

    cmd = [
        "ffmpeg", "-y",
        "-ss", "1", "-i", sample_path,
        "-i", decor_path,
        "-filter_complex", ";".join(parts),
        "-map", "[v]", "-frames:v", "1", "-q:v", "3",
        output_path,
    ]
    if not FFmpegHelper.run_command(cmd) or not os.path.isfile(output_path):
        return _error("FFmpeg khong tao duoc preview khung.", code="decor_preview_failed", status=500)

    # Keep only the newest still per decor image.
    for name in os.listdir(preview_dir):
        stale = os.path.join(preview_dir, name)
        if name.startswith(f"{image_id}_") and stale != output_path:
            try:
                os.remove(stale)
            except OSError:
                pass

    rel = os.path.relpath(output_path, Config.STORAGE_DIR).replace(os.sep, "/")
    return jsonify({"previewPath": rel})


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


@story_video_bp.route("/api/story-video/effect-preview/sources", methods=["GET"])
def list_effect_preview_sources():
    from src.utils.story_effect_preview import load_sources

    return jsonify({"sources": load_sources()})


@story_video_bp.route("/api/story-video/effect-preview/sources", methods=["POST"])
def upload_effect_preview_source():
    """Upload a handful of short clips and build the preview base video."""
    from src.utils.story_effect_preview import EffectPreviewError, build_source_set

    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return _error("Chua chon video nao.", code="no_files")

    try:
        record = build_source_set(files, str(request.form.get("name") or ""))
    except EffectPreviewError as exc:
        return _error(str(exc), code="effect_preview_source_failed")
    except Exception as exc:
        logger.error(f"[StoryVideo] Effect preview source failed: {exc}", exc_info=True)
        return _error(f"Khong the tao bo clip preview: {exc}", code="effect_preview_source_failed", status=500)
    return jsonify({"source": record}), 201


@story_video_bp.route("/api/story-video/effect-preview/sources/<source_id>", methods=["DELETE"])
def delete_effect_preview_source(source_id: str):
    from src.utils.story_effect_preview import delete_source

    if not delete_source(source_id):
        return _error("Khong tim thay bo clip preview.", code="source_not_found", status=404)
    return jsonify({"deleted": source_id})


@story_video_bp.route("/api/story-video/effect-preview/render", methods=["POST"])
def render_effect_preview():
    """Re-apply the current effect stack to a stored clip set.

    Runs on a worker thread: a 30-60s preview takes a while, and the point of the
    feature is to keep tweaking settings and re-rendering.
    """
    from src.utils.story_effect_preview import get_source

    data = request.get_json(silent=True) or {}
    source_id = str(data.get("sourceId") or "").strip()
    record = get_source(source_id)
    if not record:
        return _error("Khong tim thay bo clip preview.", code="source_not_found", status=404)

    include_style = bool(data.get("includeStyle", True))
    include_overlays = bool(data.get("includeOverlays", True))
    compare = bool(data.get("compare", False))
    decor_image_id = str(data.get("decorImageId") or "").strip()
    try:
        max_seconds = float(data.get("maxSeconds") or 0)
    except (TypeError, ValueError):
        max_seconds = 0.0

    session_id = f"efp-{str(uuid.uuid4())[:8]}"
    with _effect_preview_jobs_lock:
        _effect_preview_jobs[session_id] = {
            "sessionId": session_id,
            "status": "processing",
            "message": "Dang render preview...",
            "sourceId": source_id,
            "source": None,
            "error": None,
        }

    def _worker():
        from src.utils.story_effect_preview import render_preview

        try:
            updated = render_preview(
                source_id,
                include_style=include_style,
                include_overlays=include_overlays,
                compare=compare,
                max_seconds=max_seconds,
                decor_image_id=decor_image_id,
            )
            payload = {"status": "completed", "message": "Preview da san sang.", "source": updated}
        except Exception as exc:
            logger.error(f"[StoryVideo] Effect preview render failed: {exc}", exc_info=True)
            payload = {"status": "failed", "message": str(exc), "error": str(exc)}
        with _effect_preview_jobs_lock:
            if session_id in _effect_preview_jobs:
                _effect_preview_jobs[session_id].update(payload)

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"sessionId": session_id}), 202


@story_video_bp.route("/api/story-video/effect-preview/jobs/<session_id>", methods=["GET"])
def get_effect_preview_job(session_id: str):
    with _effect_preview_jobs_lock:
        job = _effect_preview_jobs.get(session_id)
    if not job:
        return _error("Preview job khong ton tai.", code="job_not_found", status=404)
    return jsonify(job)


@story_video_bp.route("/api/story-video/sparkle-presets", methods=["GET"])
def get_sparkle_presets():
    from src.utils.story_sparkle_presets import SPARKLE_PARAM_SPEC, list_sparkle_presets

    return jsonify({"presets": list_sparkle_presets(), "paramSpec": SPARKLE_PARAM_SPEC})


@story_video_bp.route("/api/story-video/sparkle-overlays", methods=["POST"])
def create_sparkle_overlay():
    """Generate a sparkle layer and register it as an overlay.

    Generation takes 25-90s depending on preset, so it runs on a worker thread and
    reports through the existing TV noise job tracker. The resulting record is an
    ordinary overlay from there on: the same list, demo, enable/opacity/order and
    delete endpoints manage it.
    """
    from src.utils.story_sparkle_presets import get_sparkle_preset, sanitize_sparkle_params
    from src.utils.story_tv_noise_overlays import mark_tv_noise_failed

    data = request.get_json(silent=True) or {}
    preset_id = str(data.get("presetId") or "").strip()
    preset = get_sparkle_preset(preset_id)
    if not preset:
        return _error("Preset lap lanh khong hop le.", code="invalid_sparkle_preset", status=404)

    raw_params = data.get("params")
    if raw_params is not None and not isinstance(raw_params, dict):
        return _error("params khong hop le.", code="invalid_params")
    params = sanitize_sparkle_params(preset_id, raw_params)

    name = str(data.get("name") or "").strip() or preset["name"]
    try:
        opacity = max(0.0, min(1.0, float(data.get("opacity", 0.7))))
        luma_gain = max(1.0, min(8.0, float(data.get("lumaGain", Config.STORY_TV_NOISE_LUMA_GAIN))))
    except (TypeError, ValueError):
        return _error("opacity/lumaGain khong hop le.", code="invalid_params")

    session_id = _new_tv_noise_job("create_sparkle")
    _update_tv_noise_job(session_id, message="Dang dung lop lap lanh...")

    def _worker():
        from src.utils.story_sparkle_presets import generate_sparkle_source
        from src.utils.story_tv_noise_overlays import (
            create_generated_overlay,
            run_tv_noise_preprocess,
        )

        overlay_id = ""
        try:
            _update_tv_noise_job(session_id, status="generating", message="Dang dung lop lap lanh...")
            source_path = generate_sparkle_source(preset_id, params)

            record = create_generated_overlay(
                name,
                source_path,
                kind="sparkle",
                meta={"presetId": preset_id, "params": params},
                blend_mode="luma",
                opacity=opacity,
                luma_gain=luma_gain,
            )
            overlay_id = str(record["id"])
            _update_tv_noise_job(
                session_id, status="processing", message="Dang tao alpha MOV...", overlayId=overlay_id
            )

            processed = run_tv_noise_preprocess(overlay_id)
            if processed and processed.get("status") == "ready":
                _update_tv_noise_job(
                    session_id,
                    status="completed",
                    current=1,
                    message="Lop lap lanh da san sang.",
                    overlayId=overlay_id,
                )
            else:
                error = (processed or {}).get("error") or "Sparkle preprocess failed."
                _update_tv_noise_job(
                    session_id, status="failed", current=1, message=error, error=error, overlayId=overlay_id
                )
        except Exception as exc:
            logger.error(f"[StoryVideo] Sparkle overlay creation failed: {exc}", exc_info=True)
            if overlay_id:
                mark_tv_noise_failed(overlay_id, str(exc))
            _update_tv_noise_job(
                session_id, status="failed", current=1, message=str(exc), error=str(exc), overlayId=overlay_id
            )

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"sessionId": session_id, "presetId": preset_id, "params": params}), 202


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
        for key in ("enabled", "name", "order", "opacity", "tolerance", "softness", "blendMode", "lumaGain"):
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
    from src.utils.story_tv_noise_overlays import (
        get_active_tv_noise_overlays,
        get_tv_noise_overlay,
        overlay_blend_mode,
        processed_abs_path,
    )

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

    # _find_sample_clip resolves the clip's own library root (default lib now lives
    # under default/, not the shared top-level dir).
    sample_path = _find_sample_clip(sample_clip_id or None, data.get("libraryId"))
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
    for index, item in enumerate(ready_overlays):
        input_idx = index + 1
        noise_label = f"noise{index}"
        out_label = f"tvn{index}"
        if overlay_blend_mode(item) == "screen":
            opacity = max(0.0, min(1.0, float(item.get("opacity") or Config.STORY_TV_NOISE_OPACITY)))
            filter_parts.append(f"[{input_idx}:v]setpts=PTS-STARTPTS,format=yuv420p[{noise_label}]")
            filter_parts.append(f"{chain_label}format=yuv420p[{noise_label}base]")
            filter_parts.append(
                f"[{noise_label}base][{noise_label}]"
                f"blend=all_mode=screen:all_opacity={opacity}:eof_action=repeat[{out_label}]"
            )
        else:
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
