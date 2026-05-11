"""Flask Blueprint for News Bulletin and Channel Management API routes.

Registered as a blueprint on the main app to avoid bloating web_app.py.
"""

import os
import shutil
import uuid

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from src.config import Config
from src.processors.news_script_parser import NewsScriptParseError, parse_news_script
from src.utils.channel_manager import (
    create_channel,
    create_group,
    delete_channel,
    delete_group,
    get_channel,
    get_full_registry,
    list_channels,
    list_channels_by_group,
    list_groups,
    update_channel,
    update_group,
)
from src.utils.logger import logger
from src.utils.news_bulletin_pipeline import (
    add_resource_files,
    create_bulletin,
    get_resource_summary,
    list_bulletins,
    load_bulletin_progress,
    load_bulletin_state,
    save_bulletin_progress,
    save_bulletin_state,
)
from src.utils.news_bulletin_render_worker import start_bulletin_render

news_bp = Blueprint("news_bulletin", __name__)


def _error_response(message: str, code: str = "bad_request", status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _bulletin_status(bulletin_id: str) -> str:
    progress = load_bulletin_progress(bulletin_id) or {}
    return progress.get("status", "draft")


def _ensure_draft_bulletin(bulletin_id: str):
    status = _bulletin_status(bulletin_id)
    if status != "draft":
        return _error_response(
            "Bulletin da bat dau render hoac da hoan tat, khong the chinh sua.",
            code="bulletin_locked",
            status=409,
        )
    return None


def _init_resource_state(parsed_script: dict) -> dict:
    return {
        str(item["id"]): {"vidClips": [], "images": []}
        for item in parsed_script.get("newsItems", [])
    }


def _reset_resource_dirs(bulletin_id: str, parsed_script: dict):
    resource_root = os.path.join(Config.STORAGE_DIR, "news_bulletin", bulletin_id, "resources")
    if os.path.isdir(resource_root):
        shutil.rmtree(resource_root)
    for item in parsed_script.get("newsItems", []):
        news_id = item["id"]
        res_dir = os.path.join(resource_root, f"news_{news_id}")
        os.makedirs(os.path.join(res_dir, "vid_clips"), exist_ok=True)
        os.makedirs(os.path.join(res_dir, "images"), exist_ok=True)


def _reset_single_resource_dir(bulletin_id: str, news_idx: int):
    res_dir = os.path.join(Config.STORAGE_DIR, "news_bulletin", bulletin_id, "resources", f"news_{news_idx}")
    if os.path.isdir(res_dir):
        shutil.rmtree(res_dir)
    os.makedirs(os.path.join(res_dir, "vid_clips"), exist_ok=True)
    os.makedirs(os.path.join(res_dir, "images"), exist_ok=True)


def _resource_details(bulletin_id: str) -> dict:
    state = load_bulletin_state(bulletin_id)
    if not state:
        return {}
    details = {}
    for news_key, entry in state.get("resources", {}).items():
        details[news_key] = {
            "vidClips": [
                {
                    "filename": item.get("filename") or os.path.basename(item.get("relativePath", "")),
                    "relativePath": item.get("relativePath", ""),
                }
                for item in entry.get("vidClips", [])
                if item.get("relativePath")
            ],
            "images": [
                {
                    "filename": item.get("filename") or os.path.basename(item.get("relativePath", "")),
                    "relativePath": item.get("relativePath", ""),
                }
                for item in entry.get("images", [])
                if item.get("relativePath")
            ],
        }
    return details


def _sync_progress_channels(bulletin_id: str, channel_ids: list[str]):
    progress = load_bulletin_progress(bulletin_id) or {}
    progress["channelIds"] = channel_ids
    next_channels = {}
    current_channels = progress.get("channels", {})
    for ch_id in channel_ids:
        channel = get_channel(ch_id)
        existing = current_channels.get(ch_id, {})
        next_channels[ch_id] = {
            "channelId": ch_id,
            "channelName": channel.get("channelName", ch_id) if channel else ch_id,
            "status": existing.get("status", "pending"),
            "stage": existing.get("stage", "pending"),
            "percent": existing.get("percent", 0),
            "message": existing.get("message", "Cho xu ly..."),
            "outputVideo": existing.get("outputVideo"),
            "error": existing.get("error"),
        }
    progress["channels"] = next_channels
    save_bulletin_progress(bulletin_id, progress)


# ===================================================================
# Channel Group endpoints
# ===================================================================

@news_bp.route("/api/channel-groups", methods=["GET"])
def api_list_groups():
    return jsonify({"groups": list_groups()})


@news_bp.route("/api/channel-groups", methods=["POST"])
def api_create_group():
    data = request.get_json(silent=True) or {}
    group_name = (data.get("groupName") or "").strip()
    if not group_name:
        return _error_response("groupName la bat buoc.")
    try:
        group = create_group(group_name, data.get("language", ""))
        return jsonify({"group": group}), 201
    except ValueError as exc:
        return _error_response(str(exc))


@news_bp.route("/api/channel-groups/<group_id>", methods=["PUT"])
def api_update_group(group_id: str):
    data = request.get_json(silent=True) or {}
    try:
        group = update_group(group_id, data)
        return jsonify({"group": group})
    except ValueError as exc:
        return _error_response(str(exc), status=404)


@news_bp.route("/api/channel-groups/<group_id>", methods=["DELETE"])
def api_delete_group(group_id: str):
    try:
        delete_group(group_id)
        return jsonify({"deleted": True})
    except ValueError as exc:
        return _error_response(str(exc), status=404)


# ===================================================================
# Channel endpoints
# ===================================================================

@news_bp.route("/api/channels", methods=["GET"])
def api_list_channels():
    group_id = request.args.get("groupId", "")
    if group_id:
        channels = list_channels_by_group(group_id)
    else:
        channels = list_channels()
    groups = list_groups()
    return jsonify({"channels": channels, "groups": groups})


@news_bp.route("/api/channels", methods=["POST"])
def api_create_channel():
    data = request.get_json(silent=True) or {}
    channel_name = (data.get("channelName") or "").strip()
    if not channel_name:
        return _error_response("channelName la bat buoc.")
    try:
        channel = create_channel(
            channel_name=channel_name,
            group_id=data.get("groupId", ""),
            voice_id=data.get("voiceId", ""),
            transition_video_path=data.get("transitionVideoPath", ""),
            decor_video_id=data.get("decorVideoId", ""),
            source_text=data.get("sourceText", ""),
            is_active=data.get("isActive", True),
        )
        return jsonify({"channel": channel}), 201
    except ValueError as exc:
        return _error_response(str(exc))


@news_bp.route("/api/channels/<channel_id>", methods=["PUT"])
def api_update_channel(channel_id: str):
    data = request.get_json(silent=True) or {}
    try:
        channel = update_channel(channel_id, data)
        return jsonify({"channel": channel})
    except ValueError as exc:
        return _error_response(str(exc), status=404)


@news_bp.route("/api/channels/<channel_id>", methods=["DELETE"])
def api_delete_channel(channel_id: str):
    try:
        delete_channel(channel_id)
        return jsonify({"deleted": True})
    except ValueError as exc:
        return _error_response(str(exc), status=404)


# ===================================================================
# Channel transition video upload
# ===================================================================

@news_bp.route("/api/channels/<channel_id>/transition-video", methods=["POST"])
def api_upload_channel_transition(channel_id: str):
    """Upload a transition video for a channel."""
    if "video" not in request.files:
        return _error_response("Can upload file video (field name: video).")

    video_file = request.files["video"]
    if not video_file.filename:
        return _error_response("File video khong co ten.")

    ext = video_file.filename.rsplit(".", 1)[-1].lower() if "." in video_file.filename else ""
    if ext not in Config.ALLOWED_VIDEO_EXTENSIONS:
        return _error_response(f"Dinh dang video khong hop le: .{ext}")

    transition_dir = os.path.join(Config.STORAGE_DIR, "channels", "transitions")
    os.makedirs(transition_dir, exist_ok=True)

    filename = secure_filename(f"transition_{channel_id}.{ext}")
    file_path = os.path.join(transition_dir, filename)
    video_file.save(file_path)

    try:
        channel = update_channel(channel_id, {"transitionVideoPath": file_path})
        return jsonify({"channel": channel, "transitionVideoPath": file_path})
    except ValueError as exc:
        return _error_response(str(exc), status=404)


# ===================================================================
# News Bulletin endpoints
# ===================================================================

@news_bp.route("/api/news-bulletin/list", methods=["GET"])
def api_list_bulletins():
    return jsonify({"bulletins": list_bulletins()})


@news_bp.route("/api/news-bulletin/parse", methods=["POST"])
def api_parse_script():
    """Parse script text and return preview without creating a bulletin."""
    data = request.get_json(silent=True) or {}
    script_text = data.get("scriptText", "")

    if not script_text.strip():
        # Try file upload
        if "scriptFile" in request.files:
            script_file = request.files["scriptFile"]
            script_text = script_file.read().decode("utf-8", errors="replace")

    if not script_text.strip():
        return _error_response("Can nhap noi dung kich ban hoac upload file .txt.")

    try:
        parsed = parse_news_script(script_text)
        return jsonify({"parsed": parsed, "newsCount": len(parsed.get("newsItems", []))})
    except NewsScriptParseError as exc:
        return _error_response(str(exc), code="parse_error")


@news_bp.route("/api/news-bulletin", methods=["POST"])
def api_create_bulletin():
    """Create a new bulletin from script text and selected channels."""
    data = request.get_json(silent=True) or {}
    script_text = (data.get("scriptText") or "").strip()
    channel_ids = data.get("channelIds", [])

    if not script_text:
        return _error_response("Can nhap noi dung kich ban.")

    if not channel_ids:
        return _error_response("Can chon it nhat 1 channel.")

    try:
        state = create_bulletin(script_text, channel_ids)
        return jsonify({
            "bulletinId": state["bulletinId"],
            "newsCount": state["newsCount"],
            "channelIds": state["channelIds"],
        }), 201
    except ValueError as exc:
        return _error_response(str(exc))


@news_bp.route("/api/news-bulletin/<bulletin_id>", methods=["GET"])
def api_get_bulletin(bulletin_id: str):
    state = load_bulletin_state(bulletin_id)
    if not state:
        return _error_response(f"Bulletin '{bulletin_id}' khong ton tai.", status=404)

    progress = load_bulletin_progress(bulletin_id) or {}
    resource_summary = get_resource_summary(bulletin_id)

    return jsonify({
        "bulletinId": state.get("bulletinId"),
        "scriptText": state.get("scriptText", ""),
        "parsedScript": state.get("parsedScript"),
        "channelIds": state.get("channelIds", []),
        "newsCount": state.get("newsCount", 0),
        "resourceSummary": resource_summary,
        "resourceDetails": _resource_details(bulletin_id),
        "status": progress.get("status", "draft"),
        "channels": progress.get("channels", {}),
        "createdAt": state.get("createdAt"),
        "updatedAt": state.get("updatedAt"),
    })


@news_bp.route("/api/news-bulletin/<bulletin_id>/progress", methods=["GET"])
def api_get_bulletin_progress(bulletin_id: str):
    progress = load_bulletin_progress(bulletin_id)
    if not progress:
        return _error_response(f"Bulletin '{bulletin_id}' khong ton tai.", status=404)
    return jsonify(progress)


@news_bp.route("/api/news-bulletin/<bulletin_id>/resources/<int:news_idx>", methods=["POST"])
def api_upload_bulletin_resources(bulletin_id: str, news_idx: int):
    """Upload images or video clips for a specific news item.

    Accepts multipart form with 'images' and/or 'videos' file fields.
    """
    state = load_bulletin_state(bulletin_id)
    if not state:
        return _error_response(f"Bulletin '{bulletin_id}' khong ton tai.", status=404)
    locked = _ensure_draft_bulletin(bulletin_id)
    if locked:
        return locked

    # Verify news_idx exists
    news_ids = [item["id"] for item in state.get("parsedScript", {}).get("newsItems", [])]
    if news_idx not in news_ids:
        return _error_response(f"News item {news_idx} khong ton tai trong bulletin nay.")

    res_dir = os.path.join(
        Config.STORAGE_DIR, "news_bulletin", bulletin_id, "resources", f"news_{news_idx}"
    )

    added_videos = []
    added_images = []

    # Handle video uploads
    for video_file in request.files.getlist("videos"):
        if not video_file.filename:
            continue
        ext = video_file.filename.rsplit(".", 1)[-1].lower() if "." in video_file.filename else ""
        if ext not in Config.ALLOWED_VIDEO_EXTENSIONS:
            continue
        vid_dir = os.path.join(res_dir, "vid_clips")
        os.makedirs(vid_dir, exist_ok=True)
        filename = secure_filename(f"{uuid.uuid4().hex[:8]}_{video_file.filename}")
        file_path = os.path.join(vid_dir, filename)
        video_file.save(file_path)
        added_videos.append(file_path)

    # Handle image uploads
    for image_file in request.files.getlist("images"):
        if not image_file.filename:
            continue
        ext = image_file.filename.rsplit(".", 1)[-1].lower() if "." in image_file.filename else ""
        if ext not in Config.ALLOWED_IMAGE_EXTENSIONS:
            continue
        img_dir = os.path.join(res_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        filename = secure_filename(f"{uuid.uuid4().hex[:8]}_{image_file.filename}")
        file_path = os.path.join(img_dir, filename)
        image_file.save(file_path)
        added_images.append(file_path)

    # Update state
    if added_videos:
        add_resource_files(bulletin_id, news_idx, "vid_clips", added_videos)
    if added_images:
        add_resource_files(bulletin_id, news_idx, "images", added_images)

    resource_summary = get_resource_summary(bulletin_id)

    return jsonify({
        "newsIdx": news_idx,
        "addedVideos": len(added_videos),
        "addedImages": len(added_images),
        "resourceSummary": resource_summary,
        "resourceDetails": _resource_details(bulletin_id),
    })


@news_bp.route("/api/news-bulletin/<bulletin_id>/resources/<int:news_idx>", methods=["DELETE"])
def api_clear_bulletin_resources(bulletin_id: str, news_idx: int):
    """Clear all images and video clips for a specific news item."""
    state = load_bulletin_state(bulletin_id)
    if not state:
        return _error_response(f"Bulletin '{bulletin_id}' khong ton tai.", status=404)
    locked = _ensure_draft_bulletin(bulletin_id)
    if locked:
        return locked

    news_ids = [item["id"] for item in state.get("parsedScript", {}).get("newsItems", [])]
    if news_idx not in news_ids:
        return _error_response(f"News item {news_idx} khong ton tai trong bulletin nay.")

    state.setdefault("resources", {})[str(news_idx)] = {"vidClips": [], "images": []}
    save_bulletin_state(bulletin_id, state)
    _reset_single_resource_dir(bulletin_id, news_idx)

    return jsonify({
        "newsIdx": news_idx,
        "resourceSummary": get_resource_summary(bulletin_id),
        "resourceDetails": _resource_details(bulletin_id),
    })


@news_bp.route("/api/news-bulletin/<bulletin_id>/channels", methods=["PUT"])
def api_update_bulletin_channels(bulletin_id: str):
    """Update the selected channels for a bulletin."""
    state = load_bulletin_state(bulletin_id)
    if not state:
        return _error_response(f"Bulletin '{bulletin_id}' khong ton tai.", status=404)
    locked = _ensure_draft_bulletin(bulletin_id)
    if locked:
        return locked

    data = request.get_json(silent=True) or {}
    channel_ids = data.get("channelIds", [])
    if not channel_ids:
        return _error_response("Can chon it nhat 1 channel.")

    state["channelIds"] = channel_ids
    save_bulletin_state(bulletin_id, state)
    _sync_progress_channels(bulletin_id, channel_ids)

    return jsonify({"bulletinId": bulletin_id, "channelIds": channel_ids})


@news_bp.route("/api/news-bulletin/<bulletin_id>/script", methods=["PATCH"])
def api_update_bulletin_script(bulletin_id: str):
    """Update script text for a draft bulletin and reset resources when news ids change."""
    state = load_bulletin_state(bulletin_id)
    if not state:
        return _error_response(f"Bulletin '{bulletin_id}' khong ton tai.", status=404)
    locked = _ensure_draft_bulletin(bulletin_id)
    if locked:
        return locked

    data = request.get_json(silent=True) or {}
    script_text = (data.get("scriptText") or "").strip()
    if not script_text:
        return _error_response("Can nhap noi dung kich ban.")

    try:
        parsed = parse_news_script(script_text)
    except NewsScriptParseError as exc:
        return _error_response(str(exc), code="parse_error")

    old_ids = [item["id"] for item in state.get("parsedScript", {}).get("newsItems", [])]
    new_ids = [item["id"] for item in parsed.get("newsItems", [])]
    resources_reset = old_ids != new_ids

    state["scriptText"] = script_text
    state["parsedScript"] = parsed
    state["newsCount"] = len(new_ids)
    if resources_reset:
        state["resources"] = _init_resource_state(parsed)
        _reset_resource_dirs(bulletin_id, parsed)
    save_bulletin_state(bulletin_id, state)

    script_txt_path = os.path.join(Config.STORAGE_DIR, "news_bulletin", bulletin_id, "script.txt")
    script_json_path = os.path.join(Config.STORAGE_DIR, "news_bulletin", bulletin_id, "script.json")
    os.makedirs(os.path.dirname(script_txt_path), exist_ok=True)
    with open(script_txt_path, "w", encoding="utf-8") as fh:
        fh.write(script_text)
    with open(script_json_path, "w", encoding="utf-8") as fh:
        import json
        json.dump(parsed, fh, ensure_ascii=False, indent=2)

    progress = load_bulletin_progress(bulletin_id) or {}
    progress["newsCount"] = len(new_ids)
    save_bulletin_progress(bulletin_id, progress)

    return jsonify({
        "bulletinId": bulletin_id,
        "parsed": parsed,
        "newsCount": len(new_ids),
        "resourceSummary": get_resource_summary(bulletin_id),
        "resourceDetails": _resource_details(bulletin_id),
        "resourcesReset": resources_reset,
    })


@news_bp.route("/api/news-bulletin/<bulletin_id>/start-render", methods=["POST"])
def api_start_bulletin_render(bulletin_id: str):
    """Start the rendering process for a bulletin.

    Validates that resources exist for all news items, then launches
    background render threads for each selected channel.
    """
    state = load_bulletin_state(bulletin_id)
    if not state:
        return _error_response(f"Bulletin '{bulletin_id}' khong ton tai.", status=404)
    status = _bulletin_status(bulletin_id)
    if status != "draft":
        return _error_response(
            "Bulletin da bat dau render hoac da hoan tat.",
            code="bulletin_locked",
            status=409,
        )

    # Validate resources
    parsed_script = state.get("parsedScript", {})
    news_items = parsed_script.get("newsItems", [])
    resource_summary = get_resource_summary(bulletin_id)

    missing = []
    for item in news_items:
        nid = str(item["id"])
        res = resource_summary.get(nid, {})
        total = (res.get("vidClips", 0) or 0) + (res.get("images", 0) or 0)
        if total == 0:
            missing.append(nid)

    if missing:
        return _error_response(
            f"Cac tin sau chua co tai nguyen: {', '.join(missing)}. "
            f"Vui long them anh/video cho tat ca tin truoc khi render."
        )

    channel_ids = state.get("channelIds", [])
    if not channel_ids:
        return _error_response("Bulletin khong co channel nao duoc chon.")

    try:
        progress = start_bulletin_render(bulletin_id)
        return jsonify({
            "bulletinId": bulletin_id,
            "status": progress.get("status", "running"),
            "channelCount": len(channel_ids),
        })
    except ValueError as exc:
        return _error_response(str(exc))


@news_bp.route("/api/news-bulletin/<bulletin_id>/resources/<int:news_idx>/from-source", methods=["POST"])
def api_add_resources_from_source(bulletin_id: str, news_idx: int):
    """Copy clips from batch source sets or library into a news item's resources.

    Expects JSON body: { "clipPaths": ["relative/path/to/clip.mp4", ...] }
    The paths are relative to STORAGE_DIR.
    """
    state = load_bulletin_state(bulletin_id)
    if not state:
        return _error_response(f"Bulletin '{bulletin_id}' khong ton tai.", status=404)
    locked = _ensure_draft_bulletin(bulletin_id)
    if locked:
        return locked

    news_ids = [item["id"] for item in state.get("parsedScript", {}).get("newsItems", [])]
    if news_idx not in news_ids:
        return _error_response(f"News item {news_idx} khong ton tai trong bulletin nay.")

    data = request.get_json(silent=True) or {}
    clip_paths = data.get("clipPaths", [])
    if not clip_paths:
        return _error_response("Can cung cap danh sach clipPaths.")

    added_videos = []
    added_images = []
    image_exts = {"jpg", "jpeg", "png", "webp", "bmp"}
    video_exts = getattr(Config, "ALLOWED_VIDEO_EXTENSIONS", {"mp4", "mov", "mkv", "webm", "avi"})

    res_dir = os.path.join(
        Config.STORAGE_DIR, "news_bulletin", bulletin_id, "resources", f"news_{news_idx}"
    )

    storage_root = os.path.abspath(Config.STORAGE_DIR)
    for rel_path in clip_paths:
        normalized_rel = os.path.normpath(str(rel_path).replace("/", os.sep))
        if normalized_rel.startswith("..") or os.path.isabs(normalized_rel):
            logger.warning(f"[FromSource] Rejected unsafe path: {rel_path}")
            continue
        abs_path = os.path.abspath(os.path.join(storage_root, normalized_rel))
        if os.path.commonpath([storage_root, abs_path]) != storage_root:
            logger.warning(f"[FromSource] Rejected path outside storage: {rel_path}")
            continue
        if not os.path.isfile(abs_path):
            logger.warning(f"[FromSource] File not found: {abs_path}")
            continue

        ext = abs_path.rsplit(".", 1)[-1].lower() if "." in abs_path else ""

        if ext in image_exts:
            dest_dir = os.path.join(res_dir, "images")
            os.makedirs(dest_dir, exist_ok=True)
            filename = secure_filename(f"{uuid.uuid4().hex[:8]}_{os.path.basename(abs_path)}")
            dest_path = os.path.join(dest_dir, filename)
            shutil.copy2(abs_path, dest_path)
            added_images.append(dest_path)
        elif ext in video_exts:
            dest_dir = os.path.join(res_dir, "vid_clips")
            os.makedirs(dest_dir, exist_ok=True)
            filename = secure_filename(f"{uuid.uuid4().hex[:8]}_{os.path.basename(abs_path)}")
            dest_path = os.path.join(dest_dir, filename)
            shutil.copy2(abs_path, dest_path)
            added_videos.append(dest_path)
        else:
            logger.warning(f"[FromSource] Unsupported extension: {ext}")

    if added_videos:
        add_resource_files(bulletin_id, news_idx, "vid_clips", added_videos)
    if added_images:
        add_resource_files(bulletin_id, news_idx, "images", added_images)

    resource_summary = get_resource_summary(bulletin_id)

    return jsonify({
        "newsIdx": news_idx,
        "addedVideos": len(added_videos),
        "addedImages": len(added_images),
        "resourceSummary": resource_summary,
        "resourceDetails": _resource_details(bulletin_id),
    })
