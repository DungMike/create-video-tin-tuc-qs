"""Decor Video Library – upload, list, delete overlay videos for PiP compositing.

When a video is uploaded the module also creates a *pre-scaled* copy whose
width matches the configured ``OVERLAY_VIDEO_SCALE`` ratio.  During the
overlay render pass this pre-scaled file is used directly so that FFmpeg
skips the expensive per-frame ``scale`` filter.
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

_INDEX_FILENAME = "index.json"
_PRESCALED_SUFFIX = "_prescaled"
_LONG_SUFFIX = "_long"  # e.g. foo_prescaled_long.mp4
_LONG_DECOR_DURATION = 3660  # 61 minutes – covers any video up to 1 hour


def _decor_dir() -> str:
    path = os.path.abspath(Config.DECOR_VIDEOS_DIR)
    os.makedirs(path, exist_ok=True)
    return path


# ------------------------------------------------------------------ #
#  Pre-scaling helpers                                               #
# ------------------------------------------------------------------ #

def _prescaled_filename(original_filename: str) -> str:
    """Derive the pre-scaled filename from the original stored filename."""
    stem = Path(original_filename).stem
    ext = Path(original_filename).suffix
    return f"{stem}{_PRESCALED_SUFFIX}{ext}"


def get_prescaled_path(original_path: str | None) -> str | None:
    """Return the absolute path to the pre-scaled copy if it exists, else None.

    ``original_path`` may be the absolute path to the original decor video.
    """
    if not original_path:
        return None
    parent = os.path.dirname(original_path)
    original_name = os.path.basename(original_path)
    prescaled_name = _prescaled_filename(original_name)
    prescaled_abs = os.path.join(parent, prescaled_name)
    if os.path.isfile(prescaled_abs):
        return prescaled_abs
    return None


def _long_decor_filename(original_filename: str) -> str:
    """Derive the long (1h) pre-scaled+looped filename.

    e.g. ``foo.mp4`` → ``foo_prescaled_long.mp4``
    """
    stem = Path(original_filename).stem
    # Strip existing _prescaled* suffix to avoid double-naming
    if _PRESCALED_SUFFIX in stem:
        stem = stem.split(_PRESCALED_SUFFIX)[0]
    return f"{stem}{_PRESCALED_SUFFIX}{_LONG_SUFFIX}.mp4"


def get_long_decor_path(original_path: str | None) -> str | None:
    """Return the absolute path to the long (1h) pre-scaled decor if it exists."""
    if not original_path:
        return None
    parent = os.path.dirname(original_path)
    long_name = _long_decor_filename(os.path.basename(original_path))
    long_abs = os.path.join(parent, long_name)
    if os.path.isfile(long_abs):
        return long_abs
    return None


def get_long_decor_path_validated(
    original_path: str | None,
    min_duration: float = 0,
) -> str | None:
    """Return the long decor path only if its duration >= *min_duration*.

    If the file exists but is shorter than required (e.g. a truncated render),
    it is deleted so that the caller falls back to ``stream_loop``.
    """
    long_abs = get_long_decor_path(original_path)
    if not long_abs:
        return None

    actual_dur = FFmpegHelper.probe_duration(long_abs)
    if actual_dur >= min_duration:
        return long_abs

    logger.warning(
        f"[Decor] Long decor is too short ({actual_dur:.0f}s < {min_duration:.0f}s). "
        f"Deleting truncated file: {long_abs}"
    )
    try:
        os.remove(long_abs)
    except OSError as exc:
        logger.warning(f"[Decor] Could not delete truncated long decor: {exc}")
    return None


def ensure_long_decor(original_path: str) -> str | None:
    """Ensure a long (1-hour) pre-scaled decor video exists.

    Strategy:
      1. If the long version already exists, return it immediately.
      2. Otherwise, take the original decor video, loop + prescale it into
         a single 1-hour 480p file via a single FFmpeg pass.

    Removing ``-stream_loop`` from the overlay pass gives ~40% speed boost
    because FFmpeg can do a single sequential read instead of seeking back
    to the start of the decor file repeatedly.

    Returns the absolute path on success, ``None`` on failure.
    """
    if not original_path or not os.path.isfile(original_path):
        return None

    parent = os.path.dirname(original_path)
    long_name = _long_decor_filename(os.path.basename(original_path))
    long_path = os.path.join(parent, long_name)

    # Already exists – reuse
    if os.path.isfile(long_path):
        logger.info(f"[Decor] Long decor already exists: {long_path}")
        return long_path

    # Determine target overlay width
    width = int(Config.TARGET_RESOLUTION.split("x")[0])
    overlay_w = int(width * Config.OVERLAY_VIDEO_SCALE)
    overlay_w += overlay_w % 2

    logger.info(
        f"[Decor] Creating long decor ({_LONG_DECOR_DURATION}s, {overlay_w}px): "
        f"{os.path.basename(original_path)} -> {long_name}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", original_path,
        "-t", str(_LONG_DECOR_DURATION),
        "-vf", f"scale={overlay_w}:-2",
        "-an",
        "-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "1M",
        "-pix_fmt", "yuv420p",
        long_path,
    ]
    ok = FFmpegHelper.run_command(cmd)
    if ok and os.path.isfile(long_path):
        size_mb = os.path.getsize(long_path) / (1024 * 1024)
        logger.info(f"[Decor] Long decor created: {long_path} ({size_mb:.0f}MB)")
        return long_path

    logger.warning(f"[Decor] Failed to create long decor: {long_name}")
    return None


def ensure_all_long_decors() -> int:
    """Pre-render long decor versions for ALL decor videos in the library.

    Returns the number of new files created.
    """
    decor_dir = _decor_dir()
    items = _load_index()
    created = 0
    for item in items:
        original = os.path.join(decor_dir, item["filename"])
        if not os.path.isfile(original):
            continue
        long_path = get_long_decor_path(original)
        if long_path:
            continue  # already exists
        result = ensure_long_decor(original)
        if result:
            created += 1
    if created:
        logger.info(f"[Decor] Pre-rendered {created} long decor video(s).")
    return created


def _prescale_decor_video(source_path: str) -> str | None:
    """Create a pre-scaled copy of *source_path* at the configured overlay width.

    Returns the path to the pre-scaled file on success, or ``None`` on failure.
    """
    width = int(Config.TARGET_RESOLUTION.split("x")[0])
    overlay_w = int(width * Config.OVERLAY_VIDEO_SCALE)
    overlay_w += overlay_w % 2  # ensure even for H.264

    parent = os.path.dirname(source_path)
    prescaled_name = _prescaled_filename(os.path.basename(source_path))
    prescaled_path = os.path.join(parent, prescaled_name)

    cmd = [
        "ffmpeg", "-y",
        "-i", source_path,
        "-vf", f"scale={overlay_w}:-2",
        "-an",                     # decor PiP is always muted
    ]
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(["-pix_fmt", "yuv420p", prescaled_path])

    logger.info(f"Pre-scaling decor video to {overlay_w}px wide: {prescaled_path}")
    if FFmpegHelper.run_command(cmd):
        logger.info(f"Pre-scaled decor video saved: {prescaled_path}")
        return prescaled_path

    logger.warning("Pre-scaling decor video failed; runtime scale will be used as fallback.")
    return None


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


_MIN_DECOR_DURATION = 3600  # 1 hour minimum


def add_decor_video(filename: str, file_bytes: bytes, display_name: str) -> dict:
    """Save a new decor video and return its metadata record.

    Raises ``ValueError`` if the video is shorter than 1 hour.
    Long decor videos are used directly (no stream_loop) in the overlay
    pipeline, which is ~3-4× faster than looping short clips.
    """
    decor_id = str(uuid.uuid4())[:8]
    # Keep safe filename
    safe_name = filename.replace(" ", "_").replace("'", "").replace('"', "")
    stored_filename = f"{decor_id}_{safe_name}"
    dest_path = os.path.join(_decor_dir(), stored_filename)

    with open(dest_path, "wb") as fobj:
        fobj.write(file_bytes)

    duration = FFmpegHelper.probe_duration(dest_path)

    # Reject short videos – they force expensive stream_loop + runtime scale
    if duration < _MIN_DECOR_DURATION:
        os.remove(dest_path)
        raise ValueError(
            f"Decor video quá ngắn ({duration:.0f}s / {duration/60:.1f} phút). "
            f"Yêu cầu tối thiểu {_MIN_DECOR_DURATION // 60} phút (1 tiếng). "
            f"Hãy upload video decor dài hơn 1 tiếng."
        )

    # Pre-scale the video to the configured overlay width so the render
    # step can skip the per-frame scale filter entirely.
    prescaled_path = _prescale_decor_video(dest_path)

    record = {
        "id": decor_id,
        "name": display_name or os.path.splitext(safe_name)[0],
        "filename": stored_filename,
        "prescaledFilename": _prescaled_filename(stored_filename) if prescaled_path else None,
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

    # Also remove the pre-scaled copy if it exists
    prescaled = os.path.join(_decor_dir(), _prescaled_filename(removed["filename"]))
    if os.path.isfile(prescaled):
        os.remove(prescaled)

    _save_index(remaining)
    logger.info(f"Deleted decor video: {removed['name']} ({decor_id})")
    return True
