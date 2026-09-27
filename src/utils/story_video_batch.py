"""Batch runner for multiple story videos.

Batches go through one process-wide FIFO queue: only one batch renders at a time,
and the next one starts when the active batch finishes. Inside a batch, stories
still render concurrently with a bounded worker pool. Every batch writes its
videos to its own folder ``OUTPUT_DIR/story-video/<batch_id>/`` so the outputs of
different batches never mix.

The queue survives a server restart: a queued batch persists its story configs to
``batches/<id>/config.json`` and ``resume_batch_queue()`` re-enqueues it on boot.
"""

import os
import shutil
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime

from src.config import Config
from src.utils.logger import logger
from src.utils.render_priority import RenderResourcePriority
from src.utils.story_clip_bag import SharedClipBag
from src.utils.story_video_pipeline import (
    StoryVideoPipelineRunner,
    _progress_path,
    _save_json,
    _load_json,
    is_story_cancel_requested,
    load_story_progress,
    request_story_cancel,
)

_TERMINAL_STORY_STATUSES = {"completed", "failed", "cancelled"}
# Batch statuses that mean "a runner was working on it" -- after a restart nobody is.
_ACTIVE_BATCH_STATUSES = {"pending", "running", "cancelling"}
_BATCH_HISTORY_LIMIT = 20
_INTERRUPTED_ERROR = "Bi gian doan do server khoi dong lai."


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _batches_root() -> str:
    return os.path.join(Config.STORY_VIDEO_DIR, "batches")


def _batch_dir(batch_id: str) -> str:
    path = os.path.join(_batches_root(), batch_id)
    os.makedirs(path, exist_ok=True)
    return path


def _batch_progress_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "progress.json")


def _batch_cancel_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "cancel.requested")


def _batch_config_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "config.json")


def load_batch_progress(batch_id: str) -> dict | None:
    return _load_json(_batch_progress_path(batch_id))


def is_batch_cancel_requested(batch_id: str) -> bool:
    return os.path.isfile(_batch_cancel_path(batch_id))


def batch_output_dir(progress: dict) -> str:
    """Absolute folder holding this batch's videos; "" for batches made before
    per-batch folders existed (their videos sit directly in story-video/)."""
    subdir = str(progress.get("outputSubdir") or "")
    if not subdir:
        return ""
    return os.path.abspath(os.path.join(Config.OUTPUT_DIR, "story-video", subdir))


def decor_ids_still_needed() -> set[str]:
    """Decor images some batch on disk will still render.

    Every story that has not finished for good counts: pending and running ones,
    and failed ones too, since retry-failed re-renders them with the image they
    were dealt. Purging used decor images must leave these alone.
    """
    root = _batches_root()
    if not os.path.isdir(root):
        return set()
    needed: set[str] = set()
    for batch_id in os.listdir(root):
        progress_path = os.path.join(root, batch_id, "progress.json")
        if not os.path.isfile(progress_path):
            continue
        progress = _load_json(progress_path) or {}
        for story in progress.get("stories") or []:
            decor_id = str(story.get("decor_image_id") or "").strip()
            if decor_id and story.get("status") not in {"completed", "cancelled"}:
                needed.add(decor_id)
    return needed


# ---------------------------------------------------------------------------
# Queue: one worker thread drains a FIFO of runners, one batch at a time.
# ---------------------------------------------------------------------------
_queue_lock = threading.Lock()
_pending: "deque[StoryVideoBatchRunner]" = deque()
_active_batch_id: str | None = None
_worker_running = False
# Recently enqueued batch ids (oldest first) so the UI can list them after a reload.
_history: deque[str] = deque(maxlen=_BATCH_HISTORY_LIMIT)


def _enqueue_runner(runner: "StoryVideoBatchRunner") -> int:
    """Append a runner to the queue, starting the worker if idle. Returns its
    1-based position among waiting batches."""
    global _worker_running
    with _queue_lock:
        _pending.append(runner)
        if runner.batch_id in _history:
            _history.remove(runner.batch_id)
        _history.append(runner.batch_id)
        position = len(_pending)
        if not _worker_running:
            _worker_running = True
            threading.Thread(target=_queue_worker, name="story-batch-queue", daemon=True).start()
    logger.info(f"[StoryBatch:{runner.batch_id}] Queued at position {position}.")
    return position


def _queue_worker():
    global _active_batch_id, _worker_running
    while True:
        with _queue_lock:
            if not _pending:
                _active_batch_id = None
                _worker_running = False
                return
            runner = _pending.popleft()
            _active_batch_id = runner.batch_id
        try:
            runner._run_batch()
        except Exception as exc:
            # A crashed batch must not stall every batch queued behind it.
            logger.error(f"[StoryBatch:{runner.batch_id}] Batch crashed: {exc}", exc_info=True)
            try:
                runner._mark_crashed(exc)
            except Exception:
                logger.error(f"[StoryBatch:{runner.batch_id}] Could not record crash.", exc_info=True)


def queue_snapshot() -> dict:
    with _queue_lock:
        return {
            "activeBatchId": _active_batch_id,
            "queuedIds": [runner.batch_id for runner in _pending],
            # Newest first, the order the UI lists them in.
            "historyIds": list(reversed(_history)),
        }


def queue_position(batch_id: str) -> int:
    """1-based position among waiting batches; 0 when running, finished or unknown."""
    with _queue_lock:
        for index, runner in enumerate(_pending):
            if runner.batch_id == batch_id:
                return index + 1
    return 0


def is_batch_queued_or_active(batch_id: str) -> bool:
    with _queue_lock:
        return batch_id == _active_batch_id or any(r.batch_id == batch_id for r in _pending)


def _queued_runner(batch_id: str) -> "StoryVideoBatchRunner | None":
    with _queue_lock:
        return next((r for r in _pending if r.batch_id == batch_id), None)


def _remove_queued_batch(batch_id: str) -> "StoryVideoBatchRunner | None":
    """Take a still-waiting batch out of the queue; None if it already started."""
    with _queue_lock:
        for runner in _pending:
            if runner.batch_id == batch_id:
                _pending.remove(runner)
                return runner
    return None


def request_batch_story_cancel(batch_id: str, story_id: str) -> dict | None:
    """Cancel one queued or active story and persist an immediate UI state."""
    progress = load_batch_progress(batch_id)
    if not progress:
        return None
    story = next((item for item in progress.get("stories", []) if item.get("storyId") == story_id), None)
    if not story:
        return None
    if story.get("status") not in _TERMINAL_STORY_STATUSES:
        request_story_cancel(story_id)
        story["status"] = "cancelling"
        progress["updatedAt"] = _utc_now()
        _save_json(_batch_progress_path(batch_id), progress)
        # A waiting batch rewrites progress.json from memory when it starts; mirror the
        # state there too so the item doesn't flash back to "pending" in the UI.
        runner = _queued_runner(batch_id)
        if runner is not None:
            runner._mark_story_status(story_id, "cancelling")
    return story


def request_batch_cancel(batch_id: str) -> dict | None:
    """Cancel all active and queued stories in a batch."""
    queued = _remove_queued_batch(batch_id)
    if queued is not None:
        # Nothing of it ran yet: finish it right away instead of making it wait its
        # turn just to skip every story.
        queued._finalize_cancelled_before_start()
        return queued.progress

    progress = load_batch_progress(batch_id)
    if not progress:
        return None
    if progress.get("status") in {"completed", "failed", "partial", "cancelled"}:
        return progress
    with open(_batch_cancel_path(batch_id), "w", encoding="utf-8") as file_obj:
        file_obj.write(_utc_now())
    for story in progress.get("stories", []):
        if story.get("status") in _TERMINAL_STORY_STATUSES:
            continue
        story_id = str(story.get("storyId") or "")
        if story_id:
            request_story_cancel(story_id)
        story["status"] = "cancelling"
    progress["status"] = "cancelling"
    progress["message"] = "Dang huy batch..."
    progress["updatedAt"] = _utc_now()
    _save_json(_batch_progress_path(batch_id), progress)
    return progress


def _final_batch_status(*, total: int, completed: int, failed: int, cancelled: int, cancel_requested: bool) -> str:
    if cancel_requested or cancelled == total:
        return "cancelled"
    if failed == 0 and cancelled == 0:
        return "completed"
    if completed > 0 or cancelled > 0:
        return "partial"
    return "failed"


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------
def _mark_story_interrupted(story_id: str, status: str):
    """Close out a story's own progress.json, which the batch progress API prefers
    over the batch copy -- left alone it would read "running" forever."""
    story_progress = load_story_progress(story_id)
    if not story_progress or story_progress.get("status") in _TERMINAL_STORY_STATUSES:
        return
    story_progress["status"] = status
    story_progress["message"] = _INTERRUPTED_ERROR
    if status == "failed":
        story_progress["error"] = _INTERRUPTED_ERROR
    story_progress["updatedAt"] = _utc_now()
    _save_json(_progress_path(story_id), story_progress)


def _mark_batch_interrupted(batch_id: str, progress: dict):
    """A batch that was running when the server went down: unfinished stories
    become failed (so Retry picks them up), or cancelled if a cancel was pending."""
    cancel_requested = progress.get("status") == "cancelling" or is_batch_cancel_requested(batch_id)
    story_status = "cancelled" if cancel_requested else "failed"
    stories = progress.get("stories", [])
    results = progress.setdefault("results", [])
    for story in stories:
        if story.get("status") in _TERMINAL_STORY_STATUSES:
            continue
        story_id = str(story.get("storyId") or "")
        story["status"] = story_status
        entry = {"storyId": story_id, "status": story_status}
        if story_status == "failed":
            entry["error"] = _INTERRUPTED_ERROR
        results.append(entry)
        if story_id:
            _mark_story_interrupted(story_id, story_status)

    completed = sum(1 for s in stories if s.get("status") == "completed")
    failed = sum(1 for s in stories if s.get("status") == "failed")
    cancelled = sum(1 for s in stories if s.get("status") == "cancelled")
    progress.update({
        "status": _final_batch_status(
            total=len(stories),
            completed=completed,
            failed=failed,
            cancelled=cancelled,
            cancel_requested=cancel_requested,
        ),
        "completed": completed,
        "failed": failed,
        "cancelled": cancelled,
        "current": completed + failed + cancelled,
        "percent": 100,
        "message": f"{_INTERRUPTED_ERROR} {completed} thanh cong, {failed} that bai, {cancelled} da huy.",
        "updatedAt": _utc_now(),
    })
    _save_json(_batch_progress_path(batch_id), progress)
    logger.warning(
        f"[StoryBatch:{batch_id}] Interrupted by restart: completed={completed}, "
        f"failed={failed}, cancelled={cancelled}"
    )


def resume_batch_queue() -> int:
    """Rebuild the queue after a server start. Returns how many batches were re-queued.

    Call it from the real server entry point only -- never at import time, or a test
    importing the app would start rendering whatever is queued on real storage.
    """
    root = _batches_root()
    if not os.path.isdir(root):
        return 0

    to_requeue: list[tuple[str, str, dict]] = []
    for batch_id in sorted(os.listdir(root)):
        progress_path = os.path.join(root, batch_id, "progress.json")
        if not os.path.isfile(progress_path) or is_batch_queued_or_active(batch_id):
            continue
        progress = _load_json(progress_path)
        if not progress:
            continue
        status = progress.get("status")
        if status == "queued":
            config = _load_json(os.path.join(root, batch_id, "config.json"))
            if config and config.get("storyConfigs"):
                to_requeue.append((str(progress.get("queuedAt") or ""), batch_id, config))
            else:
                _mark_batch_interrupted(batch_id, progress)
        elif status in _ACTIVE_BATCH_STATUSES:
            _mark_batch_interrupted(batch_id, progress)

    for queued_at, batch_id, config in sorted(to_requeue):
        runner = StoryVideoBatchRunner(
            batch_id,
            config["storyConfigs"],
            optimize_mode=bool(config.get("optimizeMode", False)),
            output_subdir=config.get("outputSubdir") or batch_id,
        )
        # Keep the original place in line rather than the restart time.
        runner.progress["queuedAt"] = queued_at or _utc_now()
        runner.enqueue()

    if to_requeue:
        logger.info(f"[StoryBatch] Resumed {len(to_requeue)} queued batch(es) after restart.")
    return len(to_requeue)


class StoryVideoBatchRunner:
    """Runs a batch of story video pipelines; batches are serialized by the queue.

    Stories are rendered concurrently with a bounded worker pool
    (``Config.STORY_BATCH_MAX_WORKERS``). Each story renders in its own
    ``story_id``-namespaced dirs and as an independent ffmpeg process, so the
    output is identical to sequential rendering — only the scheduling differs.
    """

    @staticmethod
    def _decor_name(image_id: str) -> str:
        """Display name for the decor image this item drew, for the batch UI."""
        if not image_id:
            return ""
        from src.utils.story_decor_images import get_decor_image

        record = get_decor_image(image_id)
        return str(record.get("name") or image_id) if record else image_id

    @staticmethod
    def _waveform_name(overlay_id: str) -> str:
        """Display name for the waveform this item drew, for the batch UI."""
        if not overlay_id:
            return ""
        from src.utils.waveform_overlays import get_waveform_overlay

        record = get_waveform_overlay(overlay_id)
        return str(record.get("name") or overlay_id) if record else overlay_id

    @staticmethod
    def _cta_name(overlay_id: str) -> str:
        """Display name for the CTA overlay this item drew, for the batch UI."""
        if not overlay_id:
            return ""
        from src.utils.story_cta_overlay import get_cta_overlay

        record = get_cta_overlay(overlay_id)
        return str(record.get("name") or overlay_id) if record else overlay_id

    @staticmethod
    def _subtitle_style_name(style_id: str) -> str:
        """Display name for the subtitle style this item drew, for the batch UI."""
        if not style_id:
            return ""
        from src.utils.subtitle_styles import get_subtitle_style

        record = get_subtitle_style(style_id)
        return str(record.get("name") or style_id) if record else style_id

    @staticmethod
    def _edit_style_name(style_id: str) -> str:
        """Display name for a layout/modifier record, for the batch UI."""
        if not style_id:
            return ""
        from src.utils.edit_styles.store import get_edit_style

        record = get_edit_style(style_id)
        return str(record.get("name") or style_id) if record else style_id

    def __init__(
        self,
        batch_id: str,
        story_configs: list[dict],
        *,
        optimize_mode: bool = False,
        output_subdir: str | None = None,
    ):
        self.batch_id = batch_id
        self.story_configs = story_configs
        # Folder under OUTPUT_DIR/story-video/ this batch's videos land in. A retry
        # passes the original batch's folder so the replacements sit with the rest.
        self.output_subdir = output_subdir or batch_id
        # Optimize mode (per-batch toggle): when True, suspend configured competing
        # apps and boost ffmpeg priority for this batch's duration; when False, render
        # alongside everything else. Defaults off so batches don't freeze other apps
        # unless the caller opts in.
        self._optimize_mode = bool(optimize_mode)
        # RLock so a locked section can safely call another locked helper.
        self._lock = threading.RLock()
        self._max_workers = max(1, min(Config.STORY_BATCH_MAX_WORKERS, len(story_configs) or 1))
        # One shuffled deck shared by every story of this batch, so clips are
        # drawn without replacement across videos instead of each video
        # re-shuffling the whole library pool independently.
        self._clip_bag = SharedClipBag()

        # Counters shared across worker threads; always mutate under self._lock.
        self.completed_count = 0
        self.failed_count = 0
        self.cancelled_count = 0

        self.progress = {
            "batchId": batch_id,
            "status": "pending",
            "total": len(story_configs),
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "current": 0,
            "percent": 0,
            "optimizeMode": self._optimize_mode,
            "outputSubdir": self.output_subdir,
            "message": "Cho xu ly...",
            "stories": [],
            "results": [],
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
        }

        for i, config in enumerate(story_configs):
            story_id = config.get("story_id") or f"s-{batch_id}-{i}"
            config["story_id"] = story_id
            config["output_subdir"] = self.output_subdir
            self.progress["stories"].append({
                "storyId": story_id,
                "status": "pending",
                "outputName": config.get("output_name", ""),
                "input_type": config.get("input_type", ""),
                "input_value": config.get("input_value", ""),
                "output_name": config.get("output_name", ""),
                "output_subdir": self.output_subdir,
                "clip_tags": config.get("clip_tags", []),
                "library_ids": config.get("library_ids") or (
                    [config["library_id"]] if config.get("library_id") else []
                ),
                "skip_tv_effect": bool(config.get("skip_tv_effect", False)),
                "clip_usage_mode": config.get("clip_usage_mode", "reuse"),
                "decor_image_id": config.get("decor_image_id", ""),
                "decor_image_name": self._decor_name(config.get("decor_image_id", "")),
                "waveform_overlay_id": config.get("waveform_overlay_id", ""),
                "waveform_overlay_name": self._waveform_name(config.get("waveform_overlay_id", "")),
                "cta_overlay_id": config.get("cta_overlay_id", ""),
                "cta_overlay_name": self._cta_name(config.get("cta_overlay_id", "")),
                "voice_id": config.get("voice_id", ""),
                "intro_video_path": config.get("intro_video_path", ""),
                "subtitle_path": config.get("subtitle_path", ""),
                "subtitle_font": config.get("subtitle_font", ""),
                "subtitle_preset": config.get("subtitle_preset", "clean"),
                "subtitle_max_chars_per_line": config.get(
                    "subtitle_max_chars_per_line", Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE
                ),
                "subtitle_max_lines": config.get(
                    "subtitle_max_lines", Config.STORY_SUBTITLE_MAX_LINES
                ),
                # Persisted so a retry can rebuild the same font size and colours;
                # without it the retried item comes back styled differently.
                "subtitle_style_overrides": config.get("subtitle_style_overrides") or {},
                "subtitle_style_id": config.get("subtitle_style_id", ""),
                "subtitle_style_name": self._subtitle_style_name(config.get("subtitle_style_id", "")),
                # Retry-failed rebuilds configs from this progress entry only, so the
                # edit style has to be recorded here or a retried item loses it.
                "layout_id": config.get("layout_id", ""),
                "layout_name": self._edit_style_name(config.get("layout_id", "")),
                "modifier_ids": list(config.get("modifier_ids") or []),
                "modifier_names": [self._edit_style_name(m) for m in config.get("modifier_ids") or []],
                "chapters_path": config.get("chapters_path", ""),
            })

        self._save_progress()

    def _save_progress(self):
        with self._lock:
            self.progress["updatedAt"] = _utc_now()
            _save_json(_batch_progress_path(self.batch_id), self.progress)

    def _update_progress(
        self,
        status: str,
        percent: float,
        message: str,
        *,
        current: int | None = None,
        completed: int | None = None,
        failed: int | None = None,
        cancelled: int | None = None,
    ):
        self.progress["status"] = status
        self.progress["percent"] = round(min(100, max(0, percent)), 1)
        self.progress["message"] = message
        if current is not None:
            self.progress["current"] = current
        if completed is not None:
            self.progress["completed"] = completed
        if failed is not None:
            self.progress["failed"] = failed
        if cancelled is not None:
            self.progress["cancelled"] = cancelled
        self._save_progress()

    def enqueue(self) -> int:
        """Put this batch at the back of the render queue.

        Returns its 1-based position among waiting batches (the worker may pick it
        up immediately when nothing else is running).
        """
        with self._lock:
            self.progress["status"] = "queued"
            self.progress["message"] = "Dang cho trong hang doi..."
            self.progress["queuedAt"] = self.progress.get("queuedAt") or _utc_now()
        # config.json before the "queued" progress: once progress says queued, a
        # restart must be able to rebuild this runner from disk.
        _save_json(_batch_config_path(self.batch_id), {
            "batchId": self.batch_id,
            "storyConfigs": self.story_configs,
            "optimizeMode": self._optimize_mode,
            "outputSubdir": self.output_subdir,
            "queuedAt": self.progress["queuedAt"],
        })
        self._save_progress()
        return _enqueue_runner(self)

    def _mark_story_status(self, story_id: str, status: str):
        with self._lock:
            for story in self.progress["stories"]:
                if story.get("storyId") == story_id:
                    story["status"] = status
        self._save_progress()

    def _finalize_cancelled_before_start(self):
        """The batch was cancelled while still waiting: no story ever ran."""
        total = self.progress["total"]
        with self._lock:
            for story in self.progress["stories"]:
                story["status"] = "cancelled"
                self.progress["results"].append({"storyId": story["storyId"], "status": "cancelled"})
            self.cancelled_count = total
        # Same as _run_batch with no failures: the working cache (uploaded audio...)
        # has nothing left to retry. _update_progress recreates the dir for progress.json.
        shutil.rmtree(_batch_dir(self.batch_id), ignore_errors=True)
        self._update_progress(
            "cancelled", 100,
            f"Batch da huy khi dang cho trong hang doi ({total} video chua chay).",
            current=total, completed=0, failed=0, cancelled=total,
        )
        logger.info(f"[StoryBatch:{self.batch_id}] Cancelled while queued.")

    def _mark_crashed(self, exc: Exception):
        """_run_batch blew up outside the per-story guards: close out what's left."""
        with self._lock:
            for story in self.progress["stories"]:
                if story.get("status") in _TERMINAL_STORY_STATUSES:
                    continue
                story["status"] = "failed"
                self.progress["results"].append({
                    "storyId": story["storyId"], "status": "failed", "error": str(exc),
                })
                self.failed_count += 1
            completed, failed, cancelled = self.completed_count, self.failed_count, self.cancelled_count
        self._update_progress(
            "partial" if completed or cancelled else "failed", 100,
            f"Batch loi: {exc}",
            current=completed + failed + cancelled,
            completed=completed, failed=failed, cancelled=cancelled,
        )

    def _emit_progress(self, *, message: str | None = None):
        """Recompute batch counters/percent from finished stories and persist."""
        with self._lock:
            total = self.progress["total"] or 1
            finished = self.completed_count + self.failed_count + self.cancelled_count
            self.progress["completed"] = self.completed_count
            self.progress["failed"] = self.failed_count
            self.progress["cancelled"] = self.cancelled_count
            self.progress["current"] = finished
            self.progress["percent"] = round(min(100, max(0, (finished / total) * 100)), 1)
            self.progress["status"] = (
                "cancelling" if is_batch_cancel_requested(self.batch_id) else "running"
            )
            self.progress["message"] = message or (
                f"Da xu ly {finished}/{self.progress['total']} video..."
            )
            self.progress["updatedAt"] = _utc_now()
            _save_json(_batch_progress_path(self.batch_id), self.progress)

    def _run_single_story(self, index: int, config: dict):
        """Render one story; updates shared progress under the lock. Never raises."""
        story_id = config["story_id"]

        if is_batch_cancel_requested(self.batch_id):
            request_story_cancel(story_id)
        if is_story_cancel_requested(story_id):
            with self._lock:
                self.progress["stories"][index]["status"] = "cancelled"
                self.progress["results"].append({"storyId": story_id, "status": "cancelled"})
                self.cancelled_count += 1
            self._emit_progress()
            return

        with self._lock:
            self.progress["stories"][index]["status"] = "running"
        self._emit_progress(message=f"Dang xu ly video {story_id}...")

        try:
            runner = StoryVideoPipelineRunner(story_id, config, clip_bag=self._clip_bag)
            output_path = runner.run()

            if output_path:
                with self._lock:
                    self.progress["stories"][index]["status"] = "completed"
                    self.progress["results"].append({
                        "storyId": story_id,
                        "status": "completed",
                        "videoPath": output_path,
                    })
                    self.completed_count += 1
            else:
                story_progress = load_story_progress(story_id)
                error_msg = story_progress.get("error", "Unknown error") if story_progress else ""
                if is_story_cancel_requested(story_id) or (
                    story_progress and story_progress.get("status") == "cancelled"
                ):
                    with self._lock:
                        self.progress["stories"][index]["status"] = "cancelled"
                        self.progress["results"].append({"storyId": story_id, "status": "cancelled"})
                        self.cancelled_count += 1
                else:
                    with self._lock:
                        self.progress["stories"][index]["status"] = "failed"
                        self.progress["results"].append({
                            "storyId": story_id,
                            "status": "failed",
                            "error": error_msg,
                        })
                        self.failed_count += 1

        except Exception as exc:
            if is_story_cancel_requested(story_id):
                with self._lock:
                    self.progress["stories"][index]["status"] = "cancelled"
                    self.progress["results"].append({"storyId": story_id, "status": "cancelled"})
                    self.cancelled_count += 1
            else:
                logger.error(
                    f"[StoryBatch:{self.batch_id}] Story {story_id} exception: {exc}",
                    exc_info=True,
                )
                with self._lock:
                    self.progress["stories"][index]["status"] = "failed"
                    self.progress["results"].append({
                        "storyId": story_id,
                        "status": "failed",
                        "error": str(exc),
                    })
                    self.failed_count += 1

        self._emit_progress()

    def _run_batch(self):
        """Process stories concurrently with a bounded worker pool."""
        total = len(self.story_configs)
        # startedAt was the enqueue time; the render actually starts now.
        self.progress["startedAt"] = _utc_now()
        self._update_progress("running", 0, f"Bat dau xu ly batch {total} video...")
        logger.info(
            f"[StoryBatch:{self.batch_id}] Started batch with {total} stories "
            f"(max_workers={self._max_workers}, output_subdir={self.output_subdir})."
        )

        # Optimize mode: suspend configured competing apps + boost ffmpeg for the whole
        # batch; the guard always resumes them on exit (even on exception). Off = a
        # no-op nullcontext so other apps keep running alongside the render.
        resource_guard = (
            RenderResourcePriority(label=f":{self.batch_id}")
            if self._optimize_mode
            else nullcontext()
        )
        with resource_guard:
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                futures = {
                    executor.submit(self._run_single_story, i, config): i
                    for i, config in enumerate(self.story_configs)
                }
                for future in as_completed(futures):
                    # _run_single_story never raises, but surface anything unexpected.
                    future.result()

        completed_count = self.completed_count
        failed_count = self.failed_count
        cancelled_count = self.cancelled_count

        final_status = _final_batch_status(
            total=total,
            completed=completed_count,
            failed=failed_count,
            cancelled=cancelled_count,
            cancel_requested=is_batch_cancel_requested(self.batch_id),
        )

        if failed_count == 0:
            # Nothing left to retry, so the uploaded audio/subtitle originals and any
            # other working cache under the batch dir can go. progress.json gets
            # rewritten right after by _update_progress, which recreates the dir via
            # _batch_dir()'s makedirs. Batches with failures keep their dir intact
            # since retry-failed re-reads the originals from it.
            batch_dir = _batch_dir(self.batch_id)
            try:
                shutil.rmtree(batch_dir, ignore_errors=True)
            except OSError:
                pass

        self._update_progress(
            final_status, 100,
            f"Batch hoan tat: {completed_count} thanh cong, {failed_count} that bai, {cancelled_count} da huy.",
            current=completed_count + failed_count + cancelled_count,
            completed=completed_count,
            failed=failed_count,
            cancelled=cancelled_count,
        )
        logger.info(
            f"[StoryBatch:{self.batch_id}] Batch finished: "
            f"completed={completed_count}, failed={failed_count}, cancelled={cancelled_count}"
        )
