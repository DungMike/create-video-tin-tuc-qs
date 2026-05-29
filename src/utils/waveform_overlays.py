"""Waveform overlay storage and preprocessing helpers."""

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from werkzeug.utils import secure_filename

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger


def _index_path() -> str:
    os.makedirs(Config.WAVEFORM_OVERLAY_DIR, exist_ok=True)
    return os.path.join(Config.WAVEFORM_OVERLAY_DIR, "index.json")


def load_waveform_index() -> dict:
    path = _index_path()
    if not os.path.isfile(path):
        return {"overlays": []}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else {"overlays": []}
    except (OSError, json.JSONDecodeError):
        return {"overlays": []}


def save_waveform_index(data: dict):
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2, ensure_ascii=False)
    shutil.move(tmp, path)


def _relative(filename: str) -> str:
    return f"waveform_overlays/{filename}"


def _absolute(filename: str) -> str:
    return os.path.join(Config.WAVEFORM_OVERLAY_DIR, filename)


def _position_expr(position: str, margin: int) -> tuple[str, str]:
    safe_margin = max(0, int(margin))
    if position == "top_left":
        return str(safe_margin), str(safe_margin)
    if position == "top_right":
        return f"W-w-{safe_margin}", str(safe_margin)
    if position == "bottom_left":
        return str(safe_margin), f"H-h-{safe_margin}"
    return f"W-w-{safe_margin}", f"H-h-{safe_margin}"


def overlay_position_expr(record: dict) -> tuple[str, str]:
    return _position_expr(
        str(record.get("position") or Config.WAVEFORM_OVERLAY_POSITION),
        int(record.get("margin") or Config.WAVEFORM_OVERLAY_MARGIN),
    )


def processed_abs_path(record: dict) -> str | None:
    filename = record.get("processedFilename")
    if not filename:
        return None
    path = _absolute(str(filename))
    return path if os.path.isfile(path) else None


def get_default_waveform_overlay() -> dict | None:
    overlays = load_waveform_index().get("overlays", [])
    valid = [item for item in overlays if processed_abs_path(item)]
    if not valid:
        return None
    default = next((item for item in valid if item.get("isDefault")), None)
    return default or valid[0]


def preprocess_waveform_overlay(source_path: str, record: dict) -> str:
    overlay_id = str(record["id"])
    processed_filename = f"{overlay_id}_alpha.mov"
    output_path = _absolute(processed_filename)
    key_color = str(record.get("keyColor") or Config.WAVEFORM_OVERLAY_KEY_COLOR)
    similarity = float(record.get("similarity") or Config.WAVEFORM_OVERLAY_KEY_SIMILARITY)
    blend = float(record.get("blend") or Config.WAVEFORM_OVERLAY_KEY_BLEND)
    width = max(64, int(record.get("scaleWidth") or Config.WAVEFORM_OVERLAY_WIDTH))

    filter_str = (
        f"scale={width}:-2,"
        f"format=rgba,"
        f"colorkey={key_color}:{similarity}:{blend},"
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

    logger.info(f"[WaveformOverlay] Preprocessing alpha MOV: {output_path}")
    if not FFmpegHelper.run_command(cmd):
        raise RuntimeError("FFmpeg failed to preprocess waveform overlay.")
    if not os.path.isfile(output_path):
        raise RuntimeError("Processed waveform overlay was not created.")
    return processed_filename


def create_waveform_overlay(file_storage) -> dict:
    if not file_storage or not file_storage.filename:
        raise ValueError("No waveform file provided.")

    overlay_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(file_storage.filename)
    filename = f"{overlay_id}_{safe_name}"
    filepath = _absolute(filename)
    file_storage.save(filepath)

    index = load_waveform_index()
    overlays = index.get("overlays", [])
    is_first = len(overlays) == 0
    record = {
        "id": overlay_id,
        "name": safe_name,
        "filename": filename,
        "relativePath": _relative(filename),
        "durationSeconds": round(FFmpegHelper.probe_duration(filepath), 2),
        "isDefault": True,
        "keyColor": Config.WAVEFORM_OVERLAY_KEY_COLOR,
        "similarity": Config.WAVEFORM_OVERLAY_KEY_SIMILARITY,
        "blend": Config.WAVEFORM_OVERLAY_KEY_BLEND,
        "scaleWidth": Config.WAVEFORM_OVERLAY_WIDTH,
        "position": Config.WAVEFORM_OVERLAY_POSITION,
        "margin": Config.WAVEFORM_OVERLAY_MARGIN,
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
    }

    record["processedFilename"] = preprocess_waveform_overlay(filepath, record)
    record["processedRelativePath"] = _relative(record["processedFilename"])

    for item in overlays:
        item["isDefault"] = False if record["isDefault"] else item.get("isDefault", is_first)
    overlays.append(record)
    index["overlays"] = overlays
    save_waveform_index(index)
    return record


def update_waveform_overlay(overlay_id: str, updates: dict) -> dict | None:
    index = load_waveform_index()
    overlays = index.get("overlays", [])
    record = next((item for item in overlays if item.get("id") == overlay_id), None)
    if not record:
        return None

    regenerate = False
    for key in ("keyColor", "similarity", "blend", "scaleWidth"):
        if key in updates and updates[key] is not None and record.get(key) != updates[key]:
            record[key] = updates[key]
            regenerate = True

    for key in ("position", "margin"):
        if key in updates and updates[key] is not None:
            record[key] = updates[key]

    if updates.get("isDefault") is True:
        for item in overlays:
            item["isDefault"] = item.get("id") == overlay_id

    if not processed_abs_path(record):
        regenerate = True

    if regenerate:
        source_path = _absolute(str(record["filename"]))
        old_processed = record.get("processedFilename")
        record["processedFilename"] = preprocess_waveform_overlay(source_path, record)
        record["processedRelativePath"] = _relative(record["processedFilename"])
        if old_processed and old_processed != record["processedFilename"]:
            try:
                os.remove(_absolute(str(old_processed)))
            except OSError:
                pass

    record["updatedAt"] = datetime.now().isoformat()
    save_waveform_index(index)
    return record


def delete_waveform_overlay_record(overlay_id: str) -> bool:
    index = load_waveform_index()
    overlays = index.get("overlays", [])
    record = next((item for item in overlays if item.get("id") == overlay_id), None)
    if not record:
        return False

    for key in ("filename", "processedFilename"):
        filename = record.get(key)
        if filename:
            try:
                os.remove(_absolute(str(filename)))
            except OSError:
                pass

    remaining = [item for item in overlays if item.get("id") != overlay_id]
    if record.get("isDefault") and remaining:
        remaining[0]["isDefault"] = True
    index["overlays"] = remaining
    save_waveform_index(index)
    return True
