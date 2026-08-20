"""Prefetch-first import branch for the Story Video clip library.

The original import flow is preview -> select -> download: the browser streams
each provider's candidate video straight off the Pixabay/Pexels CDN so the user
can judge it, and only the picked ones are downloaded, cut and ingested (see
``download_from_provider_items`` in src/utils/video_source_downloader.py).

That ordering is expensive when almost everything is worth keeping: every judged
video costs a CDN stream and a wait, and paging through results burns free API
quota on material that was going to be downloaded anyway.

This module implements the other ordering -- download everything a keyword has
first, then review it locally, then cut what survived:

    Phase A (run_prefetch)   sweep every search page -> download all to
                             STORY_RAW_DIR/<sessionId>/  -> status "ready"
    (user prunes)            discard_items() deletes the rejects
    Phase B (run_commit)     split_into_clips() -> _ingest_clips() -> library

Reviewing local files is instant and spends no provider quota. Phase B delegates
to the same primitives the old flow uses, so canonical re-encoding, dropping the
short trailing segment, and re-baking into a styled library all behave
identically across both branches.

State lives in a manifest on disk rather than in a process dict, because a sweep
can run for a long time and the panel driving it gets remounted whenever the
settings page refreshes its library key -- an in-memory job would be orphaned.
"""

import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from src.config import Config
from src.utils.logger import logger
from src.utils.story_library import load_story_library_index, resolve_library_id

_SESSION_ID_PATTERN = re.compile(r"pf-[0-9a-f]{8}")
_MANIFEST_FILENAME = "_prefetch.json"
_CANCEL_FILENAME = "cancel.requested"
# Cached poster frames for the review grid (see ensure_item_poster).
_THUMBS_DIRNAME = "_thumbs"

# Statuses a session can no longer be steered out of.
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
# The one status that means "waiting for the user to prune".
REVIEW_STATUS = "ready"

# Tag written on every clip ingested through this branch, so a later sweep of the
# same keyword can skip what is already in the library. The old branch tags clips
# with the provider name and session only, which cannot identify a source video.
_SOURCE_TAG_PREFIX = "src:"

_manifest_locks_guard = threading.Lock()
_manifest_locks: dict[str, threading.RLock] = {}

# Windows refuses os.replace with WinError 5 while anything still holds the target
# open -- an antivirus scanning the file we just wrote, or Explorer previewing the
# staging dir. Reads inside this process are serialised by the manifest lock, so
# the remaining holders are always short-lived: a few retries clear them.
_REPLACE_RETRIES = 5
_REPLACE_DELAY_SECONDS = 0.4


class PrefetchError(RuntimeError):
    """Expected prefetch failure carrying an API-safe error code."""

    def __init__(self, message: str, code: str = "prefetch_failed"):
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_session_id() -> str:
    return f"pf-{uuid.uuid4().hex[:8]}"


def _staging_root() -> str:
    return os.path.abspath(Config.STORY_RAW_DIR)


def session_dir(session_id: str) -> str:
    """Absolute staging dir for a session, rejecting anything not our own id."""
    if not _SESSION_ID_PATTERN.fullmatch(str(session_id or "")):
        raise PrefetchError("Prefetch session khong hop le.", "invalid_prefetch_session")
    return os.path.join(_staging_root(), session_id)


def manifest_path(session_id: str) -> str:
    return os.path.join(session_dir(session_id), _MANIFEST_FILENAME)


def _manifest_lock(session_id: str) -> threading.RLock:
    with _manifest_locks_guard:
        lock = _manifest_locks.get(session_id)
        if lock is None:
            lock = threading.RLock()
            _manifest_locks[session_id] = lock
        return lock


def load_manifest(session_id: str) -> dict | None:
    """Load a session manifest, or None when the session does not exist.

    Read under the session lock: the panel polls status every second while a
    commit runs, and on Windows an open read handle makes the writer's
    ``os.replace`` fail with WinError 5.
    """
    try:
        path = manifest_path(session_id)
    except PrefetchError:
        return None
    if not os.path.isfile(path):
        return None
    try:
        with _manifest_lock(session_id):
            with open(path, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"[Prefetch] Could not read manifest {path}: {exc}")
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("items", [])
    return data


def save_manifest(manifest: dict) -> dict:
    """Atomically write a manifest (temp file + os.replace), stamping updatedAt."""
    session_id = str(manifest.get("sessionId") or "")
    target = manifest_path(session_id)
    os.makedirs(session_dir(session_id), exist_ok=True)
    manifest["updatedAt"] = _utc_now()
    temp_path = f"{target}.{secrets.token_hex(4)}.tmp"
    with _manifest_lock(session_id):
        with open(temp_path, "w", encoding="utf-8") as file_obj:
            json.dump(manifest, file_obj, ensure_ascii=False, indent=2)

        last_error: OSError | None = None
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(temp_path, target)
                return manifest
            except PermissionError as exc:
                last_error = exc
                if attempt < _REPLACE_RETRIES - 1:
                    time.sleep(_REPLACE_DELAY_SECONDS)

    # Leaving the temp behind would let one locked write litter the staging dir
    # with a .tmp per retry for the rest of the session.
    try:
        os.remove(temp_path)
    except OSError:
        pass
    logger.error(
        f"[Prefetch] Could not replace manifest after {_REPLACE_RETRIES} "
        f"attempts: {target} | {last_error}"
    )
    raise last_error


def update_manifest(session_id: str, mutate) -> dict | None:
    """Read-modify-write a manifest under its lock.

    Download workers run concurrently and each appends its own item, so every
    mutation has to go through here or updates get lost.
    """
    with _manifest_lock(session_id):
        manifest = load_manifest(session_id)
        if manifest is None:
            return None
        mutate(manifest)
        return save_manifest(manifest)


def create_session(
    *,
    library_id,
    provider: str,
    query: str,
    tags: list[str] | None = None,
    orientation: str | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    skip_imported: bool = True,
) -> dict:
    session_id = new_session_id()
    os.makedirs(session_dir(session_id), exist_ok=True)
    return save_manifest({
        "sessionId": session_id,
        "libraryId": resolve_library_id(library_id),
        "provider": provider,
        "query": query,
        "tags": list(tags or []),
        "filters": {
            "orientation": orientation,
            "minWidth": min_width,
            "minHeight": min_height,
            "skipImported": bool(skip_imported),
        },
        "status": "searching",
        "current": 0,
        "total": 0,
        "message": "Dang tim kiem tren provider...",
        "pagesFetched": 0,
        "providerTotal": 0,
        "skippedImported": 0,
        "skippedFiltered": 0,
        "failedCount": 0,
        "downloadedBytes": 0,
        "addedClips": 0,
        # Set when the page backstop, not the provider, ended the sweep.
        "truncated": None,
        "items": [],
        "error": None,
        "createdAt": _utc_now(),
    })


# --------------------------------------------------------------------------- #
# Cancellation -- a marker file, so a request thread can stop a worker thread
# without shared memory (same approach as the library bake jobs).
# --------------------------------------------------------------------------- #
def _cancel_path(session_id: str) -> str:
    return os.path.join(session_dir(session_id), _CANCEL_FILENAME)


def is_cancel_requested(session_id: str) -> bool:
    try:
        return os.path.isfile(_cancel_path(session_id))
    except PrefetchError:
        return False


def request_cancel(session_id: str) -> dict | None:
    manifest = load_manifest(session_id)
    if manifest is None:
        return None
    if manifest.get("status") in TERMINAL_STATUSES:
        return manifest
    with open(_cancel_path(session_id), "w", encoding="utf-8") as file_obj:
        file_obj.write(_utc_now())
    return update_manifest(session_id, lambda m: m.update({
        "status": "cancelling",
        "message": "Dang huy, cho cac video dang tai hoan tat...",
    }))


# --------------------------------------------------------------------------- #
# Already-imported detection
# --------------------------------------------------------------------------- #
def imported_provider_video_ids(library_id=None) -> set[str]:
    """`{"pixabay:12345", ...}` for everything this branch already ingested here.

    Scoped to one library on purpose: importing the same stock video into a second
    library is a legitimate thing to want, so only the target library's own index
    is consulted.
    """
    index = load_story_library_index(library_id)
    found: set[str] = set()
    for asset in index.get("assets", []):
        for tag in asset.get("tags", []) or []:
            text = str(tag)
            if text.startswith(_SOURCE_TAG_PREFIX):
                found.add(text[len(_SOURCE_TAG_PREFIX):])
    return found


def source_tag(provider: str, video_id: str) -> str:
    return f"{_SOURCE_TAG_PREFIX}{provider}:{video_id}"


# --------------------------------------------------------------------------- #
# Phase A -- sweep every page, download everything
# --------------------------------------------------------------------------- #
def _media_path(session_id: str, filename: str) -> str:
    """Path the browser fetches this staged file at, via /media/<...>."""
    return f"story_raw/{session_id}/{filename}"


def run_prefetch(session_id: str):
    """Search every page of the keyword and download each hit into staging.

    Downloads run on a small pool and overlap the page fetches, so the review grid
    fills in while later pages are still being requested.
    """
    from src.utils.video_source_downloader import (
        _download_file,
        iter_all_provider_videos,
        prefetch_item_rejection_reason,
    )

    manifest = load_manifest(session_id)
    if manifest is None:
        logger.error(f"[Prefetch] Session manifest missing: {session_id}")
        return

    provider = str(manifest.get("provider") or "")
    query = str(manifest.get("query") or "")
    filters = manifest.get("filters") or {}
    orientation = filters.get("orientation") or None
    min_width = filters.get("minWidth") or None
    min_height = filters.get("minHeight") or None
    target_dir = session_dir(session_id)

    already_imported: set[str] = set()
    if filters.get("skipImported"):
        already_imported = imported_provider_video_ids(manifest.get("libraryId"))

    stop_reason: list[str] = []
    # Both default to 0 = unlimited: sweeping the whole keyword is the point. The
    # count brake is enforced at the item that crosses it (below) so it is an exact
    # cap; the byte brake can only be judged after downloads land, so it is checked
    # between pages and overshoots by at most the in-flight page.
    max_videos = Config.STORY_PREFETCH_MAX_VIDEOS
    max_bytes = Config.STORY_PREFETCH_MAX_TOTAL_MB * 1024 * 1024

    def should_stop() -> bool:
        """Per-page check: stop asking the provider for more.

        Only reads the manifest when a byte brake is configured, so the default
        (unlimited) path costs one stat() per page.
        """
        if stop_reason or is_cancel_requested(session_id):
            return True
        if not max_bytes:
            return False
        current = load_manifest(session_id)
        if current is None:
            return True
        if current.get("downloadedBytes", 0) >= max_bytes:
            stop_reason.append(
                f"Da dat gioi han {Config.STORY_PREFETCH_MAX_TOTAL_MB} MB (STORY_PREFETCH_MAX_TOTAL_MB)."
            )
            return True
        return False

    truncated: list[str] = []

    def on_page(page: int, total: int, seen: int):
        update_manifest(session_id, lambda m: m.update({
            "pagesFetched": page,
            "providerTotal": total,
            "message": f"Da quet {page} trang, tim thay {seen} video...",
        }))

    def on_truncated(pages: int, total: int):
        # Never let a partial sweep read as a complete one.
        truncated.append(
            f"Da dung o {pages} trang (provider bao co {total} ket qua) — chua quet het."
        )

    def download_one(index: int, key: str, item: dict):
        # Only a real cancel abandons queued work. A size brake stops the sweep from
        # asking for more, but whatever it already queued still finishes -- otherwise
        # "cap at N videos" would end up delivering fewer than N.
        if is_cancel_requested(session_id):
            return
        video_id = str(item.get("id") or "")
        download_url = str(item.get("previewUrl") or "").strip()
        filename = f"{provider}_{index:04d}_{video_id}_{uuid.uuid4().hex[:8]}.mp4"
        dest_path = os.path.join(target_dir, filename)

        record = {
            "itemId": key,
            "provider": provider,
            "videoId": video_id,
            "title": item.get("title") or "",
            "pageUrl": item.get("pageUrl") or "",
            "thumbnailUrl": item.get("thumbnailUrl") or "",
            "width": item.get("width") or 0,
            "height": item.get("height") or 0,
            "duration": item.get("duration") or 0,
            "author": item.get("author") or "",
            "filename": filename,
            "mediaPath": _media_path(session_id, filename),
            "sizeBytes": 0,
            "status": "downloaded",
            "clipCount": 0,
            "error": None,
        }

        if not download_url or not _download_file(download_url, dest_path):
            record["status"] = "failed"
            record["error"] = "Tai that bai"
            record["sizeBytes"] = 0
        else:
            try:
                record["sizeBytes"] = os.path.getsize(dest_path)
            except OSError:
                record["sizeBytes"] = 0

        def _apply(m: dict):
            m["items"].append(record)
            if record["status"] == "failed":
                m["failedCount"] = m.get("failedCount", 0) + 1
            else:
                m["current"] = m.get("current", 0) + 1
                m["downloadedBytes"] = m.get("downloadedBytes", 0) + record["sizeBytes"]
            m["message"] = f"Da tai {m.get('current', 0)} video..."

        update_manifest(session_id, _apply)

    workers = max(1, Config.STORY_PREFETCH_DOWNLOAD_WORKERS)
    queued = 0
    skipped_imported = 0
    skipped_filtered = 0

    try:
        update_manifest(session_id, lambda m: m.update({"status": "downloading"}))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, item in iter_all_provider_videos(
                provider,
                query,
                orientation=orientation,
                min_width=min_width,
                min_height=min_height,
                should_stop=should_stop,
                on_page=on_page,
                on_truncated=on_truncated,
            ):
                if key in already_imported:
                    skipped_imported += 1
                    continue
                if prefetch_item_rejection_reason(
                    item,
                    orientation=orientation,
                    min_width=min_width,
                    min_height=min_height,
                ):
                    skipped_filtered += 1
                    continue
                pool.submit(download_one, queued, key, item)
                queued += 1
                if max_videos and queued >= max_videos:
                    stop_reason.append(
                        f"Da dat gioi han {max_videos} video (STORY_PREFETCH_MAX_VIDEOS)."
                    )
                    break

        final_status = "cancelled" if is_cancel_requested(session_id) else REVIEW_STATUS

        def _finish(m: dict):
            m["skippedImported"] = skipped_imported
            m["skippedFiltered"] = skipped_filtered
            m["total"] = queued
            m["status"] = final_status
            m["truncated"] = truncated[0] if truncated else None
            kept = m.get("current", 0)
            if final_status == "cancelled":
                m["message"] = f"Da huy. Giu lai {kept} video da tai xong."
            elif stop_reason:
                m["message"] = f"{stop_reason[0]} Da tai {kept} video."
            elif truncated:
                m["message"] = f"Da tai {kept} video. {truncated[0]}"
            else:
                m["message"] = f"Da tai xong {kept} video. Xoa cac video khong dat roi cat clip."

        update_manifest(session_id, _finish)
        logger.info(
            f"[Prefetch] {session_id} {final_status}: queued={queued} "
            f"skippedImported={skipped_imported} skippedFiltered={skipped_filtered}"
        )
    except Exception as exc:
        logger.error(f"[Prefetch] {session_id} failed: {exc}", exc_info=True)
        update_manifest(session_id, lambda m: m.update({
            "status": "failed",
            "message": f"Loi: {exc}",
            "error": str(exc),
        }))


# --------------------------------------------------------------------------- #
# Review -- poster frames
# --------------------------------------------------------------------------- #
def _poster_path(session_id: str, item_id: str) -> str:
    """Cache path for one item's poster. Item ids are ``provider:videoId``, so the
    id is sanitised into a filename rather than trusted as one."""
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", str(item_id or ""))
    if not safe or safe.strip(".") == "":
        raise PrefetchError("Video khong hop le.", "prefetch_item_not_found")
    return os.path.join(session_dir(session_id), _THUMBS_DIRNAME, f"{safe}.jpg")


def _find_item(manifest: dict, item_id: str) -> dict | None:
    return next(
        (record for record in manifest.get("items", []) if str(record.get("itemId")) == str(item_id)),
        None,
    )


def ensure_item_poster(session_id: str, item_id: str) -> str:
    """Absolute path to a cached JPEG frame of one staged video, cutting it on first ask.

    The review grid mounts its videos with ``preload="none"`` -- fetching bytes for
    two dozen multi-megabyte files just to show a grid would stall the tab -- so
    each card needs a poster or it renders as a black box. Pexels ships a thumbnail
    URL with every search hit, but Pixabay's does not travel the same way and older
    manifests were written with an empty one, so provider metadata cannot be relied
    on. The staged file is already on local disk: pulling one frame out of it works
    for any session regardless of provider, spends no provider quota, and the result
    is exactly the footage being judged.
    """
    from src.utils.ffmpeg_helper import FFmpegHelper

    manifest = load_manifest(session_id)
    if manifest is None:
        raise PrefetchError("Prefetch session khong ton tai.", "prefetch_session_not_found")

    record = _find_item(manifest, item_id)
    if record is None or record.get("status") == "deleted":
        raise PrefetchError("Video khong con trong luot nay.", "prefetch_item_not_found")

    poster = _poster_path(session_id, item_id)
    try:
        if os.path.getsize(poster) > 0:
            return poster
    except OSError:
        pass  # not cached yet

    video_path = os.path.join(session_dir(session_id), str(record.get("filename") or ""))
    if not os.path.isfile(video_path):
        raise PrefetchError("File goc khong ton tai.", "prefetch_item_missing_file")

    os.makedirs(os.path.dirname(poster), exist_ok=True)
    # Random temp name + os.replace: two cards can ask for the same poster at once
    # (React remounts, or a page revisit) and neither may see a half-written JPEG.
    temp_path = f"{poster}.{secrets.token_hex(4)}.tmp.jpg"
    try:
        duration = float(record.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    # A second in avoids the fade-in/black frame most stock clips open on, but only
    # when the clip is long enough to have one.
    seek = "1" if duration >= 2 else "0"
    # -ss before -i so ffmpeg seeks instead of decoding up to the timestamp, and a
    # 480px wide JPEG because that is roughly what a card is on a 4-column grid.
    ok = FFmpegHelper.run_command(
        [
            "ffmpeg", "-y", "-ss", seek, "-i", video_path,
            "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", temp_path,
        ],
        timeout_seconds=60,
    )
    if not ok or not os.path.isfile(temp_path) or os.path.getsize(temp_path) == 0:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise PrefetchError("Khong the tao anh xem truoc.", "prefetch_poster_failed")

    os.replace(temp_path, poster)
    return poster


def _remove_item_poster(session_id: str, item_id: str):
    try:
        os.remove(_poster_path(session_id, item_id))
    except (OSError, PrefetchError):
        pass


# --------------------------------------------------------------------------- #
# Review -- drop the rejects
# --------------------------------------------------------------------------- #
def discard_items(session_id: str, item_ids: list[str]) -> dict:
    """Delete staged files for the given items and mark them discarded."""
    wanted = {str(item_id) for item_id in item_ids if str(item_id).strip()}
    target_dir = session_dir(session_id)
    deleted: list[str] = []
    failed: list[str] = []

    def _apply(manifest: dict):
        for record in manifest.get("items", []):
            if record.get("itemId") not in wanted or record.get("status") == "deleted":
                continue
            path = os.path.join(target_dir, str(record.get("filename") or ""))
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError as exc:
                logger.warning(f"[Prefetch] Could not delete staged file {path}: {exc}")
                failed.append(record["itemId"])
                continue
            if record.get("status") == "downloaded":
                manifest["current"] = max(0, manifest.get("current", 0) - 1)
                manifest["downloadedBytes"] = max(
                    0, manifest.get("downloadedBytes", 0) - int(record.get("sizeBytes") or 0)
                )
            record["status"] = "deleted"
            _remove_item_poster(session_id, record["itemId"])
            deleted.append(record["itemId"])

    manifest = update_manifest(session_id, _apply)
    if manifest is None:
        raise PrefetchError("Prefetch session khong ton tai.", "prefetch_session_not_found")
    return {
        "sessionId": session_id,
        "requestedCount": len(wanted),
        "deletedCount": len(deleted),
        "failedItemIds": failed,
        "session": manifest,
    }


def discard_session(session_id: str) -> bool:
    """Throw away a whole staging session, files and all."""
    target_dir = session_dir(session_id)
    if not os.path.isdir(target_dir):
        return False
    shutil.rmtree(target_dir, ignore_errors=True)
    with _manifest_locks_guard:
        _manifest_locks.pop(session_id, None)
    return True


def purge_session_media(session_id: str) -> int:
    """Delete every staged file but keep the manifest.

    Used by "delete sources after cutting": the clips are already copied into the
    library, so the videos are dead weight -- but the polling UI still needs the
    manifest to render its completed state, and retention still needs a status to
    read. ``cleanup_expired_sessions`` removes the husk later.
    """
    target_dir = session_dir(session_id)
    if not os.path.isdir(target_dir):
        return 0
    removed = 0
    # The poster cache is a subdir, so the file loop below would skip it -- and its
    # frames are meaningless once the videos they came from are gone.
    shutil.rmtree(os.path.join(target_dir, _THUMBS_DIRNAME), ignore_errors=True)
    for entry in os.scandir(target_dir):
        if not entry.is_file() or entry.name == _MANIFEST_FILENAME:
            continue
        try:
            os.remove(entry.path)
            removed += 1
        except OSError as exc:
            logger.warning(f"[Prefetch] Could not delete staged file {entry.path}: {exc}")
    return removed


def _remove_split_clips(clip_paths: list[str]):
    """Drop the segment files once they are copied into the library.

    ``add_clips_to_library`` copies rather than moves, so without this every kept
    clip would sit in staging as well -- which is exactly how the original flow
    left 12 orphaned session dirs full of duplicated clips on this machine.
    """
    for clip_path in clip_paths:
        try:
            os.remove(clip_path)
        except OSError as exc:
            logger.warning(f"[Prefetch] Could not delete split clip {clip_path}: {exc}")


# --------------------------------------------------------------------------- #
# Phase B -- cut what survived and hand it to the library
# --------------------------------------------------------------------------- #
def run_commit(
    session_id: str,
    tags: list[str] | None = None,
    delete_raw_after: bool = False,
    progress_callback=None,
):
    """Split every kept staged video into clips and ingest them.

    Serial on purpose: ffmpeg (NVENC especially) does not benefit from several
    concurrent encodes here, and the ingest path takes the library index lock.
    """
    from src.utils.video_source_downloader import (
        _ingest_clips,
        _target_clip_duration,
        split_into_clips,
    )

    manifest = load_manifest(session_id)
    if manifest is None:
        logger.error(f"[Prefetch] Commit skipped, manifest missing: {session_id}")
        return

    library_id = manifest.get("libraryId")
    provider = str(manifest.get("provider") or "")
    extra_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
    target_dir = session_dir(session_id)
    pending = [
        record for record in manifest.get("items", [])
        if record.get("status") == "downloaded"
    ]
    total = len(pending)
    clip_duration = _target_clip_duration(library_id)
    added_total = 0

    def _emit(payload: dict):
        # Progress bookkeeping must never abort a commit: the clips already cut
        # and ingested are real work, and a manifest that is one step stale is a
        # far smaller loss than dropping the rest of the batch.
        try:
            update_manifest(session_id, lambda m: m.update(payload))
        except OSError as exc:
            logger.warning(f"[Prefetch] Could not record commit progress for {session_id}: {exc}")
        if progress_callback:
            progress_callback(payload)

    try:
        _emit({
            "status": "committing",
            "current": 0,
            "total": total,
            "message": f"Bat dau cat {total} video thanh clip...",
        })

        for index, record in enumerate(pending):
            video_path = os.path.join(target_dir, str(record.get("filename") or ""))
            video_id = str(record.get("videoId") or "")
            _emit({
                "stage": "splitting",
                "current": index + 1,
                "total": total,
                "message": f"Dang cat video {index + 1}/{total} thanh clip...",
            })

            if not os.path.isfile(video_path):
                _mark_item(session_id, record.get("itemId"), status="failed", error="File goc khong ton tai")
                continue

            clips = split_into_clips(video_path, clip_duration)
            if not clips:
                _mark_item(session_id, record.get("itemId"), status="failed", error="Cat clip that bai")
                continue

            clip_tags = [
                provider,
                f"session:{session_id}",
                source_tag(provider, video_id),
                *extra_tags,
            ]
            added = _ingest_clips(
                clips,
                provider,
                clip_tags,
                library_id=library_id,
                session_id=session_id,
                progress_callback=progress_callback,
                current=index + 1,
                total=total,
            )
            added_total += len(added)
            _remove_split_clips(clips)
            _mark_item(
                session_id,
                record.get("itemId"),
                status="committed",
                clip_count=len(added),
            )

        _emit({
            "stage": "complete",
            "status": "completed",
            "current": total,
            "total": total,
            "addedClips": added_total,
            "message": f"Hoan tat! Da them {added_total} clip tu {total} video.",
        })

        if delete_raw_after:
            purged = purge_session_media(session_id)
            logger.info(f"[Prefetch] {session_id} purged {purged} staged file(s) after commit")

        logger.info(f"[Prefetch] {session_id} committed {added_total} clips from {total} videos")
    except Exception as exc:
        logger.error(f"[Prefetch] Commit failed for {session_id}: {exc}", exc_info=True)
        update_manifest(session_id, lambda m: m.update({
            "status": "failed",
            "message": f"Loi khi cat clip: {exc}",
            "error": str(exc),
        }))


def _mark_item(session_id: str, item_id, *, status: str, error: str | None = None, clip_count: int = 0):
    def _apply(manifest: dict):
        for record in manifest.get("items", []):
            if record.get("itemId") == item_id:
                record["status"] = status
                record["error"] = error
                record["clipCount"] = clip_count
                return

    try:
        update_manifest(session_id, _apply)
    except OSError as exc:
        logger.warning(f"[Prefetch] Could not mark item {item_id} as {status}: {exc}")


# --------------------------------------------------------------------------- #
# Listing and retention
# --------------------------------------------------------------------------- #
def list_sessions(library_id=None, *, include_completed: bool = False) -> list[dict]:
    """Session manifests newest-first, so a remounted panel can pick its own back up."""
    root = _staging_root()
    if not os.path.isdir(root):
        return []
    target = resolve_library_id(library_id) if library_id else None
    sessions: list[dict] = []
    for entry in os.scandir(root):
        if not entry.is_dir() or not _SESSION_ID_PATTERN.fullmatch(entry.name):
            continue
        manifest = load_manifest(entry.name)
        if manifest is None:
            continue
        if target is not None and resolve_library_id(manifest.get("libraryId")) != target:
            continue
        if not include_completed and manifest.get("status") == "completed":
            continue
        sessions.append(manifest)
    sessions.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    return sessions


def has_active_session(library_id=None) -> bool:
    """True while a sweep or a commit is still running for that library."""
    active = {"searching", "downloading", "cancelling", "committing"}
    return any(
        manifest.get("status") in active
        for manifest in list_sessions(library_id, include_completed=True)
    )


def cleanup_expired_sessions(*, now: float | None = None, ttl_seconds: int | None = None) -> list[str]:
    """Sweep finished/cancelled staging dirs past their retention window.

    Only ``pf-*`` dirs are touched. The older ``imp-*``/``dl-*``/``up-*`` staging
    dirs from the original flow have never been cleaned up automatically and are
    deliberately left alone here -- deciding their fate is a separate call.
    """
    root = _staging_root()
    if not os.path.isdir(root):
        return []
    expiry = Config.STORY_PREFETCH_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    if expiry <= 0:
        return []
    current_time = time.time() if now is None else now
    removed: list[str] = []

    for entry in os.scandir(root):
        if not entry.is_dir() or not _SESSION_ID_PATTERN.fullmatch(entry.name):
            continue
        manifest = load_manifest(entry.name)
        # A pf-* dir with no readable manifest is debris from a crash mid-create;
        # age it out on the dir mtime alone.
        if manifest is not None and manifest.get("status") not in TERMINAL_STATUSES:
            continue
        path = os.path.join(root, entry.name, _MANIFEST_FILENAME)
        latest_mtime = entry.stat().st_mtime
        if os.path.isfile(path):
            latest_mtime = max(latest_mtime, os.path.getmtime(path))
        if current_time - latest_mtime <= expiry:
            continue
        shutil.rmtree(entry.path, ignore_errors=True)
        with _manifest_locks_guard:
            _manifest_locks.pop(entry.name, None)
        removed.append(entry.name)

    if removed:
        logger.info(f"[Prefetch] Cleaned up {len(removed)} expired staging session(s)")
    return removed
