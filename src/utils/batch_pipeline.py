"""Batch Pipeline — orchestrate sequential TTS → Image → Render for multiple Google Docs URLs."""

import json
import os
import random
import shutil
import uuid
from datetime import datetime

from src.composer.renderer import Renderer
from src.composer.timeline import TimelineComposer
from src.config import Config
from src.processors.audio_utils import get_audio_duration, validate_audio
from src.processors.image_processor import ImageProcessor
from src.utils.decor_videos import get_decor_video, get_decor_video_absolute_path, list_decor_videos
from src.utils.effects_library import load_active_animation_presets, load_active_transition_presets
from src.utils.file_manager import (
    cleanup_job_files,
    save_job_manifest,
    setup_directories,
    storage_relative_path,
)
from src.utils.logger import logger
from src.utils.tts_audio import (
    copy_generated_audio_to_job,
    create_audio_from_google_doc,
)


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _batch_dir(batch_id: str) -> str:
    path = os.path.join(Config.STORAGE_DIR, "batch", batch_id)
    os.makedirs(path, exist_ok=True)
    return path


def _progress_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "progress.json")


def load_batch_progress(batch_id: str) -> dict | None:
    path = _progress_path(batch_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fobj:
            return json.load(fobj)
    except (json.JSONDecodeError, OSError):
        return None


def _save_progress(batch_id: str, data: dict):
    path = _progress_path(batch_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fobj:
        json.dump(data, fobj, ensure_ascii=False, indent=2)


class BatchPipelineRunner:
    """Runs the full Image+Audio pipeline sequentially for a list of Google Docs URLs."""

    def __init__(
        self,
        batch_id: str,
        items: list[dict],
        shared_image_paths: list[str],
        voice_id: str,
        speed: float,
        volume: float,
    ):
        self.batch_id = batch_id
        self.items = list(items)
        self.shared_image_paths = list(shared_image_paths)
        self.voice_id = voice_id
        self.speed = speed
        self.volume = volume

        # Build initial progress state
        self.progress: dict = {
            "batchId": batch_id,
            "status": "pending",
            "totalUrls": len(items),
            "completedUrls": 0,
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
            "items": [],
        }
        for index, item in enumerate(items):
            decor_name = ""
            if item.get("decorVideoId"):
                decor_record = get_decor_video(item["decorVideoId"])
                decor_name = decor_record["name"] if decor_record else ""
            self.progress["items"].append(
                {
                    "index": index,
                    "outputName": item["outputName"],
                    "docUrl": item["docUrl"],
                    "decorVideoId": item.get("decorVideoId", ""),
                    "decorVideoName": decor_name,
                    "status": "pending",
                    "stage": "pending",
                    "percent": 0,
                    "message": "Cho xu ly...",
                    "outputVideo": None,
                    "jobId": None,
                    "error": None,
                }
            )
        self._assign_decor_videos()
        self._save()

    def _save(self):
        self.progress["updatedAt"] = _utc_now()
        _save_progress(self.batch_id, self.progress)

    def _assign_decor_videos(self):
        """Auto-assign PiP overlays for items that don't have one.

        Uses round-robin random selection without repeats. When the pool
        is exhausted (more URLs than decor videos), it resets and continues.
        """
        all_decor = list_decor_videos()
        if not all_decor:
            logger.info("[BatchPipeline] No decor videos in library. Skipping PiP auto-assign.")
            return

        # Collect IDs that are already explicitly chosen
        used_ids: set[str] = set()
        for item in self.items:
            if item.get("decorVideoId"):
                used_ids.add(item["decorVideoId"])

        all_ids = [dv["id"] for dv in all_decor]
        decor_lookup = {dv["id"]: dv for dv in all_decor}

        # Build a pool of available IDs (not yet used)
        available_pool = [did for did in all_ids if did not in used_ids]
        random.shuffle(available_pool)

        for index, item in enumerate(self.items):
            if item.get("decorVideoId"):
                continue

            # Refill pool if exhausted
            if not available_pool:
                available_pool = list(all_ids)
                random.shuffle(available_pool)

            chosen_id = available_pool.pop(0)
            item["decorVideoId"] = chosen_id
            used_ids.add(chosen_id)

            # Update progress item with the auto-assigned info
            decor_record = decor_lookup.get(chosen_id)
            self.progress["items"][index]["decorVideoId"] = chosen_id
            self.progress["items"][index]["decorVideoName"] = (
                decor_record["name"] if decor_record else chosen_id
            )
            logger.info(
                f"[BatchPipeline] Auto-assigned decor '{decor_record['name'] if decor_record else chosen_id}' "
                f"to item {index} ({item['outputName']})"
            )

    def _update_item_progress(
        self,
        index: int,
        stage: str,
        percent: float,
        message: str,
        *,
        status: str = "running",
        output_video: str | None = None,
        job_id: str | None = None,
        error: str | None = None,
    ):
        item = self.progress["items"][index]
        item["status"] = status
        item["stage"] = stage
        item["percent"] = round(min(100, max(0, percent)), 1)
        item["message"] = message
        if output_video is not None:
            item["outputVideo"] = output_video
        if job_id is not None:
            item["jobId"] = job_id
        if error is not None:
            item["error"] = error

        # Recalculate overall completed count
        self.progress["completedUrls"] = sum(
            1 for it in self.progress["items"] if it["status"] in ("completed", "failed")
        )
        self._save()

    def _shuffle_images_for_index(self, index: int) -> list[str]:
        """Return a shuffled copy of shared images using a deterministic seed per index."""
        paths = list(self.shared_image_paths)
        rng = random.Random(hash((self.batch_id, index)))
        rng.shuffle(paths)
        return paths

    def _copy_images_to_job(self, shuffled_paths: list[str], target_dir: str) -> list[str]:
        """Copy shared images into a job's raw_images directory."""
        os.makedirs(target_dir, exist_ok=True)
        copied = []
        for idx, src_path in enumerate(shuffled_paths):
            ext = os.path.splitext(src_path)[1] or ".jpg"
            dst = os.path.join(target_dir, f"batch_img_{idx:04d}{ext}")
            shutil.copy2(src_path, dst)
            copied.append(dst)
        return copied

    def _run_single_url(self, index: int, item: dict):
        """Execute the full pipeline for one URL: TTS → Job → Images → Timeline → Render."""
        output_name = item["outputName"]
        doc_url = item["docUrl"]
        decor_video_id = item.get("decorVideoId", "")

        logger.info(f"[BatchPipeline] Starting item {index}: {output_name} | {doc_url}")

        # --- Stage 1: TTS Audio ---
        self._update_item_progress(index, "tts_audio", 5, f"Dang tao audio tu Google Docs...")
        audio_result = create_audio_from_google_doc(
            doc_url=doc_url,
            output_name=output_name,
            voice_id=self.voice_id,
            speed=self.speed,
            volume=self.volume,
        )
        audio_relative_path = audio_result["audio"]["relativePath"]
        logger.info(f"[BatchPipeline] Item {index}: Audio created → {audio_relative_path}")

        # --- Stage 2: Job Setup ---
        self._update_item_progress(index, "job_setup", 25, "Dang tao job va copy anh nguon...")
        job_id = str(uuid.uuid4())[:8]
        self._update_item_progress(index, "job_setup", 25, "Dang tao job...", job_id=job_id)
        dirs = setup_directories(job_id)

        # Copy audio to job
        audio_path = copy_generated_audio_to_job(audio_relative_path, dirs["audio"])
        if not validate_audio(audio_path):
            raise RuntimeError(f"Audio generated for '{output_name}' is invalid.")
        audio_duration = get_audio_duration(audio_path)

        # Copy shuffled images to job
        shuffled_images = self._shuffle_images_for_index(index)
        image_paths = self._copy_images_to_job(shuffled_images, dirs["raw_images"])

        # Save manifest
        manifest = {
            "job_id": job_id,
            "created_at": _utc_now(),
            "audio_relative_path": storage_relative_path(audio_path),
            "audio_duration": round(audio_duration, 3),
            "render_mode": "image_audio_only",
            "image_paths": [storage_relative_path(p) for p in image_paths],
            "source_videos": [],
            "download_errors": [],
            "review_clips": [],
            "selected_clip_ids": [],
            "selected_library_asset_ids": [],
            "clip_tags": {},
            "output_video": None,
            "batch_id": self.batch_id,
            "batch_item_index": index,
            "batch_output_name": output_name,
        }
        save_job_manifest(job_id, manifest)
        logger.info(f"[BatchPipeline] Item {index}: Job {job_id} created with {len(image_paths)} images")

        # --- Stage 3: Image Processing ---
        self._update_item_progress(index, "image_processing", 35, f"Dang xu ly {len(image_paths)} anh...")
        animation_presets = load_active_animation_presets()
        img_processor = ImageProcessor(job_id, dirs, animation_presets=animation_presets)

        def _img_progress(event: dict):
            current = int(event.get("currentImage") or 0)
            total = max(int(event.get("totalImages") or len(image_paths)), 1)
            pct = 35 + (20 * current / total)
            self._update_item_progress(index, "image_processing", pct, event.get("message", ""))

        image_clips = img_processor.process_images(image_paths, progress_callback=_img_progress)
        if not image_clips:
            raise RuntimeError(f"No valid image clips generated for '{output_name}'.")

        # Update manifest with image render plan
        manifest["image_render_plan"] = img_processor.updated_image_render_plan
        save_job_manifest(job_id, manifest)

        # --- Stage 4: Timeline ---
        self._update_item_progress(index, "timeline", 58, "Dang tao timeline render...")
        timeline_composer = TimelineComposer(job_id, dirs)
        timeline_data = timeline_composer.create_timeline([], image_clips, audio_duration)
        segments = timeline_data.get("segments", [])
        if not segments:
            raise RuntimeError(f"Timeline generated 0 segments for '{output_name}'.")
        logger.info(f"[BatchPipeline] Item {index}: Timeline has {len(segments)} segments")

        # --- Stage 5: Render ---
        self._update_item_progress(index, "render_video", 62, f"Dang render video ({len(segments)} segments)...")
        transition_presets = load_active_transition_presets()
        renderer = Renderer(job_id, dirs, transition_presets=transition_presets)
        decor_path = get_decor_video_absolute_path(decor_video_id) if decor_video_id else None

        def _render_progress(event: dict):
            stage = event.get("stage", "render_video")
            ffmpeg_pct = event.get("ffmpegPercent")
            if stage in ("render_chunks", "render_video") and isinstance(ffmpeg_pct, (int, float)):
                pct = 62 + (30 * float(ffmpeg_pct) / 100)
            elif stage == "join_chunks":
                pct = 92 + (5 * (float(ffmpeg_pct) / 100 if isinstance(ffmpeg_pct, (int, float)) else 0))
            elif stage == "finalize":
                pct = 97
            else:
                pct = 65
            self._update_item_progress(
                index,
                stage,
                pct,
                event.get("message", f"Dang render {output_name}..."),
            )

        output_path = renderer.render(
            timeline_data,
            audio_path,
            audio_duration,
            progress_callback=_render_progress,
            decor_video_path=decor_path,
        )
        if not output_path:
            raise RuntimeError(f"Render failed for '{output_name}'. Check logs/app.log.")

        # Rename output to use the user-provided outputName
        desired_output = os.path.join(Config.OUTPUT_DIR, f"{output_name}.mp4")
        if os.path.abspath(output_path) != os.path.abspath(desired_output):
            os.makedirs(os.path.dirname(desired_output), exist_ok=True)
            if os.path.isfile(desired_output):
                os.remove(desired_output)
            shutil.move(output_path, desired_output)
            output_path = desired_output

        output_relative = storage_relative_path(output_path)
        manifest["output_video"] = output_relative
        save_job_manifest(job_id, manifest)
        cleanup_job_files(job_id)

        self._update_item_progress(
            index,
            "completed",
            100,
            f"Render hoan tat: {output_name}.mp4",
            status="completed",
            output_video=output_relative,
        )
        logger.info(f"[BatchPipeline] Item {index} ({output_name}) completed → {output_relative}")

    def run_batch(self):
        """Process all items sequentially. Failed items are skipped."""
        self.progress["status"] = "running"
        self._save()
        logger.info(f"[BatchPipeline] Batch {self.batch_id} started with {len(self.items)} items")

        for index, item in enumerate(self.items):
            try:
                self._run_single_url(index, item)
            except Exception as exc:
                error_msg = str(exc)
                logger.exception(f"[BatchPipeline] Item {index} ({item['outputName']}) failed: {error_msg}")
                self._update_item_progress(
                    index,
                    "failed",
                    0,
                    f"That bai: {error_msg[:200]}",
                    status="failed",
                    error=error_msg[:500],
                )

        # Determine overall batch status
        statuses = {it["status"] for it in self.progress["items"]}
        if statuses == {"completed"}:
            self.progress["status"] = "completed"
        elif "completed" in statuses:
            self.progress["status"] = "completed"  # partial success is still "completed"
        else:
            self.progress["status"] = "failed"

        self._save()
        logger.info(
            f"[BatchPipeline] Batch {self.batch_id} finished. "
            f"Status: {self.progress['status']}, "
            f"Completed: {self.progress['completedUrls']}/{self.progress['totalUrls']}"
        )

        # Cleanup shared images to save space
        batch_images_dir = os.path.join(Config.STORAGE_DIR, "batch", self.batch_id, "shared_images")
        if os.path.exists(batch_images_dir):
            try:
                shutil.rmtree(batch_images_dir)
                logger.info(f"[BatchPipeline] Cleaned up shared images for batch {self.batch_id}")
            except Exception as e:
                logger.warning(f"[BatchPipeline] Failed to clean shared images for batch {self.batch_id}: {e}")
