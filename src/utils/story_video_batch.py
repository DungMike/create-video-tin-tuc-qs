"""Batch runner for multiple story videos.

Queues and processes a list of story configs sequentially in a background thread
with unified batch progress tracking.
"""

import os
import threading
from datetime import datetime

from src.config import Config
from src.utils.logger import logger
from src.utils.story_video_pipeline import (
    StoryVideoPipelineRunner,
    _save_json,
    _load_json,
    load_story_progress,
)


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _batch_dir(batch_id: str) -> str:
    path = os.path.join(Config.STORY_VIDEO_DIR, "batches", batch_id)
    os.makedirs(path, exist_ok=True)
    return path


def _batch_progress_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "progress.json")


def load_batch_progress(batch_id: str) -> dict | None:
    return _load_json(_batch_progress_path(batch_id))


class StoryVideoBatchRunner:
    """Runs a batch of story video pipelines sequentially in a daemon thread."""

    def __init__(self, batch_id: str, story_configs: list[dict]):
        self.batch_id = batch_id
        self.story_configs = story_configs
        self._lock = threading.Lock()

        self.progress = {
            "batchId": batch_id,
            "status": "pending",
            "total": len(story_configs),
            "completed": 0,
            "failed": 0,
            "current": 0,
            "percent": 0,
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
                "voice_id": config.get("voice_id", ""),
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
        self._save_progress()

    def start_async(self):
        """Start batch processing in a daemon thread."""
        thread = threading.Thread(target=self._run_batch, daemon=True)
        thread.start()
        logger.info(
            f"[StoryBatch:{self.batch_id}] Started batch with {len(self.story_configs)} stories."
        )

    def _run_batch(self):
        """Process each story sequentially."""
        total = len(self.story_configs)
        self._update_progress("running", 0, f"Bat dau xu ly batch {total} video...")

        completed_count = 0
        failed_count = 0

        for i, config in enumerate(self.story_configs):
            story_id = config["story_id"]

            with self._lock:
                self.progress["stories"][i]["status"] = "running"

            overall_pct = (i / total) * 100
            self._update_progress(
                "running", overall_pct,
                f"Dang xu ly video {i + 1}/{total} ({story_id})...",
                current=i + 1,
            )

            try:
                runner = StoryVideoPipelineRunner(story_id, config)
                output_path = runner.run()

                if output_path:
                    completed_count += 1
                    with self._lock:
                        self.progress["stories"][i]["status"] = "completed"
                        self.progress["results"].append({
                            "storyId": story_id,
                            "status": "completed",
                            "videoPath": output_path,
                        })
                else:
                    failed_count += 1
                    story_progress = load_story_progress(story_id)
                    error_msg = ""
                    if story_progress:
                        error_msg = story_progress.get("error", "Unknown error")
                    with self._lock:
                        self.progress["stories"][i]["status"] = "failed"
                        self.progress["results"].append({
                            "storyId": story_id,
                            "status": "failed",
                            "error": error_msg,
                        })

            except Exception as exc:
                failed_count += 1
                logger.error(
                    f"[StoryBatch:{self.batch_id}] Story {story_id} exception: {exc}",
                    exc_info=True,
                )
                with self._lock:
                    self.progress["stories"][i]["status"] = "failed"
                    self.progress["results"].append({
                        "storyId": story_id,
                        "status": "failed",
                        "error": str(exc),
                    })

            self._update_progress(
                "running",
                ((i + 1) / total) * 100,
                f"Hoan tat video {i + 1}/{total}.",
                current=i + 1,
                completed=completed_count,
                failed=failed_count,
            )

        final_status = "completed" if failed_count == 0 else ("partial" if completed_count > 0 else "failed")
        self._update_progress(
            final_status, 100,
            f"Batch hoan tat: {completed_count} thanh cong, {failed_count} that bai.",
            completed=completed_count,
            failed=failed_count,
        )
        logger.info(
            f"[StoryBatch:{self.batch_id}] Batch finished: "
            f"completed={completed_count}, failed={failed_count}"
        )
