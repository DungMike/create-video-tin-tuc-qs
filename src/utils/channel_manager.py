"""Channel Manager — JSON-file-based channel / group registry for News Bulletins.

Each *channel* maps 1-to-1 with a YouTube channel.  A *group* is a label used
for quick filtering (e.g. "Tin quân sự tiếng Việt").
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from src.config import Config
from src.utils.logger import logger

_CHANNELS_DIR = os.path.join(Config.STORAGE_DIR, "channels")
_INDEX_PATH = os.path.join(_CHANNELS_DIR, "index.json")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dir():
    os.makedirs(_CHANNELS_DIR, exist_ok=True)


def _load_index() -> dict:
    _ensure_dir()
    if not os.path.isfile(_INDEX_PATH):
        return {"groups": [], "channels": []}
    try:
        with open(_INDEX_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"groups": [], "channels": []}
        data.setdefault("groups", [])
        data.setdefault("channels", [])
        for channel in data["channels"]:
            channel.setdefault("introVideoPath", "")
            channel.setdefault("transitionVideoPath", "")
            channel.setdefault("outroVideoPath", "")
            channel.setdefault("decorVideoId", "")
            channel.setdefault("sourceText", "")
            channel.setdefault("isActive", True)
        return data
    except (json.JSONDecodeError, OSError):
        return {"groups": [], "channels": []}


def _save_index(data: dict):
    _ensure_dir()
    with open(_INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------

def list_groups() -> list[dict]:
    return list(_load_index().get("groups", []))


def get_group(group_id: str) -> dict | None:
    for group in _load_index().get("groups", []):
        if group.get("groupId") == group_id:
            return group
    return None


def create_group(group_name: str, language: str = "") -> dict:
    index = _load_index()
    for existing in index["groups"]:
        if existing.get("groupName") == group_name:
            raise ValueError(f"Group name '{group_name}' da ton tai.")
    group = {
        "groupId": f"grp_{uuid.uuid4().hex[:12]}",
        "groupName": group_name.strip(),
        "language": language.strip(),
        "createdAt": _utc_now(),
    }
    index["groups"].append(group)
    _save_index(index)
    logger.info(f"[ChannelManager] Created group '{group_name}' ({group['groupId']})")
    return group


def update_group(group_id: str, updates: dict) -> dict:
    index = _load_index()
    for group in index["groups"]:
        if group.get("groupId") == group_id:
            if "groupName" in updates:
                new_name = updates["groupName"].strip()
                for other in index["groups"]:
                    if other["groupId"] != group_id and other.get("groupName") == new_name:
                        raise ValueError(f"Group name '{new_name}' da ton tai.")
                group["groupName"] = new_name
            if "language" in updates:
                group["language"] = updates["language"].strip()
            group["updatedAt"] = _utc_now()
            _save_index(index)
            return group
    raise ValueError(f"Group '{group_id}' khong ton tai.")


def delete_group(group_id: str) -> bool:
    index = _load_index()
    original_len = len(index["groups"])
    index["groups"] = [g for g in index["groups"] if g.get("groupId") != group_id]
    if len(index["groups"]) == original_len:
        raise ValueError(f"Group '{group_id}' khong ton tai.")
    # Clear groupId references from channels
    for channel in index["channels"]:
        if channel.get("groupId") == group_id:
            channel["groupId"] = ""
    _save_index(index)
    logger.info(f"[ChannelManager] Deleted group {group_id}")
    return True


# ---------------------------------------------------------------------------
# Channel CRUD
# ---------------------------------------------------------------------------

def list_channels() -> list[dict]:
    return list(_load_index().get("channels", []))


def get_channel(channel_id: str) -> dict | None:
    for channel in _load_index().get("channels", []):
        if channel.get("channelId") == channel_id:
            return channel
    return None


def create_channel(
    channel_name: str,
    group_id: str = "",
    voice_id: str = "",
    intro_video_path: str = "",
    transition_video_path: str = "",
    outro_video_path: str = "",
    decor_video_id: str = "",
    source_text: str = "",
    is_active: bool = True,
) -> dict:
    index = _load_index()
    for existing in index["channels"]:
        if existing.get("channelName") == channel_name:
            raise ValueError(f"Channel name '{channel_name}' da ton tai.")
    channel = {
        "channelId": f"ch_{uuid.uuid4().hex[:12]}",
        "channelName": channel_name.strip(),
        "groupId": group_id.strip(),
        "voiceId": voice_id.strip(),
        "introVideoPath": intro_video_path.strip(),
        "transitionVideoPath": transition_video_path.strip(),
        "outroVideoPath": outro_video_path.strip(),
        "decorVideoId": decor_video_id.strip(),
        "sourceText": source_text.strip(),
        "isActive": bool(is_active),
        "createdAt": _utc_now(),
    }
    index["channels"].append(channel)
    _save_index(index)
    logger.info(f"[ChannelManager] Created channel '{channel_name}' ({channel['channelId']})")
    return channel


def update_channel(channel_id: str, updates: dict) -> dict:
    index = _load_index()
    for channel in index["channels"]:
        if channel.get("channelId") == channel_id:
            if "channelName" in updates:
                new_name = updates["channelName"].strip()
                for other in index["channels"]:
                    if other["channelId"] != channel_id and other.get("channelName") == new_name:
                        raise ValueError(f"Channel name '{new_name}' da ton tai.")
                channel["channelName"] = new_name
            for field in (
                "groupId",
                "voiceId",
                "introVideoPath",
                "transitionVideoPath",
                "outroVideoPath",
                "decorVideoId",
                "sourceText",
            ):
                if field in updates:
                    channel[field] = str(updates[field]).strip()
            if "isActive" in updates:
                channel["isActive"] = bool(updates["isActive"])
            channel["updatedAt"] = _utc_now()
            _save_index(index)
            return channel
    raise ValueError(f"Channel '{channel_id}' khong ton tai.")


def delete_channel(channel_id: str) -> bool:
    index = _load_index()
    original_len = len(index["channels"])
    index["channels"] = [c for c in index["channels"] if c.get("channelId") != channel_id]
    if len(index["channels"]) == original_len:
        raise ValueError(f"Channel '{channel_id}' khong ton tai.")
    _save_index(index)
    logger.info(f"[ChannelManager] Deleted channel {channel_id}")
    return True


def list_channels_by_group(group_id: str) -> list[dict]:
    return [c for c in list_channels() if c.get("groupId") == group_id]


def get_full_registry() -> dict:
    """Return the full registry with groups and channels."""
    return _load_index()
