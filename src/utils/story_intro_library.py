"""Storage + normalization for the Story Video intro-clip library.

An *intro* is a short opening video prepended to the front of every video of a
batch render. Unlike clip libraries (folders of many clips), the intro library is
a flat list: each intro is a single video file plus metadata, tracked in a
registry file ``intros.json`` under ``Config.STORY_INTRO_DIR``.

On upload each intro is **normalized once** to the pipeline's canonical output
spec (``TARGET_RESOLUTION`` @ ``TARGET_FPS``, h264 yuv420p, AAC 48k stereo,
``+faststart``). This lets the render pipeline prepend it to each finished video
with a cheap concat-demuxer stream copy instead of a per-video re-encode.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import unicodedata
import uuid
from datetime import datetime, timezone

from src.config import Config
from src.utils.clip_canonical import canonical_output_args, canonical_tag_filter
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import storage_relative_path
from src.utils.logger import logger

_registry_lock = threading.RLock()


class IntroLibraryError(Exception):
    """Raised for intro CRUD/normalization failures. ``code`` maps to an API error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
# Registry I/O
# --------------------------------------------------------------------------- #
def _registry_path() -> str:
    return os.path.join(Config.STORY_INTRO_DIR, "intros.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_registry() -> dict:
    """Read intros.json without creating it. Returns {"intros": [...]}"""
    path = _registry_path()
    if not os.path.isfile(path):
        return {"intros": []}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Could not load Story Video intros registry: {path} | {exc}")
        return {"intros": []}
    if not isinstance(data, dict) or not isinstance(data.get("intros", []), list):
        logger.warning(f"Invalid Story Video intros registry format: {path}")
        return {"intros": []}
    data.setdefault("intros", [])
    return data


def _save_registry(data: dict):
    with _registry_lock:
        path = _registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)


def _slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_str).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:40] or "intro"


# --------------------------------------------------------------------------- #
# ffmpeg normalization
# --------------------------------------------------------------------------- #
def _has_audio_stream(media_path: str) -> bool:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        media_path,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return bool(result.stdout.strip())
    except Exception as exc:
        logger.warning(f"Could not probe audio stream for {media_path}: {exc}")
        return False


def _normalize_intro(src_path: str, out_path: str) -> bool:
    """Re-encode ``src_path`` to the pipeline's canonical spec at ``out_path``.

    Scales+pads to TARGET_RESOLUTION (letterbox, keep aspect), forces TARGET_FPS,
    yuv420p and a 48k stereo AAC track. When the source has no audio a silent
    stereo track is synthesized so every intro carries a uniform audio stream,
    keeping the downstream concat homogeneous.
    """
    try:
        width, height = (int(value) for value in Config.TARGET_RESOLUTION.split("x", 1))
    except (ValueError, AttributeError):
        width, height = 1920, 1080
    fps = max(1, int(Config.TARGET_FPS))

    # Letterbox instead of crop (an intro must keep its whole frame), then the
    # canonical color conversion + tags so the intro matches the render it is
    # concatenated onto. See src/utils/clip_canonical.py.
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"fps={fps},format=yuv420p,{canonical_tag_filter()}"
    )

    has_audio = _has_audio_stream(src_path)
    cmd = ["ffmpeg", "-y", "-i", src_path]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-vf", vf]
    if not has_audio:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += FFmpegHelper.get_nvenc_flags()
    cmd += canonical_output_args()
    cmd += [
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]

    ok = FFmpegHelper.run_command(cmd)
    return ok and os.path.isfile(out_path)


# --------------------------------------------------------------------------- #
# Public CRUD
# --------------------------------------------------------------------------- #
def _serialize(record: dict) -> dict:
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "relativePath": record.get("relativePath"),
        "duration": record.get("duration", 0),
        "createdAt": record.get("createdAt"),
    }


def list_intros() -> list[dict]:
    """All intros, newest first."""
    intros = list(_read_registry().get("intros", []))
    intros.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    return [_serialize(item) for item in intros]


def get_intro(intro_id) -> dict | None:
    lid = str(intro_id or "").strip()
    if not lid:
        return None
    for item in _read_registry().get("intros", []):
        if item.get("id") == lid:
            return item
    return None


def resolve_intro_path(intro_id) -> str:
    """Absolute path to a normalized intro file, or "" if unknown/missing."""
    record = get_intro(intro_id)
    if not record:
        return ""
    path = os.path.join(Config.STORY_INTRO_DIR, f"{record['id']}.mp4")
    return path if os.path.isfile(path) else ""


def add_intro(name: str, src_path: str) -> dict:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise IntroLibraryError("missing_name", "Tên intro không được để trống.")
    if not src_path or not os.path.isfile(src_path):
        raise IntroLibraryError("missing_file", "Không tìm thấy file intro.")

    with _registry_lock:
        data = _read_registry()
        intros = data.get("intros", [])
        existing_ids = {str(item.get("id") or "") for item in intros}
        slug = _slugify(clean_name)
        new_id = f"{slug}-{uuid.uuid4().hex[:6]}"
        while new_id in existing_ids:
            new_id = f"{slug}-{uuid.uuid4().hex[:6]}"

        os.makedirs(Config.STORY_INTRO_DIR, exist_ok=True)
        out_path = os.path.join(Config.STORY_INTRO_DIR, f"{new_id}.mp4")

        if not _normalize_intro(src_path, out_path):
            if os.path.isfile(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            raise IntroLibraryError("normalize_failed", "Không xử lý được file intro.")

        record = {
            "id": new_id,
            "name": clean_name,
            "relativePath": storage_relative_path(out_path),
            "duration": FFmpegHelper.probe_duration(out_path),
            "createdAt": _utc_now_iso(),
        }
        intros.append(record)
        data["intros"] = intros
        _save_registry(data)
        logger.info(f"[StoryIntro] Added intro {new_id} ({clean_name}) -> {out_path}")
        return _serialize(record)


def rename_intro(intro_id, name: str) -> dict:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise IntroLibraryError("missing_name", "Tên intro không được để trống.")

    with _registry_lock:
        data = _read_registry()
        target = next((item for item in data.get("intros", []) if item.get("id") == intro_id), None)
        if target is None:
            raise IntroLibraryError("intro_not_found", "Không tìm thấy intro.")
        target["name"] = clean_name
        target["updatedAt"] = _utc_now_iso()
        _save_registry(data)
        return _serialize(target)


def delete_intro(intro_id) -> dict:
    lid = str(intro_id or "").strip()
    if not lid:
        raise IntroLibraryError("intro_not_found", "Không tìm thấy intro.")

    with _registry_lock:
        data = _read_registry()
        intros = data.get("intros", [])
        target = next((item for item in intros if item.get("id") == lid), None)
        if target is None:
            raise IntroLibraryError("intro_not_found", "Không tìm thấy intro.")

        out_path = os.path.join(Config.STORY_INTRO_DIR, f"{lid}.mp4")
        if os.path.isfile(out_path):
            try:
                os.remove(out_path)
            except OSError as exc:
                logger.warning(f"[StoryIntro] Could not remove intro file {out_path}: {exc}")

        data["intros"] = [item for item in intros if item.get("id") != lid]
        _save_registry(data)
        return {"deleted": True, "introId": lid}
