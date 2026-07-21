import json
import os
import re
import shutil
import time
from datetime import datetime

from src.config import Config
from src.utils.logger import logger


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _default_library_index() -> dict:
    return {"assets": []}


def ensure_library_storage() -> dict:
    library_dir = os.path.join(Config.STORAGE_DIR, "library")
    clips_dir = os.path.join(library_dir, "clips")
    index_path = os.path.join(library_dir, "index.json")
    os.makedirs(clips_dir, exist_ok=True)
    if not os.path.exists(index_path):
        with open(index_path, "w", encoding="utf-8") as file_obj:
            json.dump(_default_library_index(), file_obj, ensure_ascii=False, indent=2)
    return {
        "library": library_dir,
        "clips": clips_dir,
        "index": index_path,
    }


def setup_directories(job_id: str) -> dict:
    """Create and return necessary directories for a job."""
    library_dirs = ensure_library_storage()
    dirs_to_create = {
        "job": os.path.join(Config.STORAGE_DIR, "jobs", job_id),
        "audio": os.path.join(Config.STORAGE_DIR, "audio", job_id),
        "raw_images": os.path.join(Config.STORAGE_DIR, "raw_images", job_id),
        "raw_videos": os.path.join(Config.STORAGE_DIR, "raw_videos", job_id),
        "img_clips": os.path.join(Config.STORAGE_DIR, "clips", "img_clips", job_id),
        "vid_clips": os.path.join(Config.STORAGE_DIR, "clips", "vid_clips", job_id),
        "temp": os.path.join(Config.STORAGE_DIR, "temp", job_id),
        "output": Config.OUTPUT_DIR,
        "library": library_dirs["library"],
        "library_clips": library_dirs["clips"],
    }

    for path in dirs_to_create.values():
        os.makedirs(path, exist_ok=True)

    return dirs_to_create


def get_job_manifest_path(job_id: str) -> str:
    return os.path.join(Config.STORAGE_DIR, "jobs", job_id, "manifest.json")


def get_library_index_path() -> str:
    return ensure_library_storage()["index"]


def save_job_manifest(job_id: str, data: dict):
    manifest_path = get_job_manifest_path(job_id)
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)


def load_job_manifest(job_id: str) -> dict:
    manifest_path = get_job_manifest_path(job_id)
    with open(manifest_path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def load_library_index() -> dict:
    index_path = get_library_index_path()
    with open(index_path, "r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    if not isinstance(data, dict):
        return _default_library_index()
    data.setdefault("assets", [])
    return data


def save_library_index(data: dict):
    index_path = get_library_index_path()
    with open(index_path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)


def _is_within(child: str, parent: str) -> bool:
    """True if `child` is inside `parent`. Handles different drives on Windows."""
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        # Different drives (e.g. F: vs E:) raise ValueError on Windows.
        return False


def storage_relative_path(abs_path: str) -> str:
    """Convert an absolute path to a path servable via the /media/ route.

    Files under STORAGE_DIR are returned relative to it (unchanged behaviour).
    Files under OUTPUT_DIR — which may live on a different drive — are returned
    with an ``output/`` prefix. When OUTPUT_DIR is the default ``./storage/output``
    this yields exactly the same ``output/...`` strings as before, so the mapping
    is backward compatible. Anything else falls back to the absolute path so
    callers never crash on cross-drive inputs.
    """
    abs_norm = os.path.abspath(abs_path)
    storage_root = os.path.abspath(Config.STORAGE_DIR)
    if _is_within(abs_norm, storage_root):
        return os.path.relpath(abs_norm, storage_root).replace("\\", "/")

    output_root = os.path.abspath(Config.OUTPUT_DIR)
    if _is_within(abs_norm, output_root):
        rel = os.path.relpath(abs_norm, output_root).replace("\\", "/")
        return "output" if rel == "." else f"output/{rel}"

    return abs_norm.replace("\\", "/")


def storage_absolute_path(rel_path: str) -> str:
    """Inverse of :func:`storage_relative_path`.

    Absolute inputs are returned normalized. ``output/...`` paths resolve against
    OUTPUT_DIR (which may be on another drive); everything else resolves against
    STORAGE_DIR. With the default OUTPUT_DIR under storage this is a no-op change.
    """
    norm = (rel_path or "").replace("\\", "/")
    if os.path.isabs(norm):
        return os.path.normpath(norm)
    if norm == "output" or norm.startswith("output/"):
        sub = norm[len("output"):].lstrip("/")
        return os.path.normpath(os.path.join(os.path.abspath(Config.OUTPUT_DIR), sub))
    return os.path.normpath(os.path.join(os.path.abspath(Config.STORAGE_DIR), norm))


def normalize_tags(raw_values: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for value in raw_values:
        tag = " ".join((value or "").strip().lower().split())
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def collect_library_tags(library_index: dict) -> list[str]:
    tags = set()
    for asset in library_index.get("assets", []):
        tags.update(normalize_tags(asset.get("tags", [])))
    return sorted(tags)


def _asset_id_for_clip(job_id: str, clip_id: str) -> str:
    safe_job_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(job_id or "")).strip("._") or "unknown"
    safe_clip_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(clip_id or "")).strip("._") or "clip"
    return f"job_{safe_job_id}_{safe_clip_id}"


def upsert_library_assets(job_id: str, clips_with_tags: list[dict]) -> list[dict]:
    if not clips_with_tags:
        return []

    library_dirs = ensure_library_storage()
    library_index = load_library_index()
    assets = library_index.get("assets", [])
    asset_lookup = {asset["asset_id"]: asset for asset in assets if asset.get("asset_id")}
    updated_assets = []

    for clip in clips_with_tags:
        tags = normalize_tags(clip.get("tags", []))
        if not tags:
            continue

        asset_id = _asset_id_for_clip(job_id, clip["id"])
        source_path = storage_absolute_path(clip["relative_path"])
        if not os.path.isfile(source_path):
            logger.warning(f"Cannot promote missing clip to library: {source_path}")
            continue

        extension = os.path.splitext(source_path)[1] or ".mp4"
        target_path = os.path.join(library_dirs["clips"], f"{asset_id}{extension}")
        shutil.copy2(source_path, target_path)

        existing_asset = asset_lookup.get(asset_id)
        created_at = existing_asset.get("created_at") if existing_asset else _utc_now()
        merged_tags = normalize_tags((existing_asset or {}).get("tags", []) + tags)
        asset_record = {
            "asset_id": asset_id,
            "relative_path": storage_relative_path(target_path),
            "source_job_id": job_id,
            "source_clip_id": clip["id"],
            "source_name": clip.get("source_name"),
            "start": clip.get("start"),
            "end": clip.get("end"),
            "duration": clip.get("duration"),
            "tags": merged_tags,
            "created_at": created_at,
            "updated_at": _utc_now(),
        }
        asset_lookup[asset_id] = asset_record
        updated_assets.append(asset_record)

    library_index["assets"] = sorted(asset_lookup.values(), key=lambda asset: asset["asset_id"])
    save_library_index(library_index)
    return updated_assets


def remove_file_with_retries(path: str, retries: int = 5, delay_seconds: float = 0.4) -> bool:
    if not os.path.exists(path):
        return True

    last_error = None
    for attempt in range(retries):
        try:
            os.remove(path)
            return True
        except PermissionError as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(delay_seconds)
        except FileNotFoundError:
            return True

    logger.warning(f"Could not remove file after retries: {path} | {last_error}")
    return False


def clear_directory(path: str):
    if not os.path.isdir(path):
        return

    for entry in os.listdir(path):
        full_path = os.path.join(path, entry)
        if os.path.isdir(full_path):
            try:
                shutil.rmtree(full_path)
            except Exception as exc:
                logger.warning(f"Could not remove directory: {full_path} | {exc}")
        else:
            remove_file_with_retries(full_path)


def cleanup_temp_files(job_id: str):
    temp_dir = os.path.join(Config.STORAGE_DIR, "temp", job_id)
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
        logger.info(f"Cleaned up temp dir: {temp_dir}")


def cleanup_review_video_assets(dirs: dict, selected_clip_paths: list[str]):
    selected = {os.path.normcase(os.path.abspath(path)) for path in selected_clip_paths}

    if os.path.isdir(dirs["vid_clips"]):
        for filename in os.listdir(dirs["vid_clips"]):
            full_path = os.path.join(dirs["vid_clips"], filename)
            if os.path.normcase(os.path.abspath(full_path)) not in selected and os.path.isfile(full_path):
                remove_file_with_retries(full_path)

    clear_directory(dirs["raw_videos"])


def cleanup_job_files(job_id: str):
    """Remove temporary files and assets for a job after render."""
    try:
        cleanup_temp_files(job_id)

        # Remove other intermediate directories for the job
        dirs_to_clean = [
            os.path.join(Config.STORAGE_DIR, "audio", job_id),
            os.path.join(Config.STORAGE_DIR, "raw_images", job_id),
            os.path.join(Config.STORAGE_DIR, "raw_videos", job_id),
            os.path.join(Config.STORAGE_DIR, "clips", "img_clips", job_id),
            os.path.join(Config.STORAGE_DIR, "clips", "vid_clips", job_id),
        ]

        for path in dirs_to_clean:
            if os.path.exists(path):
                try:
                    shutil.rmtree(path)
                    logger.info(f"Cleaned up job intermediate dir: {path}")
                except Exception as exc:
                    logger.warning(f"Could not clean up intermediate dir: {path} | {exc}")

    except Exception as exc:
        logger.error(f"Error cleaning up job {job_id}: {exc}")
