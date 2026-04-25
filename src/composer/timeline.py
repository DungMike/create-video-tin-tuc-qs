import random

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger


class TimelineComposer:
    def __init__(self, job_id: str, dirs: dict):
        self.job_id = job_id
        self.temp_dir = dirs["temp"]

    def _effective_duration(self, items: list[dict]) -> float:
        overlap_count = 0
        for left, right in zip(items, items[1:]):
            if left["kind"] == "image" and right["kind"] == "image":
                overlap_count += 1
        raw = sum(float(item["duration"]) for item in items)
        return raw - (overlap_count * Config.IMAGE_TRANSITION_DURATION)

    def _timeline_segment(self, clip: dict, timeline_index: int) -> dict:
        segment = dict(clip)
        source_id = str(clip.get("id") or clip.get("kind") or "segment")
        segment["source_id"] = source_id
        segment["id"] = f"{source_id}_timeline_{timeline_index}"
        return segment

    def _timeline_payload(self, segments: list[dict], raw_duration: float, audio_duration: float, mode: str) -> dict:
        effective_duration = self._effective_duration(segments)
        logger.info(
            f"Generated {mode} timeline with {len(segments)} segments. "
            f"Raw duration: {round(raw_duration, 3)}s | effective duration: {round(effective_duration, 3)}s "
            f"(target: {round(audio_duration, 3)}s)"
        )
        return {
            "segments": segments,
            "total_duration": round(raw_duration, 3),
            "effective_duration": round(effective_duration, 3),
            "target_audio_duration": round(audio_duration, 3),
            "mode": mode,
        }

    def _create_image_only_timeline(self, image_items: list[dict], audio_duration: float) -> dict:
        segments = []
        raw_duration = 0.0
        image_index = 0

        while self._effective_duration(segments) < audio_duration:
            clip = image_items[image_index % len(image_items)]
            segment = self._timeline_segment(clip, len(segments))
            segments.append(segment)
            raw_duration += float(segment["duration"])
            image_index += 1

        if image_index > len(image_items):
            logger.info(
                f"Image-only timeline reused {len(image_items)} source image clips to cover {round(audio_duration, 3)}s audio."
            )

        return self._timeline_payload(segments, raw_duration, audio_duration, "image_audio_only")

    def create_timeline(
        self,
        vid_clips: list[str],
        img_clips: list[dict],
        audio_duration: float,
        shuffle_inputs: bool = True,
    ) -> dict:
        """Arrange timeline items alternately to match audio duration."""
        shuffled_video_paths = list(vid_clips)
        shuffled_image_items = list(img_clips)
        if shuffle_inputs:
            random.shuffle(shuffled_video_paths)
            random.shuffle(shuffled_image_items)

        video_items = [
            {
                "id": f"video_{index}",
                "kind": "video",
                "path": clip_path,
                "duration": round(FFmpegHelper.probe_duration(clip_path), 3),
            }
            for index, clip_path in enumerate(shuffled_video_paths)
        ]
        video_items = [item for item in video_items if item["duration"] > 0]

        image_items = [item for item in shuffled_image_items if item.get("duration", 0) > 0]

        if not video_items and not image_items:
            logger.error("Cannot create timeline without any valid video or image clips.")
            return self._timeline_payload([], 0.0, audio_duration, "empty")

        if not video_items and image_items:
            return self._create_image_only_timeline(image_items, audio_duration)

        segments = []
        raw_duration = 0.0
        v_idx, i_idx = 0, 0
        use_video = True
        reuse_cycles = 0

        while self._effective_duration(segments) < audio_duration:
            clip = None

            if use_video and v_idx < len(video_items):
                clip = dict(video_items[v_idx])
                v_idx += 1
            elif not use_video and i_idx < len(image_items):
                clip = dict(image_items[i_idx])
                i_idx += 1
            else:
                if v_idx < len(video_items):
                    clip = dict(video_items[v_idx])
                    v_idx += 1
                elif i_idx < len(image_items):
                    clip = dict(image_items[i_idx])
                    i_idx += 1
                else:
                    reuse_cycles += 1
                    logger.info(f"Reusing source clips to fill timeline, cycle {reuse_cycles}.")
                    v_idx, i_idx = 0, 0
                    continue

            if clip and clip["duration"] > 0:
                segment = self._timeline_segment(clip, len(segments))
                segments.append(segment)
                raw_duration += float(segment["duration"])

            use_video = not use_video

        return self._timeline_payload(segments, raw_duration, audio_duration, "mixed_media")
