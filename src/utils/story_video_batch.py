"""Batch runner for multiple story videos.

Queues and processes a list of story configs sequentially in a background thread
with unified batch progress tracking.
"""

import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime

from src.config import Config
from src.utils.logger import logger
from src.utils.render_priority import RenderResourcePriority
from src.utils.story_clip_bag import SharedClipBag
from src.utils.story_video_pipeline import (
    StoryVideoPipelineRunner,
    _save_json,
    _load_json,
    is_story_cancel_requested,
    load_story_progress,
    request_story_cancel,
)


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _batch_dir(batch_id: str) -> str:
    path = os.path.join(Config.STORY_VIDEO_DIR, "batches", batch_id)
    os.makedirs(path, exist_ok=True)
    return path


def _batch_progress_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "progress.json")


def _batch_cancel_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "cancel.requested")


def load_batch_progress(batch_id: str) -> dict | None:
    return _load_json(_batch_progress_path(batch_id))


def is_batch_cancel_requested(batch_id: str) -> bool:
    return os.path.isfile(_batch_cancel_path(batch_id))


def request_batch_story_cancel(batch_id: str, story_id: str) -> dict | None:
    """Cancel one queued or active story and persist an immediate UI state."""
    progress = load_batch_progress(batch_id)
    if not progress:
        return None
    story = next((item for item in progress.get("stories", []) if item.get("storyId") == story_id), None)
    if not story:
        return None
    if story.get("status") not in {"completed", "failed", "cancelled"}:
        request_story_cancel(story_id)
        story["status"] = "cancelling"
        progress["updatedAt"] = _utc_now()
        _save_json(_batch_progress_path(batch_id), progress)
    return story


def request_batch_cancel(batch_id: str) -> dict | None:
    """Cancel all active and queued stories in a batch."""
    progress = load_batch_progress(batch_id)
    if not progress:
        return None
    if progress.get("status") in {"completed", "failed", "partial", "cancelled"}:
        return progress
    with open(_batch_cancel_path(batch_id), "w", encoding="utf-8") as file_obj:
        file_obj.write(_utc_now())
    for story in progress.get("stories", []):
        if story.get("status") in {"completed", "failed", "cancelled"}:
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


class StoryVideoBatchRunner:
    """Runs a batch of story video pipelines in a daemon thread.

    Stories are rendered concurrently with a bounded worker pool
    (``Config.STORY_BATCH_MAX_WORKERS``). Each story renders in its own
    ``story_id``-namespaced dirs and as an independent ffmpeg process, so the
    output is identical to sequential rendering — only the scheduling differs.
    """

    def __init__(self, batch_id: str, story_configs: list[dict], *, optimize_mode: bool = False):
        self.batch_id = batch_id
        self.story_configs = story_configs
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
            "message": "Cho xu ly...",
            "stories": [],
            "results": [],
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
        }

        for i, config in enumerate(story_configs):
            story_id = config.get("story_id") or f"s-{batch_id}-{i}"
            config["story_id"] = story_id
            self.progress["stories"].append({
                "storyId": story_id,
                "status": "pending",
                "outputName": config.get("output_name", ""),
                "input_type": config.get("input_type", ""),
                "input_value": config.get("input_value", ""),
                "output_name": config.get("output_name", ""),
                "clip_tags": config.get("clip_tags", []),
                "library_id": config.get("library_id", ""),
                "skip_tv_effect": bool(config.get("skip_tv_effect", False)),
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

    def start_async(self):
        """Start batch processing in a daemon thread."""
        thread = threading.Thread(target=self._run_batch, daemon=True)
        thread.start()
        logger.info(
            f"[StoryBatch:{self.batch_id}] Started batch with {len(self.story_configs)} stories "
            f"(max_workers={self._max_workers})."
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
        self._update_progress("running", 0, f"Bat dau xu ly batch {total} video...")

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

        if is_batch_cancel_requested(self.batch_id) or cancelled_count == total:
            final_status = "cancelled"
        elif failed_count == 0 and cancelled_count == 0:
            final_status = "completed"
        elif completed_count > 0 or cancelled_count > 0:
            final_status = "partial"
        else:
            final_status = "failed"

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
