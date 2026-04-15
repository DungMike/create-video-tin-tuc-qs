import json
import os
import uuid
from datetime import datetime

import yt_dlp
from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from src.composer.renderer import Renderer
from src.composer.timeline import TimelineComposer
from src.config import Config
from src.processors.audio_utils import get_audio_duration, validate_audio
from src.processors.image_processor import ImageProcessor
from src.processors.video_processor import VideoProcessor
from src.utils.file_manager import (
    collect_library_tags,
    cleanup_job_files,
    cleanup_review_video_assets,
    clear_directory,
    load_job_manifest,
    load_library_index,
    normalize_tags,
    save_job_manifest,
    save_library_index,
    setup_directories,
    storage_absolute_path,
    storage_relative_path,
    upsert_library_assets,
)
from src.utils.logger import logger

app = Flask(__name__, static_folder=None)
app.secret_key = Config.WEB_SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _json_error(message: str, status_code: int = 400, code: str = "bad_request", details: dict | None = None):
    payload = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    return jsonify(payload), status_code


def _allowed_file(filename: str, allowed_extensions: set[str]) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def _parse_links(raw_text: str) -> list[str]:
    seen = set()
    links = []
    for line in (raw_text or "").splitlines():
        value = line.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        links.append(value)
    return links


def _save_uploaded_file(file_storage, target_dir: str, prefix: str = "") -> str:
    filename = secure_filename(file_storage.filename or "")
    if not filename:
        raise ValueError("File upload missing a filename.")

    if prefix:
        filename = f"{prefix}_{filename}"

    filepath = os.path.join(target_dir, filename)
    file_storage.save(filepath)
    return filepath


def _download_youtube_links(links: list[str], output_dir: str) -> tuple[list[str], list[str]]:
    downloaded_paths = []
    errors = []

    for index, link in enumerate(links):
        logger.info(f"Downloading source video from link: {link}")
        output_template = os.path.join(output_dir, f"source_{index}_%(id)s.%(ext)s")
        options = {
            "format": "bestvideo[ext=mp4][height>=720]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
        }

        before_files = set(os.listdir(output_dir))
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.download([link])
        except Exception as exc:
            errors.append(f"{link}: {exc}")
            continue

        after_files = set(os.listdir(output_dir))
        new_files = sorted(after_files - before_files)
        for filename in new_files:
            if filename.lower().endswith(".mp4"):
                downloaded_paths.append(os.path.join(output_dir, filename))

    return downloaded_paths, errors


def _serialize_clip(clip: dict) -> dict:
    return {
        "id": clip["id"],
        "relativePath": clip["relative_path"],
        "sourceName": clip.get("source_name"),
        "start": clip.get("start"),
        "end": clip.get("end"),
        "duration": clip.get("duration"),
    }


def _serialize_asset(asset: dict) -> dict:
    return {
        "assetId": asset["asset_id"],
        "relativePath": asset["relative_path"],
        "sourceJobId": asset.get("source_job_id"),
        "sourceClipId": asset.get("source_clip_id"),
        "sourceName": asset.get("source_name"),
        "start": asset.get("start"),
        "end": asset.get("end"),
        "duration": asset.get("duration"),
        "tags": asset.get("tags", []),
        "createdAt": asset.get("created_at"),
        "updatedAt": asset.get("updated_at"),
    }


def _serialize_job(manifest: dict) -> dict:
    return {
        "jobId": manifest["job_id"],
        "createdAt": manifest.get("created_at"),
        "audioRelativePath": manifest.get("audio_relative_path"),
        "audioDuration": manifest.get("audio_duration"),
        "imagePaths": manifest.get("image_paths", []),
        "sourceVideos": manifest.get("source_videos", []),
        "downloadErrors": manifest.get("download_errors", []),
        "reviewClips": [_serialize_clip(clip) for clip in manifest.get("review_clips", [])],
        "selectedClipIds": manifest.get("selected_clip_ids", []),
        "selectedLibraryAssetIds": manifest.get("selected_library_asset_ids", []),
        "clipTags": manifest.get("clip_tags", {}),
        "outputVideo": manifest.get("output_video"),
    }


def _frontend_dist_dir() -> str:
    return os.path.abspath(Config.FRONTEND_DIST_DIR)


def _load_library_assets() -> tuple[dict, list[dict]]:
    library_index = load_library_index()
    assets = library_index.get("assets", [])
    existing_assets = []
    missing_count = 0

    for asset in assets:
        asset_path = storage_absolute_path(asset["relative_path"])
        if os.path.exists(asset_path):
            existing_assets.append(asset)
        else:
            missing_count += 1

    if missing_count:
        library_index["assets"] = existing_assets
        save_library_index(library_index)
        logger.warning(f"Pruned {missing_count} missing library assets from index")

    return library_index, existing_assets


def _parse_clip_tags_payload(raw_payload: dict | None, valid_clip_ids: set[str]) -> dict[str, list[str]]:
    if not isinstance(raw_payload, dict):
        return {}

    clip_tags = {}
    for clip_id, values in raw_payload.items():
        if clip_id not in valid_clip_ids or not isinstance(values, list):
            continue
        clip_tags[clip_id] = normalize_tags([str(value) for value in values])
    return clip_tags


def _load_job(job_id: str) -> tuple[dict, dict]:
    try:
        manifest = load_job_manifest(job_id)
    except FileNotFoundError:
        abort(404)

    manifest.setdefault("selected_library_asset_ids", [])
    manifest.setdefault("clip_tags", {})
    review_clips = manifest.get("review_clips", [])
    existing_clips = []

    for clip in review_clips:
        clip_path = storage_absolute_path(clip["relative_path"])
        if os.path.exists(clip_path):
            existing_clips.append(clip)

    if len(existing_clips) != len(review_clips):
        existing_ids = {clip["id"] for clip in existing_clips}
        missing_count = len(review_clips) - len(existing_clips)
        manifest["review_clips"] = existing_clips
        manifest["selected_clip_ids"] = [
            clip_id for clip_id in manifest.get("selected_clip_ids", []) if clip_id in existing_ids
        ]
        manifest["clip_tags"] = {
            clip_id: tags for clip_id, tags in manifest.get("clip_tags", {}).items() if clip_id in existing_ids
        }
        save_job_manifest(job_id, manifest)
        logger.warning(f"Pruned {missing_count} missing review clips from manifest for job {job_id}")

    dirs = setup_directories(job_id)
    return manifest, dirs


def _coerce_page_number(raw_value, default: int = 1) -> int:
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


@app.route("/api/jobs", methods=["POST"])
def create_job():
    audio_file = request.files.get("audio")
    image_files = [file_obj for file_obj in request.files.getlist("images") if file_obj and file_obj.filename]
    uploaded_videos = [file_obj for file_obj in request.files.getlist("videos") if file_obj and file_obj.filename]
    youtube_links = _parse_links(request.form.get("youtube_links", ""))

    if not audio_file or not audio_file.filename:
        return _json_error("Cần chọn file audio trước khi xử lý.", code="audio_required")

    if not _allowed_file(audio_file.filename, Config.ALLOWED_AUDIO_EXTENSIONS):
        return _json_error("Định dạng audio chưa được hỗ trợ.", code="invalid_audio_type")

    invalid_images = [
        file_obj.filename for file_obj in image_files if not _allowed_file(file_obj.filename, Config.ALLOWED_IMAGE_EXTENSIONS)
    ]
    invalid_videos = [
        file_obj.filename for file_obj in uploaded_videos if not _allowed_file(file_obj.filename, Config.ALLOWED_VIDEO_EXTENSIONS)
    ]
    if invalid_images or invalid_videos:
        return _json_error(
            "Có file upload không đúng định dạng cho ảnh hoặc video.",
            code="invalid_media_type",
            details={"invalidImages": invalid_images, "invalidVideos": invalid_videos},
        )

    job_id = str(uuid.uuid4())[:8]
    dirs = setup_directories(job_id)

    audio_path = _save_uploaded_file(audio_file, dirs["audio"], "audio")
    if not validate_audio(audio_path):
        return _json_error("Audio không hợp lệ hoặc không đọc được.", code="invalid_audio")

    audio_duration = get_audio_duration(audio_path)
    image_paths = [_save_uploaded_file(file_obj, dirs["raw_images"], f"image_{index}") for index, file_obj in enumerate(image_files)]
    uploaded_video_paths = [
        _save_uploaded_file(file_obj, dirs["raw_videos"], f"upload_{index}") for index, file_obj in enumerate(uploaded_videos)
    ]
    downloaded_video_paths, download_errors = _download_youtube_links(youtube_links, dirs["raw_videos"])
    source_video_paths = uploaded_video_paths + downloaded_video_paths

    if not image_paths and not source_video_paths:
        return _json_error("Cần có ít nhất một ảnh hoặc một video nguồn để tạo job.", code="empty_job")

    video_processor = VideoProcessor(job_id, dirs)
    review_clips = video_processor.create_review_clips(source_video_paths)

    manifest = {
        "job_id": job_id,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "audio_relative_path": storage_relative_path(audio_path),
        "audio_duration": round(audio_duration, 3),
        "image_paths": [storage_relative_path(path) for path in image_paths],
        "source_videos": [storage_relative_path(path) for path in source_video_paths],
        "download_errors": download_errors,
        "review_clips": review_clips,
        "selected_clip_ids": [],
        "selected_library_asset_ids": [],
        "clip_tags": {},
        "output_video": None,
    }
    save_job_manifest(job_id, manifest)
    return jsonify({"jobId": job_id, "redirectUrl": f"/jobs/{job_id}/review"}), 201


@app.route("/api/jobs/<job_id>/review", methods=["GET"])
def review_job(job_id: str):
    manifest, _dirs = _load_job(job_id)
    library_index, library_assets = _load_library_assets()
    review_clips = manifest.get("review_clips", [])
    page_size = max(1, Config.REVIEW_PAGE_SIZE)
    total_items = len(review_clips)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = min(_coerce_page_number(request.args.get("page"), 1), total_pages)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size

    return jsonify(
        {
            "job": _serialize_job(manifest),
            "pageClips": [_serialize_clip(clip) for clip in review_clips[start_idx:end_idx]],
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "totalItems": total_items,
                "totalPages": total_pages,
                "startItem": start_idx + 1 if total_items else 0,
                "endItem": min(end_idx, total_items),
            },
            "availableTags": collect_library_tags(library_index),
            "libraryAssetCount": len(library_assets),
        }
    )


@app.route("/api/jobs/<job_id>/resources", methods=["GET"])
def library_resources(job_id: str):
    manifest, _dirs = _load_job(job_id)
    library_index, library_assets = _load_library_assets()
    selected_tags = normalize_tags(request.args.getlist("tag"))
    selected_tag_set = set(selected_tags)

    if selected_tag_set:
        filtered_assets = [
            asset for asset in library_assets if set(normalize_tags(asset.get("tags", []))) & selected_tag_set
        ]
    else:
        filtered_assets = library_assets

    return jsonify(
        {
            "job": _serialize_job(manifest),
            "assets": [_serialize_asset(asset) for asset in filtered_assets],
            "availableTags": collect_library_tags(library_index),
            "selectedTags": selected_tags,
            "totalAssetCount": len(library_assets),
        }
    )


@app.route("/api/jobs/<job_id>/render", methods=["POST"])
def render_job(job_id: str):
    manifest, dirs = _load_job(job_id)
    _library_index, library_assets = _load_library_assets()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("Body JSON không hợp lệ.", code="invalid_json")

    current_page = _coerce_page_number(payload.get("currentPage"), 1)
    review_clips = manifest.get("review_clips", [])
    clip_lookup = {clip["id"]: clip for clip in review_clips}
    library_lookup = {asset["asset_id"]: asset for asset in library_assets}

    raw_selected_clip_ids = payload.get("selectedClipIds", [])
    raw_selected_library_asset_ids = payload.get("selectedLibraryAssetIds", [])
    if not isinstance(raw_selected_clip_ids, list) or not isinstance(raw_selected_library_asset_ids, list):
        return _json_error("Payload selection không đúng định dạng.", code="invalid_selection_payload")

    selected_ids = [clip_id for clip_id in raw_selected_clip_ids if isinstance(clip_id, str) and clip_id in clip_lookup]
    selected_library_asset_ids = [
        asset_id
        for asset_id in raw_selected_library_asset_ids
        if isinstance(asset_id, str) and asset_id in library_lookup
    ]
    clip_tags = _parse_clip_tags_payload(payload.get("clipTags"), set(clip_lookup))

    selected_clips = [clip_lookup[clip_id] for clip_id in selected_ids]
    selected_paths = [storage_absolute_path(clip["relative_path"]) for clip in selected_clips]
    selected_library_paths = [
        storage_absolute_path(library_lookup[asset_id]["relative_path"]) for asset_id in selected_library_asset_ids
    ]

    tagged_clips = []
    for clip_id, tags in clip_tags.items():
        if not tags:
            continue
        tagged_clip = dict(clip_lookup[clip_id])
        tagged_clip["tags"] = tags
        tagged_clips.append(tagged_clip)
    upsert_library_assets(job_id, tagged_clips)

    clear_directory(dirs["img_clips"])

    image_paths = [storage_absolute_path(path) for path in manifest.get("image_paths", [])]
    img_processor = ImageProcessor(job_id, dirs)
    image_clips = img_processor.process_images(image_paths) if image_paths else []

    if not selected_paths and not selected_library_paths and not image_clips:
        return _json_error(
            "Cần chọn ít nhất một clip video hoặc cung cấp ảnh để render.",
            code="empty_render_selection",
            details={"currentPage": current_page},
        )

    timeline_composer = TimelineComposer(job_id, dirs)
    concat_file = timeline_composer.create_timeline(
        selected_paths + selected_library_paths,
        image_clips,
        manifest["audio_duration"],
    )

    renderer = Renderer(job_id, dirs)
    output_path = renderer.render(
        concat_file,
        storage_absolute_path(manifest["audio_relative_path"]),
        manifest["audio_duration"],
    )
    if not output_path:
        return _json_error("Render thất bại. Kiểm tra lại log.", status_code=500, code="render_failed")

    manifest["review_clips"] = selected_clips
    manifest["selected_clip_ids"] = selected_ids
    manifest["selected_library_asset_ids"] = selected_library_asset_ids
    manifest["clip_tags"] = clip_tags
    manifest["output_video"] = storage_relative_path(output_path)
    save_job_manifest(job_id, manifest)
    cleanup_review_video_assets(dirs, selected_paths)
    cleanup_job_files(job_id)

    return jsonify(
        {
            "jobId": job_id,
            "outputVideo": manifest["output_video"],
            "redirectUrl": f"/jobs/{job_id}/result",
        }
    )


@app.route("/api/jobs/<job_id>/result", methods=["GET"])
def job_result(job_id: str):
    manifest, _dirs = _load_job(job_id)
    return jsonify({"job": _serialize_job(manifest)})


@app.route("/media/<path:relative_path>", methods=["GET"])
def media(relative_path: str):
    normalized = os.path.normpath(relative_path).replace("\\", "/")
    if normalized.startswith(".."):
        abort(404)
    return send_from_directory(os.path.abspath(Config.STORAGE_DIR), normalized, as_attachment=False)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def spa(path: str):
    if path.startswith("api/") or path.startswith("media/"):
        abort(404)

    dist_dir = _frontend_dist_dir()
    if not os.path.isdir(dist_dir):
        return (
            "Frontend build chưa tồn tại. Chạy `npm run dev` để dùng UI dev server hoặc `npm run build` để tạo dist.",
            503,
        )

    candidate_path = os.path.join(dist_dir, path)
    if path and os.path.isfile(candidate_path):
        return send_from_directory(dist_dir, path)

    index_path = os.path.join(dist_dir, "index.html")
    if not os.path.isfile(index_path):
        return (
            "Frontend build chưa tồn tại. Chạy `npm run dev` để dùng UI dev server hoặc `npm run build` để tạo dist.",
            503,
        )

    return send_from_directory(dist_dir, "index.html")


if __name__ == "__main__":
    app.run(host=Config.WEB_HOST, port=Config.WEB_PORT, debug=False)
