"""Shared storage helpers for the Story Video clip library."""

import json
import os
import threading

from src.config import Config
from src.utils.file_manager import remove_file_with_retries
from src.utils.logger import logger


story_library_index_lock = threading.RLock()


def _index_path() -> str:
    return os.path.join(Config.STORY_LIBRARY_DIR, "index.json")


def _clips_dir() -> str:
    return os.path.join(Config.STORY_LIBRARY_DIR, "clips")


def load_story_library_index() -> dict:
    """Load the Story Video library index under the shared library lock."""
    with story_library_index_lock:
        index_path = _index_path()
        if not os.path.isfile(index_path):
            return {"assets": []}

        try:
            with open(index_path, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Could not load Story Video library index: {index_path} | {exc}")
            return {"assets": []}

        if not isinstance(data, dict) or not isinstance(data.get("assets", []), list):
            logger.warning(f"Invalid Story Video library index format: {index_path}")
            return {"assets": []}
        data.setdefault("assets", [])
        return data


def save_story_library_index(data: dict):
    """Save the Story Video library index atomically under the shared lock."""
    with story_library_index_lock:
        os.makedirs(Config.STORY_LIBRARY_DIR, exist_ok=True)
        index_path = _index_path()
        tmp_path = index_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, indent=2, ensure_ascii=False)
        os.replace(tmp_path, index_path)


def _asset_absolute_path(asset: dict) -> str | None:
    relative_path = str(asset.get("relative_path") or "").strip()
    if not relative_path:
        return ""

    library_root = os.path.normcase(os.path.realpath(Config.STORY_LIBRARY_DIR))
    candidate = os.path.normcase(os.path.realpath(os.path.join(Config.STORY_LIBRARY_DIR, relative_path)))
    try:
        if os.path.commonpath([library_root, candidate]) != library_root:
            logger.warning(f"Refusing to delete Story Video clip outside library: {relative_path}")
            return None
    except ValueError:
        logger.warning(f"Refusing to delete Story Video clip on another drive: {relative_path}")
        return None
    return candidate


def _remove_asset_file(asset: dict) -> bool:
    clip_path = _asset_absolute_path(asset)
    if clip_path is None:
        return False
    if not clip_path:
        return True
    try:
        return remove_file_with_retries(clip_path)
    except OSError as exc:
        logger.warning(f"Could not remove Story Video clip: {clip_path} | {exc}")
        return False


def _clean_orphan_clip_files(protected_paths: set[str]) -> list[str]:
    clips_dir = _clips_dir()
    if not os.path.isdir(clips_dir):
        return []

    clips_root = os.path.normcase(os.path.realpath(clips_dir))
    failed_files: list[str] = []
    for root, _dirs, filenames in os.walk(clips_dir):
        for filename in filenames:
            file_path = os.path.abspath(os.path.join(root, filename))
            real_path = os.path.normcase(os.path.realpath(file_path))
            try:
                if os.path.commonpath([clips_root, real_path]) != clips_root:
                    logger.warning(f"Refusing to delete orphan clip outside Story Video clips directory: {file_path}")
                    failed_files.append(os.path.relpath(file_path, Config.STORY_LIBRARY_DIR).replace("\\", "/"))
                    continue
            except ValueError:
                failed_files.append(os.path.relpath(file_path, Config.STORY_LIBRARY_DIR).replace("\\", "/"))
                continue
            if real_path in protected_paths:
                continue
            try:
                removed = remove_file_with_retries(file_path)
            except OSError as exc:
                logger.warning(f"Could not remove orphan Story Video clip: {file_path} | {exc}")
                removed = False
            if not removed:
                failed_files.append(os.path.relpath(file_path, Config.STORY_LIBRARY_DIR).replace("\\", "/"))
    return failed_files


def delete_story_library_assets(
    clip_ids: list[str] | None = None,
    *,
    delete_all: bool = False,
    clean_orphans: bool = False,
) -> dict:
    """Delete selected or all Story Video library assets and return a summary."""
    requested_ids: list[str] = []
    seen_ids: set[str] = set()
    for clip_id in clip_ids or []:
        normalized = str(clip_id or "").strip()
        if normalized and normalized not in seen_ids:
            seen_ids.add(normalized)
            requested_ids.append(normalized)

    with story_library_index_lock:
        index = load_story_library_index()
        assets = list(index.get("assets", []))
        target_ids = {str(asset.get("id") or "") for asset in assets} if delete_all else set(requested_ids)
        existing_ids = {str(asset.get("id") or "") for asset in assets}
        missing_ids = [] if delete_all else [clip_id for clip_id in requested_ids if clip_id not in existing_ids]

        deleted_positions: set[int] = set()
        failed_ids: list[str] = []
        for position, asset in enumerate(assets):
            asset_id = str(asset.get("id") or "")
            if asset_id not in target_ids:
                continue
            if _remove_asset_file(asset):
                deleted_positions.add(position)
            elif asset_id and asset_id not in failed_ids:
                failed_ids.append(asset_id)

        remaining_assets = [
            asset
            for position, asset in enumerate(assets)
            if position not in deleted_positions
        ]
        index["assets"] = remaining_assets
        save_story_library_index(index)

        failed_files: list[str] = []
        if clean_orphans:
            protected_paths = {
                path
                for asset in remaining_assets
                if (path := _asset_absolute_path(asset))
            }
            failed_files = _clean_orphan_clip_files(protected_paths)

        return {
            "requestedCount": len(assets) if delete_all else len(requested_ids),
            "deletedCount": len(deleted_positions),
            "remainingCount": len(remaining_assets),
            "missingClipIds": missing_ids,
            "failedClipIds": failed_ids,
            "failedFiles": failed_files,
        }
