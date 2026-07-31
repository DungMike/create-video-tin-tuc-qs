"""Background runner that bakes a TV effect into a clip library (style-only).

Each 5s clip is re-encoded with the style-only filter (eq / noise / scanline /
vignette) and marked ``styled:true``. Waveform + CTA are NOT baked here — they
stay runtime overlays added at the render step, which also skips the style pass
for a styled library. Clips keep their 5s length (no pairing into longer units).

Concurrency mirrors ``StoryVideoBatchRunner``: a daemon thread drives a bounded
``ThreadPoolExecutor`` (``Config.STORY_BAKE_MAX_WORKERS``); progress is persisted
to JSON for UI polling and marker files stop it.

Stopping comes in two flavours, because a full bake takes hours and rarely fits
one idle window:

- **cancel** (``cancel.requested``) — abandon: the partial target library is
  deleted, nothing is kept.
- **pause** (``pause.requested``) — park: the target library is kept and stays
  usable for renders with however many clips it already has. ``resume_bake_job``
  later bakes only what is missing.

Resume needs no bookkeeping of its own: a baked clip keeps the *source clip's
id*, so "what is left" is exactly the source ids absent from the target index.
That is only true because finished clips are appended to that index every
``_CHECKPOINT_EVERY`` clips instead of once at the end — which is also what makes
a half-baked library renderable and what limits the loss from a hard crash to the
last checkpoint.
"""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.config import Config
from src.utils.clip_canonical import canonical_output_args, canonical_video_filter
from src.utils.clip_spec_validation import probe_clip_spec
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger
from src.utils.story_library import (
    _index_lock,
    delete_library,
    load_story_library_index,
    save_story_library_index,
    set_library_metadata,
    story_library_root,
)
from src.utils.story_video_pipeline import _load_json, _save_json

# Sentinel: a clip that was skipped because a pause/cancel arrived, not one that
# failed. Kept distinct so draining the queue never inflates the error count.
_STOPPED = "__stopped__"


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# --------------------------------------------------------------------------- #
# Shared ffmpeg bake primitives (used by StoryLibraryBakeRunner and by the
# import path that re-bakes freshly-added clips into an existing styled library).
# --------------------------------------------------------------------------- #
def _hwaccel_flags() -> list[str]:
    return ["-hwaccel", "cuda"] if Config.USE_GPU_NVENC else []


def _run_ffmpeg_with_fallback(cmd: list, hwaccel_len: int, cancel_cb) -> bool:
    """Run a bake command; on GPU-decode failure retry once with CPU decode."""
    ok = FFmpegHelper.run_command(cmd, cancel_callback=cancel_cb)
    if not ok and hwaccel_len and not cancel_cb():
        ok = FFmpegHelper.run_command(cmd[:2] + cmd[2 + hwaccel_len:], cancel_callback=cancel_cb)
    return ok


def _style_only_cmd(src_path: str, out_path: str, style_filter: str, hwaccel: list) -> list:
    """Bake the style, then land on the canonical clip spec.

    Without the canonical tail a bake inherits whatever color tags the source clip
    carried, so an off-spec source produced an off-spec baked clip and the render
    excluded both copies. See src/utils/clip_canonical.py.
    """
    cmd = ["ffmpeg", "-y", *hwaccel, "-i", src_path,
           "-vf", canonical_video_filter(probe_clip_spec(src_path), prefix=style_filter), "-an"]
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(canonical_output_args())
    cmd.extend(["-movflags", "+faststart", out_path])
    return cmd


def _bake_dir(job_id: str) -> str:
    path = os.path.join(Config.STORY_LIBRARY_DIR, "_bake_jobs", job_id)
    os.makedirs(path, exist_ok=True)
    return path


def _bake_progress_path(job_id: str) -> str:
    return os.path.join(_bake_dir(job_id), "progress.json")


def _bake_cancel_path(job_id: str) -> str:
    return os.path.join(_bake_dir(job_id), "cancel.requested")


def _bake_pause_path(job_id: str) -> str:
    return os.path.join(_bake_dir(job_id), "pause.requested")


# States a job can no longer be steered out of.
TERMINAL_BAKE_STATUSES = {"completed", "failed", "cancelled"}
# States a paused/interrupted job can be resumed from. "partial" and "failed"
# are included so a run that hit errors can be retried without redoing the clips
# that already succeeded.
RESUMABLE_BAKE_STATUSES = {"paused", "pausing", "partial", "failed", "interrupted"}


def load_bake_progress(job_id: str) -> dict | None:
    return _load_json(_bake_progress_path(job_id))


def is_bake_cancel_requested(job_id: str) -> bool:
    return os.path.isfile(_bake_cancel_path(job_id))


def is_bake_pause_requested(job_id: str) -> bool:
    return os.path.isfile(_bake_pause_path(job_id))


def is_bake_stop_requested(job_id: str) -> bool:
    """Either kind of stop. Workers use this; only the finish path tells them apart."""
    return is_bake_cancel_requested(job_id) or is_bake_pause_requested(job_id)


def _clear_marker(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def request_bake_cancel(job_id: str) -> dict | None:
    """Stop the job and DISCARD its target library (see request_bake_pause to keep it)."""
    progress = load_bake_progress(job_id)
    if not progress:
        return None
    if progress.get("status") not in TERMINAL_BAKE_STATUSES:
        with open(_bake_cancel_path(job_id), "w", encoding="utf-8") as file_obj:
            file_obj.write(_utc_now())
        progress["status"] = "cancelling"
        progress["message"] = "Đang hủy bake..."
        progress["updatedAt"] = _utc_now()
        _save_json(_bake_progress_path(job_id), progress)
    return progress


def request_bake_pause(job_id: str) -> dict | None:
    """Stop the job but KEEP everything baked so far.

    The target library stays registered and usable — every clip already written
    is in its index — and ``resume_bake_job`` picks up exactly what is missing.
    """
    progress = load_bake_progress(job_id)
    if not progress:
        return None
    if progress.get("status") not in TERMINAL_BAKE_STATUSES | {"paused"}:
        with open(_bake_pause_path(job_id), "w", encoding="utf-8") as file_obj:
            file_obj.write(_utc_now())
        progress["status"] = "pausing"
        progress["message"] = "Đang tạm dừng, chờ các clip đang xử lý hoàn tất..."
        progress["updatedAt"] = _utc_now()
        _save_json(_bake_progress_path(job_id), progress)
    return progress


class StoryLibraryBakeRunner:
    """Bakes a TV style (style-only) into a new target library.

    The target library is created synchronously by the route (so duplicate-name
    errors surface immediately). Cancelling deletes the partial target library;
    pausing keeps it — see the module docstring.

    Baked clips are appended to the target index every ``_CHECKPOINT_EVERY``
    clips rather than once at the end, so a paused (or crashed) job leaves a
    library that is immediately usable for renders, and ``resume_bake_job`` can
    tell what is left purely by diffing ids against that index.
    """

    _CHECKPOINT_EVERY = 100

    def __init__(
        self,
        job_id: str,
        *,
        source_library_id: str,
        target_library_id: str,
        target_name: str,
        style_filter: str,
        style_id: str,
        style_label: str,
    ):
        self.job_id = job_id
        self.source_library_id = source_library_id
        self.target_library_id = target_library_id
        self.target_name = target_name
        self.style_filter = style_filter
        self.style_id = style_id
        self.style_label = style_label

        self._lock = threading.RLock()
        self._max_workers = max(1, int(Config.STORY_BAKE_MAX_WORKERS))
        self.completed_count = 0
        self.failed_count = 0
        self.stopped_count = 0
        self._pending_records: list[dict] = []

        assets = load_story_library_index(source_library_id).get("assets", [])
        # Anything already in the target index is done: a fresh bake sees an empty
        # index and takes everything, a resumed one takes only the remainder.
        self._already_baked = {
            str(a.get("id") or "")
            for a in load_story_library_index(target_library_id).get("assets", [])
            if a.get("id")
        }
        # Frozen count of what earlier runs finished. `_already_baked` keeps growing
        # as checkpoints land, so it must never be added to `completed_count` (which
        # already counts those same clips) -- that double-counts every checkpointed
        # clip and reports >100%.
        self._baseline_baked = len(self._already_baked)
        self.source_total = len([a for a in assets if a.get("id")])
        self._items = self._build_items(list(assets))
        self.resumed = bool(self._already_baked)

        self.progress = {
            "jobId": job_id,
            "status": "pending",
            "mode": "style",
            "sourceLibraryId": source_library_id,
            "targetLibraryId": target_library_id,
            "targetName": target_name,
            "styleId": style_id,
            "styleLabel": style_label,
            # `total`/`completed` count the WHOLE library across every run, so a
            # resumed job continues the same progress bar instead of restarting at 0.
            "total": self.source_total,
            "completed": self._baseline_baked,
            "remaining": len(self._items),
            "failed": 0,
            "percent": round(min(100, self._baseline_baked / max(1, self.source_total) * 100), 1),
            "resumed": self.resumed,
            "message": "Chờ xử lý...",
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
        }
        self._save_progress()

    def _build_items(self, assets: list[dict]) -> list[dict]:
        """One work item per source clip still missing from the target library."""
        return [
            {"out_id": str(a.get("id") or ""), "srcs": [a]}
            for a in assets
            if a.get("id") and str(a.get("id")) not in self._already_baked
        ]

    def _save_progress(self):
        with self._lock:
            self.progress["updatedAt"] = _utc_now()
            _save_json(_bake_progress_path(self.job_id), self.progress)

    def _baked_total(self) -> int:
        """Clips this library has baked overall: earlier runs plus the current one."""
        return self._baseline_baked + self.completed_count

    def _emit_progress(self, *, status: str | None = None, message: str | None = None):
        with self._lock:
            total = self.progress["total"] or 1
            done = self._baked_total()
            self.progress["completed"] = done
            self.progress["failed"] = self.failed_count
            self.progress["remaining"] = max(0, len(self._items) - self.completed_count
                                             - self.failed_count - self.stopped_count)
            self.progress["percent"] = round(min(100, max(0, (done / total) * 100)), 1)
            if status:
                self.progress["status"] = status
            elif is_bake_cancel_requested(self.job_id):
                self.progress["status"] = "cancelling"
            elif is_bake_pause_requested(self.job_id):
                self.progress["status"] = "pausing"
            else:
                self.progress["status"] = "running"
            self.progress["message"] = message or f"Đã bake {done}/{total} clip..."
            self.progress["updatedAt"] = _utc_now()
            _save_json(_bake_progress_path(self.job_id), self.progress)

    def start_async(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        logger.info(
            f"[StoryBake:{self.job_id}] "
            f"{'Resuming' if self.resumed else 'Started'} bake: {len(self._items)} clip(s) "
            f"remaining of {self.source_total} (style={self.style_id}, "
            f"source={self.source_library_id} -> {self.target_library_id}, "
            f"max_workers={self._max_workers})."
        )

    def _src_abs(self, asset: dict) -> str | None:
        rel = str(asset.get("relative_path") or "").strip()
        if not rel:
            return None
        path = os.path.join(story_library_root(self.source_library_id), rel)
        return path if os.path.isfile(path) else None

    def _run_ffmpeg_with_fallback(self, cmd: list, hwaccel_len: int) -> bool:
        return _run_ffmpeg_with_fallback(
            cmd, hwaccel_len, lambda: is_bake_cancel_requested(self.job_id)
        )

    def _bake_one(self, item: dict) -> dict | None | str:
        """Bake one clip style-only.

        Returns the new asset record, ``None`` on failure, or ``_STOPPED`` when a
        pause/cancel is in flight — the caller must not count a stopped clip as a
        failure, otherwise draining a 30k-item queue reports 30k "errors".
        """
        if is_bake_stop_requested(self.job_id):
            return _STOPPED

        out_id = item["out_id"]
        srcs = [a for a in item["srcs"] if a]
        src_paths = [p for p in (self._src_abs(a) for a in srcs) if p]
        if not out_id or not src_paths:
            return None

        out_rel = f"clips/{out_id}.mp4"
        out_path = os.path.join(story_library_root(self.target_library_id), out_rel)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        hwaccel = _hwaccel_flags()

        # style-only: single clip, style filter, keep source metadata
        cmd = _style_only_cmd(src_paths[0], out_path, self.style_filter, hwaccel)
        if not self._run_ffmpeg_with_fallback(cmd, len(hwaccel)) or not os.path.isfile(out_path):
            return None
        a = srcs[0]
        return {
            "id": out_id,
            "source_type": a.get("source_type", "styled"),
            "source_name": a.get("source_name", ""),
            "relative_path": out_rel,
            "duration": a.get("duration", 0),
            "tags": list(a.get("tags", [])),
            "created_at": _utc_now(),
            "styled_from": self.source_library_id,
        }

    def _flush_records(self, *, force: bool = False):
        """Append finished clips to the target index (the checkpoint).

        Everything written here is immediately renderable, which is what makes a
        paused library usable and a resume able to skip what is done.
        """
        with self._lock:
            if not self._pending_records:
                return
            if not force and len(self._pending_records) < self._CHECKPOINT_EVERY:
                return
            records, self._pending_records = self._pending_records, []

        try:
            with _index_lock(self.target_library_id):
                index = load_story_library_index(self.target_library_id)
                index.setdefault("assets", []).extend(records)
                save_story_library_index(index, self.target_library_id)
            with self._lock:
                self._already_baked.update(str(r["id"]) for r in records)
        except Exception as exc:  # noqa: BLE001 - keep the clips, retry next checkpoint
            logger.error(
                f"[StoryBake:{self.job_id}] Checkpoint failed ({len(records)} clip(s) "
                f"held for the next flush): {exc}"
            )
            with self._lock:
                self._pending_records = records + self._pending_records

    def _run(self):
        if not self._items:
            if self._already_baked:
                self._sync_library_state("completed")
                self._emit_progress(
                    status="completed",
                    message=f"Thư viện đã bake đủ {self._baseline_baked} clip.",
                )
            else:
                self._finish_failed("Thư viện nguồn không có clip nào để bake.")
            return

        self._emit_progress(
            status="running",
            message="Tiếp tục bake..." if self.resumed else "Bắt đầu bake...",
        )
        self._sync_library_state("running")

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(self._bake_one, item): idx for idx, item in enumerate(self._items)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    record = future.result()
                except Exception as exc:  # noqa: BLE001 - never let one unit kill the job
                    logger.error(f"[StoryBake:{self.job_id}] Unit {idx} raised: {exc}", exc_info=True)
                    record = None
                with self._lock:
                    if record is _STOPPED:
                        self.stopped_count += 1
                        continue
                    if record is not None:
                        self._pending_records.append(record)
                        self.completed_count += 1
                    else:
                        self.failed_count += 1
                self._flush_records()
                self._emit_progress()

        self._flush_records(force=True)

        if is_bake_cancel_requested(self.job_id):
            self._cleanup_target()
            self._emit_progress(status="cancelled", message="Đã hủy bake.")
            logger.info(f"[StoryBake:{self.job_id}] Cancelled; partial library removed.")
            return

        if is_bake_pause_requested(self.job_id):
            done = self._baked_total()
            self._sync_library_state("paused")
            self._emit_progress(
                status="paused",
                message=(
                    f"Đã tạm dừng ở {done}/{self.source_total} clip. "
                    "Thư viện dùng được ngay; bấm Tiếp tục để bake nốt."
                ),
            )
            logger.info(
                f"[StoryBake:{self.job_id}] Paused at {done}/{self.source_total}; "
                f"target library kept."
            )
            return

        if not self._already_baked:
            self._cleanup_target()
            self._finish_failed("Bake thất bại cho toàn bộ clip.")
            return

        status = "completed" if self.failed_count == 0 else "partial"
        message = (
            f"Bake hoàn tất: {self._baked_total()}/{self.source_total} clip"
            + (f", {self.failed_count} lỗi." if self.failed_count else ".")
        )
        self._sync_library_state(status)
        self._emit_progress(status=status, message=message)
        logger.info(
            f"[StoryBake:{self.job_id}] Finished: baked={self._baked_total()}, "
            f"failed={self.failed_count}, target={self.target_library_id}"
        )

    def _sync_library_state(self, status: str):
        """Mirror bake state onto the library record so the UI can offer Resume
        for a job whose runner is long gone (e.g. after an app restart)."""
        try:
            set_library_metadata(
                self.target_library_id,
                bakeJobId=self.job_id,
                bakeStatus=status,
                bakeCompleted=self._baked_total(),
                bakeTotal=self.source_total,
            )
        except Exception as exc:  # noqa: BLE001 - metadata is a convenience, not state
            logger.warning(f"[StoryBake:{self.job_id}] Could not update library state: {exc}")

    def _cleanup_target(self):
        try:
            delete_library(self.target_library_id, delete_clips=True)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            logger.warning(f"[StoryBake:{self.job_id}] Could not remove partial library: {exc}")

    def _finish_failed(self, message: str):
        with self._lock:
            self.progress["status"] = "failed"
            self.progress["message"] = message
            self.progress["error"] = message
            self.progress["updatedAt"] = _utc_now()
            _save_json(_bake_progress_path(self.job_id), self.progress)
        logger.error(f"[StoryBake:{self.job_id}] {message}")


def resume_bake_job(job_id: str) -> "StoryLibraryBakeRunner | None":
    """Restart a paused/partial bake, baking only what the target library lacks.

    Rebuilds the style filter from the target library's own stored metadata, so a
    resume works even after an app restart when the original runner is gone.
    Returns None when the job is unknown or not in a resumable state.
    """
    from src.processors.crt_effect_processor import _target_resolution
    from src.utils.story_library import get_library

    progress = load_bake_progress(job_id)
    if not progress or progress.get("status") not in RESUMABLE_BAKE_STATUSES:
        return None

    target_library_id = progress.get("targetLibraryId")
    record = get_library(target_library_id) if target_library_id else None
    if not record:
        logger.error(f"[StoryBake:{job_id}] Target library {target_library_id!r} no longer exists.")
        return None

    width, height = _target_resolution()
    style_filter = _style_filter_from_record(record, width, height)
    if not style_filter:
        logger.error(f"[StoryBake:{job_id}] Could not rebuild style filter for resume.")
        return None

    _clear_marker(_bake_pause_path(job_id))
    _clear_marker(_bake_cancel_path(job_id))

    runner = StoryLibraryBakeRunner(
        job_id,
        source_library_id=progress.get("sourceLibraryId"),
        target_library_id=target_library_id,
        target_name=progress.get("targetName") or record.get("name") or "",
        style_filter=style_filter,
        style_id=progress.get("styleId") or record.get("styleId") or "",
        style_label=progress.get("styleLabel") or record.get("styleLabel") or "",
    )
    runner.start_async()
    return runner


# --------------------------------------------------------------------------- #
# Append-mode bake: re-bake freshly imported/uploaded clips into an EXISTING
# styled library so they match its baked look. Unlike the runner above (which
# creates a fresh target and overwrites its index), this reads loose raw clip
# files, bakes them style-only with the target library's own stored style, and
# appends the results to the target's index.
# --------------------------------------------------------------------------- #
def _style_filter_from_record(record: dict, width: int, height: int) -> str:
    """Rebuild the exact style filter a library was baked with, from its metadata.

    Mirrors the route's ``_resolve_bake_style``: prefer the stored ``styleParams``
    (captures both preset and custom bakes); fall back to the named ``styleId``.
    """
    from src.processors.crt_effect_processor import (
        build_custom_tv_effect_filter,
        build_tv_effect_filter,
        sanitize_tv_effect_params,
    )

    params = record.get("styleParams")
    if isinstance(params, dict):
        return build_custom_tv_effect_filter(sanitize_tv_effect_params(params), width, height)
    return build_tv_effect_filter(str(record.get("styleId") or "").strip(), width, height)


def bake_and_append_clips(
    library_id,
    raw_clip_paths: list[str],
    *,
    extra_tags: list[str] | None = None,
    source_type: str = "styled",
    session_id: str | None = None,
    progress_cb=None,
) -> list[dict]:
    """Bake loose raw 5s clips style-only with a styled library's own style filter
    and append them to that library. Returns the list of added asset records.

    Each clip keeps its 5s length (no pairing); waveform + CTA are added at render
    time, not baked here. Raises ``ValueError`` if the library is not a styled
    library or its style filter cannot be rebuilt (callers treat this as an
    import failure).
    """
    from src.processors.crt_effect_processor import _target_resolution
    from src.utils.story_library import _index_lock, get_library

    record = get_library(library_id) or {}
    if not record.get("styled"):
        raise ValueError(f"Library {library_id!r} is not a styled/baked library.")

    width, height = _target_resolution()
    style_filter = _style_filter_from_record(record, width, height)
    if not style_filter:
        raise ValueError(f"Could not rebuild style filter for library {library_id!r}.")

    valid_paths = [p for p in raw_clip_paths if p and os.path.isfile(p)]
    items = [{"out_id": uuid.uuid4().hex[:12], "src": p} for p in valid_paths]

    target_root = story_library_root(library_id)
    os.makedirs(os.path.join(target_root, "clips"), exist_ok=True)
    hwaccel = _hwaccel_flags()
    no_cancel = lambda: False

    added: list[dict] = []
    total = len(items)
    for idx, item in enumerate(items):
        out_id = item["out_id"]
        src_path = item["src"]
        if not os.path.isfile(src_path):
            continue

        out_rel = f"clips/{out_id}.mp4"
        out_path = os.path.join(target_root, out_rel)
        if progress_cb:
            progress_cb({"stage": "baking", "current": idx + 1, "total": total,
                         "message": f"Đang bake clip {idx + 1}/{total} vào thư viện..."})

        cmd = _style_only_cmd(src_path, out_path, style_filter, hwaccel)
        if not _run_ffmpeg_with_fallback(cmd, len(hwaccel), no_cancel) or not os.path.isfile(out_path):
            continue
        duration = FFmpegHelper.probe_duration(out_path) or 0

        added.append({
            "id": out_id,
            "source_type": source_type or "styled",
            "source_name": os.path.basename(src_path),
            "relative_path": out_rel,
            "duration": round(float(duration), 3),
            "tags": list(extra_tags or []),
            "created_at": _utc_now(),
            "styled_from": record.get("sourceLibraryId") or library_id,
        })

    if added:
        with _index_lock(library_id):
            index = load_story_library_index(library_id)
            index.setdefault("assets", []).extend(added)
            save_story_library_index(index, library_id)

    logger.info(
        f"[StoryBake] Appended {len(added)}/{total} style-only clips into library {library_id!r}."
    )
    return added
