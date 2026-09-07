"""Shared storage helpers for the Story Video clip libraries.

Supports multiple named libraries ("folders"). Each library is a directory with
its own ``index.json`` + ``clips/``. The Default library is special: its root IS
``Config.STORY_LIBRARY_DIR`` itself (so existing clips need no migration); every
other library lives under ``Config.STORY_LIBRARY_DIR/<library_id>/``. A registry
file (``libraries.json``) lists all libraries.

Every public helper takes an optional ``library_id`` that defaults to the Default
library, so callers that omit it keep the original single-library behaviour.
"""

import json
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone

from src.config import Config
from src.utils.file_manager import remove_file_with_retries
from src.utils.logger import logger


# Default library lock kept under its historical name because other modules
# import it directly. Non-default libraries get their own locks on demand.
story_library_index_lock = threading.RLock()
_registry_lock = threading.RLock()
_library_locks_guard = threading.Lock()
_library_locks: dict[str, threading.RLock] = {}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Mirrors file_manager.remove_file_with_retries: Windows can transiently deny a
# rename/delete while another handle (often an antivirus scan) is open.
_INDEX_REPLACE_RETRIES = 5
_INDEX_REPLACE_DELAY_SECONDS = 0.4


# --------------------------------------------------------------------------- #
# Library id / path resolution
# --------------------------------------------------------------------------- #
def _registry_path() -> str:
    # Resolved at call time (not a static Config attribute) so it always tracks
    # the current STORY_LIBRARY_DIR, including when overridden in tests.
    return os.path.join(Config.STORY_LIBRARY_DIR, "libraries.json")


def _read_registry_raw() -> dict:
    """Read libraries.json without creating it. Returns {"libraries": [...]}"""
    path = _registry_path()
    if not os.path.isfile(path):
        return {"libraries": []}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Could not load Story Video libraries registry: {path} | {exc}")
        return {"libraries": []}
    if not isinstance(data, dict) or not isinstance(data.get("libraries", []), list):
        logger.warning(f"Invalid Story Video libraries registry format: {path}")
        return {"libraries": []}
    data.setdefault("libraries", [])
    return data


def resolve_library_id(library_id=None) -> str:
    """Map any input to a usable library id, falling back to the current default.

    A known, well-formed id resolves to itself. Blank / malformed / unknown ids
    fall back to whichever library is currently flagged default (see
    ``get_default_library_id``) — which is "" when no library exists at all. Used by
    the path helpers so generation paths stay resilient; management routes should
    instead call ``get_library`` and 404 explicitly on unknown ids.
    """
    lid = str(library_id or "").strip()
    if lid and _ID_RE.match(lid):
        for lib in _read_registry_raw().get("libraries", []):
            if lib.get("id") == lid:
                return lid
    return get_default_library_id()


def _library_root(library_id=None) -> str:
    lid = resolve_library_id(library_id)
    if not lid:
        # No resolvable library (registry intentionally empty). Return a path that
        # never exists and is never the shared top-level dir, so index reads yield
        # empty and no operation ever touches STORY_LIBRARY_DIR itself.
        return os.path.join(Config.STORY_LIBRARY_DIR, "__none__")
    return os.path.join(Config.STORY_LIBRARY_DIR, lid)


def story_library_root(library_id=None) -> str:
    """Public accessor for a library's root directory (used by the pipeline)."""
    return _library_root(library_id)


def _index_path(library_id=None) -> str:
    return os.path.join(_library_root(library_id), "index.json")


def _clips_dir(library_id=None) -> str:
    return os.path.join(_library_root(library_id), "clips")


def _index_lock(library_id=None) -> threading.RLock:
    lid = resolve_library_id(library_id)
    if lid == Config.STORY_LIBRARY_DEFAULT_ID:
        return story_library_index_lock
    with _library_locks_guard:
        lock = _library_locks.get(lid)
        if lock is None:
            lock = threading.RLock()
            _library_locks[lid] = lock
        return lock


# --------------------------------------------------------------------------- #
# Per-library index I/O
# --------------------------------------------------------------------------- #
def load_story_library_index(library_id=None) -> dict:
    """Load a Story Video library index under that library's lock."""
    with _index_lock(library_id):
        index_path = _index_path(library_id)
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


def save_story_library_index(data: dict, library_id=None):
    """Save a Story Video library index atomically under that library's lock.

    The final rename is retried: on Windows ``os.replace`` raises WinError 5 when
    anything holds the target open for even a moment (observed with an antivirus
    scanning the freshly written .tmp during a bulk import). One transient lock
    would otherwise lose a whole batch of freshly ingested clips.
    """
    with _index_lock(library_id):
        root = _library_root(library_id)
        os.makedirs(root, exist_ok=True)
        index_path = _index_path(library_id)
        tmp_path = index_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, indent=2, ensure_ascii=False)

        last_error: OSError | None = None
        for attempt in range(_INDEX_REPLACE_RETRIES):
            try:
                os.replace(tmp_path, index_path)
                return
            except PermissionError as exc:
                last_error = exc
                if attempt < _INDEX_REPLACE_RETRIES - 1:
                    time.sleep(_INDEX_REPLACE_DELAY_SECONDS)
        logger.error(
            f"Could not replace library index after {_INDEX_REPLACE_RETRIES} "
            f"attempts: {index_path} | {last_error}"
        )
        raise last_error


def count_library_clips(library_id=None) -> int:
    return len(load_story_library_index(library_id).get("assets", []))


def _asset_absolute_path(asset: dict, library_id=None) -> str | None:
    relative_path = str(asset.get("relative_path") or "").strip()
    if not relative_path:
        return ""

    root = _library_root(library_id)
    library_root = os.path.normcase(os.path.realpath(root))
    candidate = os.path.normcase(os.path.realpath(os.path.join(root, relative_path)))
    try:
        if os.path.commonpath([library_root, candidate]) != library_root:
            logger.warning(f"Refusing to delete Story Video clip outside library: {relative_path}")
            return None
    except ValueError:
        logger.warning(f"Refusing to delete Story Video clip on another drive: {relative_path}")
        return None
    return candidate


def _remove_asset_file(asset: dict, library_id=None) -> bool:
    clip_path = _asset_absolute_path(asset, library_id)
    if clip_path is None:
        return False
    if not clip_path:
        return True
    try:
        return remove_file_with_retries(clip_path)
    except OSError as exc:
        logger.warning(f"Could not remove Story Video clip: {clip_path} | {exc}")
        return False


def _clean_orphan_clip_files(protected_paths: set[str], library_id=None) -> list[str]:
    root = _library_root(library_id)
    clips_dir = _clips_dir(library_id)
    if not os.path.isdir(clips_dir):
        return []

    clips_root = os.path.normcase(os.path.realpath(clips_dir))
    failed_files: list[str] = []
    for walk_root, _dirs, filenames in os.walk(clips_dir):
        for filename in filenames:
            file_path = os.path.abspath(os.path.join(walk_root, filename))
            real_path = os.path.normcase(os.path.realpath(file_path))
            try:
                if os.path.commonpath([clips_root, real_path]) != clips_root:
                    logger.warning(f"Refusing to delete orphan clip outside Story Video clips directory: {file_path}")
                    failed_files.append(os.path.relpath(file_path, root).replace("\\", "/"))
                    continue
            except ValueError:
                failed_files.append(os.path.relpath(file_path, root).replace("\\", "/"))
                continue
            if real_path in protected_paths:
                continue
            try:
                removed = remove_file_with_retries(file_path)
            except OSError as exc:
                logger.warning(f"Could not remove orphan Story Video clip: {file_path} | {exc}")
                removed = False
            if not removed:
                failed_files.append(os.path.relpath(file_path, root).replace("\\", "/"))
    return failed_files


def delete_story_library_assets(
    clip_ids: list[str] | None = None,
    *,
    library_id=None,
    delete_all: bool = False,
    clean_orphans: bool = False,
) -> dict:
    """Delete selected or all assets in one library and return a summary."""
    requested_ids: list[str] = []
    seen_ids: set[str] = set()
    for clip_id in clip_ids or []:
        normalized = str(clip_id or "").strip()
        if normalized and normalized not in seen_ids:
            seen_ids.add(normalized)
            requested_ids.append(normalized)

    with _index_lock(library_id):
        index = load_story_library_index(library_id)
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
            if _remove_asset_file(asset, library_id):
                deleted_positions.add(position)
            elif asset_id and asset_id not in failed_ids:
                failed_ids.append(asset_id)

        remaining_assets = [
            asset
            for position, asset in enumerate(assets)
            if position not in deleted_positions
        ]
        index["assets"] = remaining_assets
        save_story_library_index(index, library_id)

        failed_files: list[str] = []
        if clean_orphans:
            protected_paths = {
                path
                for asset in remaining_assets
                if (path := _asset_absolute_path(asset, library_id))
            }
            failed_files = _clean_orphan_clip_files(protected_paths, library_id)

        return {
            "requestedCount": len(assets) if delete_all else len(requested_ids),
            "deletedCount": len(deleted_positions),
            "remainingCount": len(remaining_assets),
            "missingClipIds": missing_ids,
            "failedClipIds": failed_ids,
            "failedFiles": failed_files,
        }


# --------------------------------------------------------------------------- #
# Library registry CRUD
# --------------------------------------------------------------------------- #
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_registry(data: dict):
    with _registry_lock:
        path = _registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)


def _default_library_record() -> dict:
    return {
        "id": Config.STORY_LIBRARY_DEFAULT_ID,
        "name": Config.STORY_LIBRARY_DEFAULT_NAME,
        "isDefault": True,
        "createdAt": _utc_now_iso(),
    }


def _migrate_legacy_default_layout():
    """Move legacy loose top-level ``clips/`` + ``index.json`` into ``default/``.

    Historically the Default library's root WAS ``Config.STORY_LIBRARY_DIR`` itself.
    Now every library (including default) lives under
    ``Config.STORY_LIBRARY_DIR/<id>/``. This one-time move makes the default library
    a normal, self-contained (and therefore deletable) folder. Idempotent and
    crash-resumable: each of ``clips/`` and ``index.json`` is checked and moved
    independently, and a same-volume ``shutil.move`` of a directory is a fast rename,
    not a per-file copy.
    """
    root = Config.STORY_LIBRARY_DIR
    legacy_clips = os.path.join(root, "clips")
    legacy_index = os.path.join(root, "index.json")
    if not os.path.isdir(legacy_clips) and not os.path.isfile(legacy_index):
        return

    default_dir = os.path.join(root, Config.STORY_LIBRARY_DEFAULT_ID)
    os.makedirs(default_dir, exist_ok=True)

    dest_clips = os.path.join(default_dir, "clips")
    if os.path.isdir(legacy_clips) and not os.path.exists(dest_clips):
        shutil.move(legacy_clips, dest_clips)

    dest_index = os.path.join(default_dir, "index.json")
    if os.path.isfile(legacy_index) and not os.path.exists(dest_index):
        shutil.move(legacy_index, dest_index)


def ensure_libraries_registry() -> dict:
    """Ensure libraries.json exists, seeding a Default entry only on first init.

    On a brand-new install (or an upgrade from the pre-multi-library era) the
    registry file does not exist yet: any loose top-level clips are migrated into
    ``default/`` and a Default entry is seeded. Once the file exists it is trusted
    as-is — an intentionally emptied registry (user deleted every library) is left
    empty rather than resurrecting a Default. Idempotent.
    """
    with _registry_lock:
        registry_existed = os.path.isfile(_registry_path())
        _migrate_legacy_default_layout()
        data = _read_registry_raw()
        if not registry_existed:
            libraries = list(data.get("libraries", []))
            if not any(
                lib.get("id") == Config.STORY_LIBRARY_DEFAULT_ID for lib in libraries
            ):
                os.makedirs(
                    os.path.join(
                        Config.STORY_LIBRARY_DIR,
                        Config.STORY_LIBRARY_DEFAULT_ID,
                        "clips",
                    ),
                    exist_ok=True,
                )
                libraries = [_default_library_record()] + libraries
                data["libraries"] = libraries
            _save_registry(data)
        return data


def load_libraries() -> list[dict]:
    """Return all libraries, Default first."""
    data = ensure_libraries_registry()
    libraries = list(data.get("libraries", []))
    libraries.sort(key=lambda lib: (not lib.get("isDefault"), str(lib.get("createdAt") or "")))
    return libraries


def get_library(library_id) -> dict | None:
    lid = str(library_id or "").strip()
    if not lid:
        return None
    for lib in load_libraries():
        if lib.get("id") == lid:
            return lib
    return None


def get_default_library_id() -> str:
    """Id of the library currently flagged default.

    Bootstraps a fresh install (see ``ensure_libraries_registry``) and returns "" when
    the registry exists but holds no libraries (the user deleted them all).
    """
    data = ensure_libraries_registry()
    for lib in data.get("libraries", []):
        if lib.get("isDefault"):
            return str(lib.get("id") or "")
    return ""


def _slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_str).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:40] or "lib"


class LibraryError(Exception):
    """Raised for library CRUD validation failures. ``code`` maps to an API error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _name_taken(name: str, libraries: list[dict], *, exclude_id: str | None = None) -> bool:
    target = name.strip().lower()
    for lib in libraries:
        if exclude_id is not None and lib.get("id") == exclude_id:
            continue
        if str(lib.get("name") or "").strip().lower() == target:
            return True
    return False


def create_library(name: str) -> dict:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise LibraryError("missing_name", "Tên thư viện không được để trống.")

    with _registry_lock:
        data = ensure_libraries_registry()
        libraries = data.get("libraries", [])
        if _name_taken(clean_name, libraries):
            raise LibraryError("duplicate_library_name", "Tên thư viện đã tồn tại.")

        existing_ids = {str(lib.get("id") or "") for lib in libraries}
        slug = _slugify(clean_name)
        new_id = f"{slug}-{uuid.uuid4().hex[:6]}"
        while new_id in existing_ids or new_id == Config.STORY_LIBRARY_DEFAULT_ID:
            new_id = f"{slug}-{uuid.uuid4().hex[:6]}"

        # Newly created libraries are never the default, so the root is always a subdir.
        new_root = os.path.join(Config.STORY_LIBRARY_DIR, new_id)
        os.makedirs(os.path.join(new_root, "clips"), exist_ok=True)

        record = {
            "id": new_id,
            "name": clean_name,
            "isDefault": False,
            "createdAt": _utc_now_iso(),
        }
        libraries.append(record)
        data["libraries"] = libraries
        _save_registry(data)
        return record


def rename_library(library_id, name: str) -> dict:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise LibraryError("missing_name", "Tên thư viện không được để trống.")

    with _registry_lock:
        data = ensure_libraries_registry()
        libraries = data.get("libraries", [])
        target = next((lib for lib in libraries if lib.get("id") == library_id), None)
        if target is None:
            raise LibraryError("library_not_found", "Không tìm thấy thư viện.")
        if _name_taken(clean_name, libraries, exclude_id=str(library_id)):
            raise LibraryError("duplicate_library_name", "Tên thư viện đã tồn tại.")
        target["name"] = clean_name
        target["updatedAt"] = _utc_now_iso()
        _save_registry(data)
        return target


def delete_library(library_id, *, delete_clips: bool = True) -> dict:
    lid = str(library_id or "").strip()
    if not lid:
        raise LibraryError("library_not_found", "Không tìm thấy thư viện.")

    with _registry_lock:
        data = ensure_libraries_registry()
        libraries = data.get("libraries", [])
        target = next((lib for lib in libraries if lib.get("id") == lid), None)
        if target is None:
            raise LibraryError("library_not_found", "Không tìm thấy thư viện.")

        was_default = bool(target.get("isDefault"))

        deleted_clips = 0
        if delete_clips:
            summary = delete_story_library_assets(
                delete_all=True, clean_orphans=True, library_id=lid
            )
            deleted_clips = summary.get("deletedCount", 0)

        root = _library_root(lid)
        # Defensive: never rmtree the shared top-level dir. Unreachable in normal flow
        # now that every library (incl. default) has its own subfolder, but kept as a
        # safety net against ever nuking libraries.json and every sibling library.
        if root and os.path.realpath(root) != os.path.realpath(Config.STORY_LIBRARY_DIR):
            shutil.rmtree(root, ignore_errors=True)

        remaining = [lib for lib in libraries if lib.get("id") != lid]

        # If the deleted library was the default, promote the first remaining library
        # (earliest createdAt, matching load_libraries' order) to be the new default.
        new_default_id = ""
        if was_default and remaining:
            promoted = min(remaining, key=lambda lib: str(lib.get("createdAt") or ""))
            promoted["isDefault"] = True
            new_default_id = str(promoted.get("id") or "")

        data["libraries"] = remaining
        _save_registry(data)
        with _library_locks_guard:
            _library_locks.pop(lid, None)

        return {
            "deleted": True,
            "libraryId": lid,
            "deletedClips": deleted_clips,
            "newDefaultLibraryId": new_default_id,
        }


# --------------------------------------------------------------------------- #
# Library metadata (extra registry fields, e.g. "styled" pre-baked libraries)
# --------------------------------------------------------------------------- #
# Reserved registry fields that callers must not overwrite via set_library_metadata.
_PROTECTED_LIBRARY_FIELDS = {"id", "isDefault"}


def set_library_metadata(library_id, **fields) -> dict:
    """Merge extra key/value pairs into a library's registry record.

    Records are persisted whole, so any extra fields automatically surface in
    ``get_library`` / ``load_libraries``. Used to mark pre-baked "styled"
    libraries with their effect + source provenance. Raises ``LibraryError`` if
    the library is unknown.
    """
    lid = str(library_id or "").strip()
    with _registry_lock:
        data = ensure_libraries_registry()
        libraries = data.get("libraries", [])
        target = next((lib for lib in libraries if lib.get("id") == lid), None)
        if target is None:
            raise LibraryError("library_not_found", "Không tìm thấy thư viện.")
        for key, value in fields.items():
            if key in _PROTECTED_LIBRARY_FIELDS:
                continue
            target[key] = value
        _save_registry(data)
        return target


def is_styled_library(library_id=None) -> bool:
    """True when the resolved library is a pre-baked "styled" library.

    Defensive: returns False for the Default/unknown libraries.
    """
    record = get_library(resolve_library_id(library_id))
    return bool(record and record.get("styled"))


def is_fully_baked_library(library_id=None) -> bool:
    """True when the library has style + waveform + CTA all baked into the clips.

    Such libraries need only subtitle burn-in at render time (no overlay pass).
    """
    record = get_library(resolve_library_id(library_id))
    return bool(record and record.get("fullyBaked"))


def library_clip_duration(library_id=None, default: int = 0) -> int:
    """Return the per-clip/unit duration a library was built with.

    Pre-baked "full" libraries use longer units (e.g. 10s, to match the CTA
    loop); regular libraries fall back to ``default`` (the caller passes
    ``Config.STORY_CLIP_DURATION``).
    """
    record = get_library(resolve_library_id(library_id)) or {}
    try:
        value = int(record.get("clipDuration") or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else default


# --------------------------------------------------------------------------- #
# Multi-library selection (a render can draw clips from several libraries)
# --------------------------------------------------------------------------- #
def resolve_library_ids(values=None) -> list[str]:
    """Normalize a multi-library selection into resolvable ids, order preserved.

    Accepts a single id (str) or any iterable of ids — each is put through
    ``resolve_library_id``, blanks and duplicates are dropped. An empty/unusable
    selection falls back to the default library (``[]`` only when no library
    exists at all), so callers can always treat the result as "the libraries this
    render draws from".
    """
    if values is None:
        raw_values = []
    elif isinstance(values, str):
        raw_values = [values]
    else:
        raw_values = list(values)

    resolved: list[str] = []
    for value in raw_values:
        lid = resolve_library_id(value)
        if lid and lid not in resolved:
            resolved.append(lid)

    if not resolved:
        default_id = get_default_library_id()
        return [default_id] if default_id else []
    return resolved


def any_styled_library(library_ids=None) -> bool:
    """True when at least one selected library is a pre-baked "styled" library.

    A mixed selection renders under the baked profile: the style pass is skipped
    for the whole video so the already-styled clips are never styled twice.
    """
    return any(is_styled_library(lid) for lid in resolve_library_ids(library_ids))


def any_fully_baked_library(library_ids=None) -> bool:
    """True when at least one selected library has style + waveform + CTA baked in."""
    return any(is_fully_baked_library(lid) for lid in resolve_library_ids(library_ids))


def libraries_clip_duration(library_ids=None, default: int = 0) -> int:
    """Smallest per-clip unit duration across the selected libraries.

    The concat demuxer writes one ``outpoint`` for every segment, so a mixed
    selection has to trim to the shortest unit — anything longer would overrun
    the clips coming from the library built with the smaller unit.
    """
    durations = [
        library_clip_duration(lid, default) for lid in resolve_library_ids(library_ids)
    ]
    positive = [value for value in durations if value > 0]
    return min(positive) if positive else default
