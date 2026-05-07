import json
import os
import shutil
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
from src.utils.effects_library import (
    ensure_effect_library_generated,
    load_active_animation_presets,
    load_active_transition_presets,
    load_animation_presets,
    load_transition_presets,
    update_effect_config,
)
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
from src.utils.job_progress import init_job_progress, load_job_progress, update_job_progress
from src.utils.logger import logger
from src.utils.batch_pipeline import BatchPipelineRunner, cleanup_expired_batch_retry_artifacts, load_batch_progress
from src.utils.decor_videos import (
    add_decor_video,
    delete_decor_video,
    get_decor_video_absolute_path,
    list_decor_videos,
)
from src.utils.tts_audio import (
    TTSAudioError,
    clone_voice,
    copy_generated_audio_to_job,
    create_audio_from_google_doc,
    list_generated_audio,
    load_voices,
    next_default_audio_name,
)

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


def _batch_input_mode(raw_value: str | None) -> str:
    normalized = str(raw_value or "docs").strip().lower()
    return normalized if normalized in {"docs", "audio_upload"} else ""


def _batch_source_root(batch_source_id: str) -> str:
    return os.path.join(Config.STORAGE_DIR, "batch_sources", batch_source_id)


def _batch_source_manifest_path(batch_source_id: str) -> str:
    return os.path.join(_batch_source_root(batch_source_id), "manifest.json")


def _load_batch_source_manifest(batch_source_id: str) -> dict | None:
    normalized = str(batch_source_id or "").strip()
    if not normalized.startswith("batchsrc_"):
        return None
    manifest_path = _batch_source_manifest_path(normalized)
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_batch_source_manifest(batch_source_id: str, data: dict):
    manifest_path = _batch_source_manifest_path(batch_source_id)
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)


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
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com/",
            },
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


def _infer_render_mode(manifest: dict) -> str:
    explicit_mode = manifest.get("render_mode")
    if explicit_mode in {"image_audio_only", "mixed_media", "video_only"}:
        return explicit_mode

    image_paths = manifest.get("image_paths", [])
    source_videos = manifest.get("source_videos", [])
    review_clips = manifest.get("review_clips", [])

    if image_paths and not source_videos and not review_clips:
        return "image_audio_only"
    if image_paths:
        return "mixed_media"
    return "video_only"


def _serialize_job(manifest: dict) -> dict:
    return {
        "jobId": manifest["job_id"],
        "createdAt": manifest.get("created_at"),
        "audioRelativePath": manifest.get("audio_relative_path"),
        "audioDuration": manifest.get("audio_duration"),
        "renderMode": _infer_render_mode(manifest),
        "imagePaths": manifest.get("image_paths", []),
        "sourceVideos": manifest.get("source_videos", []),
        "downloadErrors": manifest.get("download_errors", []),
        "reviewClips": [_serialize_clip(clip) for clip in manifest.get("review_clips", [])],
        "selectedClipIds": manifest.get("selected_clip_ids", []),
        "selectedLibraryAssetIds": manifest.get("selected_library_asset_ids", []),
        "clipTags": manifest.get("clip_tags", {}),
        "outputVideo": manifest.get("output_video"),
    }


def _serialize_effect_preset(preset: dict) -> dict:
    preset_type = preset.get("type") or ("animation" if preset.get("ffmpeg_filter") else "transition")
    payload = {
        "id": preset["id"],
        "type": preset_type,
        "name": preset.get("name", preset["id"]),
        "description": preset.get("description", ""),
        "previewRelativePath": preset.get("preview_relative_path"),
        "active": bool(preset.get("active", False)),
        "createdAt": preset.get("created_at"),
        "updatedAt": preset.get("updated_at"),
    }
    if preset_type == "animation":
        payload["ffmpegFilter"] = preset.get("ffmpeg_filter")
        payload["sourceImageRelativePath"] = preset.get("source_image_relative_path")
        payload["durationSeconds"] = preset.get("duration_seconds")
    else:
        payload["xfadeTransition"] = preset.get("xfade_transition")
        payload["sourceImageARelativePath"] = preset.get("source_image_a_relative_path")
        payload["sourceImageBRelativePath"] = preset.get("source_image_b_relative_path")
        payload["clipDurationSeconds"] = preset.get("clip_duration_seconds")
        payload["transitionDurationSeconds"] = preset.get("transition_duration_seconds")
    return payload


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


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _batch_draft_root(draft_id: str) -> str:
    return os.path.join(Config.STORAGE_DIR, "batch_drafts", draft_id)


def _batch_draft_manifest_path(draft_id: str) -> str:
    return os.path.join(_batch_draft_root(draft_id), "draft.json")


def _batch_draft_media_dir(draft_id: str, kind: str) -> str:
    dirname = "audio" if kind == "audio" else "images"
    return os.path.join(_batch_draft_root(draft_id), dirname)


def _load_json_file(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_json_file(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)


def _default_batch_draft(draft_id: str) -> dict:
    now = _utc_now()
    return {
        "draftId": draft_id,
        "createdAt": now,
        "updatedAt": now,
        "inputMode": "docs",
        "voiceId": "",
        "speed": 1,
        "volume": 1,
        "docEntries": [{"docUrl": "", "outputName": "", "decorVideoId": ""}],
        "audioEntries": [],
        "sourceVideoLinks": "",
        "selectedSourceRefs": [],
        "clipTags": {},
        "timelineConfig": {
            "firstPhaseSeconds": 300,
            "firstPhaseVideoCount": 3,
            "firstPhaseImageCount": 1,
            "afterPhaseImageEveryMin": 2,
            "afterPhaseImageEveryMax": 5,
        },
    }


def _load_batch_draft(draft_id: str) -> dict | None:
    normalized = str(draft_id or "").strip()
    if not normalized.startswith("draft_"):
        return None
    draft = _load_json_file(_batch_draft_manifest_path(normalized))
    if not draft:
        return None
    draft.setdefault("draftId", normalized)
    draft.setdefault("inputMode", "docs")
    draft.setdefault("docEntries", [{"docUrl": "", "outputName": "", "decorVideoId": ""}])
    draft.setdefault("audioEntries", [])
    draft.setdefault("sourceVideoLinks", "")
    draft.setdefault("selectedSourceRefs", [])
    draft.setdefault("clipTags", {})
    draft.setdefault("timelineConfig", _default_batch_draft(normalized)["timelineConfig"])
    return draft


def _save_batch_draft(draft_id: str, draft: dict):
    draft["draftId"] = draft_id
    draft["updatedAt"] = _utc_now()
    _save_json_file(_batch_draft_manifest_path(draft_id), draft)


def _draft_file_records(draft_id: str, kind: str) -> list[dict]:
    media_dir = _batch_draft_media_dir(draft_id, kind)
    if not os.path.isdir(media_dir):
        return []
    records = []
    for index, entry in enumerate(sorted(os.scandir(media_dir), key=lambda item: item.name)):
        if not entry.is_file():
            continue
        records.append(
            {
                "index": index,
                "name": entry.name.split("_", 1)[1] if "_" in entry.name else entry.name,
                "filename": entry.name,
                "relativePath": storage_relative_path(entry.path),
                "size": entry.stat().st_size,
            }
        )
    return records


def _serialize_batch_draft(draft: dict) -> dict:
    draft_id = draft["draftId"]
    payload = dict(draft)
    payload["imageFiles"] = _draft_file_records(draft_id, "images")
    payload["audioFiles"] = _draft_file_records(draft_id, "audio")
    return payload


def _source_set_display_name(batch_source_id: str, manifest: dict) -> str:
    explicit = str(manifest.get("display_name") or "").strip()
    if explicit:
        return explicit
    source_videos = manifest.get("source_videos") if isinstance(manifest.get("source_videos"), list) else []
    if source_videos:
        return os.path.basename(str(source_videos[0])) or batch_source_id
    review_clips = manifest.get("review_clips") if isinstance(manifest.get("review_clips"), list) else []
    if review_clips:
        return str(review_clips[0].get("source_name") or batch_source_id)
    return batch_source_id


def _serialize_source_set(batch_source_id: str, manifest: dict) -> dict:
    review_clips = [clip for clip in manifest.get("review_clips", []) if isinstance(clip, dict)]
    valid_count = 0
    source_names = []
    for clip in review_clips:
        if clip.get("source_name") and clip.get("source_name") not in source_names:
            source_names.append(clip.get("source_name"))
        relative_path = clip.get("relative_path") or clip.get("relativePath")
        if relative_path and os.path.isfile(storage_absolute_path(relative_path)):
            valid_count += 1
    return {
        "batchSourceId": batch_source_id,
        "displayName": _source_set_display_name(batch_source_id, manifest),
        "createdAt": manifest.get("created_at"),
        "sourceNames": source_names,
        "clipCount": len(review_clips),
        "validClipCount": valid_count,
        "downloadErrors": [str(error) for error in manifest.get("download_errors", []) if isinstance(error, str)],
    }


def _source_ref_id(origin: str, source_id: str, item_id: str) -> str:
    return f"{origin}:{source_id}:{item_id}" if origin == "batch_source" else f"library:{item_id}"


def _clip_from_batch_source(batch_source_id: str, clip: dict, legacy_id: bool = False) -> dict:
    source_clip_id = str(clip.get("id") or "")
    payload = dict(clip)
    payload["id"] = source_clip_id if legacy_id else _source_ref_id("batch_source", batch_source_id, source_clip_id)
    payload["relative_path"] = str(clip.get("relative_path") or clip.get("relativePath") or "").replace("\\", "/")
    payload["source_name"] = clip.get("source_name") or clip.get("sourceName")
    payload["origin"] = "batch_source"
    payload["batch_source_id"] = batch_source_id
    payload["source_clip_id"] = source_clip_id
    return payload


def _clip_from_library_asset(asset: dict) -> dict:
    asset_id = str(asset.get("asset_id") or asset.get("assetId") or "")
    return {
        "id": _source_ref_id("library", "", asset_id),
        "relative_path": str(asset.get("relative_path") or asset.get("relativePath") or "").replace("\\", "/"),
        "source_name": asset.get("source_name") or asset.get("sourceName"),
        "start": asset.get("start"),
        "end": asset.get("end"),
        "duration": asset.get("duration"),
        "origin": "library",
        "asset_id": asset_id,
        "source_clip_id": asset.get("source_clip_id"),
    }


def _parse_json_payload(raw_value, default):
    if raw_value in (None, ""):
        return default
    if isinstance(raw_value, (dict, list)):
        return raw_value
    try:
        return json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return default


def _normalize_timeline_config(raw_value) -> dict:
    timeline_config = {
        "mode": "batch_mixed_media",
        "firstPhaseSeconds": 300,
        "firstPhaseVideoCount": 3,
        "firstPhaseImageCount": 1,
        "afterPhaseImageEveryMin": 2,
        "afterPhaseImageEveryMax": 5,
    }
    parsed_timeline_config = _parse_json_payload(raw_value, {})
    if isinstance(parsed_timeline_config, dict):
        for key in (
            "firstPhaseSeconds",
            "firstPhaseVideoCount",
            "firstPhaseImageCount",
            "afterPhaseImageEveryMin",
            "afterPhaseImageEveryMax",
        ):
            if key in parsed_timeline_config:
                try:
                    timeline_config[key] = int(parsed_timeline_config[key])
                except (TypeError, ValueError):
                    pass
    timeline_config["firstPhaseSeconds"] = max(0, int(timeline_config["firstPhaseSeconds"]))
    timeline_config["firstPhaseVideoCount"] = max(1, int(timeline_config["firstPhaseVideoCount"]))
    timeline_config["firstPhaseImageCount"] = max(0, int(timeline_config["firstPhaseImageCount"]))
    timeline_config["afterPhaseImageEveryMin"] = max(1, int(timeline_config["afterPhaseImageEveryMin"]))
    timeline_config["afterPhaseImageEveryMax"] = max(
        timeline_config["afterPhaseImageEveryMin"],
        int(timeline_config["afterPhaseImageEveryMax"]),
    )
    return timeline_config


def _resolve_selected_source_pool(
    selected_source_refs_raw,
    clip_tags_raw,
    *,
    legacy_batch_source_id: str = "",
    legacy_selected_clip_ids_raw=None,
) -> tuple[dict | None, tuple | None]:
    library_index, library_assets = _load_library_assets()
    library_lookup = {asset["asset_id"]: asset for asset in library_assets if isinstance(asset.get("asset_id"), str)}
    selected_clips: list[dict] = []
    all_lookup: dict[str, dict] = {}
    source_download_errors: list[str] = []
    source_ids: list[str] = []
    missing_refs: list[str] = []
    batch_manifest_cache: dict[str, dict] = {}

    selected_source_refs = selected_source_refs_raw if isinstance(selected_source_refs_raw, list) else []
    legacy_selected_clip_ids = legacy_selected_clip_ids_raw if isinstance(legacy_selected_clip_ids_raw, list) else []

    if selected_source_refs:
        for ref in selected_source_refs:
            if not isinstance(ref, dict):
                continue
            origin = str(ref.get("origin") or "")
            if origin == "batch_source":
                batch_source_id = str(ref.get("batchSourceId") or ref.get("batch_source_id") or "")
                clip_id = str(ref.get("clipId") or ref.get("clip_id") or "")
                if not batch_source_id.startswith("batchsrc_") or not clip_id:
                    continue
                manifest = batch_manifest_cache.get(batch_source_id)
                if manifest is None:
                    manifest = _load_batch_source_manifest(batch_source_id)
                    if not manifest:
                        missing_refs.append(_source_ref_id("batch_source", batch_source_id, clip_id))
                        continue
                    batch_manifest_cache[batch_source_id] = manifest
                    source_download_errors.extend(
                        str(error) for error in manifest.get("download_errors", []) if isinstance(error, str)
                    )
                    if batch_source_id not in source_ids:
                        source_ids.append(batch_source_id)
                clip_lookup = {
                    str(clip.get("id")): clip
                    for clip in manifest.get("review_clips", [])
                    if isinstance(clip, dict) and clip.get("id")
                }
                source_clip = clip_lookup.get(clip_id)
                if not source_clip:
                    missing_refs.append(_source_ref_id("batch_source", batch_source_id, clip_id))
                    continue
                normalized = _clip_from_batch_source(batch_source_id, source_clip)
                all_lookup[normalized["id"]] = normalized
                selected_clips.append(normalized)
            elif origin == "library":
                asset_id = str(ref.get("assetId") or ref.get("asset_id") or "")
                asset = library_lookup.get(asset_id)
                if not asset:
                    missing_refs.append(_source_ref_id("library", "", asset_id))
                    continue
                normalized = _clip_from_library_asset(asset)
                all_lookup[normalized["id"]] = normalized
                selected_clips.append(normalized)
                if "library" not in source_ids:
                    source_ids.append("library")
    elif legacy_batch_source_id:
        manifest = _load_batch_source_manifest(legacy_batch_source_id)
        if not manifest:
            return None, _json_error("Batch source video khong tim thay hoac da het han.", code="batch_source_not_found")
        source_download_errors = [str(error) for error in manifest.get("download_errors", []) if isinstance(error, str)]
        source_ids = [legacy_batch_source_id]
        clip_lookup = {
            str(clip.get("id")): clip
            for clip in manifest.get("review_clips", [])
            if isinstance(clip, dict) and clip.get("id")
        }
        for clip_id in legacy_selected_clip_ids:
            if not isinstance(clip_id, str) or clip_id not in clip_lookup:
                continue
            normalized = _clip_from_batch_source(legacy_batch_source_id, clip_lookup[clip_id], legacy_id=True)
            all_lookup[normalized["id"]] = normalized
            selected_clips.append(normalized)

    if missing_refs:
        return None, _json_error(
            "Mot so source clip da chon khong tim thay.",
            code="source_clip_missing",
            details={"missingRefs": missing_refs},
        )

    missing_files = [
        clip["id"] for clip in selected_clips if not os.path.isfile(storage_absolute_path(clip["relative_path"]))
    ]
    if missing_files:
        return None, _json_error(
            "Mot so source clip da chon khong con ton tai.",
            code="source_clip_missing",
            details={"missingRefs": missing_files},
        )

    clip_tags = _parse_clip_tags_payload(clip_tags_raw if isinstance(clip_tags_raw, dict) else {}, set(all_lookup))
    return {
        "sourceVideoClips": selected_clips,
        "sourceVideoBatchSourceId": ",".join(source_ids),
        "sourceVideoDownloadErrors": source_download_errors,
        "sourceVideoClipTags": clip_tags,
    }, None


def _load_job(job_id: str) -> tuple[dict, dict]:
    try:
        manifest = load_job_manifest(job_id)
    except FileNotFoundError:
        abort(404)

    manifest.setdefault("selected_library_asset_ids", [])
    manifest.setdefault("clip_tags", {})
    manifest.setdefault("render_mode", _infer_render_mode(manifest))
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


def _tts_error_response(exc: TTSAudioError):
    return _json_error(str(exc), code=exc.code, details=exc.details or None)


@app.route("/api/jobs", methods=["POST"])
def create_job():
    audio_file = request.files.get("audio")
    existing_audio_relative_path = (request.form.get("existingAudioRelativePath") or "").strip()
    image_files = [file_obj for file_obj in request.files.getlist("images") if file_obj and file_obj.filename]
    uploaded_videos = [file_obj for file_obj in request.files.getlist("videos") if file_obj and file_obj.filename]
    youtube_links = _parse_links(request.form.get("youtube_links", ""))
    has_uploaded_audio = bool(audio_file and audio_file.filename)

    if not has_uploaded_audio and not existing_audio_relative_path:
        return _json_error("Cần chọn file audio trước khi xử lý.", code="audio_required")

    if has_uploaded_audio and not _allowed_file(audio_file.filename, Config.ALLOWED_AUDIO_EXTENSIONS):
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

    try:
        if has_uploaded_audio:
            audio_path = _save_uploaded_file(audio_file, dirs["audio"], "audio")
        else:
            audio_path = copy_generated_audio_to_job(existing_audio_relative_path, dirs["audio"])
    except TTSAudioError as exc:
        return _tts_error_response(exc)

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
    render_mode = "image_audio_only" if image_paths and not source_video_paths else "mixed_media" if image_paths else "video_only"

    manifest = {
        "job_id": job_id,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "audio_relative_path": storage_relative_path(audio_path),
        "audio_duration": round(audio_duration, 3),
        "render_mode": render_mode,
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


@app.route("/api/jobs/<job_id>/progress", methods=["GET"])
def job_progress(job_id: str):
    manifest, _dirs = _load_job(job_id)
    return jsonify({"progress": load_job_progress(job_id, manifest)})


def _image_only_chunk_count(total_segments: int, image_only_render: bool) -> int:
    if not total_segments:
        return 0
    chunk_limit = Config.IMAGE_ONLY_CHUNK_SEGMENT_LIMIT if image_only_render else Config.RENDER_CHUNK_SEGMENT_LIMIT
    return (total_segments + max(1, chunk_limit) - 1) // max(1, chunk_limit)


def _progress_extra(event: dict) -> dict:
    allowed_keys = (
        "cacheHits",
        "cacheMisses",
        "ffmpegPercent",
        "outTimeSeconds",
    )
    return {key: event[key] for key in allowed_keys if key in event}


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
    raw_decor_video_id = payload.get("decorVideoId") or ""
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
    image_paths = [storage_absolute_path(path) for path in manifest.get("image_paths", [])]

    init_job_progress(
        job_id,
        totals={
            "images": len(image_paths),
            "reviewClips": len(selected_paths),
            "libraryAssets": len(selected_library_paths),
            "segments": 0,
            "chunks": 0,
        },
        message="Bat dau render job.",
    )

    def _image_progress(event: dict):
        total_images = max(int(event.get("totalImages") or len(image_paths) or 1), 1)
        current_image = int(event.get("currentImage") or 0)
        update_job_progress(
            job_id,
            status="running",
            stage="image_processing",
            percent=5 + (30 * current_image / total_images),
            message=event.get("message"),
            level=event.get("level", "info"),
            totals={"images": total_images},
            current={"image": current_image},
            extra=_progress_extra(event),
        )

    def _render_progress(event: dict):
        stage = event.get("stage") or "render_video"
        total_segments = max(int(event.get("totalSegments") or 1), 1)
        current_segment = max(int(event.get("currentSegment") or 0), 0)
        total_chunks = max(int(event.get("totalChunks") or 1), 1)
        current_chunk = max(int(event.get("currentChunk") or 0), 0)
        ffmpeg_percent = event.get("ffmpegPercent")
        ffmpeg_fraction = 0.0
        if isinstance(ffmpeg_percent, (int, float)):
            ffmpeg_fraction = max(0.0, min(1.0, float(ffmpeg_percent) / 100))

        if stage == "join_chunks":
            percent = 90 + (8 * ffmpeg_fraction) if ffmpeg_percent is not None else 95
        elif stage == "finalize":
            percent = 98
        elif stage == "render_chunks" and total_chunks > 1:
            percent = 40 + (50 * (current_chunk + ffmpeg_fraction) / total_chunks)
        elif total_chunks > 1:
            percent = 40 + (50 * current_chunk / total_chunks)
        else:
            percent = 45 + (45 * current_segment / total_segments)

        update_job_progress(
            job_id,
            status="running",
            stage=stage,
            percent=percent,
            message=event.get("message"),
            level=event.get("level", "info"),
            totals={"segments": total_segments, "chunks": total_chunks},
            current={"segment": current_segment, "chunk": current_chunk},
            extra=_progress_extra(event),
        )

    tagged_clips = []
    for clip_id, tags in clip_tags.items():
        if not tags:
            continue
        tagged_clip = dict(clip_lookup[clip_id])
        tagged_clip["tags"] = tags
        tagged_clips.append(tagged_clip)
    upsert_library_assets(job_id, tagged_clips)

    clear_directory(dirs["img_clips"])

    animation_presets = load_active_animation_presets()
    transition_presets = load_active_transition_presets()
    img_processor = ImageProcessor(
        job_id,
        dirs,
        animation_presets=animation_presets,
        image_render_plan=manifest.get("image_render_plan"),
    )
    image_clips = img_processor.process_images(image_paths, progress_callback=_image_progress) if image_paths else []
    if image_paths:
        manifest["image_render_plan"] = img_processor.updated_image_render_plan
        save_job_manifest(job_id, manifest)

    if not selected_paths and not selected_library_paths and not image_clips:
        update_job_progress(
            job_id,
            status="failed",
            stage="failed",
            percent=0,
            message="Khong co clip video hoac anh hop le de render.",
            level="error",
        )
        return _json_error(
            "Cần chọn ít nhất một clip video hoặc cung cấp ảnh để render.",
            code="empty_render_selection",
            details={"currentPage": current_page},
        )

    update_job_progress(
        job_id,
        status="running",
        stage="timeline",
        percent=35,
        message="Dang tao timeline render.",
        current={"image": len(image_clips)},
    )
    timeline_composer = TimelineComposer(job_id, dirs)
    timeline_data = timeline_composer.create_timeline(
        selected_paths + selected_library_paths,
        image_clips,
        manifest["audio_duration"],
    )
    segments = timeline_data.get("segments", [])
    total_segments = len(segments)
    image_only_render = total_segments > 0 and all(segment.get("kind") == "image" for segment in segments)
    if image_only_render:
        total_chunks = _image_only_chunk_count(total_segments, image_only_render)
    else:
        total_chunks = 1 if total_segments else 0

    update_job_progress(
        job_id,
        status="running",
        stage="timeline",
        percent=40,
        message=f"Timeline san sang voi {total_segments} segment.",
        totals={"segments": total_segments, "chunks": total_chunks},
        current={"segment": 0, "chunk": 0},
        extra={"timelineMode": timeline_data.get("mode")},
    )

    renderer = Renderer(job_id, dirs, transition_presets=transition_presets)
    decor_video_path = get_decor_video_absolute_path(raw_decor_video_id) if raw_decor_video_id else None
    output_path = renderer.render(
        timeline_data,
        storage_absolute_path(manifest["audio_relative_path"]),
        manifest["audio_duration"],
        progress_callback=_render_progress,
        decor_video_path=decor_video_path,
    )
    if not output_path:
        update_job_progress(
            job_id,
            status="failed",
            stage="failed",
            message="Render that bai. Kiem tra logs/app.log.",
            level="error",
        )
        return _json_error("Render thất bại. Kiểm tra lại log.", status_code=500, code="render_failed")

    manifest["review_clips"] = selected_clips
    manifest["selected_clip_ids"] = selected_ids
    manifest["selected_library_asset_ids"] = selected_library_asset_ids
    manifest["clip_tags"] = clip_tags
    manifest["render_mode"] = _infer_render_mode(manifest)
    manifest["output_video"] = storage_relative_path(output_path)
    save_job_manifest(job_id, manifest)
    cleanup_review_video_assets(dirs, selected_paths)
    cleanup_job_files(job_id)
    update_job_progress(
        job_id,
        status="completed",
        stage="completed",
        percent=100,
        message="Render hoan tat.",
        current={"segment": total_segments, "chunk": total_chunks, "image": len(image_clips)},
        extra={"outputVideo": manifest["output_video"]},
    )

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


@app.route("/api/effects-library", methods=["GET"])
def effects_library():
    ensure_effect_library_generated()
    return jsonify(
        {
            "animations": [_serialize_effect_preset(preset) for preset in load_animation_presets()],
            "transitions": [_serialize_effect_preset(preset) for preset in load_transition_presets()],
        }
    )


@app.route("/api/effects-library/config", methods=["PUT"])
def update_effects_library_config():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("Body JSON không hợp lệ.", code="invalid_json")

    active_animation_ids = payload.get("activeAnimationIds", [])
    active_transition_ids = payload.get("activeTransitionIds", [])
    if not isinstance(active_animation_ids, list) or not isinstance(active_transition_ids, list):
        return _json_error("Payload config hiệu ứng không đúng định dạng.", code="invalid_effects_config")

    update_effect_config(
        [item for item in active_animation_ids if isinstance(item, str)],
        [item for item in active_transition_ids if isinstance(item, str)],
    )
    return jsonify(
        {
            "animations": [_serialize_effect_preset(preset) for preset in load_animation_presets()],
            "transitions": [_serialize_effect_preset(preset) for preset in load_transition_presets()],
        }
    )


@app.route("/api/audio-library", methods=["GET"])
def audio_library():
    return jsonify({"audios": list_generated_audio(), "defaultOutputName": next_default_audio_name()})


@app.route("/api/docs-to-audio", methods=["POST"])
def docs_to_audio():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("Body JSON khong hop le.", code="invalid_json")

    try:
        result = create_audio_from_google_doc(
            doc_url=str(payload.get("docUrl") or ""),
            output_name=payload.get("outputName") if isinstance(payload.get("outputName"), str) else None,
            voice_id=payload.get("voiceId") if isinstance(payload.get("voiceId"), str) else None,
            speed=float(payload["speed"]) if payload.get("speed") not in (None, "") else None,
            volume=float(payload["volume"]) if payload.get("volume") not in (None, "") else None,
        )
        return jsonify(result)
    except TTSAudioError as exc:
        return _tts_error_response(exc)
    except ValueError:
        return _json_error("Speed hoac volume khong hop le.", code="bad_request")
    except Exception as exc:
        logger.exception(f"Docs to audio failed: {exc}")
        return _json_error("Docs to audio failed.", status_code=500, code="tts_task_failed")


@app.route("/api/voices", methods=["GET"])
def voices():
    return jsonify({"voices": load_voices(), "defaultVoiceId": Config.TTS_DEFAULT_VOICE_ID})


@app.route("/api/voices/clone", methods=["POST"])
def clone_voice_route():
    audio_file = request.files.get("audio")
    voice_name = (request.form.get("voiceName") or "").strip()
    if not audio_file or not audio_file.filename:
        return _json_error("Can upload file audio mau.", code="audio_required")
    if not _allowed_file(audio_file.filename, Config.ALLOWED_AUDIO_EXTENSIONS):
        return _json_error("Dinh dang audio chua duoc ho tro.", code="invalid_audio_type")

    try:
        record = clone_voice(audio_file.read(), audio_file.filename, voice_name)
        return jsonify({"voice": record}), 201
    except TTSAudioError as exc:
        return _tts_error_response(exc)
    except Exception as exc:
        logger.exception(f"Clone voice failed: {exc}")
        return _json_error("Clone voice failed.", status_code=500, code="tts_task_failed")


@app.route("/api/decor-videos", methods=["GET"])
def decor_videos_list():
    return jsonify({"decorVideos": list_decor_videos()})


@app.route("/api/source-text-options", methods=["GET"])
def source_text_options():
    """Return all configured SOURCE_TEXT_N options from .env."""
    options = Config.get_source_text_options()
    default_key = next(iter(options), "")
    return jsonify({
        "sourceTextOptions": [
            {"key": key, "label": value}
            for key, value in options.items()
        ],
        "defaultKey": default_key,
    })

@app.route("/api/decor-videos", methods=["POST"])
def decor_videos_upload():
    video_file = request.files.get("video")
    display_name = (request.form.get("name") or "").strip()
    if not video_file or not video_file.filename:
        return _json_error("Can upload file video.", code="video_required")
    if not _allowed_file(video_file.filename, Config.ALLOWED_VIDEO_EXTENSIONS):
        return _json_error("Dinh dang video chua duoc ho tro.", code="invalid_video_type")

    try:
        record = add_decor_video(video_file.filename, video_file.read(), display_name)
        return jsonify({"decorVideo": record}), 201
    except ValueError as exc:
        return _json_error(str(exc), status_code=400, code="video_too_short")
    except Exception as exc:
        logger.exception(f"Upload decor video failed: {exc}")
        return _json_error("Upload decor video that bai.", status_code=500, code="upload_failed")


@app.route("/api/decor-videos/<decor_id>", methods=["DELETE"])
def decor_videos_delete(decor_id: str):
    if delete_decor_video(decor_id):
        return jsonify({"deleted": True})
    return _json_error("Decor video khong tim thay.", status_code=404, code="not_found")


@app.route("/api/batch-pipeline/source-videos", methods=["POST"])
def prepare_batch_source_videos():
    youtube_links = _parse_links(request.form.get("youtube_links", ""))
    uploaded_videos = [file_obj for file_obj in request.files.getlist("videos") if file_obj and file_obj.filename]

    if not youtube_links and not uploaded_videos:
        return _json_error("Can nhap it nhat 1 link video hoac upload video nguon.", code="empty_source_videos")

    invalid_videos = [
        file_obj.filename for file_obj in uploaded_videos if not _allowed_file(file_obj.filename, Config.ALLOWED_VIDEO_EXTENSIONS)
    ]
    if invalid_videos:
        return _json_error(
            "Co file video khong dung dinh dang.",
            code="invalid_video_type",
            details={"invalidVideos": invalid_videos},
        )

    batch_source_id = f"batchsrc_{uuid.uuid4().hex[:8]}"
    source_root = _batch_source_root(batch_source_id)
    raw_videos_dir = os.path.join(source_root, "raw_videos")
    vid_clips_dir = os.path.join(source_root, "vid_clips")
    os.makedirs(raw_videos_dir, exist_ok=True)
    os.makedirs(vid_clips_dir, exist_ok=True)

    try:
        uploaded_video_paths = [
            _save_uploaded_file(file_obj, raw_videos_dir, f"upload_{index}") for index, file_obj in enumerate(uploaded_videos)
        ]
        downloaded_video_paths, download_errors = _download_youtube_links(youtube_links, raw_videos_dir)
        source_video_paths = uploaded_video_paths + downloaded_video_paths

        video_processor = VideoProcessor(batch_source_id, {"vid_clips": vid_clips_dir})
        review_clips = video_processor.create_review_clips(
            source_video_paths,
            min_duration=Config.VID_CLIP_MIN_DURATION,
            max_duration=Config.VID_CLIP_MAX_DURATION,
            randomize_duration=False,
            seed=batch_source_id,
        )
    except Exception as exc:
        shutil.rmtree(source_root, ignore_errors=True)
        logger.exception(f"Prepare batch source videos failed: {exc}")
        return _json_error("Khong the tai/cat source video.", status_code=500, code="batch_source_prepare_failed")

    if not review_clips:
        manifest = {
            "batch_source_id": batch_source_id,
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "source_videos": [storage_relative_path(path) for path in source_video_paths],
            "download_errors": download_errors,
            "review_clips": [],
        }
        _save_batch_source_manifest(batch_source_id, manifest)
        return _json_error(
            "Khong tao duoc clip review nao tu source video.",
            code="empty_review_clips",
            details={"batchSourceId": batch_source_id, "downloadErrors": download_errors},
        )

    manifest = {
        "batch_source_id": batch_source_id,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_videos": [storage_relative_path(path) for path in source_video_paths],
        "download_errors": download_errors,
        "review_clips": review_clips,
        "clip_duration_min": Config.VID_CLIP_MIN_DURATION,
        "clip_duration_max": Config.VID_CLIP_MAX_DURATION,
    }
    _save_batch_source_manifest(batch_source_id, manifest)
    library_index, _assets = _load_library_assets()

    return jsonify(
        {
            "batchSourceId": batch_source_id,
            "reviewClips": [_serialize_clip(clip) for clip in review_clips],
            "downloadErrors": download_errors,
            "availableTags": collect_library_tags(library_index),
        }
    ), 201


@app.route("/api/batch-pipeline/source-sets", methods=["GET"])
def batch_source_sets():
    source_root = os.path.join(Config.STORAGE_DIR, "batch_sources")
    source_sets = []
    if os.path.isdir(source_root):
        for entry in os.scandir(source_root):
            if not entry.is_dir() or not entry.name.startswith("batchsrc_"):
                continue
            manifest = _load_batch_source_manifest(entry.name)
            if manifest:
                source_sets.append(_serialize_source_set(entry.name, manifest))
    source_sets.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    return jsonify({"sourceSets": source_sets})


@app.route("/api/batch-pipeline/source-sets/<batch_source_id>", methods=["GET", "PATCH"])
def batch_source_set_detail(batch_source_id: str):
    manifest = _load_batch_source_manifest(batch_source_id)
    if not manifest:
        return _json_error("Batch source video khong tim thay.", status_code=404, code="not_found")

    if request.method == "PATCH":
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _json_error("Body JSON khong hop le.", code="invalid_json")
        display_name = str(payload.get("displayName") or "").strip()
        if display_name:
            manifest["display_name"] = display_name[:160]
        else:
            manifest.pop("display_name", None)
        _save_batch_source_manifest(batch_source_id, manifest)

    library_index, _assets = _load_library_assets()
    return jsonify(
        {
            "sourceSet": _serialize_source_set(batch_source_id, manifest),
            "reviewClips": [_serialize_clip(clip) for clip in manifest.get("review_clips", []) if isinstance(clip, dict)],
            "availableTags": collect_library_tags(library_index),
        }
    )


@app.route("/api/batch-pipeline/source-sets/<batch_source_id>/clips/<clip_id>", methods=["DELETE"])
def delete_batch_source_clip(batch_source_id: str, clip_id: str):
    manifest = _load_batch_source_manifest(batch_source_id)
    if not manifest:
        return _json_error("Batch source video khong tim thay.", status_code=404, code="not_found")

    review_clips = [clip for clip in manifest.get("review_clips", []) if isinstance(clip, dict)]
    clip_to_delete = None
    remaining_clips = []
    for clip in review_clips:
        if str(clip.get("id")) == clip_id:
            clip_to_delete = clip
        else:
            remaining_clips.append(clip)

    if not clip_to_delete:
        return _json_error("Clip khong tim thay trong batch source.", status_code=404, code="clip_not_found")

    relative_path = str(clip_to_delete.get("relative_path") or clip_to_delete.get("relativePath") or "")
    if relative_path:
        abs_path = storage_absolute_path(relative_path)
        if os.path.isfile(abs_path):
            try:
                os.remove(abs_path)
                logger.info(f"Deleted clip file: {abs_path}")
            except OSError as exc:
                logger.warning(f"Failed to delete clip file {abs_path}: {exc}")

    manifest["review_clips"] = remaining_clips
    _save_batch_source_manifest(batch_source_id, manifest)

    return jsonify({"deleted": True, "clipId": clip_id, "batchSourceId": batch_source_id, "remainingClips": len(remaining_clips)})


@app.route("/api/batch-pipeline/library-sources", methods=["GET"])
def batch_library_sources():
    library_index, library_assets = _load_library_assets()
    selected_tags = normalize_tags(request.args.getlist("tags") or request.args.getlist("tag"))
    selected_tag_set = set(selected_tags)
    if selected_tag_set:
        filtered_assets = [
            asset for asset in library_assets if set(normalize_tags(asset.get("tags", []))) & selected_tag_set
        ]
    else:
        filtered_assets = library_assets
    return jsonify(
        {
            "clips": [_serialize_clip(_clip_from_library_asset(asset)) for asset in filtered_assets],
            "assets": [_serialize_asset(asset) for asset in filtered_assets],
            "availableTags": collect_library_tags(library_index),
            "selectedTags": selected_tags,
            "totalAssetCount": len(library_assets),
        }
    )


@app.route("/api/batch-pipeline/drafts", methods=["POST"])
def create_batch_draft():
    draft_id = f"draft_{uuid.uuid4().hex[:8]}"
    os.makedirs(_batch_draft_media_dir(draft_id, "images"), exist_ok=True)
    os.makedirs(_batch_draft_media_dir(draft_id, "audio"), exist_ok=True)
    draft = _default_batch_draft(draft_id)
    _save_batch_draft(draft_id, draft)
    return jsonify(_serialize_batch_draft(draft)), 201


@app.route("/api/batch-pipeline/drafts/<draft_id>", methods=["GET", "PATCH"])
def batch_draft_detail(draft_id: str):
    draft = _load_batch_draft(draft_id)
    if not draft:
        return _json_error("Batch draft khong tim thay.", status_code=404, code="not_found")

    if request.method == "PATCH":
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _json_error("Body JSON khong hop le.", code="invalid_json")
        for key in (
            "inputMode",
            "voiceId",
            "speed",
            "volume",
            "docEntries",
            "audioEntries",
            "sourceVideoLinks",
            "selectedSourceRefs",
            "clipTags",
            "timelineConfig",
        ):
            if key in payload:
                draft[key] = payload[key]
        if _batch_input_mode(draft.get("inputMode")):
            draft["inputMode"] = _batch_input_mode(draft.get("inputMode"))
        else:
            draft["inputMode"] = "docs"
        _save_batch_draft(draft_id, draft)

    return jsonify(_serialize_batch_draft(draft))


@app.route("/api/batch-pipeline/drafts/<draft_id>/files", methods=["POST"])
def batch_draft_files(draft_id: str):
    draft = _load_batch_draft(draft_id)
    if not draft:
        return _json_error("Batch draft khong tim thay.", status_code=404, code="not_found")

    file_kind = str(request.form.get("kind") or "").strip()
    if file_kind not in {"images", "audio"}:
        return _json_error("kind phai la images hoac audio.", code="bad_request")

    field_name = "images" if file_kind == "images" else "audioFiles"
    allowed_extensions = Config.ALLOWED_IMAGE_EXTENSIONS if file_kind == "images" else Config.ALLOWED_AUDIO_EXTENSIONS
    upload_files = [file_obj for file_obj in request.files.getlist(field_name) if file_obj and file_obj.filename]
    invalid_files = [
        file_obj.filename for file_obj in upload_files if not _allowed_file(file_obj.filename, allowed_extensions)
    ]
    if invalid_files:
        return _json_error(
            "Co file upload khong dung dinh dang.",
            code="invalid_file_type",
            details={"invalidFiles": invalid_files},
        )

    media_dir = _batch_draft_media_dir(draft_id, "audio" if file_kind == "audio" else "images")
    clear_directory(media_dir)
    os.makedirs(media_dir, exist_ok=True)
    for index, file_obj in enumerate(upload_files):
        original_name = file_obj.filename or f"{file_kind}_{index + 1}"
        safe_name = secure_filename(original_name) or f"{file_kind}_{index + 1}"
        file_obj.save(os.path.join(media_dir, f"{index:04d}_{safe_name}"))

    if file_kind == "audio":
        draft["audioEntries"] = [
            {
                "outputName": os.path.splitext(record["name"])[0] or f"audio-{index + 1}",
                "decorVideoId": "",
                "sourceText": next(iter(Config.get_source_text_options()), "SOURCE_TEXT_1"),
                "sourceAudioName": record["name"],
                "audioFileIndex": index,
            }
            for index, record in enumerate(_draft_file_records(draft_id, "audio"))
        ]
    _save_batch_draft(draft_id, draft)
    return jsonify(_serialize_batch_draft(draft))


def _start_batch_from_saved_files(
    *,
    input_mode: str,
    items: list[dict],
    shared_image_source_paths: list[str],
    audio_source_paths: list[str],
    voice_id: str,
    speed: float,
    volume: float,
    source_pool: dict,
    timeline_config: dict,
):
    import threading

    cleanup_expired_batch_retry_artifacts()
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    batch_root_dir = os.path.join(Config.STORAGE_DIR, "batch", batch_id)
    batch_images_dir = os.path.join(batch_root_dir, "shared_images")
    batch_uploaded_audio_dir = os.path.join(batch_root_dir, "uploaded_audio")
    os.makedirs(batch_images_dir, exist_ok=True)
    if input_mode == "audio_upload":
        os.makedirs(batch_uploaded_audio_dir, exist_ok=True)

    shared_image_paths = []
    try:
        for idx, source_path in enumerate(shared_image_source_paths):
            filename = secure_filename(os.path.basename(source_path)) or f"image_{idx}.jpg"
            target_path = os.path.join(batch_images_dir, f"{idx:04d}_{filename}")
            shutil.copy2(source_path, target_path)
            shared_image_paths.append(target_path)

        if input_mode == "audio_upload":
            for idx, source_path in enumerate(audio_source_paths):
                filename = secure_filename(os.path.basename(source_path)) or f"audio_{idx + 1}.mp3"
                target_path = os.path.join(batch_uploaded_audio_dir, f"{idx:04d}_{filename}")
                shutil.copy2(source_path, target_path)
                if not validate_audio(target_path):
                    shutil.rmtree(batch_root_dir, ignore_errors=True)
                    return _json_error(
                        "Co file audio khong hop le hoac khong doc duoc.",
                        code="invalid_audio",
                        details={"invalidAudioFiles": [os.path.basename(source_path)]},
                    )
                items[idx]["sourceAudioName"] = items[idx].get("sourceAudioName") or os.path.basename(source_path)
                items[idx]["sourceAudioRelativePath"] = storage_relative_path(target_path)
    except Exception:
        shutil.rmtree(batch_root_dir, ignore_errors=True)
        raise

    tagged_source_clips = []
    source_clip_lookup_for_tags = {
        source_clip.get("id"): source_clip
        for source_clip in source_pool["sourceVideoClips"]
        if isinstance(source_clip, dict) and source_clip.get("origin") != "library"
    }
    for clip_id, tags in source_pool["sourceVideoClipTags"].items():
        clip = source_clip_lookup_for_tags.get(clip_id)
        if clip and tags:
            tagged_clip = dict(clip)
            tagged_clip["tags"] = tags
            tagged_source_clips.append(tagged_clip)
    upsert_library_assets(batch_id, tagged_source_clips)

    runner = BatchPipelineRunner(
        batch_id=batch_id,
        items=items,
        shared_image_paths=shared_image_paths,
        voice_id=voice_id,
        speed=speed,
        volume=volume,
        input_mode=input_mode,
        source_video_clips=source_pool["sourceVideoClips"],
        source_video_batch_source_id=source_pool["sourceVideoBatchSourceId"],
        source_video_download_errors=source_pool["sourceVideoDownloadErrors"],
        source_video_clip_tags=source_pool["sourceVideoClipTags"],
        timeline_config=timeline_config,
    )

    thread = threading.Thread(target=runner.run_batch, daemon=True)
    thread.start()
    logger.info(f"Batch pipeline started: {batch_id} with {len(items)} items")
    return jsonify(
        {
            "batchId": batch_id,
            "totalUrls": len(items),
            "inputMode": input_mode,
            "message": "Batch pipeline started",
        }
    ), 201


@app.route("/api/batch-pipeline/drafts/<draft_id>/submit", methods=["POST"])
def submit_batch_draft(draft_id: str):
    draft = _load_batch_draft(draft_id)
    if not draft:
        return _json_error("Batch draft khong tim thay.", status_code=404, code="not_found")

    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        for key in (
            "inputMode",
            "voiceId",
            "speed",
            "volume",
            "docEntries",
            "audioEntries",
            "sourceVideoLinks",
            "selectedSourceRefs",
            "clipTags",
            "timelineConfig",
        ):
            if key in payload:
                draft[key] = payload[key]
        _save_batch_draft(draft_id, draft)

    input_mode = _batch_input_mode(draft.get("inputMode"))
    if not input_mode:
        return _json_error("Input mode khong hop le.", code="bad_request")

    items = draft.get("docEntries") if input_mode == "docs" else draft.get("audioEntries")
    if not isinstance(items, list) or not items:
        return _json_error("Can it nhat 1 item trong batch.", code="empty_items")
    items = [dict(item) for item in items if isinstance(item, dict)]

    output_names_seen: set[str] = set()
    for idx, item in enumerate(items):
        output_name = (item.get("outputName") or "").strip()
        if not output_name:
            return _json_error(f"Item {idx}: outputName la bat buoc.", code="missing_output_name")
        if output_name in output_names_seen:
            return _json_error(
                f"Ten output '{output_name}' bi trung lap. Moi ten output phai la duy nhat.",
                code="duplicate_output_name",
            )
        output_names_seen.add(output_name)
        item["outputName"] = output_name
        item["decorVideoId"] = (item.get("decorVideoId") or "").strip()
        # Resolve per-item source text: key like "SOURCE_TEXT_1" -> actual value
        source_text_key = (item.get("sourceText") or "").strip()
        if source_text_key:
            all_source_texts = Config.get_source_text_options()
            item["sourceText"] = all_source_texts.get(source_text_key, source_text_key)
        else:
            item["sourceText"] = Config.SOURCE_TEXT
        if input_mode == "docs":
            doc_url = (item.get("docUrl") or "").strip()
            if not doc_url:
                return _json_error(f"Item {idx}: docUrl la bat buoc.", code="missing_doc_url")
            item["sourceType"] = "doc_url"
            item["docUrl"] = doc_url
            item["sourceAudioName"] = None
            item["sourceAudioRelativePath"] = None
            item["audioFileIndex"] = None
        else:
            item["sourceType"] = "uploaded_audio"
            item["docUrl"] = None
            item["sourceAudioName"] = (item.get("sourceAudioName") or f"audio_{idx + 1}").strip()
            item["sourceAudioRelativePath"] = None
            item["audioFileIndex"] = idx

    image_paths = [
        entry.path
        for entry in sorted(os.scandir(_batch_draft_media_dir(draft_id, "images")), key=lambda item: item.name)
        if entry.is_file()
    ] if os.path.isdir(_batch_draft_media_dir(draft_id, "images")) else []
    if not image_paths:
        return _json_error("Can upload it nhat 1 anh nguon.", code="images_required")

    audio_paths = [
        entry.path
        for entry in sorted(os.scandir(_batch_draft_media_dir(draft_id, "audio")), key=lambda item: item.name)
        if entry.is_file()
    ] if os.path.isdir(_batch_draft_media_dir(draft_id, "audio")) else []
    if input_mode == "audio_upload" and not audio_paths:
        return _json_error("Can upload it nhat 1 file audio.", code="audio_required")
    if input_mode == "audio_upload" and len(audio_paths) != len(items):
        return _json_error(
            "So file audio phai khop so item trong batch.",
            code="batch_audio_count_mismatch",
            details={"audioFiles": len(audio_paths), "items": len(items)},
        )

    source_pool, source_pool_error = _resolve_selected_source_pool(
        draft.get("selectedSourceRefs") if isinstance(draft.get("selectedSourceRefs"), list) else [],
        draft.get("clipTags") if isinstance(draft.get("clipTags"), dict) else {},
    )
    if source_pool_error:
        return source_pool_error
    source_pool = source_pool or {
        "sourceVideoClips": [],
        "sourceVideoBatchSourceId": "",
        "sourceVideoDownloadErrors": [],
        "sourceVideoClipTags": {},
    }

    try:
        speed = float(draft.get("speed") or 1)
        volume = float(draft.get("volume") or 1)
    except (TypeError, ValueError):
        return _json_error("Speed hoac volume khong hop le.", code="bad_request")

    return _start_batch_from_saved_files(
        input_mode=input_mode,
        items=items,
        shared_image_source_paths=image_paths,
        audio_source_paths=audio_paths,
        voice_id=str(draft.get("voiceId") or ""),
        speed=speed,
        volume=volume,
        source_pool=source_pool,
        timeline_config=_normalize_timeline_config(draft.get("timelineConfig")),
    )


@app.route("/api/batch-pipeline", methods=["POST"])
def start_batch_pipeline():
    import threading
    cleanup_expired_batch_retry_artifacts()
    input_mode = _batch_input_mode(request.form.get("inputMode"))
    if not input_mode:
        return _json_error("Input mode khong hop le.", code="bad_request")

    raw_items = request.form.get("items", "")
    try:
        items = json.loads(raw_items)
    except (json.JSONDecodeError, TypeError):
        return _json_error("Truong 'items' JSON khong hop le.", code="invalid_json")

    if not isinstance(items, list) or not items:
        return _json_error("Can it nhat 1 item trong batch.", code="empty_items")

    legacy_batch_source_id = (request.form.get("batchSourceId") or "").strip()
    selected_source_refs = _parse_json_payload(request.form.get("selectedSourceRefs"), [])
    legacy_selected_clip_ids = _parse_json_payload(request.form.get("selectedClipIds"), [])
    source_clip_tags_payload = _parse_json_payload(request.form.get("clipTags"), {})
    source_pool, source_pool_error = _resolve_selected_source_pool(
        selected_source_refs,
        source_clip_tags_payload,
        legacy_batch_source_id=legacy_batch_source_id,
        legacy_selected_clip_ids_raw=legacy_selected_clip_ids,
    )
    if source_pool_error:
        return source_pool_error
    source_pool = source_pool or {
        "sourceVideoClips": [],
        "sourceVideoBatchSourceId": "",
        "sourceVideoDownloadErrors": [],
        "sourceVideoClipTags": {},
    }

    timeline_config = _normalize_timeline_config(request.form.get("timelineConfig"))

    # Validate each item
    output_names_seen: set[str] = set()
    audio_files = [file_obj for file_obj in request.files.getlist("audioFiles") if file_obj and file_obj.filename]
    if input_mode == "audio_upload" and not audio_files:
        return _json_error("Can upload it nhat 1 file audio.", code="audio_required")
    if input_mode == "audio_upload" and len(audio_files) != len(items):
        return _json_error(
            "So file audio phai khop so item trong batch.",
            code="batch_audio_count_mismatch",
            details={"audioFiles": len(audio_files), "items": len(items)},
        )

    invalid_audio_files = [
        file_obj.filename for file_obj in audio_files if not _allowed_file(file_obj.filename, Config.ALLOWED_AUDIO_EXTENSIONS)
    ]
    if invalid_audio_files:
        return _json_error(
            "Co file audio khong dung dinh dang.",
            code="invalid_audio_type",
            details={"invalidAudioFiles": invalid_audio_files},
        )

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            return _json_error(f"Item {idx} khong hop le.", code="invalid_item")
        output_name = (item.get("outputName") or "").strip()
        if not output_name:
            return _json_error(f"Item {idx}: outputName la bat buoc.", code="missing_output_name")
        if output_name in output_names_seen:
            return _json_error(
                f"Ten output '{output_name}' bi trung lap. Moi ten output phai la duy nhat.",
                code="duplicate_output_name",
        )
        output_names_seen.add(output_name)
        item["outputName"] = output_name
        item["decorVideoId"] = (item.get("decorVideoId") or "").strip()
        # Resolve per-item source text
        source_text_key = (item.get("sourceText") or "").strip()
        if source_text_key:
            all_source_texts = Config.get_source_text_options()
            item["sourceText"] = all_source_texts.get(source_text_key, source_text_key)
        else:
            item["sourceText"] = Config.SOURCE_TEXT
        if input_mode == "docs":
            doc_url = (item.get("docUrl") or "").strip()
            if not doc_url:
                return _json_error(f"Item {idx}: docUrl la bat buoc.", code="missing_doc_url")
            item["sourceType"] = "doc_url"
            item["docUrl"] = doc_url
            item["sourceAudioName"] = None
            item["sourceAudioRelativePath"] = None
            item["audioFileIndex"] = None
        else:
            source_type = str(item.get("sourceType") or "uploaded_audio")
            if source_type != "uploaded_audio":
                return _json_error(f"Item {idx}: sourceType khong hop le cho audio upload.", code="invalid_item")
            try:
                audio_file_index = int(item.get("audioFileIndex") if item.get("audioFileIndex") is not None else idx)
            except (TypeError, ValueError):
                return _json_error(f"Item {idx}: audioFileIndex khong hop le.", code="invalid_item")
            if audio_file_index != idx:
                return _json_error(
                    f"Item {idx}: audioFileIndex phai trung thu tu file audio upload.",
                    code="batch_audio_index_mismatch",
                )
            source_audio_name = (item.get("sourceAudioName") or audio_files[idx].filename or "").strip()
            item["sourceType"] = "uploaded_audio"
            item["docUrl"] = None
            item["sourceAudioName"] = source_audio_name or (audio_files[idx].filename or f"audio_{idx + 1}")
            item["sourceAudioRelativePath"] = None
            item["audioFileIndex"] = audio_file_index

    # Validate images
    image_files = [f for f in request.files.getlist("images") if f and f.filename]
    if not image_files:
        return _json_error("Can upload it nhat 1 anh nguon.", code="images_required")

    invalid_images = [
        f.filename for f in image_files if not _allowed_file(f.filename, Config.ALLOWED_IMAGE_EXTENSIONS)
    ]
    if invalid_images:
        return _json_error(
            "Co file anh khong dung dinh dang.",
            code="invalid_image_type",
            details={"invalidImages": invalid_images},
        )

    voice_id = (request.form.get("voiceId") or "").strip()
    speed = 1.0
    volume = 1.0
    if input_mode == "docs":
        try:
            speed = float(request.form.get("speed") or 1)
            volume = float(request.form.get("volume") or 1)
        except (ValueError, TypeError):
            return _json_error("Speed hoac volume khong hop le.", code="bad_request")
    else:
        voice_id = ""

    # Create batch dir and save shared images
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    batch_root_dir = os.path.join(Config.STORAGE_DIR, "batch", batch_id)
    batch_images_dir = os.path.join(batch_root_dir, "shared_images")
    batch_uploaded_audio_dir = os.path.join(batch_root_dir, "uploaded_audio")
    os.makedirs(batch_images_dir, exist_ok=True)
    if input_mode == "audio_upload":
        os.makedirs(batch_uploaded_audio_dir, exist_ok=True)

    shared_image_paths = []
    for idx, img_file in enumerate(image_files):
        filename = secure_filename(img_file.filename or f"image_{idx}.jpg")
        filepath = os.path.join(batch_images_dir, f"{idx:04d}_{filename}")
        img_file.save(filepath)
        shared_image_paths.append(filepath)

    invalid_saved_audio_files: list[str] = []
    try:
        if input_mode == "audio_upload":
            for idx, audio_file in enumerate(audio_files):
                original_name = audio_file.filename or f"audio_{idx + 1}"
                safe_name = secure_filename(original_name) or f"audio_{idx + 1}.mp3"
                filepath = os.path.join(batch_uploaded_audio_dir, f"{idx:04d}_{safe_name}")
                audio_file.save(filepath)
                if not validate_audio(filepath):
                    invalid_saved_audio_files.append(original_name)
                    continue
                items[idx]["sourceAudioName"] = items[idx].get("sourceAudioName") or original_name
                items[idx]["sourceAudioRelativePath"] = storage_relative_path(filepath)

        if invalid_saved_audio_files:
            shutil.rmtree(batch_root_dir, ignore_errors=True)
            return _json_error(
                "Co file audio khong hop le hoac khong doc duoc.",
                code="invalid_audio",
                details={"invalidAudioFiles": invalid_saved_audio_files},
            )
    except Exception:
        shutil.rmtree(batch_root_dir, ignore_errors=True)
        raise

    tagged_source_clips = []
    source_clip_lookup_for_tags = {
        source_clip.get("id"): source_clip
        for source_clip in source_pool["sourceVideoClips"]
        if isinstance(source_clip, dict) and source_clip.get("origin") != "library"
    }
    for clip_id, tags in source_pool["sourceVideoClipTags"].items():
        if not tags:
            continue
        clip = source_clip_lookup_for_tags.get(clip_id)
        if clip:
            tagged_clip = dict(clip)
            tagged_clip["tags"] = tags
            tagged_source_clips.append(tagged_clip)
    upsert_library_assets(batch_id, tagged_source_clips)

    runner = BatchPipelineRunner(
        batch_id=batch_id,
        items=items,
        shared_image_paths=shared_image_paths,
        voice_id=voice_id,
        speed=speed,
        volume=volume,
        input_mode=input_mode,
        source_video_clips=source_pool["sourceVideoClips"],
        source_video_batch_source_id=source_pool["sourceVideoBatchSourceId"],
        source_video_download_errors=source_pool["sourceVideoDownloadErrors"],
        source_video_clip_tags=source_pool["sourceVideoClipTags"],
        timeline_config=timeline_config,
    )

    thread = threading.Thread(target=runner.run_batch, daemon=True)
    thread.start()
    logger.info(f"Batch pipeline started: {batch_id} with {len(items)} items")

    return jsonify(
        {
            "batchId": batch_id,
            "totalUrls": len(items),
            "inputMode": input_mode,
            "message": "Batch pipeline started",
        }
    ), 201


@app.route("/api/batch-pipeline/<batch_id>/retry-failed", methods=["POST"])
def retry_failed_batch_pipeline(batch_id: str):
    import threading

    cleanup_expired_batch_retry_artifacts()
    progress = load_batch_progress(batch_id)
    if not progress:
        return _json_error("Batch pipeline khong tim thay.", status_code=404, code="not_found")

    try:
        runner = BatchPipelineRunner.from_saved_batch(batch_id)
        target_indexes = runner.retry_failed_items()
    except RuntimeError as exc:
        return _json_error(str(exc), status_code=409, code="batch_retry_not_allowed")

    thread = threading.Thread(target=runner.run_batch, kwargs={"target_indexes": target_indexes}, daemon=True)
    thread.start()
    logger.info(f"[BatchPipeline] Retry all failed started for {batch_id}: {len(target_indexes)} items")
    return jsonify(
        {
            "batchId": batch_id,
            "failedUrls": len(target_indexes),
            "message": "Retry all failed started",
        }
    )


@app.route("/api/batch-pipeline/<batch_id>/progress", methods=["GET"])
def batch_pipeline_progress(batch_id: str):
    progress = load_batch_progress(batch_id)
    if not progress:
        return _json_error("Batch pipeline khong tim thay.", status_code=404, code="not_found")
    return jsonify(progress)


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
