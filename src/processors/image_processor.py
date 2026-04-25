import hashlib
import os
import random
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image, ImageOps

from src.config import Config
from src.utils.effects_library import (
    DEFAULT_ANIMATION_FALLBACK,
    create_image_motion_clip,
    load_active_animation_presets,
)
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import remove_file_with_retries, storage_relative_path
from src.utils.logger import logger


class ImageProcessor:
    def __init__(
        self,
        job_id: str,
        dirs: dict,
        animation_presets: list[dict] | None = None,
        image_render_plan: dict | None = None,
    ):
        self.job_id = job_id
        self.output_dir = dirs["img_clips"]
        self.normalized_dir = os.path.join(dirs["temp"], "normalized_images")
        self.cache_dir = os.path.join(Config.STORAGE_DIR, "cache", "image_motion")
        self.animation_presets = list(animation_presets or load_active_animation_presets())
        if not self.animation_presets:
            self.animation_presets = [dict(DEFAULT_ANIMATION_FALLBACK, active=True)]
        self.image_render_plan = image_render_plan if isinstance(image_render_plan, dict) else {}
        self.updated_image_render_plan: dict = {"version": 1, "items": []}

    def _prepare_image_for_ffmpeg(self, image_path: str, idx: int) -> str:
        """Create a clean RGB image so FFmpeg does not have to decode edge-case source files."""
        os.makedirs(self.normalized_dir, exist_ok=True)
        normalized_path = os.path.join(self.normalized_dir, f"normalized_{idx}.jpg")

        try:
            with Image.open(image_path) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                    rgba_image = image.convert("RGBA")
                    background = Image.new("RGB", rgba_image.size, (255, 255, 255))
                    background.paste(rgba_image, mask=rgba_image.getchannel("A"))
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(normalized_path, "JPEG", quality=95, optimize=True)
            return normalized_path
        except Exception as exc:
            logger.warning(f"Could not normalize image for FFmpeg, using original: {image_path} | {exc}")
            return image_path

    def _is_valid_clip(self, clip_path: str, expected_duration: float, remove_invalid: bool = True) -> tuple[bool, float]:
        if not os.path.isfile(clip_path):
            return False, 0.0

        duration = FFmpegHelper.probe_duration(clip_path)
        min_duration = max(0.1, expected_duration * 0.8)
        if duration < min_duration:
            logger.error(
                f"Generated image clip is invalid or too short: {clip_path} "
                f"| duration={duration:.3f}s expected={expected_duration:.3f}s"
            )
            if remove_invalid:
                remove_file_with_retries(clip_path)
            return False, duration

        return True, duration

    def _source_hash(self, image_path: str) -> str:
        digest = hashlib.sha256()
        with open(image_path, "rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _cache_key(self, image_path: str, preset: dict, duration_seconds: float) -> str:
        fade_dur = Config.IMAGE_CLIP_FADE_DURATION if Config.IMAGE_ONLY_SKIP_XFADE else 0.0
        payload = "|".join(
            [
                self._source_hash(image_path),
                str(preset.get("id", "")),
                str(preset.get("ffmpeg_filter", "")),
                str(duration_seconds),
                Config.TARGET_RESOLUTION,
                str(Config.TARGET_FPS),
                str(Config.USE_GPU_NVENC),
                Config.FFMPEG_PRESET,
                Config.VIDEO_BITRATE,
                str(fade_dur),
                "v2_jitter_fix", # Invalidate old shaky cache
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, cache_key: str) -> str:
        return os.path.join(self.cache_dir, f"{cache_key}.mp4")

    def _preset_for_image(self, idx: int, image_path: str) -> dict:
        preset_lookup = {preset.get("id"): preset for preset in self.animation_presets}
        plan_items = self.image_render_plan.get("items", [])
        source_relative_path = storage_relative_path(image_path)

        if isinstance(plan_items, list) and idx < len(plan_items):
            item = plan_items[idx]
            if isinstance(item, dict) and item.get("source_image_relative_path") == source_relative_path:
                preset = preset_lookup.get(item.get("animation_preset_id"))
                if preset:
                    return preset

        if isinstance(plan_items, list):
            for item in plan_items:
                if not isinstance(item, dict):
                    continue
                if item.get("source_image_relative_path") == source_relative_path:
                    preset = preset_lookup.get(item.get("animation_preset_id"))
                    if preset:
                        return preset

        return random.choice(self.animation_presets)

    def _copy_clip(self, source_path: str, target_path: str):
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if os.path.isfile(target_path):
            remove_file_with_retries(target_path)
        shutil.copy2(source_path, target_path)

    def _write_cache_clip(self, clip_path: str, cache_path: str):
        if not Config.IMAGE_MOTION_CACHE_ENABLED:
            return
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp_path = f"{cache_path}.{os.getpid()}.tmp"
            shutil.copy2(clip_path, tmp_path)
            os.replace(tmp_path, cache_path)
        except Exception as exc:
            logger.warning(f"Could not write image motion cache: {cache_path} | {exc}")

    def _process_one_image(self, task: dict) -> dict:
        idx = task["idx"]
        img_path = task["image_path"]
        preset = task["preset"]
        clip_path = task["clip_path"]
        duration_seconds = float(Config.IMG_CLIP_DURATION)

        logger.info(f"Processing image: {img_path}")
        cache_key = self._cache_key(img_path, preset, duration_seconds)
        cache_path = self._cache_path(cache_key)

        if Config.IMAGE_MOTION_CACHE_ENABLED and os.path.isfile(cache_path):
            is_cache_valid, cache_duration = self._is_valid_clip(cache_path, duration_seconds, remove_invalid=False)
            if is_cache_valid:
                self._copy_clip(cache_path, clip_path)
                return self._clip_result(idx, img_path, clip_path, preset, cache_duration, cache_key, True)
            logger.warning(f"Ignoring invalid image motion cache: {cache_path}")

        prepared_img_path = self._prepare_image_for_ffmpeg(img_path, idx)
        fade_dur = Config.IMAGE_CLIP_FADE_DURATION if Config.IMAGE_ONLY_SKIP_XFADE else 0.0
        if not create_image_motion_clip(prepared_img_path, clip_path, preset, duration_seconds, fade_dur):
            remove_file_with_retries(clip_path)
            raise RuntimeError(f"Failed to process image: {img_path}")

        is_valid, duration = self._is_valid_clip(clip_path, duration_seconds)
        if not is_valid:
            raise RuntimeError(f"Generated clip is invalid for image: {img_path}")

        self._write_cache_clip(clip_path, cache_path)
        return self._clip_result(idx, img_path, clip_path, preset, duration, cache_key, False)

    def _clip_result(
        self,
        idx: int,
        img_path: str,
        clip_path: str,
        preset: dict,
        duration: float,
        cache_key: str,
        cache_hit: bool,
    ) -> dict:
        return {
            "idx": idx,
            "cache_hit": cache_hit,
            "plan_item": {
                "clip_index": idx,
                "source_image_relative_path": storage_relative_path(img_path),
                "animation_preset_id": preset["id"],
                "cache_key": cache_key,
            },
            "clip": {
                "id": f"img_clip_{idx}",
                "kind": "image",
                "path": clip_path,
                "relative_path": storage_relative_path(clip_path),
                "source_image_relative_path": storage_relative_path(img_path),
                "duration": round(duration, 3),
                "animation_preset_id": preset["id"],
                "cache_key": cache_key,
                "cache_hit": cache_hit,
            },
        }

    def process_images(self, image_paths: list[str], progress_callback=None) -> list[dict]:
        clip_results: dict[int, dict] = {}
        plan_items: dict[int, dict] = {}
        total_images = len(image_paths)
        cache_hits = 0
        cache_misses = 0

        if not image_paths:
            self.updated_image_render_plan = {"version": 1, "items": []}
            return []

        worker_count = max(1, min(Config.IMAGE_MOTION_WORKERS, total_images))
        if progress_callback:
            progress_callback(
                {
                    "stage": "image_processing",
                    "currentImage": 0,
                    "totalImages": total_images,
                    "cacheHits": 0,
                    "cacheMisses": 0,
                    "message": f"Bat dau xu ly {total_images} anh voi {worker_count} worker.",
                }
            )

        tasks = []
        for idx, img_path in enumerate(image_paths):
            tasks.append(
                {
                    "idx": idx,
                    "image_path": img_path,
                    "clip_path": os.path.join(self.output_dir, f"img_clip_{idx}.mp4"),
                    "preset": self._preset_for_image(idx, img_path),
                }
            )

        completed = 0
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_lookup = {executor.submit(self._process_one_image, task): task for task in tasks}
            for future in as_completed(future_lookup):
                task = future_lookup[future]
                idx = task["idx"]
                completed += 1
                try:
                    result = future.result()
                    clip_results[idx] = result["clip"]
                    plan_items[idx] = result["plan_item"]
                    if result["cache_hit"]:
                        cache_hits += 1
                        message = f"Dung cache motion clip {idx + 1}/{total_images}."
                    else:
                        cache_misses += 1
                        message = f"Da tao motion clip {idx + 1}/{total_images}."
                    logger.info(message)
                    level = "info"
                except Exception as exc:
                    remove_file_with_retries(task["clip_path"])
                    logger.error(f"Failed to process image: {task['image_path']} | {exc}")
                    message = f"Xu ly anh {idx + 1}/{total_images} that bai."
                    level = "error"

                if progress_callback:
                    progress_callback(
                        {
                            "stage": "image_processing",
                            "currentImage": completed,
                            "totalImages": total_images,
                            "cacheHits": cache_hits,
                            "cacheMisses": cache_misses,
                            "message": f"{message} Cache hit {cache_hits}, miss {cache_misses}.",
                            "level": level,
                        }
                    )

        ordered_plan_items = [plan_items[idx] for idx in sorted(plan_items)]
        self.updated_image_render_plan = {
            "version": 1,
            "target_resolution": Config.TARGET_RESOLUTION,
            "target_fps": Config.TARGET_FPS,
            "duration_seconds": Config.IMG_CLIP_DURATION,
            "cache_enabled": Config.IMAGE_MOTION_CACHE_ENABLED,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "items": ordered_plan_items,
        }
        return [clip_results[idx] for idx in sorted(clip_results)]
