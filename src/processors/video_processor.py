import os
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
        width, height = Config.TARGET_RESOLUTION.split("x")
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={Config.TARGET_FPS},setpts=PTS-STARTPTS"
        )

    def _clip_duration(self, remaining: float) -> float:
        target = max(Config.VID_CLIP_MIN_DURATION, min(Config.REVIEW_CLIP_DURATION, Config.VID_CLIP_MAX_DURATION))
        if remaining <= Config.VID_CLIP_MAX_DURATION:
            return remaining
        if remaining - target < Config.VID_CLIP_MIN_DURATION:
            return remaining
        return float(target)

    def create_review_clips(self, video_paths: list[str]) -> list[dict]:
        clip_metadata = []
        clip_count = 0
        filter_str = self._build_filter()

        for vid_path in video_paths:
            duration = self._get_duration(vid_path)
            if duration < Config.VID_CLIP_MIN_DURATION:
                logger.warning(f"Video too short, skipping: {vid_path}")
                continue

            logger.info(f"Creating review clips from video: {vid_path} ({duration}s)")
            source_name = os.path.basename(vid_path)
            start_time = 0.0

            while start_time < duration:
                remaining = duration - start_time
                if remaining < Config.VID_CLIP_MIN_DURATION:
                    break

                segment_duration = self._clip_duration(remaining)
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
