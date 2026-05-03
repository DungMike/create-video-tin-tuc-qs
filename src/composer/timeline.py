import random
import time
from collections import deque

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

    def _video_items_from_paths(self, video_paths: list[str], prefix: str = "video") -> list[dict]:
        t0 = time.monotonic()
        video_items = [
            {
                "id": f"{prefix}_{index}",
                "kind": "video",
                "path": clip_path,
                "duration": round(FFmpegHelper.probe_duration(clip_path), 3),
            }
            for index, clip_path in enumerate(video_paths)
        ]
        valid_items = [item for item in video_items if item["duration"] > 0]
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            f"[Timeline] probe_duration for {len(video_paths)} video clips: "
            f"{len(valid_items)} valid, elapsed={elapsed_ms:.0f}ms"
        )
        return valid_items

    def create_batch_mixed_timeline(
        self,
        vid_clips: list[str],
        img_clips: list[dict],
        audio_duration: float,
        *,
        seed: int | str,
        config: dict | None = None,
    ) -> dict:
        """Create batch timeline: first 5 minutes 3 video/1 image, then sparse random images."""
        timeline_config = config if isinstance(config, dict) else {}
        first_phase_seconds = max(0, int(timeline_config.get("firstPhaseSeconds") or 300))
        first_phase_video_count = max(1, int(timeline_config.get("firstPhaseVideoCount") or 3))
        first_phase_image_count = max(0, int(timeline_config.get("firstPhaseImageCount") if timeline_config.get("firstPhaseImageCount") is not None else 1))
        after_min = max(1, int(timeline_config.get("afterPhaseImageEveryMin") or 2))
        after_max = max(after_min, int(timeline_config.get("afterPhaseImageEveryMax") or 5))

        rng = random.Random(seed)
        video_items = self._video_items_from_paths(list(vid_clips), "batch_video")
        image_items = [item for item in img_clips if item.get("duration", 0) > 0]
        rng.shuffle(video_items)
        rng.shuffle(image_items)

        if not video_items and not image_items:
            logger.error("Cannot create batch mixed timeline without any valid video or image clips.")
            return self._timeline_payload([], 0.0, audio_duration, "empty")
        if not video_items:
            return self._create_image_only_timeline(image_items, audio_duration)

        segments = []
        raw_duration = 0.0
        image_index = 0
        after_video_remaining = rng.randint(after_min, after_max)

        # --- Anti-repeat: cooldown window for video clips ---
        # Clip sẽ không được dùng lại cho đến khi đủ cooldown_len clips khác đã dùng
        _cooldown_len = max(1, len(video_items) // 2)
        _video_pool = list(video_items)  # pool hiện tại (có thể re-shuffle)
        _video_round = 0  # số vòng đã qua
        _recently_used: deque = deque(maxlen=_cooldown_len)
        _pool_pos = 0  # vị trí hiện tại trong pool

        def append_item(item: dict):
            nonlocal raw_duration
            segment = self._timeline_segment(item, len(segments))
            segments.append(segment)
            raw_duration += float(segment["duration"])

        def next_video() -> dict:
            nonlocal _pool_pos, _video_round, _video_pool
            pool_size = len(_video_pool)
            # Thử tìm clip chưa dùng gần đây trong pool hiện tại
            for attempt in range(pool_size):
                candidate = _video_pool[_pool_pos % pool_size]
                _pool_pos += 1
                if candidate["id"] not in _recently_used:
                    _recently_used.append(candidate["id"])
                    return dict(candidate)
            # Tất cả đều trong cooldown -> hết vòng, re-shuffle để thay đổi thứ tự
            _video_round += 1
            rng.shuffle(_video_pool)
            _pool_pos = 0
            logger.info(
                f"[Timeline] Video pool exhausted cooldown (round {_video_round}), "
                f"re-shuffling {len(_video_pool)} clips to avoid repeat."
            )
            candidate = _video_pool[0]
            _pool_pos = 1
            _recently_used.append(candidate["id"])
            return dict(candidate)

        def next_image() -> dict | None:
            nonlocal image_index
            if not image_items:
                return None
            # Image cũng re-shuffle sau mỗi vòng
            if image_index > 0 and image_index % len(image_items) == 0:
                rng.shuffle(image_items)
                logger.info(f"[Timeline] Image pool re-shuffled after full cycle (index={image_index}).")
            item = dict(image_items[image_index % len(image_items)])
            image_index += 1
            return item

        first_phase_target = min(float(audio_duration), float(first_phase_seconds))
        first_phase_pattern = (["video"] * first_phase_video_count) + (["image"] * first_phase_image_count)
        if not first_phase_pattern:
            first_phase_pattern = ["video"]
        pattern_index = 0

        logger.info(
            f"[Timeline] Building batch_mixed_timeline: audio={audio_duration:.1f}s, "
            f"first_phase={first_phase_target:.1f}s, "
            f"video_pool={len(_video_pool)}, image_pool={len(image_items)}, "
            f"cooldown_len={_cooldown_len}, pattern={first_phase_pattern}"
        )
        t_build = time.monotonic()

        while self._effective_duration(segments) < first_phase_target:
            slot = first_phase_pattern[pattern_index % len(first_phase_pattern)]
            pattern_index += 1
            if slot == "image":
                image_item = next_image()
                append_item(image_item if image_item else next_video())
            else:
                append_item(next_video())

        second_phase_pattern = (["image"] * 3) + ["video"]
        if not image_items:
            second_phase_pattern = ["video"]
        second_pattern_index = 0

        while self._effective_duration(segments) < audio_duration:
            slot = second_phase_pattern[second_pattern_index % len(second_phase_pattern)]
            second_pattern_index += 1
            if slot == "image":
                image_item = next_image()
                append_item(image_item if image_item else next_video())
            else:
                append_item(next_video())

        logger.info(
            f"[Timeline] batch_mixed_timeline built: {len(segments)} segments in "
            f"{(time.monotonic()-t_build)*1000:.0f}ms, video_rounds={_video_round}"
        )
        return self._timeline_payload(segments, raw_duration, audio_duration, "batch_mixed_media")

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

        video_items = self._video_items_from_paths(shuffled_video_paths)

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
