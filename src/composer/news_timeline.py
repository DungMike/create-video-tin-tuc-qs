"""News Timeline Composer — Builds timelines for news bulletin videos.

Responsibilities:
  - Build resume segment: match 1-2 clips from news resource pool to resume audio duration
  - Build detail segment: allocate N clips to fill detail audio duration
  - Build full bulletin timeline: intro + shuffled resumes + transition + shuffled details + outro
  - Seed-based randomization for reproducible cross-channel variation
"""

import hashlib
import os
import random
from typing import Any

from src.config import Config
from src.utils.logger import logger


def _stable_seed(*parts) -> int:
    payload = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16], 16)


class NewsTimelineComposer:
    """Builds a complete bulletin timeline from parsed script + audio + resources."""

    def __init__(
        self,
        bulletin_id: str,
        channel_id: str,
        vid_clip_duration: float = 5.0,
        img_clip_duration: float = 6.0,
    ):
        self.bulletin_id = bulletin_id
        self.channel_id = channel_id
        self.vid_clip_duration = vid_clip_duration
        self.img_clip_duration = img_clip_duration
        self._seed = _stable_seed(bulletin_id, channel_id)
        self._rng = random.Random(self._seed)

    def shuffle_news_order(self, news_ids: list[int]) -> list[int]:
        """Shuffle news item IDs for this channel (deterministic)."""
        order = list(news_ids)
        self._rng.shuffle(order)
        return order

    def build_resume_segment(
        self,
        resume_audio_duration: float,
        vid_clips: list[dict],
        img_clips: list[dict],
    ) -> list[dict]:
        """Pick 1-2 clips from resource pool to match resume audio duration.

        Rules:
        - duration <= 5s → 1 video clip (trimmed to match)
        - duration > 5s → 1 video + N images to fill
        """
        timeline_items = []
        remaining = resume_audio_duration

        if remaining <= 0:
            return timeline_items

        # Try to pick 1 video clip first
        if vid_clips:
            clip = self._rng.choice(vid_clips)
            clip_dur = min(self.vid_clip_duration, remaining)
            timeline_items.append({
                "type": "video",
                "path": clip.get("path") or clip.get("relative_path", ""),
                "duration": clip_dur,
                "source": "resume",
            })
            remaining -= clip_dur

        # Fill remaining with image clips
        while remaining > 0.5 and img_clips:
            clip = self._rng.choice(img_clips)
            clip_dur = min(self.img_clip_duration, remaining)
            timeline_items.append({
                "type": "image",
                "path": clip.get("path") or clip.get("relative_path", ""),
                "duration": clip_dur,
                "source": "resume",
            })
            remaining -= clip_dur

        return timeline_items

    def build_detail_segment(
        self,
        detail_audio_duration: float,
        vid_clips: list[dict],
        img_clips: list[dict],
    ) -> list[dict]:
        """Allocate clips to fill the detail audio duration.

        Strategy: prefer video clips, fill gaps with image clips.
        Random pick from pool without depleting (clips can repeat across channels).
        """
        timeline_items = []
        remaining = detail_audio_duration

        if remaining <= 0:
            return timeline_items

        # Build a combined shuffled pool
        pool: list[dict] = []
        available_vids = list(vid_clips)
        available_imgs = list(img_clips)
        self._rng.shuffle(available_vids)
        self._rng.shuffle(available_imgs)

        # Interleave: prefer video, then image
        vid_idx = 0
        img_idx = 0
        while remaining > 0.5:
            if vid_idx < len(available_vids):
                clip = available_vids[vid_idx]
                vid_idx += 1
                clip_dur = min(self.vid_clip_duration, remaining)
                timeline_items.append({
                    "type": "video",
                    "path": clip.get("path") or clip.get("relative_path", ""),
                    "duration": clip_dur,
                    "source": "detail",
                })
                remaining -= clip_dur
            elif img_idx < len(available_imgs):
                clip = available_imgs[img_idx]
                img_idx += 1
                clip_dur = min(self.img_clip_duration, remaining)
                timeline_items.append({
                    "type": "image",
                    "path": clip.get("path") or clip.get("relative_path", ""),
                    "duration": clip_dur,
                    "source": "detail",
                })
                remaining -= clip_dur
            else:
                # Re-shuffle and loop back (repeat pool)
                self._rng.shuffle(available_vids)
                self._rng.shuffle(available_imgs)
                vid_idx = 0
                img_idx = 0
                if not available_vids and not available_imgs:
                    logger.warning(
                        f"[NewsTimeline] No clips available for detail segment, "
                        f"{remaining:.1f}s unfilled"
                    )
                    break

        return timeline_items

    def build_full_timeline(
        self,
        parsed_script: dict,
        audio_durations: dict[str, float],
        resource_pools: dict[int, dict],
        transition_clip_path: str | None = None,
        transition_duration: float = 0.5,
    ) -> dict:
        """Build the complete bulletin timeline for this channel.

        Args:
            parsed_script: output from news_script_parser.parse_news_script()
            audio_durations: {segment_key: duration_seconds} e.g. {"intro": 5.0, "resume_1": 3.0, ...}
            resource_pools: {news_id: {"vid_clips": [...], "img_clips": [...]}}
            transition_clip_path: path to channel's transition video
            transition_duration: duration of transition clip

        Returns:
            {"segments": [...], "totalDuration": float, "newsOrder": [int, ...]}
        """
        news_items = parsed_script.get("newsItems", [])
        news_ids = [item["id"] for item in news_items]
        shuffled_order = self.shuffle_news_order(news_ids)

        segments = []
        total_duration = 0.0

        # 1. Intro segment
        intro_dur = audio_durations.get("intro", 0.0)
        if intro_dur > 0:
            segments.append({
                "segmentType": "intro",
                "segmentKey": "intro",
                "audioDuration": intro_dur,
                "clips": [],  # Intro may have no visual clips or use a static bg
            })
            total_duration += intro_dur

        # 2. Resume segments (in shuffled order)
        for news_id in shuffled_order:
            seg_key = f"resume_{news_id}"
            dur = audio_durations.get(seg_key, 0.0)
            if dur <= 0:
                continue

            pool = resource_pools.get(news_id, {})
            clips = self.build_resume_segment(
                dur,
                pool.get("vid_clips", []),
                pool.get("img_clips", []),
            )
            segments.append({
                "segmentType": "resume",
                "segmentKey": seg_key,
                "newsId": news_id,
                "audioDuration": dur,
                "clips": clips,
            })
            total_duration += dur

        # 3. Transition before details
        if transition_clip_path and os.path.isfile(transition_clip_path):
            segments.append({
                "segmentType": "transition",
                "segmentKey": "transition_pre_detail",
                "clipPath": transition_clip_path,
                "duration": transition_duration,
            })
            total_duration += transition_duration

        # 4. Detail segments (in shuffled order, with transitions between)
        for idx, news_id in enumerate(shuffled_order):
            seg_key = f"detail_{news_id}"
            dur = audio_durations.get(seg_key, 0.0)
            if dur <= 0:
                continue

            pool = resource_pools.get(news_id, {})
            clips = self.build_detail_segment(
                dur,
                pool.get("vid_clips", []),
                pool.get("img_clips", []),
            )
            segments.append({
                "segmentType": "detail",
                "segmentKey": seg_key,
                "newsId": news_id,
                "audioDuration": dur,
                "clips": clips,
            })
            total_duration += dur

            # Insert transition between details (not after last one)
            if idx < len(shuffled_order) - 1 and transition_clip_path and os.path.isfile(transition_clip_path):
                segments.append({
                    "segmentType": "transition",
                    "segmentKey": f"transition_detail_{news_id}",
                    "clipPath": transition_clip_path,
                    "duration": transition_duration,
                })
                total_duration += transition_duration

        # 5. Transition before outro
        if transition_clip_path and os.path.isfile(transition_clip_path):
            segments.append({
                "segmentType": "transition",
                "segmentKey": "transition_pre_outro",
                "clipPath": transition_clip_path,
                "duration": transition_duration,
            })
            total_duration += transition_duration

        # 6. Outro segment
        outro_dur = audio_durations.get("outro", 0.0)
        if outro_dur > 0:
            segments.append({
                "segmentType": "outro",
                "segmentKey": "outro",
                "audioDuration": outro_dur,
                "clips": [],
            })
            total_duration += outro_dur

        return {
            "bulletinId": self.bulletin_id,
            "channelId": self.channel_id,
            "segments": segments,
            "totalDuration": round(total_duration, 2),
            "newsOrder": shuffled_order,
        }
