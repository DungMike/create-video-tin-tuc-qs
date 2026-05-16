"""Decor Images — Upload, list, delete PNG decor banner images per channel.

Each decor image is a transparent PNG (1920×300 by default) used as an overlay
banner at the bottom of each news segment during rendering.  The image contains
the channel branding (logo, name, segment label like "Cập nhật trưa") and a
designated area where the news headline text is rendered by FFmpeg ``drawtext``.

Storage layout::

    {DECOR_IMAGES_DIR}/{channel_id}/{image_id}_{safe_name}.png
    {DECOR_IMAGES_DIR}/{channel_id}/index.json
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config import Config
from src.utils.logger import logger

_INDEX_FILENAME = "index.json"


def _images_dir(channel_id: str) -> str:
    path = os.path.join(os.path.abspath(Config.DECOR_IMAGES_DIR), channel_id)
    os.makedirs(path, exist_ok=True)
    return path


def _index_path(channel_id: str) -> str:
    return os.path.join(_images_dir(channel_id), _INDEX_FILENAME)


def _load_index(channel_id: str) -> list[dict]:
    path = _index_path(channel_id)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fobj:
            data = json.load(fobj)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(channel_id: str, items: list[dict]) -> None:
    with open(_index_path(channel_id), "w", encoding="utf-8") as fobj:
        json.dump(items, fobj, ensure_ascii=False, indent=2)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _relative_path(channel_id: str, filename: str) -> str:
    """Return path relative to STORAGE_DIR for serving via /media/."""
    abs_path = os.path.join(_images_dir(channel_id), filename)
    return os.path.relpath(abs_path, os.path.abspath(Config.STORAGE_DIR)).replace("\\", "/")


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------

def list_decor_images(channel_id: str) -> list[dict]:
    """Return all decor images for a channel, pruning missing files."""
    items = _load_index(channel_id)
    existing = []
    changed = False
    img_dir = _images_dir(channel_id)

    for item in items:
        abs_path = os.path.join(img_dir, item["filename"])
        if os.path.isfile(abs_path):
            # Ensure relativePath is present
            item.setdefault("relativePath", _relative_path(channel_id, item["filename"]))
            existing.append(item)
        else:
            changed = True
            logger.warning(f"[DecorImages] File missing, pruning: {item['filename']}")

    if changed:
        _save_index(channel_id, existing)
    return existing


def get_decor_image(image_id: str, channel_id: str | None = None) -> dict | None:
    """Look up a decor image by ID. If channel_id is None, scan all channels."""
    if channel_id:
        for item in _load_index(channel_id):
            if item["id"] == image_id:
                abs_path = os.path.join(_images_dir(channel_id), item["filename"])
                if os.path.isfile(abs_path):
                    return item
        return None

    # Scan all channel subdirs
    base_dir = os.path.abspath(Config.DECOR_IMAGES_DIR)
    if not os.path.isdir(base_dir):
        return None
    for ch_dir in os.listdir(base_dir):
        ch_path = os.path.join(base_dir, ch_dir)
        if os.path.isdir(ch_path):
            for item in _load_index(ch_dir):
                if item["id"] == image_id:
                    abs_path = os.path.join(ch_path, item["filename"])
                    if os.path.isfile(abs_path):
                        return item
    return None


def get_decor_image_absolute_path(image_id: str, channel_id: str | None = None) -> str | None:
    """Return the absolute filesystem path for a decor image, or None."""
    item = get_decor_image(image_id, channel_id)
    if not item:
        return None
    ch_id = item.get("channelId", channel_id or "")
    return os.path.join(_images_dir(ch_id), item["filename"])


def add_decor_image(
    channel_id: str,
    display_name: str,
    filename: str,
    file_bytes: bytes,
    title_offset_x: int | None = None,
    title_offset_y: int | None = None,
    title_max_width: int | None = None,
) -> dict:
    """Save a new decor image (PNG only) and return its metadata record.

    Raises ``ValueError`` if the file is not a PNG.
    """
    ext = Path(filename).suffix.lower()
    if ext != ".png":
        raise ValueError(f"Chỉ hỗ trợ định dạng PNG. File '{filename}' có đuôi '{ext}'.")

    image_id = f"di_{uuid.uuid4().hex[:8]}"
    safe_name = filename.replace(" ", "_").replace("'", "").replace('"', "")
    stored_filename = f"{image_id}_{safe_name}"
    dest_path = os.path.join(_images_dir(channel_id), stored_filename)

    with open(dest_path, "wb") as fobj:
        fobj.write(file_bytes)

    record = {
        "id": image_id,
        "channelId": channel_id,
        "name": display_name or Path(safe_name).stem,
        "filename": stored_filename,
        "relativePath": _relative_path(channel_id, stored_filename),
        "width": Config.DECOR_IMAGE_WIDTH,
        "height": Config.DECOR_IMAGE_HEIGHT,
        "titleOffsetX": title_offset_x if title_offset_x is not None else int(Config.DECOR_IMAGE_WIDTH * 0.5),
        "titleOffsetY": title_offset_y if title_offset_y is not None else int(Config.DECOR_IMAGE_HEIGHT * 0.4),
        "titleMaxWidth": title_max_width if title_max_width is not None else int(Config.DECOR_IMAGE_WIDTH * 0.47),
        "titleFontSize": Config.DECOR_IMAGE_TITLE_FONT_SIZE,
        "titleColor": Config.DECOR_IMAGE_TITLE_COLOR,
        "titleFont": os.path.splitext(os.path.basename(Config.DECOR_IMAGE_TITLE_FONT))[0],
        "createdAt": _utc_now(),
    }

    items = _load_index(channel_id)
    items.append(record)
    _save_index(channel_id, items)

    logger.info(f"[DecorImages] Added '{record['name']}' ({image_id}) for channel {channel_id}")
    return record


def update_decor_image(channel_id: str, image_id: str, updates: dict) -> dict | None:
    """Update metadata fields of a decor image.

    Allowed fields: titleOffsetX, titleOffsetY, titleMaxWidth,
    titleFontSize, titleColor, titleFont, name.
    Returns the updated record, or None if not found.
    """
    items = _load_index(channel_id)
    target = None
    for item in items:
        if item["id"] == image_id:
            target = item
            break

    if not target:
        return None

    allowed = {
        "titleOffsetX", "titleOffsetY", "titleMaxWidth",
        "titleFontSize", "titleColor", "titleFont", "name",
    }
    for key, value in updates.items():
        if key in allowed:
            target[key] = value

    target["updatedAt"] = _utc_now()
    _save_index(channel_id, items)
    logger.info(f"[DecorImages] Updated '{target['name']}' ({image_id}) for channel {channel_id}")
    return target


def delete_decor_image(channel_id: str, image_id: str) -> bool:
    """Delete a decor image by ID. Returns True if found and removed."""
    items = _load_index(channel_id)
    remaining = []
    removed = None

    for item in items:
        if item["id"] == image_id:
            removed = item
        else:
            remaining.append(item)

    if not removed:
        return False

    file_path = os.path.join(_images_dir(channel_id), removed["filename"])
    if os.path.isfile(file_path):
        os.remove(file_path)

    _save_index(channel_id, remaining)
    logger.info(f"[DecorImages] Deleted '{removed['name']}' ({image_id}) from channel {channel_id}")
    return True
