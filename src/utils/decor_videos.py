"""Decor Video Library – upload, list, delete overlay videos for PiP compositing."""

import json
import os
import uuid
from datetime import datetime

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

_INDEX_FILENAME = "index.json"


def _decor_dir() -> str:
    path = os.path.abspath(Config.DECOR_VIDEOS_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def _index_path() -> str:
    return os.path.join(_decor_dir(), _INDEX_FILENAME)


def _load_index() -> list[dict]:
    path = _index_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fobj:
            data = json.load(fobj)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(items: list[dict]) -> None:
    with open(_index_path(), "w", encoding="utf-8") as fobj:
        json.dump(items, fobj, ensure_ascii=False, indent=2)


def list_decor_videos() -> list[dict]:
    """Return all decor videos, pruning entries whose files no longer exist."""
    items = _load_index()
    existing = []
    changed = False

    for item in items:
        abs_path = os.path.join(_decor_dir(), item["filename"])
        if os.path.isfile(abs_path):
            existing.append(item)
        else:
            changed = True
            logger.warning(f"Decor video file missing, pruning: {item['filename']}")

    if changed:
        _save_index(existing)

    return existing


def get_decor_video(decor_id: str) -> dict | None:
    """Look up a single decor video by ID."""
    for item in _load_index():
        if item["id"] == decor_id:
            abs_path = os.path.join(_decor_dir(), item["filename"])
            if os.path.isfile(abs_path):
                return item
    return None


def get_decor_video_absolute_path(decor_id: str) -> str | None:
    """Return the absolute filesystem path for a decor video, or None."""
    item = get_decor_video(decor_id)
    if not item:
        return None
    return os.path.join(_decor_dir(), item["filename"])


def add_decor_video(filename: str, file_bytes: bytes, display_name: str) -> dict:
    """Save a new decor video and return its metadata record."""
    decor_id = str(uuid.uuid4())[:8]
    # Keep safe filename
    safe_name = filename.replace(" ", "_").replace("'", "").replace('"', "")
    stored_filename = f"{decor_id}_{safe_name}"
    dest_path = os.path.join(_decor_dir(), stored_filename)

    with open(dest_path, "wb") as fobj:
        fobj.write(file_bytes)

    duration = FFmpegHelper.probe_duration(dest_path)

    record = {
        "id": decor_id,
        "name": display_name or os.path.splitext(safe_name)[0],
        "filename": stored_filename,
        "durationSeconds": round(duration, 2),
        "createdAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }

    items = _load_index()
    items.append(record)
    _save_index(items)

    logger.info(f"Added decor video: {record['name']} ({record['id']}, {duration:.1f}s)")
    return record


def delete_decor_video(decor_id: str) -> bool:
    """Delete a decor video by ID. Returns True if found and removed."""
    items = _load_index()
    remaining = []
    removed = None

    for item in items:
        if item["id"] == decor_id:
            removed = item
        else:
            remaining.append(item)

    if not removed:
        return False

    file_path = os.path.join(_decor_dir(), removed["filename"])
    if os.path.isfile(file_path):
        os.remove(file_path)

    _save_index(remaining)
    logger.info(f"Deleted decor video: {removed['name']} ({decor_id})")
    return True
