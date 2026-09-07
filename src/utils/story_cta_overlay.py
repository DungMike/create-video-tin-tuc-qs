"""Story CTA overlay storage and preprocessing helpers.

A CTA (call-to-action) overlay is a small green-screen decoration clip — the
Like / Subscribe / Notification buttons — chroma-keyed to alpha and composited
in a corner of every Story Video to nudge viewers to like and subscribe.

Structurally identical to :mod:`src.utils.waveform_overlays` (colorkey -> alpha
MOV, positioned + looped + muted), with two additions: an ``enabled`` toggle and
an auto-seeded default sourced from ``Config.STORY_CTA_DEFAULT_VIDEO``.
"""

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
from src.utils.overlay_placement import (
    apply_placement_updates,
    corner_expr,
    placement_expr,
    probe_overlay_size,
)

_seed_lock = threading.Lock()


def _index_path() -> str:
    os.makedirs(Config.STORY_CTA_OVERLAY_DIR, exist_ok=True)
    return os.path.join(Config.STORY_CTA_OVERLAY_DIR, "index.json")


def load_cta_index() -> dict:
    path = _index_path()
    if not os.path.isfile(path):
        return {"overlays": []}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else {"overlays": []}
    except (OSError, json.JSONDecodeError):
        return {"overlays": []}


def save_cta_index(data: dict):
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2, ensure_ascii=False)
    shutil.move(tmp, path)


def _relative(filename: str) -> str:
    return f"story_cta_overlays/{filename}"


def _absolute(filename: str) -> str:
    return os.path.join(Config.STORY_CTA_OVERLAY_DIR, filename)


def _position_expr(position: str, margin: int) -> tuple[str, str]:
    return corner_expr(position, margin)


def overlay_position_expr(record: dict) -> tuple[str, str]:
    """Where this CTA sits: free x/y when set, else the legacy corner."""
    return placement_expr(
        record,
        default_position=Config.STORY_CTA_OVERLAY_POSITION,
        default_margin=Config.STORY_CTA_OVERLAY_MARGIN,
    )


def processed_abs_path(record: dict) -> str | None:
    filename = record.get("processedFilename")
    if not filename:
        return None
    path = _absolute(str(filename))
    return path if os.path.isfile(path) else None


def get_active_cta_overlay() -> dict | None:
    """Return the default + enabled CTA overlay with a usable processed file.

    Auto-seeds the bundled default on first access so a fresh install still
    shows the buttons overlay without any manual upload.
    """
    ensure_default_cta_overlay()
    overlays = load_cta_index().get("overlays", [])
    valid = [
        item
        for item in overlays
        if item.get("enabled", True) and processed_abs_path(item)
    ]
    if not valid:
        return None
    default = next((item for item in valid if item.get("isDefault")), None)
    return default or valid[0]


def get_cta_overlay(overlay_id: str) -> dict | None:
    """Ban ghi CTA theo id, chi tra ve khi file alpha da xu ly con ton tai.

    Khong loc theo co ``enabled``: chon tay mot CTA o trang batch la da co y bat
    no cho lan render do. ``enabled`` chi chi phoi :func:`get_active_cta_overlay`.
    """
    wanted = str(overlay_id or "").strip()
    if not wanted:
        return None
    overlays = load_cta_index().get("overlays", [])
    record = next((item for item in overlays if item.get("id") == wanted), None)
    return record if record and processed_abs_path(record) else None


def build_cta_rotation(overlay_ids: list[str], count: int) -> list[str]:
    """Chia cac CTA da chon cho ``count`` video (bo bai xao).

    Cac id khong tra cuu duoc (da xoa, chua xu ly xong) bi loai truoc khi chia,
    dung nhu :func:`src.utils.story_decor_images.build_decor_rotation`.
    """
    from src.utils.asset_rotation import deal_rotation

    usable = [overlay_id for overlay_id in overlay_ids if get_cta_overlay(overlay_id)]
    return deal_rotation(usable, count)


def preprocess_cta_overlay(source_path: str, record: dict) -> str:
    overlay_id = str(record["id"])
    processed_filename = f"{overlay_id}_alpha.mov"
    output_path = _absolute(processed_filename)
    key_color = str(record.get("keyColor") or Config.STORY_CTA_OVERLAY_KEY_COLOR)
    similarity = float(record.get("similarity") or Config.STORY_CTA_OVERLAY_KEY_SIMILARITY)
    blend = float(record.get("blend") or Config.STORY_CTA_OVERLAY_KEY_BLEND)
    width = max(64, int(record.get("scaleWidth") or Config.STORY_CTA_OVERLAY_WIDTH))

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

    logger.info(f"[CtaOverlay] Preprocessing alpha MOV: {output_path}")
    if not FFmpegHelper.run_command(cmd):
        raise RuntimeError("FFmpeg failed to preprocess CTA overlay.")
    if not os.path.isfile(output_path):
        raise RuntimeError("Processed CTA overlay was not created.")

    # `scale={width}:-2` means the height is only known now. Record it so the
    # placement editor can draw the overlay at its true size and free
    # coordinates can be clamped against it.
    size = probe_overlay_size(output_path)
    if size:
        record["processedWidth"], record["processedHeight"] = size

    return processed_filename


def _build_default_record(overlay_id: str, safe_name: str, filename: str, source_abs: str) -> dict:
    return {
        "id": overlay_id,
        "name": safe_name,
        "filename": filename,
        "relativePath": _relative(filename),
        "durationSeconds": round(FFmpegHelper.probe_duration(source_abs), 2),
        "isDefault": True,
        "enabled": Config.STORY_CTA_OVERLAY_DEFAULT_ENABLED,
        "keyColor": Config.STORY_CTA_OVERLAY_KEY_COLOR,
        "similarity": Config.STORY_CTA_OVERLAY_KEY_SIMILARITY,
        "blend": Config.STORY_CTA_OVERLAY_KEY_BLEND,
        "scaleWidth": Config.STORY_CTA_OVERLAY_WIDTH,
        "position": Config.STORY_CTA_OVERLAY_POSITION,
        "margin": Config.STORY_CTA_OVERLAY_MARGIN,
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
    }


def ensure_default_cta_overlay() -> dict | None:
    """Seed the bundled default CTA overlay when the library is empty.

    Idempotent and thread-safe: the render thread and an HTTP request can both
    call this concurrently. Returns the default record, or ``None`` when the
    bundled asset is missing (the pipeline then degrades to no CTA).
    """
    index = load_cta_index()
    overlays = index.get("overlays", [])
    if overlays:
        return next((item for item in overlays if item.get("isDefault")), overlays[0])

    with _seed_lock:
        # Re-check inside the lock — another thread may have just seeded.
        index = load_cta_index()
        overlays = index.get("overlays", [])
        if overlays:
            return next((item for item in overlays if item.get("isDefault")), overlays[0])

        src_path = Config.STORY_CTA_DEFAULT_VIDEO
        if not src_path or not os.path.isfile(src_path):
            logger.warning(
                f"[CtaOverlay] Default seed video missing: {src_path}; skipping auto-seed."
            )
            return None

        os.makedirs(Config.STORY_CTA_OVERLAY_DIR, exist_ok=True)
        overlay_id = str(uuid.uuid4())[:8]
        safe_name = secure_filename(os.path.basename(src_path)) or "story_cta_default.mp4"
        filename = f"{overlay_id}_{safe_name}"
        dest = _absolute(filename)
        try:
            shutil.copy2(src_path, dest)
        except OSError as exc:
            logger.error(f"[CtaOverlay] Could not copy default seed video: {exc}")
            return None

        record = _build_default_record(overlay_id, safe_name, filename, dest)
        try:
            record["processedFilename"] = preprocess_cta_overlay(dest, record)
            record["processedRelativePath"] = _relative(record["processedFilename"])
        except Exception as exc:  # noqa: BLE001 - keep startup resilient
            logger.error(f"[CtaOverlay] Auto-seed preprocess failed: {exc}")
            try:
                os.remove(dest)
            except OSError:
                pass
            return None

        index["overlays"] = [record]
        save_cta_index(index)
        logger.info(f"[CtaOverlay] Seeded default CTA overlay: {filename}")
        return record


def create_cta_overlay(file_storage) -> dict:
    if not file_storage or not file_storage.filename:
        raise ValueError("No CTA file provided.")

    overlay_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(file_storage.filename)
    filename = f"{overlay_id}_{safe_name}"
    filepath = _absolute(filename)
    file_storage.save(filepath)

    index = load_cta_index()
    overlays = index.get("overlays", [])
    record = {
        "id": overlay_id,
        "name": safe_name,
        "filename": filename,
        "relativePath": _relative(filename),
        "durationSeconds": round(FFmpegHelper.probe_duration(filepath), 2),
        "isDefault": True,
        "enabled": True,
        "keyColor": Config.STORY_CTA_OVERLAY_KEY_COLOR,
        "similarity": Config.STORY_CTA_OVERLAY_KEY_SIMILARITY,
        "blend": Config.STORY_CTA_OVERLAY_KEY_BLEND,
        "scaleWidth": Config.STORY_CTA_OVERLAY_WIDTH,
        "position": Config.STORY_CTA_OVERLAY_POSITION,
        "margin": Config.STORY_CTA_OVERLAY_MARGIN,
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
    }

    record["processedFilename"] = preprocess_cta_overlay(filepath, record)
    record["processedRelativePath"] = _relative(record["processedFilename"])

    # A freshly uploaded overlay becomes the new default.
    for item in overlays:
        item["isDefault"] = False
    overlays.append(record)
    index["overlays"] = overlays
    save_cta_index(index)
    return record


def update_cta_overlay(overlay_id: str, updates: dict) -> dict | None:
    index = load_cta_index()
    overlays = index.get("overlays", [])
    record = next((item for item in overlays if item.get("id") == overlay_id), None)
    if not record:
        return None

    regenerate = False
    for key in ("keyColor", "similarity", "blend", "scaleWidth"):
        if key in updates and updates[key] is not None and record.get(key) != updates[key]:
            record[key] = updates[key]
            regenerate = True

    if "enabled" in updates and updates["enabled"] is not None:
        record["enabled"] = bool(updates["enabled"])

    if updates.get("isDefault") is True:
        for item in overlays:
            item["isDefault"] = item.get("id") == overlay_id

    if not processed_abs_path(record):
        regenerate = True

    if regenerate:
        source_path = _absolute(str(record["filename"]))
        old_processed = record.get("processedFilename")
        record["processedFilename"] = preprocess_cta_overlay(source_path, record)
        record["processedRelativePath"] = _relative(record["processedFilename"])
        if old_processed and old_processed != record["processedFilename"]:
            try:
                os.remove(_absolute(str(old_processed)))
            except OSError:
                pass

    # After any re-encode: a new scaleWidth changes the overlay's size, and the
    # coordinates are clamped against that size.
    apply_placement_updates(record, updates)

    record["updatedAt"] = datetime.now().isoformat()
    save_cta_index(index)
    return record


def delete_cta_overlay_record(overlay_id: str) -> bool:
    index = load_cta_index()
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
    save_cta_index(index)
    return True
