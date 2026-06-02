"""Story TV noise overlay storage and preprocessing helpers."""

import json
import os
import shutil
import threading
import uuid
from datetime import datetime

from werkzeug.utils import secure_filename

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

_index_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat()


def _overlay_dir() -> str:
    os.makedirs(Config.STORY_TV_NOISE_OVERLAY_DIR, exist_ok=True)
    return Config.STORY_TV_NOISE_OVERLAY_DIR


def _index_path() -> str:
    return os.path.join(_overlay_dir(), "index.json")


def _relative(filename: str) -> str:
    return f"story_tv_noise_overlays/{filename}" if filename else ""


def _absolute(filename: str) -> str:
    return os.path.join(_overlay_dir(), filename)


def _load_index_unlocked() -> dict:
    path = _index_path()
    if not os.path.isfile(path):
        return {"overlays": []}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else {"overlays": []}
    except (OSError, json.JSONDecodeError):
        return {"overlays": []}


def _save_index_unlocked(data: dict):
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2, ensure_ascii=False)
    shutil.move(tmp, path)


def load_tv_noise_index() -> dict:
    with _index_lock:
        return _load_index_unlocked()


def save_tv_noise_index(data: dict):
    with _index_lock:
        _save_index_unlocked(data)


def _next_order(overlays: list[dict]) -> int:
    values = []
    for item in overlays:
        try:
            values.append(int(item.get("order", 0)))
        except (TypeError, ValueError):
            continue
    return (max(values) + 1) if values else 1


def _float_setting(record: dict, key: str, default: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    try:
        value = float(record.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def processed_abs_path(record: dict) -> str | None:
    filename = record.get("processedFilename")
    if not filename:
        return None
    path = _absolute(str(filename))
    return path if os.path.isfile(path) else None


def source_abs_path(record: dict) -> str | None:
    filename = record.get("filename")
    if not filename:
        return None
    path = _absolute(str(filename))
    return path if os.path.isfile(path) else None


def get_active_tv_noise_overlays() -> list[dict]:
    overlays = load_tv_noise_index().get("overlays", [])
    active = [
        item
        for item in overlays
        if item.get("enabled", True)
        and item.get("status") == "ready"
        and processed_abs_path(item)
    ]
    return sorted(active, key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")))


def get_tv_noise_overlay(overlay_id: str) -> dict | None:
    overlays = load_tv_noise_index().get("overlays", [])
    return next((item for item in overlays if item.get("id") == overlay_id), None)


def _target_size() -> tuple[int, int]:
    width, height = Config.TARGET_RESOLUTION.split("x", 1)
    return int(width), int(height)


def preprocess_tv_noise_overlay(source_path: str, record: dict) -> str:
    overlay_id = str(record["id"])
    processed_filename = f"{overlay_id}_alpha.mov"
    output_path = _absolute(processed_filename)
    width, height = _target_size()
    tolerance = _float_setting(record, "tolerance", Config.STORY_TV_NOISE_TOLERANCE)
    softness = _float_setting(record, "softness", Config.STORY_TV_NOISE_SOFTNESS)
    opacity = _float_setting(record, "opacity", Config.STORY_TV_NOISE_OPACITY)
    target_fps = max(1, int(Config.TARGET_FPS))

    filter_str = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"fps={target_fps},"
        "format=rgba,"
        f"lumakey=0:{tolerance}:{softness},"
        f"colorchannelmixer=aa={opacity},"
        "format=argb"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        source_path,
        "-vf",
        filter_str,
        "-an",
        "-c:v",
        "qtrle",
        output_path,
    ]

    logger.info(f"[StoryTVNoise] Preprocessing alpha MOV: {output_path}")
    if not FFmpegHelper.run_command(cmd):
        raise RuntimeError("FFmpeg failed to preprocess TV noise overlay.")
    if not os.path.isfile(output_path):
        raise RuntimeError("Processed TV noise overlay was not created.")
    return processed_filename


def create_tv_noise_overlay(file_storage) -> dict:
    if not file_storage or not file_storage.filename:
        raise ValueError("No TV noise file provided.")

    overlay_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(file_storage.filename)
    filename = f"{overlay_id}_{safe_name}"
    filepath = _absolute(filename)
    file_storage.save(filepath)

    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = {
            "id": overlay_id,
            "name": os.path.splitext(safe_name)[0] or safe_name,
            "filename": filename,
            "relativePath": _relative(filename),
            "durationSeconds": round(FFmpegHelper.probe_duration(filepath), 2),
            "status": "processing",
            "enabled": True,
            "order": _next_order(overlays),
            "opacity": Config.STORY_TV_NOISE_OPACITY,
            "tolerance": Config.STORY_TV_NOISE_TOLERANCE,
            "softness": Config.STORY_TV_NOISE_SOFTNESS,
            "error": None,
            "createdAt": _now(),
            "updatedAt": _now(),
        }
        overlays.append(record)
        index["overlays"] = overlays
        _save_index_unlocked(index)
    return record


def create_tv_noise_placeholder(name: str, source_url: str = "") -> dict:
    overlay_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(name or f"youtube_tv_noise_{overlay_id}") or f"youtube_tv_noise_{overlay_id}"

    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = {
            "id": overlay_id,
            "name": os.path.splitext(safe_name)[0] or safe_name,
            "filename": "",
            "relativePath": "",
            "durationSeconds": 0,
            "sourceUrl": source_url,
            "status": "processing",
            "enabled": True,
            "order": _next_order(overlays),
            "opacity": Config.STORY_TV_NOISE_OPACITY,
            "tolerance": Config.STORY_TV_NOISE_TOLERANCE,
            "softness": Config.STORY_TV_NOISE_SOFTNESS,
            "error": None,
            "createdAt": _now(),
            "updatedAt": _now(),
        }
        overlays.append(record)
        index["overlays"] = overlays
        _save_index_unlocked(index)
    return record


def attach_tv_noise_source_file(overlay_id: str, file_path: str, display_name: str | None = None) -> dict | None:
    filename = os.path.basename(file_path)
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return None
        record["filename"] = filename
        record["relativePath"] = _relative(filename)
        record["durationSeconds"] = round(FFmpegHelper.probe_duration(file_path), 2)
        if display_name:
            record["name"] = display_name
        elif not record.get("name"):
            record["name"] = os.path.splitext(filename)[0]
        record["status"] = "processing"
        record["error"] = None
        record["updatedAt"] = _now()
        _save_index_unlocked(index)
        return record


def mark_tv_noise_processing(overlay_id: str) -> dict | None:
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return None
        record["status"] = "processing"
        record["error"] = None
        record["updatedAt"] = _now()
        _save_index_unlocked(index)
        return record


def run_tv_noise_preprocess(overlay_id: str) -> dict | None:
    record = mark_tv_noise_processing(overlay_id)
    if not record:
        return None

    src_path = source_abs_path(record)
    if not src_path:
        return mark_tv_noise_failed(overlay_id, "Source TV noise file is missing.")

    old_processed = record.get("processedFilename")
    try:
        processed_filename = preprocess_tv_noise_overlay(src_path, record)
    except Exception as exc:
        logger.error(f"[StoryTVNoise] Preprocess failed for {overlay_id}: {exc}", exc_info=True)
        return mark_tv_noise_failed(overlay_id, str(exc))

    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        updated = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not updated:
            return None
        updated["processedFilename"] = processed_filename
        updated["processedRelativePath"] = _relative(processed_filename)
        updated["status"] = "ready"
        updated["error"] = None
        updated["updatedAt"] = _now()
        _save_index_unlocked(index)

    if old_processed and old_processed != processed_filename:
        try:
            os.remove(_absolute(str(old_processed)))
        except OSError:
            pass
    return get_tv_noise_overlay(overlay_id)


def mark_tv_noise_failed(overlay_id: str, error: str) -> dict | None:
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return None
        record["status"] = "failed"
        record["error"] = error
        record["updatedAt"] = _now()
        _save_index_unlocked(index)
        return record


def update_tv_noise_overlay(overlay_id: str, updates: dict) -> tuple[dict | None, bool]:
    regenerate = False
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return None, False

        for key in ("opacity", "tolerance", "softness"):
            if key in updates and updates[key] is not None:
                value = max(0.0, min(1.0, float(updates[key])))
                if record.get(key) != value:
                    record[key] = value
                    regenerate = True

        if "enabled" in updates:
            record["enabled"] = bool(updates["enabled"])
        if "order" in updates and updates["order"] is not None:
            record["order"] = max(0, int(updates["order"]))
        if "name" in updates and str(updates["name"]).strip():
            record["name"] = str(updates["name"]).strip()

        already_processing = record.get("status") == "processing"

        if regenerate and already_processing:
            regenerate = False
        elif regenerate:
            record["status"] = "processing"
            record["error"] = None
        elif not already_processing and not processed_abs_path(record) and source_abs_path(record):
            record["status"] = "processing"
            record["error"] = None
            regenerate = True

        record["updatedAt"] = _now()
        _save_index_unlocked(index)
        return record, regenerate


def delete_tv_noise_overlay_record(overlay_id: str) -> bool:
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return False
        remaining = [item for item in overlays if item.get("id") != overlay_id]
        index["overlays"] = remaining
        _save_index_unlocked(index)

    for key in ("filename", "processedFilename"):
        filename = record.get(key)
        if filename:
            try:
                os.remove(_absolute(str(filename)))
            except OSError:
                pass
    return True
