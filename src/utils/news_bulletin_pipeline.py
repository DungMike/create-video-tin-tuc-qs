"""News Bulletin Pipeline — Orchestrates multi-channel news video generation.

Phases:
  1. Parse script → structured JSON
  2. Resource validation
  3. Per-channel: TTS → probe durations → timeline → render → overlay
"""

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any

from src.composer.news_timeline import NewsTimelineComposer
from src.config import Config
from src.processors.audio_utils import get_audio_duration
from src.processors.news_script_parser import (
    NewsScriptParseError,
    get_all_tts_segments,
    parse_news_script,
)
from src.utils.channel_manager import get_channel
from src.utils.logger import logger

_BULLETIN_ROOT = os.path.join(Config.STORAGE_DIR, "news_bulletin")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bulletin_dir(bulletin_id: str) -> str:
    path = os.path.join(_BULLETIN_ROOT, bulletin_id)
    os.makedirs(path, exist_ok=True)
    return path


def _progress_path(bulletin_id: str) -> str:
    return os.path.join(_bulletin_dir(bulletin_id), "progress.json")


def _state_path(bulletin_id: str) -> str:
    return os.path.join(_bulletin_dir(bulletin_id), "state.json")


def _resources_dir(bulletin_id: str) -> str:
    return os.path.join(_bulletin_dir(bulletin_id), "resources")


def _channel_build_dir(bulletin_id: str, channel_id: str) -> str:
    return os.path.join(_bulletin_dir(bulletin_id), "channel_builds", channel_id)


def _save_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Bulletin CRUD
# ---------------------------------------------------------------------------

def create_bulletin(script_text: str, channel_ids: list[str]) -> dict:
    """Create a new bulletin from script text and selected channels.

    Returns the bulletin state dict.
    """
    bulletin_id = f"bulletin_{uuid.uuid4().hex[:12]}"
    now = _utc_now()

    # Parse the script
    try:
        parsed = parse_news_script(script_text)
    except NewsScriptParseError as exc:
        raise ValueError(str(exc))

    news_count = len(parsed.get("newsItems", []))

    # Save raw script
    script_txt_path = os.path.join(_bulletin_dir(bulletin_id), "script.txt")
    with open(script_txt_path, "w", encoding="utf-8") as fh:
        fh.write(script_text)

    # Save parsed script
    script_json_path = os.path.join(_bulletin_dir(bulletin_id), "script.json")
    _save_json(script_json_path, parsed)

    # Create resource directories for each news item
    for item in parsed.get("newsItems", []):
        news_id = item["id"]
        res_dir = os.path.join(_resources_dir(bulletin_id), f"news_{news_id}")
        os.makedirs(os.path.join(res_dir, "vid_clips"), exist_ok=True)
        os.makedirs(os.path.join(res_dir, "images"), exist_ok=True)

    # Build state
    state = {
        "bulletinId": bulletin_id,
        "createdAt": now,
        "updatedAt": now,
        "scriptText": script_text,
        "parsedScript": parsed,
        "channelIds": channel_ids,
        "newsCount": news_count,
        "resources": {
            str(item["id"]): {"vidClips": [], "images": []}
            for item in parsed.get("newsItems", [])
        },
    }
    _save_json(_state_path(bulletin_id), state)

    # Build progress
    progress = {
        "bulletinId": bulletin_id,
        "status": "draft",
        "createdAt": now,
        "updatedAt": now,
        "newsCount": news_count,
        "channelIds": channel_ids,
        "channels": {},
    }
    for ch_id in channel_ids:
        ch = get_channel(ch_id)
        progress["channels"][ch_id] = {
            "channelId": ch_id,
            "channelName": ch.get("channelName", ch_id) if ch else ch_id,
            "status": "pending",
            "stage": "pending",
            "percent": 0,
            "message": "Cho xu ly...",
            "outputVideo": None,
            "error": None,
        }
    _save_json(_progress_path(bulletin_id), progress)

    logger.info(
        f"[NewsBulletin] Created bulletin {bulletin_id}: "
        f"{news_count} news items, {len(channel_ids)} channels"
    )
    return state


def load_bulletin_state(bulletin_id: str) -> dict | None:
    return _load_json(_state_path(bulletin_id))


def load_bulletin_progress(bulletin_id: str) -> dict | None:
    return _load_json(_progress_path(bulletin_id))


def save_bulletin_progress(bulletin_id: str, progress: dict):
    progress["updatedAt"] = _utc_now()
    _save_json(_progress_path(bulletin_id), progress)


def save_bulletin_state(bulletin_id: str, state: dict):
    state["updatedAt"] = _utc_now()
    _save_json(_state_path(bulletin_id), state)


def list_bulletins() -> list[dict]:
    """List all bulletins (summary only)."""
    if not os.path.isdir(_BULLETIN_ROOT):
        return []
    bulletins = []
    for entry in sorted(os.scandir(_BULLETIN_ROOT), key=lambda e: e.name, reverse=True):
        if not entry.is_dir():
            continue
        progress = _load_json(os.path.join(entry.path, "progress.json"))
        if progress:
            bulletins.append({
                "bulletinId": progress.get("bulletinId", entry.name),
                "status": progress.get("status", "unknown"),
                "newsCount": progress.get("newsCount", 0),
                "channelCount": len(progress.get("channelIds", [])),
                "createdAt": progress.get("createdAt"),
                "updatedAt": progress.get("updatedAt"),
            })
    return bulletins


# ---------------------------------------------------------------------------
# Resource management
# ---------------------------------------------------------------------------

def add_resource_files(
    bulletin_id: str,
    news_id: int,
    kind: str,
    file_paths: list[str],
) -> dict:
    """Add resource files (vid_clips or images) for a specific news item.

    Args:
        kind: "vid_clips" or "images"

    Returns updated resource entry for this news item.
    """
    state = load_bulletin_state(bulletin_id)
    if not state:
        raise ValueError(f"Bulletin '{bulletin_id}' khong ton tai.")

    res_dir = os.path.join(_resources_dir(bulletin_id), f"news_{news_id}", kind)
    os.makedirs(res_dir, exist_ok=True)

    added = []
    for src_path in file_paths:
        if not os.path.isfile(src_path):
            continue
        filename = os.path.basename(src_path)
        dst_path = os.path.join(res_dir, filename)
        if os.path.abspath(src_path) != os.path.abspath(dst_path):
            shutil.copy2(src_path, dst_path)
        rel_path = os.path.relpath(dst_path, Config.STORAGE_DIR).replace("\\", "/")
        added.append({"path": dst_path, "relativePath": rel_path, "filename": filename})

    # Update state
    resources = state.setdefault("resources", {})
    news_key = str(news_id)
    res_entry = resources.setdefault(news_key, {"vidClips": [], "images": []})
    list_key = "vidClips" if kind == "vid_clips" else "images"
    existing_paths = {item.get("relativePath") for item in res_entry.get(list_key, [])}
    for item in added:
        if item["relativePath"] not in existing_paths:
            res_entry[list_key].append(item)

    save_bulletin_state(bulletin_id, state)
    return res_entry


def get_resource_pool(bulletin_id: str, news_id: int) -> dict:
    """Get resource pool for a specific news item.

    Returns: {"vid_clips": [{"path": ..., ...}], "img_clips": [{"path": ..., ...}]}
    """
    state = load_bulletin_state(bulletin_id)
    if not state:
        return {"vid_clips": [], "img_clips": []}

    resources = state.get("resources", {})
    news_key = str(news_id)
    entry = resources.get(news_key, {})

    vid_clips = []
    for item in entry.get("vidClips", []):
        abs_path = os.path.join(Config.STORAGE_DIR, item.get("relativePath", ""))
        if os.path.isfile(abs_path):
            vid_clips.append({"path": abs_path, "relative_path": item.get("relativePath", "")})

    img_clips = []
    for item in entry.get("images", []):
        abs_path = os.path.join(Config.STORAGE_DIR, item.get("relativePath", ""))
        if os.path.isfile(abs_path):
            img_clips.append({"path": abs_path, "relative_path": item.get("relativePath", "")})

    return {"vid_clips": vid_clips, "img_clips": img_clips}


def get_all_resource_pools(bulletin_id: str) -> dict[int, dict]:
    """Get resource pools for all news items."""
    state = load_bulletin_state(bulletin_id)
    if not state:
        return {}

    pools = {}
    for item in state.get("parsedScript", {}).get("newsItems", []):
        news_id = item["id"]
        pools[news_id] = get_resource_pool(bulletin_id, news_id)
    return pools


def get_resource_summary(bulletin_id: str) -> dict:
    """Summarize resource counts per news item."""
    state = load_bulletin_state(bulletin_id)
    if not state:
        return {}

    summary = {}
    resources = state.get("resources", {})
    for news_key, entry in resources.items():
        summary[news_key] = {
            "vidClips": len(entry.get("vidClips", [])),
            "images": len(entry.get("images", [])),
        }
    return summary


# ---------------------------------------------------------------------------
# Segment-level resources (intro / detail_intro / outro)
# ---------------------------------------------------------------------------

_VALID_SEGMENT_TYPES = {"intro", "detail_intro", "outro"}


def _segment_resources_dir(bulletin_id: str, segment_type: str) -> str:
    return os.path.join(_bulletin_dir(bulletin_id), "resources", f"segment_{segment_type}")


def add_segment_resource_files(
    bulletin_id: str,
    segment_type: str,
    kind: str,
    file_paths: list[str],
) -> dict:
    """Add resource files for a special segment (intro/detail_intro/outro).

    Args:
        segment_type: "intro", "detail_intro", or "outro"
        kind: "vid_clips" or "images"

    Returns updated resource entry for this segment.
    """
    if segment_type not in _VALID_SEGMENT_TYPES:
        raise ValueError(f"segment_type khong hop le: {segment_type}")

    state = load_bulletin_state(bulletin_id)
    if not state:
        raise ValueError(f"Bulletin '{bulletin_id}' khong ton tai.")

    res_dir = os.path.join(_segment_resources_dir(bulletin_id, segment_type), kind)
    os.makedirs(res_dir, exist_ok=True)

    added = []
    for src_path in file_paths:
        if not os.path.isfile(src_path):
            continue
        filename = os.path.basename(src_path)
        dst_path = os.path.join(res_dir, filename)
        if os.path.abspath(src_path) != os.path.abspath(dst_path):
            shutil.copy2(src_path, dst_path)
        rel_path = os.path.relpath(dst_path, Config.STORAGE_DIR).replace("\\", "/")
        added.append({"path": dst_path, "relativePath": rel_path, "filename": filename})

    # Update state
    state_key = f"{segment_type}Resources"
    res_entry = state.setdefault(state_key, {"vidClips": [], "images": []})
    list_key = "vidClips" if kind == "vid_clips" else "images"
    existing_paths = {item.get("relativePath") for item in res_entry.get(list_key, [])}
    for item in added:
        if item["relativePath"] not in existing_paths:
            res_entry[list_key].append(item)

    save_bulletin_state(bulletin_id, state)
    return res_entry


def get_segment_resource_pool(bulletin_id: str, segment_type: str) -> dict:
    """Get resource pool for a special segment.

    Returns: {"vid_clips": [...], "img_clips": [...]}
    """
    if segment_type not in _VALID_SEGMENT_TYPES:
        return {"vid_clips": [], "img_clips": []}

    state = load_bulletin_state(bulletin_id)
    if not state:
        return {"vid_clips": [], "img_clips": []}

    state_key = f"{segment_type}Resources"
    entry = state.get(state_key, {})

    vid_clips = []
    for item in entry.get("vidClips", []):
        abs_path = os.path.join(Config.STORAGE_DIR, item.get("relativePath", ""))
        if os.path.isfile(abs_path):
            vid_clips.append({"path": abs_path, "relative_path": item.get("relativePath", "")})

    img_clips = []
    for item in entry.get("images", []):
        abs_path = os.path.join(Config.STORAGE_DIR, item.get("relativePath", ""))
        if os.path.isfile(abs_path):
            img_clips.append({"path": abs_path, "relative_path": item.get("relativePath", "")})

    return {"vid_clips": vid_clips, "img_clips": img_clips}


def get_segment_resource_summary(bulletin_id: str) -> dict:
    """Summarize resource counts for special segments."""
    state = load_bulletin_state(bulletin_id)
    if not state:
        return {}

    summary = {}
    for seg_type in _VALID_SEGMENT_TYPES:
        state_key = f"{seg_type}Resources"
        entry = state.get(state_key, {})
        summary[seg_type] = {
            "vidClips": len(entry.get("vidClips", [])),
            "images": len(entry.get("images", [])),
        }
    return summary


# ---------------------------------------------------------------------------
# Audio cache (reuse same voiceId audio across channels)
# ---------------------------------------------------------------------------

def _audio_cache_dir(bulletin_id: str, voice_id: str) -> str:
    """Audio directory keyed by voiceId for reuse."""
    return os.path.join(_bulletin_dir(bulletin_id), "audio_cache", voice_id)


def get_cached_audio(bulletin_id: str, voice_id: str, segment_key: str) -> str | None:
    """Check if audio for this segment+voice already exists."""
    cache_dir = _audio_cache_dir(bulletin_id, voice_id)
    audio_path = os.path.join(cache_dir, f"{segment_key}.mp3")
    if os.path.isfile(audio_path):
        return audio_path
    return None


def save_audio_to_cache(
    bulletin_id: str, voice_id: str, segment_key: str, audio_src_path: str
) -> str:
    """Copy generated audio to the voice-specific cache directory."""
    cache_dir = _audio_cache_dir(bulletin_id, voice_id)
    os.makedirs(cache_dir, exist_ok=True)
    dst_path = os.path.join(cache_dir, f"{segment_key}.mp3")
    if os.path.abspath(audio_src_path) != os.path.abspath(dst_path):
        shutil.copy2(audio_src_path, dst_path)
    return dst_path
