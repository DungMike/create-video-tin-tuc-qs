import os
import random
import subprocess
from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import storage_relative_path
from src.utils.logger import logger


class VideoProcessor:
    def __init__(self, job_id: str, dirs: dict):
        self.job_id = job_id
        self.output_dir = dirs["vid_clips"]

    def _get_duration(self, video_path: str) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=True)
            return float(res.stdout.strip())
        except Exception:
            return 0.0

    def _build_filter(self) -> str:
        width, height = int(Config.TARGET_RESOLUTION.split("x")[0]), int(Config.TARGET_RESOLUTION.split("x")[1])
        zoom = max(1.0, Config.VID_CLIP_ZOOM_FACTOR)
        if zoom > 1.0:
            # Scale to (target * zoom) then crop back to target → zoom-in effect
            scaled_w = int(width * zoom)
            scaled_h = int(height * zoom)
            # Ensure even dimensions for H.264 compatibility
            scaled_w += scaled_w % 2
            scaled_h += scaled_h % 2
            return (
                f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={Config.TARGET_FPS},setpts=PTS-STARTPTS,format=yuv420p"
            )
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={Config.TARGET_FPS},setpts=PTS-STARTPTS,format=yuv420p"
        )

    def _clip_duration(
        self,
        remaining: float,
        *,
        min_duration: float | None = None,
        max_duration: float | None = None,
        rng: random.Random | None = None,
    ) -> float:
        minimum = float(min_duration if min_duration is not None else Config.VID_CLIP_MIN_DURATION)
        maximum = float(max_duration if max_duration is not None else Config.VID_CLIP_MAX_DURATION)
        if maximum < minimum:
            maximum = minimum
        # Fixed duration mode: min == max → no randomization needed
        if minimum == maximum:
            target = minimum
        elif rng:
            target = rng.uniform(minimum, maximum)
        else:
            target = float(Config.REVIEW_CLIP_DURATION)
        target = max(minimum, min(target, maximum))
        if remaining <= maximum:
            return remaining
        if remaining - target < minimum:
            return min(maximum, remaining)
        return float(target)

    def create_review_clips(
        self,
        video_paths: list[str],
        *,
        min_duration: float | None = None,
        max_duration: float | None = None,
        randomize_duration: bool = False,
        seed: int | str | None = None,
    ) -> list[dict]:
        clip_metadata = []
        clip_count = 0
        filter_str = self._build_filter()
        rng = random.Random(seed) if randomize_duration else None
        effective_min_duration = float(
            min_duration if min_duration is not None else Config.VID_CLIP_MIN_DURATION
        )

        for vid_path in video_paths:
            duration = self._get_duration(vid_path)
            if duration < effective_min_duration:
                logger.warning(f"Video too short, skipping: {vid_path}")
                continue

            logger.info(f"Creating review clips from video: {vid_path} ({duration}s)")
            source_name = os.path.basename(vid_path)
            start_time = 0.0

            while start_time < duration:
                remaining = duration - start_time
                if remaining < effective_min_duration:
                    break

                segment_duration = self._clip_duration(
                    remaining,
                    min_duration=min_duration,
                    max_duration=max_duration,
                    rng=rng,
                )
                clip_path = os.path.join(self.output_dir, f"vid_clip_{clip_count}.mp4")
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(round(start_time, 3)),
                    "-i",
                    vid_path,
                    "-t",
                    str(round(segment_duration, 3)),
                    "-vf",
                    filter_str,
                    "-an",
                ]
                cmd.extend(FFmpegHelper.get_nvenc_flags())
                cmd.extend(["-pix_fmt", "yuv420p", clip_path])

                if FFmpegHelper.run_command(cmd):
                    clip_id = f"clip_{clip_count}"
                    clip_metadata.append(
                        {
                            "id": clip_id,
                            "path": clip_path,
                            "relative_path": storage_relative_path(clip_path),
                            "source_name": source_name,
                            "start": round(start_time, 3),
                            "end": round(start_time + segment_duration, 3),
                            "duration": round(segment_duration, 3),
                        }
                    )
                    clip_count += 1
                else:
                    logger.error(f"Failed to cut review clip from {vid_path} at {start_time}s")

                start_time += segment_duration

        return clip_metadata

    def process_videos(self, video_paths: list[str]) -> list[str]:
        """Backward-compatible wrapper used by the CLI pipeline."""
        return [clip["path"] for clip in self.create_review_clips(video_paths)]
