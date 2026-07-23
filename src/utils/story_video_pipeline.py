"""Single Story Video pipeline runner.

Processes one story video: audio -> random 5-second clip sequence -> audio mux -> finalize.
"""

import json
import os
import random
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from src.config import Config
from src.processors.audio_utils import get_audio_duration, validate_audio
from src.utils.clip_spec_validation import filter_valid_clips
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import storage_absolute_path, storage_relative_path
from src.utils.logger import logger
from src.utils.story_clip_bag import SharedClipBag
from src.utils.story_library import (
    load_story_library_index,
    resolve_library_id,
    story_library_root,
)
from src.utils.tts_audio import (
    TTSAudioError,
    create_audio_from_google_doc,
)


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _save_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _story_dir(story_id: str) -> str:
    path = os.path.join(Config.STORY_VIDEO_DIR, story_id)
    os.makedirs(path, exist_ok=True)
    return path


def _progress_path(story_id: str) -> str:
    return os.path.join(_story_dir(story_id), "progress.json")


def _cancel_path(story_id: str) -> str:
    return os.path.join(_story_dir(story_id), "cancel.requested")


def request_story_cancel(story_id: str):
    """Persist a cancellation request so active and queued runners can observe it."""
    cancel_path = _cancel_path(story_id)
    with open(cancel_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(_utc_now())


def is_story_cancel_requested(story_id: str) -> bool:
    return os.path.isfile(_cancel_path(story_id))


def _temp_dir(story_id: str) -> str:
    path = os.path.join(_story_dir(story_id), "temp")
    os.makedirs(path, exist_ok=True)
    return path


def _output_dir() -> str:
    path = os.path.join(Config.OUTPUT_DIR, "story-video")
    os.makedirs(path, exist_ok=True)
    return path


def _overlay_max_concurrent() -> int:
    """Max overlay-pass ffmpeg processes allowed to run at once, app-wide.

    Defaults to (physical cores - 1) so at least one core stays free for the OS and
    the per-frame GPU<->CPU handoff. Overriding via OVERLAY_MAX_CONCURRENT wins."""
    configured = int(getattr(Config, "OVERLAY_MAX_CONCURRENT", 0) or 0)
    if configured > 0:
        return configured
    return max(1, (os.cpu_count() or 2) - 1)


# One shared budget of "overlay slots" across the whole process. Every heavy overlay
# ffmpeg (single-pass or a parallel segment) acquires a slot before running, so batch
# workers x segments can never oversubscribe the CPU — extras queue instead of thrash.
_OVERLAY_SLOTS = threading.BoundedSemaphore(_overlay_max_concurrent())


def _run_overlay_ffmpeg(cmd: list, **kwargs) -> bool:
    """Run an overlay-pass ffmpeg while holding one global overlay slot."""
    with _OVERLAY_SLOTS:
        return FFmpegHelper.run_command(cmd, **kwargs)


_ASS_TS_RE = re.compile(r"^\s*(\d+):(\d\d):(\d\d)\.(\d\d)\s*$")


def _parse_ass_ts(value: str) -> float:
    match = _ASS_TS_RE.match(value)
    if not match:
        return 0.0
    h, m, s, cs = (int(g) for g in match.groups())
    return h * 3600 + m * 60 + s + cs / 100.0


def _fmt_ass_ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _rebase_ass_file(src_ass: str, start: float, dur: float, out_ass: str) -> None:
    """Write a copy of `src_ass` whose Dialogue events are shifted to a segment.

    Events are moved by -start, clipped to [0, dur], and dropped when they fall
    entirely outside the window. Header/style lines are copied verbatim so the
    burned-in subtitle looks identical to the single-pass render."""
    with open(src_ass, "r", encoding="utf-8-sig") as handle:
        lines = handle.readlines()

    out_lines: list[str] = []
    for line in lines:
        if not line.startswith("Dialogue:"):
            out_lines.append(line)
            continue
        # Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        body = line[len("Dialogue:"):]
        fields = body.split(",", 9)
        if len(fields) < 10:
            out_lines.append(line)
            continue
        new_start = _parse_ass_ts(fields[1]) - start
        new_end = _parse_ass_ts(fields[2]) - start
        if new_end <= 0 or new_start >= dur:
            continue
        fields[1] = _fmt_ass_ts(max(0.0, new_start))
        fields[2] = _fmt_ass_ts(min(dur, new_end))
        out_lines.append("Dialogue:" + ",".join(fields))

    with open(out_ass, "w", encoding="utf-8") as handle:
        handle.writelines(out_lines)


def load_story_progress(story_id: str) -> dict | None:
    return _load_json(_progress_path(story_id))


def _load_story_library_index(library_id=None) -> dict:
    return load_story_library_index(library_id)


class StoryVideoPipelineRunner:
    """Runs the simple story video pipeline for a single story."""

    def __init__(self, story_id: str, config_dict: dict, clip_bag: SharedClipBag | None = None):
        self.story_id = story_id
        # Deck shared by every video of a batch; None for standalone renders.
        self.clip_bag = clip_bag
        self.input_type = config_dict.get("input_type", "script_url")
        self.input_value = config_dict.get("input_value", "")
        self.output_name = config_dict.get("output_name", "")
        self.clip_tags = config_dict.get("clip_tags", [])
        self.library_id = str(config_dict.get("library_id", "") or "").strip()
        self.voice_id = config_dict.get("voice_id", "")
        self.waveform_overlay_id = str(config_dict.get("waveform_overlay_id", "") or "").strip()
        self.tv_effect_style_id = str(config_dict.get("tv_effect_style_id", "") or "").strip()
        self.subtitle_path = str(config_dict.get("subtitle_path", "") or "").strip()
        self.subtitle_font = str(config_dict.get("subtitle_font", "") or "").strip()
        self.subtitle_preset = str(config_dict.get("subtitle_preset", "") or "").strip() or "clean"
        self.subtitle_max_chars_per_line = self._coerce_positive_int(
            config_dict.get("subtitle_max_chars_per_line"),
            Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE,
        )
        self.subtitle_max_lines = self._coerce_positive_int(
            config_dict.get("subtitle_max_lines"),
            Config.STORY_SUBTITLE_MAX_LINES,
        )
        raw_style_overrides = config_dict.get("subtitle_style_overrides")
        self.subtitle_style_overrides = (
            dict(raw_style_overrides) if isinstance(raw_style_overrides, dict) else {}
        )
        self._subtitle_ass_path = ""
        self._seg_dur_cache: int | None = None

        self._lock = threading.Lock()
        self.progress = {
            "storyId": story_id,
            "status": "pending",
            "stage": "pending",
            "percent": 0,
            "message": "Cho xu ly...",
            "outputName": self.output_name,
            "result": {"videoPath": None},
            "error": None,
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
        }
        self._save_progress()

    def _segment_duration(self) -> int:
        """Per-clip/unit duration for this library (pre-baked 'full' libraries use 10s units)."""
        if self._seg_dur_cache is None:
            from src.utils.story_library import library_clip_duration

            default = max(1, int(Config.STORY_CLIP_DURATION))
            self._seg_dur_cache = max(1, library_clip_duration(self.library_id, default))
        return self._seg_dur_cache

    @staticmethod
    def _coerce_positive_int(value, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def _raise_if_cancel_requested(self):
        if is_story_cancel_requested(self.story_id):
            raise StoryVideoCancelled()

    def _save_progress(self):
        with self._lock:
            self.progress["updatedAt"] = _utc_now()
            _save_json(_progress_path(self.story_id), self.progress)

    def _update_progress(
        self,
        stage: str,
        percent: float,
        message: str,
        *,
        status: str = "running",
        error: str | None = None,
        video_path: str | None = None,
    ):
        self.progress["status"] = status
        self.progress["stage"] = stage
        self.progress["percent"] = round(min(100, max(0, percent)), 1)
        self.progress["message"] = message
        if error is not None:
            self.progress["error"] = error
        if video_path is not None:
            self.progress["result"]["videoPath"] = video_path
        self._save_progress()

    def run(self) -> str | None:
        """Main pipeline entry. Returns output video path or None on failure."""
        try:
            self._raise_if_cancel_requested()
            self._update_progress("prepare_audio", 5, "Dang chuan bi audio...")

            audio_path = self._prepare_audio()
            self._raise_if_cancel_requested()
            if not audio_path:
                self._update_progress(
                    "prepare_audio",
                    0,
                    "Khong the chuan bi audio.",
                    status="failed",
                    error="Audio preparation failed.",
                )
                return None

            audio_duration = get_audio_duration(audio_path)
            self._raise_if_cancel_requested()
            if audio_duration <= 0:
                self._update_progress(
                    "prepare_audio",
                    0,
                    "Audio khong hop le hoac khong doc duoc.",
                    status="failed",
                    error="Invalid audio duration.",
                )
                return None

            self._update_progress("select_clips", 15, "Dang chon clip ngau nhien tu thu vien...")
            clips = self._select_clips(audio_duration)
            self._raise_if_cancel_requested()
            if not clips:
                library_hint = f" (thu vien: {self.library_id})" if self.library_id else ""
                self._update_progress(
                    "select_clips",
                    15,
                    f"Khong tim thay clip nao trong thu vien{library_hint}.",
                    status="failed",
                    error="No clips available in the selected story library.",
                )
                return None

            self._update_progress("render_video", 35, "Dang ghep clip voi audio...")
            rendered_video = self._render_simple_video(clips, audio_path, audio_duration)
            self._raise_if_cancel_requested()
            if not rendered_video:
                self._update_progress(
                    "render_video",
                    35,
                    "Ghep video voi audio that bai.",
                    status="failed",
                    error="Video render failed.",
                )
                return None

            if self.subtitle_path:
                self._update_progress("story_overlays", 90, "Dang chuan bi phu de...")
                ass_path = self._prepare_subtitle_ass(audio_duration)
                self._raise_if_cancel_requested()
                if not ass_path:
                    return None
                self._subtitle_ass_path = ass_path

            overlay_message = "Dang ap dung TV noise va song am..."
            if self._subtitle_ass_path:
                overlay_message = "Dang ap dung TV noise, song am va phu de..."
            self._update_progress("story_overlays", 90, overlay_message)
            output_video = self._apply_story_overlays(rendered_video, audio_duration)
            self._raise_if_cancel_requested()
            if not output_video:
                return None

            self._update_progress("finalize", 98, "Dang hoan tat...")
            self._raise_if_cancel_requested()
            final_path = self._finalize(output_video)
            final_rel_path = storage_relative_path(final_path)

            self._update_progress(
                "completed",
                100,
                "Hoan tat thanh cong!",
                status="completed",
                video_path=final_rel_path,
            )
            logger.info(f"[StoryPipeline:{self.story_id}] Pipeline completed: {final_path}")
            return final_path

        except StoryVideoCancelled:
            logger.info(f"[StoryPipeline:{self.story_id}] Pipeline cancelled.")
            self._update_progress(
                "cancelled",
                self.progress.get("percent", 0),
                "Da huy xu ly.",
                status="cancelled",
            )
            return None
        except Exception as exc:
            logger.error(f"[StoryPipeline:{self.story_id}] Pipeline failed: {exc}", exc_info=True)
            self._update_progress(
                self.progress.get("stage", "unknown"),
                0,
                f"Pipeline that bai: {exc}",
                status="failed",
                error=str(exc),
            )
            return None

    def _prepare_audio(self) -> str | None:
        """Prepare audio from script URL (TTS) or validate uploaded audio file."""
        temp = _temp_dir(self.story_id)

        if self.input_type == "script_url":
            try:
                result = create_audio_from_google_doc(
                    doc_url=self.input_value,
                    output_name=self.output_name or f"story_{self.story_id}",
                    voice_id=self.voice_id or None,
                )
                audio_item = result.get("audio")
                if not audio_item or not audio_item.get("relativePath"):
                    logger.error(f"[StoryPipeline:{self.story_id}] TTS returned no audio.")
                    return None

                source_path = storage_absolute_path(audio_item["relativePath"])
                if not os.path.isfile(source_path):
                    logger.error(f"[StoryPipeline:{self.story_id}] TTS audio file not found: {source_path}")
                    return None

                dest_path = os.path.join(temp, f"audio_{self.story_id}.mp3")
                shutil.copy2(source_path, dest_path)
                logger.info(f"[StoryPipeline:{self.story_id}] TTS audio ready: {dest_path}")
                return dest_path

            except TTSAudioError as exc:
                logger.error(f"[StoryPipeline:{self.story_id}] TTS failed: {exc}")
                return None

        if self.input_type == "audio_file":
            audio_path = self.input_value
            if not os.path.isfile(audio_path):
                logger.error(f"[StoryPipeline:{self.story_id}] Audio file not found: {audio_path}")
                return None
            if not validate_audio(audio_path):
                logger.error(f"[StoryPipeline:{self.story_id}] Audio validation failed: {audio_path}")
                return None
            dest_path = os.path.join(temp, f"audio_{Path(audio_path).name}")
            shutil.copy2(audio_path, dest_path)
            logger.info(f"[StoryPipeline:{self.story_id}] Audio file validated: {dest_path}")
            return dest_path

        logger.error(f"[StoryPipeline:{self.story_id}] Unknown input_type: {self.input_type}")
        return None

    def _select_clips(self, audio_duration: float) -> list[str]:
        """Select prebuilt story-library clips without re-encoding them."""
        index = _load_story_library_index(self.library_id)
        all_clips = index.get("assets", [])

        if self.clip_tags:
            selected_values = {str(value).lower() for value in self.clip_tags}
            selected_by_id = [
                clip
                for clip in all_clips
                if str(clip.get("id", "")).lower() in selected_values
            ]
            filtered = selected_by_id
            if not filtered:
                filtered = [
                    clip
                    for clip in all_clips
                    if any(str(tag).lower() in selected_values for tag in clip.get("tags", []))
                ]
            if not filtered:
                logger.warning(
                    f"[StoryPipeline:{self.story_id}] No clips match selection {self.clip_tags}, using all clips."
                )
                filtered = all_clips
        else:
            filtered = all_clips

        valid_clips: list[tuple[str, float]] = []
        for clip in filtered:
            rel_path = clip.get("relative_path", "")
            if not rel_path:
                continue
            clip_path = os.path.join(story_library_root(self.library_id), rel_path)
            if not os.path.isfile(clip_path):
                continue

            try:
                duration = float(clip.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0
            if duration <= 0:
                duration = FFmpegHelper.probe_duration(clip_path)
            if duration > 0:
                valid_clips.append((clip_path, duration))

        # Drop clips whose resolution doesn't match the pipeline target: a base
        # concatenated from mismatched clips breaks the CUDA-only overlay filter
        # chain mid-stream (NVDEC hits the parameter change and scale_cuda/
        # overlay_cuda can't reconfigure -> "Function not implemented"). Excluding
        # them here just shrinks the pool the random draw below picks from, so a
        # different clip is used in its place automatically.
        valid_clips, excluded_clips = filter_valid_clips(
            story_library_root(self.library_id), valid_clips
        )
        if excluded_clips:
            logger.warning(
                f"[StoryPipeline:{self.story_id}] Excluded {len(excluded_clips)} clip(s) with "
                f"mismatched resolution/pix_fmt/color tags (expected {Config.TARGET_RESOLUTION} "
                f"{Config.CLIP_EXPECTED_PIX_FMT}/{Config.CLIP_EXPECTED_COLOR_RANGE}/"
                f"{Config.CLIP_EXPECTED_COLOR_SPACE}): "
                f"{excluded_clips[:3]}{' ...' if len(excluded_clips) > 3 else ''}"
            )

        if not valid_clips:
            logger.error(f"[StoryPipeline:{self.story_id}] No valid clips found in story library.")
            return []

        segment_duration = self._segment_duration()
        min_duration = max(0.5, segment_duration - 0.25)
        pool = [item for item in valid_clips if item[1] >= min_duration] or valid_clips
        target_duration = audio_duration + 0.25
        selected: list[str] = []
        selected_duration = 0.0

        if self.clip_bag is not None:
            # Batch mode: draw without replacement from the deck shared by the
            # whole batch, so a clip repeats only after the entire pool has
            # been used at least once (ceil(picks/pool) cap instead of the
            # unbounded overlap independent shuffles produce).
            key = SharedClipBag.pool_key(resolve_library_id(self.library_id), self.clip_tags)
            picked: set[str] = set()
            while selected_duration < target_duration:
                self._raise_if_cancel_requested()
                clip_path, duration = self.clip_bag.draw(key, pool, exclude=picked)
                picked.add(clip_path)
                selected.append(clip_path)
                selected_duration += min(duration, float(segment_duration))
        else:
            while selected_duration < target_duration:
                self._raise_if_cancel_requested()
                shuffled = list(pool)
                random.shuffle(shuffled)
                for clip_path, duration in shuffled:
                    selected.append(clip_path)
                    selected_duration += min(duration, float(segment_duration))
                    if selected_duration >= target_duration:
                        break

        mode = "shared shuffle-bag" if self.clip_bag is not None else "per-video shuffle"
        logger.info(
            f"[StoryPipeline:{self.story_id}] Selected {len(selected)} clips ({mode}), "
            f"selected_duration={selected_duration:.1f}s, audio={audio_duration:.1f}s"
        )
        return selected

    def _render_simple_video(self, segments: list[str], audio_path: str, audio_duration: float) -> str | None:
        """Concat prebuilt library clips and mux the main audio."""
        temp = _temp_dir(self.story_id)
        output_dir = os.path.join(_story_dir(self.story_id), "renders")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"video_{self.story_id}.mp4")
        concat_file = os.path.join(temp, "story_segments.txt")
        segment_duration = self._segment_duration()

        with open(concat_file, "w", encoding="utf-8") as file_obj:
            for segment_path in segments:
                clean_path = os.path.abspath(segment_path).replace("\\", "/").replace("'", "'\\''")
                file_obj.write(f"file '{clean_path}'\n")
                file_obj.write(f"outpoint {segment_duration:.3f}\n")

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-i",
            audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            str(audio_duration),
            "-movflags",
            "+faststart",
            output_path,
        ]

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "render_video",
                35 + (float(ffmpeg_percent) * 0.55),
                "Dang ghep clip voi audio...",
            )

        ok = FFmpegHelper.run_command(
            cmd,
            progress_callback=_progress,
            progress_total_seconds=audio_duration,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if ok and os.path.isfile(output_path):
            return output_path
        return None

    def _prepare_subtitle_ass(self, audio_duration: float) -> str | None:
        """Build the burn-in .ass file from the uploaded .srt subtitle."""
        from src.utils.story_subtitles import (
            SubtitleParseError,
            build_ass,
            parse_srt,
            resegment,
            write_ass_file,
        )

        try:
            cues = parse_srt(self.subtitle_path)
            cues = resegment(
                cues,
                max_chars_per_line=self.subtitle_max_chars_per_line,
                max_lines=self.subtitle_max_lines,
            )
        except SubtitleParseError as exc:
            logger.error(f"[StoryPipeline:{self.story_id}] Subtitle parse failed: {exc}")
            self._update_progress(
                "story_overlays",
                90,
                "File subtitle (.srt) khong hop le.",
                status="failed",
                error=f"Invalid subtitle file: {exc}",
            )
            return None

        clamped: list[dict] = []
        for cue in cues:
            start = float(cue.get("start", 0) or 0)
            end = min(float(cue.get("end", 0) or 0), float(audio_duration))
            if start >= audio_duration or end <= start:
                continue
            clamped.append({**cue, "start": start, "end": end})
        if not clamped:
            logger.warning(
                f"[StoryPipeline:{self.story_id}] No subtitle cues fall within the audio duration."
            )

        width, height = (int(value) for value in Config.TARGET_RESOLUTION.split("x", 1))
        ass_text = build_ass(
            clamped,
            font_family=self.subtitle_font or Config.STORY_SUBTITLE_DEFAULT_FONT,
            preset_id=self.subtitle_preset or "clean",
            play_res=(width, height),
            style_overrides=self.subtitle_style_overrides or None,
        )
        ass_path = os.path.abspath(
            os.path.join(_temp_dir(self.story_id), f"subs_{self.story_id}.ass")
        )
        write_ass_file(ass_text, ass_path)
        logger.info(f"[StoryPipeline:{self.story_id}] Subtitle ASS ready: {ass_path}")
        return ass_path

    def _apply_default_waveform_overlay(self, current_video: str, audio_duration: float) -> str | None:
        """Overlay the globally configured preprocessed waveform, if available."""
        from src.utils.waveform_overlays import (
            get_default_waveform_overlay,
            overlay_position_expr,
            processed_abs_path,
        )

        record = get_default_waveform_overlay()
        if not record:
            logger.info(f"[StoryPipeline:{self.story_id}] No default waveform overlay configured.")
            return current_video

        waveform_path = processed_abs_path(record)
        if not waveform_path:
            logger.warning(f"[StoryPipeline:{self.story_id}] Default waveform processed file is missing.")
            return current_video

        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"waveform_overlay_{self.story_id}.mp4")
        x_expr, y_expr = overlay_position_expr(record)
        filter_str = (
            "[1:v]setpts=PTS-STARTPTS[wave];"
            f"[0:v][wave]overlay={x_expr}:{y_expr}:format=auto,format=yuv420p[v]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            current_video,
            "-stream_loop",
            "-1",
            "-i",
            waveform_path,
            "-filter_complex",
            filter_str,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            str(audio_duration),
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend([
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "waveform_overlay",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ap dung song am...",
            )

        ok = _run_overlay_ffmpeg(
            cmd,
            progress_callback=_progress,
            progress_total_seconds=audio_duration,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if ok and os.path.isfile(output_path):
            return output_path

        self._raise_if_cancel_requested()
        logger.error(f"[StoryPipeline:{self.story_id}] Failed to apply waveform overlay.")
        self._update_progress(
            "waveform_overlay",
            90,
            "Ap dung song am that bai.",
            status="failed",
            error="Waveform overlay failed.",
        )
        return None

    def _tv_effect_filter(self) -> str:
        """Filter chain for the selected 1990s TV effect style (empty when disabled).

        Pre-baked "styled" libraries already have the style burned into every
        clip, so the style pass is skipped to avoid double-styling (and to take
        the much cheaper overlay-only render path).
        """
        from src.processors.crt_effect_processor import get_tv_effect_filter
        from src.utils.story_library import is_styled_library

        if is_styled_library(self.library_id):
            logger.info(
                f"[StoryPipeline:{self.story_id}] Styled library selected; skipping TV style pass."
            )
            return ""

        return get_tv_effect_filter(self.tv_effect_style_id or None)

    @staticmethod
    def _hwaccel_flags() -> list:
        """NVDEC decode flags for the base video input (CPU fallback handled by caller)."""
        return ["-hwaccel", "cuda"] if Config.USE_GPU_NVENC else []

    def _ass_filter_suffix(self, ass_path: str = "") -> str:
        """Subtitle burn-in snippet ("ass=<path>,") chained right before the final format=yuv420p.

        Defaults to the story's subtitle .ass; pass `ass_path` to burn a per-segment
        rebased .ass instead (parallel-segment overlay path)."""
        path = ass_path or self._subtitle_ass_path
        if not path:
            return ""
        from src.utils.story_subtitles import _list_font_files, ass_filter_path

        ass_value = f"ass={ass_filter_path(path)}"
        if _list_font_files(Config.STORY_FONTS_DIR):
            ass_value += f":fontsdir={ass_filter_path(Config.STORY_FONTS_DIR)}"
        return f"{ass_value},"

    def _gpu_overlay_enabled(self) -> bool:
        """Whether the overlay pass may use the GPU (overlay_cuda) pipeline.

        Requires the feature flag, NVENC enabled, and an FFmpeg build that actually
        exposes the CUDA overlay filters. Callers additionally exclude passes that
        need CPU-only filters (screen blend, CPU TV style)."""
        return (
            Config.OVERLAY_USE_GPU_PIPELINE
            and Config.USE_GPU_NVENC
            and FFmpegHelper.cuda_overlay_available()
        )

    def _gpu_overlay_tail(self, chain_label: str, ass_path: str = "") -> str:
        """Closing filter node for a GPU overlay chain.

        With subtitles we must drop back to system memory (no CUDA ass filter):
        hwdownload -> burn ass on CPU -> yuv420p. Without subtitles the frames stay
        on the GPU (scale_cuda=format=yuv420p) and feed h264_nvenc directly."""
        ass_suffix = self._ass_filter_suffix(ass_path)
        if ass_suffix:
            return f"{chain_label}hwdownload,format=yuv420p,{ass_suffix}format=yuv420p[v]"
        return f"{chain_label}scale_cuda=format=yuv420p[v]"

    def _build_story_overlays_gpu_cmd(
        self,
        current_video: str,
        output_path: str,
        audio_duration: float,
        tv_noise_paths: list,
        waveform_path,
        waveform_record,
        cta_path,
        cta_record,
        *,
        ss: float | None = None,
        ass_path: str = "",
        with_audio: bool = True,
    ) -> list:
        """GPU (overlay_cuda) variant of the direct overlay chain.

        Input order mirrors the CPU command (base, tv-noise..., waveform, cta) so the
        filter input indices line up. Only alpha overlays reach here — screen-blend
        noise and CPU TV style keep the CPU path (see caller eligibility check).

        For a parallel time-segment, pass `ss` (input seek start), a rebased `ass_path`,
        and `with_audio=False` (audio is muxed back once after concatenation)."""
        from src.utils.story_cta_overlay import overlay_position_expr as cta_position_expr
        from src.utils.waveform_overlays import overlay_position_expr

        cmd = ["ffmpeg", "-y", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        if ss is not None:
            cmd.extend(["-ss", str(ss)])
        cmd.extend(["-i", current_video])
        for _record, overlay_path in tv_noise_paths:
            cmd.extend(["-stream_loop", "-1", "-i", overlay_path])

        waveform_input_index = None
        if waveform_path:
            waveform_input_index = 1 + len(tv_noise_paths)
            cmd.extend(["-stream_loop", "-1", "-i", waveform_path])

        cta_input_index = None
        if cta_path:
            cta_input_index = 1 + len(tv_noise_paths) + (1 if waveform_path else 0)
            cmd.extend(["-stream_loop", "-1", "-i", cta_path])

        filter_parts = ["[0:v]scale_cuda=format=yuv420p[base]"]
        chain_label = "[base]"
        for index, (_record, _overlay_path) in enumerate(tv_noise_paths):
            input_index = index + 1
            noise_label = f"tvnoise{index}"
            out_label = f"tvnoiseout{index}"
            filter_parts.append(
                f"[{input_index}:v]setpts=PTS-STARTPTS,format=yuva420p,hwupload_cuda[{noise_label}]"
            )
            filter_parts.append(
                f"{chain_label}[{noise_label}]overlay_cuda=0:0:eof_action=repeat:eval=init[{out_label}]"
            )
            chain_label = f"[{out_label}]"

        if waveform_path and waveform_record and waveform_input_index is not None:
            x_expr, y_expr = overlay_position_expr(waveform_record)
            filter_parts.append(
                f"[{waveform_input_index}:v]setpts=PTS-STARTPTS,format=yuva420p,hwupload_cuda[wave]"
            )
            filter_parts.append(
                f"{chain_label}[wave]overlay_cuda={x_expr}:{y_expr}:eof_action=repeat:eval=init[waveout]"
            )
            chain_label = "[waveout]"

        if cta_path and cta_record and cta_input_index is not None:
            cx_expr, cy_expr = cta_position_expr(cta_record)
            filter_parts.append(
                f"[{cta_input_index}:v]setpts=PTS-STARTPTS,format=yuva420p,hwupload_cuda[cta]"
            )
            filter_parts.append(
                f"{chain_label}[cta]overlay_cuda={cx_expr}:{cy_expr}:eof_action=repeat:eval=init[ctaout]"
            )
            chain_label = "[ctaout]"

        filter_parts.append(self._gpu_overlay_tail(chain_label, ass_path))

        cmd.extend(["-filter_complex", ";".join(filter_parts), "-map", "[v]"])
        if with_audio:
            cmd.extend(["-map", "0:a?"])
        cmd.extend(["-t", str(audio_duration)])
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        if with_audio:
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.append("-an")
        cmd.extend(["-movflags", "+faststart", output_path])
        return cmd

    def _apply_story_overlays_gpu_segmented(
        self,
        current_video: str,
        audio_duration: float,
        tv_noise_paths: list,
        waveform_path,
        waveform_record,
        cta_path,
        cta_record,
        segments: int,
    ) -> str | None:
        """Run the GPU overlay+subtitle pass as N parallel time-segments, then concat.

        The single-threaded libass subtitle burn is the bottleneck while the GPU sits
        mostly idle; rendering several segments at once parallelises libass across CPU
        cores and fills the GPU. Each segment seeks the base (`-ss`) and burns its own
        rebased .ass; the video-only segments are concatenated and the base audio is
        muxed back once. Returns the overlay output path, or None to let the caller
        fall back to the single-pass overlay."""
        temp = _temp_dir(self.story_id)
        seg_dur = audio_duration / segments
        seg_cmds: list[list] = []
        seg_outputs: list[str] = []
        for i in range(segments):
            start = i * seg_dur
            dur = seg_dur if i < segments - 1 else (audio_duration - start)
            seg_ass = os.path.join(temp, f"segsub_{i}_{self.story_id}.ass")
            _rebase_ass_file(self._subtitle_ass_path, start, dur, seg_ass)
            seg_out = os.path.join(temp, f"segpart_{i}_{self.story_id}.mp4")
            seg_cmds.append(
                self._build_story_overlays_gpu_cmd(
                    current_video, seg_out, dur,
                    tv_noise_paths, waveform_path, waveform_record, cta_path, cta_record,
                    ss=start, ass_path=seg_ass, with_audio=False,
                )
            )
            seg_outputs.append(seg_out)

        self._update_progress(
            "story_overlays", 92,
            f"Dang ap dung overlay + phu de ({segments} luong song song)...",
        )

        results: list[bool] = [False] * segments

        def _worker(idx: int):
            results[idx] = _run_overlay_ffmpeg(
                seg_cmds[idx],
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(segments)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self._raise_if_cancel_requested()
        if not all(results) or not all(os.path.isfile(path) for path in seg_outputs):
            logger.warning(
                f"[StoryPipeline:{self.story_id}] A parallel overlay segment failed; "
                f"falling back to single-pass overlay."
            )
            return None

        # Concat the video-only segments (all share codec/params -> stream copy).
        concat_list = os.path.join(temp, f"segconcat_{self.story_id}.txt")
        with open(concat_list, "w", encoding="utf-8") as handle:
            for path in seg_outputs:
                handle.write(f"file '{os.path.abspath(path).replace(os.sep, '/')}'\n")
        concat_video = os.path.join(temp, f"segvideo_{self.story_id}.mp4")
        ok = FFmpegHelper.run_command(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
             "-c", "copy", "-movflags", "+faststart", concat_video],
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if not ok or not os.path.isfile(concat_video):
            logger.warning(f"[StoryPipeline:{self.story_id}] Overlay segment concat failed.")
            return None

        # Mux the base audio back onto the concatenated video.
        output_path = os.path.join(temp, f"story_overlays_{self.story_id}.mp4")
        ok = FFmpegHelper.run_command(
            ["ffmpeg", "-y", "-i", concat_video, "-i", current_video,
             "-map", "0:v:0", "-map", "1:a:0?", "-c", "copy", "-t", str(audio_duration),
             "-movflags", "+faststart", output_path],
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if not ok or not os.path.isfile(output_path):
            logger.warning(f"[StoryPipeline:{self.story_id}] Overlay segment audio mux failed.")
            return None

        logger.info(
            f"[StoryPipeline:{self.story_id}] Applied story overlays via {segments} parallel GPU segments."
        )
        return output_path

    def _apply_story_overlays(self, current_video: str, audio_duration: float) -> str | None:
        """Overlay TV noise layers and the configured waveform in a single FFmpeg pass."""
        from src.utils.story_library import is_fully_baked_library

        # Fully-baked libraries already have style + waveform + CTA burned into the
        # clips, so the only remaining work is subtitle burn-in (the fastest path).
        if is_fully_baked_library(self.library_id):
            if self._subtitle_ass_path:
                logger.info(
                    f"[StoryPipeline:{self.story_id}] Fully-baked library; subtitle-only pass."
                )
                return self._apply_subtitles_only(current_video, audio_duration)
            logger.info(
                f"[StoryPipeline:{self.story_id}] Fully-baked library, no subtitle; using rendered video as-is."
            )
            return current_video

        from src.utils.story_cta_overlay import (
            get_active_cta_overlay,
            overlay_position_expr as cta_position_expr,
            processed_abs_path as cta_processed_abs_path,
        )
        from src.utils.story_overlay_packs import get_or_create_story_overlay_pack
        from src.utils.story_tv_noise_overlays import (
            get_active_tv_noise_overlays,
            overlay_blend_mode,
            processed_abs_path as tv_noise_processed_abs_path,
        )
        from src.utils.waveform_overlays import (
            get_default_waveform_overlay,
            load_waveform_index,
            overlay_position_expr,
            processed_abs_path as waveform_processed_abs_path,
        )

        tv_noise_records = get_active_tv_noise_overlays()

        waveform_record = None
        if self.waveform_overlay_id:
            waveform_records = load_waveform_index().get("overlays", [])
            waveform_record = next(
                (item for item in waveform_records if item.get("id") == self.waveform_overlay_id),
                None,
            )
        if not waveform_record:
            waveform_record = get_default_waveform_overlay()

        waveform_path = waveform_processed_abs_path(waveform_record) if waveform_record else None
        if waveform_record and not waveform_path:
            logger.warning(f"[StoryPipeline:{self.story_id}] Waveform processed file is missing.")
            waveform_record = None

        cta_record = get_active_cta_overlay()
        cta_path = cta_processed_abs_path(cta_record) if cta_record else None
        if cta_record and not cta_path:
            logger.warning(f"[StoryPipeline:{self.story_id}] CTA processed file is missing.")
            cta_record = None

        tv_noise_paths: list[tuple[dict, str]] = []
        for record in tv_noise_records:
            processed_path = tv_noise_processed_abs_path(record)
            if processed_path:
                tv_noise_paths.append((record, processed_path))

        style_filter = self._tv_effect_filter()

        if not tv_noise_paths and not waveform_path and not cta_path:
            if style_filter:
                return self._apply_tv_effect_only(current_video, audio_duration, style_filter)
            if self._subtitle_ass_path:
                return self._apply_subtitles_only(current_video, audio_duration)
            logger.info(f"[StoryPipeline:{self.story_id}] No story overlays configured.")
            return current_video

        # Precompose only when there are full-frame noise layers to merge. With
        # just the small waveform, a direct overlay is much cheaper than
        # blending a full-frame alpha pack every frame (~4% vs 100% of pixels).
        # Screen-blend overlays cannot be premerged into an alpha pack (screen
        # math needs the underlying video), so their presence forces the
        # direct-chain path.
        has_screen_noise = any(
            overlay_blend_mode(record) == "screen" for record, _path in tv_noise_paths
        )
        pack_path = None
        if tv_noise_paths and not has_screen_noise:
            pack_path = get_or_create_story_overlay_pack(
                tv_noise_paths,
                waveform_record if waveform_path else None,
                waveform_path,
                cta_record if cta_path else None,
                cta_path,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
        self._raise_if_cancel_requested()
        if pack_path:
            packed_output = self._apply_precomposed_story_overlay(
                current_video, audio_duration, pack_path, style_filter
            )
            self._raise_if_cancel_requested()
            if packed_output:
                return packed_output
            logger.warning(
                f"[StoryPipeline:{self.story_id}] Precomposed overlay pack failed; falling back to direct overlays."
            )

        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"story_overlays_{self.story_id}.mp4")
        hwaccel_flags = self._hwaccel_flags()
        cmd = ["ffmpeg", "-y", *hwaccel_flags, "-i", current_video]

        for _record, overlay_path in tv_noise_paths:
            cmd.extend(["-stream_loop", "-1", "-i", overlay_path])

        waveform_input_index = None
        if waveform_path:
            waveform_input_index = 1 + len(tv_noise_paths)
            cmd.extend(["-stream_loop", "-1", "-i", waveform_path])

        cta_input_index = None
        if cta_path:
            cta_input_index = 1 + len(tv_noise_paths) + (1 if waveform_path else 0)
            cmd.extend(["-stream_loop", "-1", "-i", cta_path])

        filter_parts: list[str] = []
        chain_label = "[0:v]"
        if style_filter:
            filter_parts.append(f"[0:v]{style_filter}[styled]")
            chain_label = "[styled]"
        for index, (record, _overlay_path) in enumerate(tv_noise_paths):
            input_index = index + 1
            noise_label = f"tvnoise{index}"
            out_label = f"tvnoiseout{index}"
            if overlay_blend_mode(record) == "screen":
                opacity = max(
                    0.0,
                    min(1.0, float(record.get("opacity") or Config.STORY_TV_NOISE_OPACITY)),
                )
                # blend needs matching pixel formats on both inputs.
                filter_parts.append(
                    f"[{input_index}:v]setpts=PTS-STARTPTS,format=yuv420p[{noise_label}]"
                )
                filter_parts.append(f"{chain_label}format=yuv420p[{noise_label}base]")
                filter_parts.append(
                    f"[{noise_label}base][{noise_label}]"
                    f"blend=all_mode=screen:all_opacity={opacity}:eof_action=repeat[{out_label}]"
                )
            else:
                filter_parts.append(f"[{input_index}:v]setpts=PTS-STARTPTS[{noise_label}]")
                filter_parts.append(
                    f"{chain_label}[{noise_label}]overlay=0:0:format=auto:eof_action=repeat:eval=init[{out_label}]"
                )
            chain_label = f"[{out_label}]"

        if waveform_path and waveform_record and waveform_input_index is not None:
            x_expr, y_expr = overlay_position_expr(waveform_record)
            filter_parts.append(f"[{waveform_input_index}:v]setpts=PTS-STARTPTS[wave]")
            filter_parts.append(
                f"{chain_label}[wave]overlay={x_expr}:{y_expr}:format=auto:eof_action=repeat:eval=init[waveout]"
            )
            chain_label = "[waveout]"

        if cta_path and cta_record and cta_input_index is not None:
            cx_expr, cy_expr = cta_position_expr(cta_record)
            filter_parts.append(f"[{cta_input_index}:v]setpts=PTS-STARTPTS[cta]")
            filter_parts.append(
                f"{chain_label}[cta]overlay={cx_expr}:{cy_expr}:format=auto:eof_action=repeat:eval=init[ctaout]"
            )
            chain_label = "[ctaout]"

        filter_parts.append(f"{chain_label}{self._ass_filter_suffix()}format=yuv420p[v]")

        cmd.extend([
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            str(audio_duration),
        ])
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend([
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "story_overlays",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ap dung TV noise va song am...",
            )

        use_gpu = (
            self._gpu_overlay_enabled()
            and not has_screen_noise
            and not style_filter
        )
        ok = False

        # Parallel-segment GPU path: only worthwhile when a subtitle burn (the
        # single-threaded libass bottleneck) is present on a long-enough clip.
        segments = max(1, int(Config.OVERLAY_PARALLEL_SEGMENTS))
        if (
            use_gpu
            and self._subtitle_ass_path
            and segments > 1
            and audio_duration >= Config.OVERLAY_SEGMENT_MIN_SECONDS
        ):
            seg_output = self._apply_story_overlays_gpu_segmented(
                current_video,
                audio_duration,
                tv_noise_paths,
                waveform_path,
                waveform_record,
                cta_path,
                cta_record,
                segments,
            )
            if seg_output and os.path.isfile(seg_output):
                return seg_output
            self._raise_if_cancel_requested()
            # segmented path bailed; continue to the single-pass overlay below

        if use_gpu:
            gpu_cmd = self._build_story_overlays_gpu_cmd(
                current_video,
                output_path,
                audio_duration,
                tv_noise_paths,
                waveform_path,
                waveform_record,
                cta_path,
                cta_record,
            )
            logger.info(
                f"[StoryPipeline:{self.story_id}] Applying story overlays on GPU (overlay_cuda)."
            )
            ok = _run_overlay_ffmpeg(
                gpu_cmd,
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
            if not ok:
                self._raise_if_cancel_requested()
                logger.warning(
                    f"[StoryPipeline:{self.story_id}] GPU overlay pass failed; falling back to CPU overlay."
                )

        if not ok:
            ok = _run_overlay_ffmpeg(
                cmd,
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
            if not ok and hwaccel_flags:
                self._raise_if_cancel_requested()
                logger.warning(
                    f"[StoryPipeline:{self.story_id}] CUDA decode failed for overlay pass; retrying with CPU decode."
                )
                ok = _run_overlay_ffmpeg(
                    cmd[:2] + cmd[2 + len(hwaccel_flags):],
                    progress_callback=_progress,
                    progress_total_seconds=audio_duration,
                    cancel_callback=lambda: is_story_cancel_requested(self.story_id),
                )
        if ok and os.path.isfile(output_path):
            return output_path

        self._raise_if_cancel_requested()
        logger.error(f"[StoryPipeline:{self.story_id}] Failed to apply story overlays.")
        self._update_progress(
            "story_overlays",
            90,
            "Ap dung TV noise/song am that bai.",
            status="failed",
            error="Story overlay pass failed.",
        )
        return None

    def _build_pack_overlay_gpu_cmd(
        self,
        current_video: str,
        output_path: str,
        audio_duration: float,
        pack_path: str,
    ) -> list:
        """GPU (overlay_cuda) variant of the precomposed alpha-pack overlay.

        Only reached when there is no CPU TV style filter (see caller). The pack is
        a single alpha layer composited on the base with overlay_cuda."""
        fps = max(1, int(Config.TARGET_FPS))
        cmd = [
            "ffmpeg",
            "-y",
            "-hwaccel",
            "cuda",
            "-hwaccel_output_format",
            "cuda",
            "-i",
            current_video,
            "-stream_loop",
            "-1",
            "-i",
            pack_path,
        ]
        filter_parts = [
            "[0:v]scale_cuda=format=yuv420p[base]",
            f"[1:v]setpts=N/{fps}/TB,format=yuva420p,hwupload_cuda[pack]",
            "[base][pack]overlay_cuda=0:0:eof_action=repeat:eval=init[packed]",
        ]
        filter_parts.append(self._gpu_overlay_tail("[packed]"))
        cmd.extend([
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            str(audio_duration),
        ])
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(["-c:a", "copy", "-movflags", "+faststart", output_path])
        return cmd

    def _apply_precomposed_story_overlay(
        self,
        current_video: str,
        audio_duration: float,
        pack_path: str,
        style_filter: str = "",
    ) -> str | None:
        """Overlay a cached precomposed alpha pack in a single FFmpeg overlay layer."""
        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"story_overlay_pack_{self.story_id}.mp4")
        fps = max(1, int(Config.TARGET_FPS))
        base_chain = "setpts=PTS-STARTPTS"
        if style_filter:
            base_chain = f"{base_chain},{style_filter}"
        filter_str = (
            f"[0:v]{base_chain},format=yuv420p[base];"
            f"[1:v]setpts=N/{fps}/TB[pack];"
            "[base][pack]overlay=0:0:format=auto:eof_action=repeat:eval=init[packed];"
            f"[packed]{self._ass_filter_suffix()}format=yuv420p[v]"
        )
        hwaccel_flags = self._hwaccel_flags()
        cmd = [
            "ffmpeg",
            "-y",
            *hwaccel_flags,
            "-i",
            current_video,
            "-stream_loop",
            "-1",
            "-i",
            pack_path,
            "-filter_complex",
            filter_str,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            str(audio_duration),
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend([
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "story_overlays",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ap dung overlay pack...",
            )

        use_gpu = self._gpu_overlay_enabled() and not style_filter
        ok = False
        if use_gpu:
            gpu_cmd = self._build_pack_overlay_gpu_cmd(
                current_video, output_path, audio_duration, pack_path
            )
            logger.info(
                f"[StoryPipeline:{self.story_id}] Applying overlay pack on GPU (overlay_cuda)."
            )
            ok = _run_overlay_ffmpeg(
                gpu_cmd,
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
            if not ok:
                self._raise_if_cancel_requested()
                logger.warning(
                    f"[StoryPipeline:{self.story_id}] GPU pack overlay failed; falling back to CPU overlay."
                )

        if not ok:
            ok = _run_overlay_ffmpeg(
                cmd,
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
            if not ok and hwaccel_flags:
                self._raise_if_cancel_requested()
                logger.warning(
                    f"[StoryPipeline:{self.story_id}] CUDA decode failed for pack overlay; retrying with CPU decode."
                )
                ok = _run_overlay_ffmpeg(
                    cmd[:2] + cmd[2 + len(hwaccel_flags):],
                    progress_callback=_progress,
                    progress_total_seconds=audio_duration,
                    cancel_callback=lambda: is_story_cancel_requested(self.story_id),
                )
        if ok and os.path.isfile(output_path):
            logger.info(f"[StoryPipeline:{self.story_id}] Applied precomposed overlay pack: {pack_path}")
            return output_path
        return None

    def _apply_tv_effect_only(
        self,
        current_video: str,
        audio_duration: float,
        style_filter: str,
    ) -> str | None:
        """Apply the selected TV effect style when no overlays are configured."""
        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"story_tv_effect_{self.story_id}.mp4")
        hwaccel_flags = self._hwaccel_flags()
        cmd = [
            "ffmpeg",
            "-y",
            *hwaccel_flags,
            "-i",
            current_video,
            "-vf",
            f"{style_filter},{self._ass_filter_suffix()}format=yuv420p",
            "-t",
            str(audio_duration),
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend([
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "story_overlays",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ap dung hieu ung TV...",
            )

        ok = _run_overlay_ffmpeg(
            cmd,
            progress_callback=_progress,
            progress_total_seconds=audio_duration,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if not ok and hwaccel_flags:
            self._raise_if_cancel_requested()
            logger.warning(
                f"[StoryPipeline:{self.story_id}] CUDA decode failed for TV effect pass; retrying with CPU decode."
            )
            ok = _run_overlay_ffmpeg(
                cmd[:2] + cmd[2 + len(hwaccel_flags):],
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
        if ok and os.path.isfile(output_path):
            return output_path

        self._raise_if_cancel_requested()
        logger.error(f"[StoryPipeline:{self.story_id}] Failed to apply TV effect style.")
        self._update_progress(
            "story_overlays",
            90,
            "Ap dung hieu ung TV that bai.",
            status="failed",
            error="TV effect pass failed.",
        )
        return None

    def _apply_subtitles_only(
        self,
        current_video: str,
        audio_duration: float,
    ) -> str | None:
        """Burn subtitles when no overlays/styles are configured (base video is stream-copied)."""
        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"story_subtitles_{self.story_id}.mp4")
        hwaccel_flags = self._hwaccel_flags()
        cmd = [
            "ffmpeg",
            "-y",
            *hwaccel_flags,
            "-i",
            current_video,
            "-vf",
            f"{self._ass_filter_suffix()}format=yuv420p",
            "-t",
            str(audio_duration),
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend([
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "story_overlays",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ghi phu de vao video...",
            )

        ok = _run_overlay_ffmpeg(
            cmd,
            progress_callback=_progress,
            progress_total_seconds=audio_duration,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if not ok and hwaccel_flags:
            self._raise_if_cancel_requested()
            logger.warning(
                f"[StoryPipeline:{self.story_id}] CUDA decode failed for subtitle pass; retrying with CPU decode."
            )
            ok = _run_overlay_ffmpeg(
                cmd[:2] + cmd[2 + len(hwaccel_flags):],
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
        if ok and os.path.isfile(output_path):
            return output_path

        self._raise_if_cancel_requested()
        logger.error(f"[StoryPipeline:{self.story_id}] Failed to burn subtitles.")
        self._update_progress(
            "story_overlays",
            90,
            "Ghi phu de vao video that bai.",
            status="failed",
            error="Subtitle burn-in pass failed.",
        )
        return None

    def _finalize(self, current_video: str) -> str:
        """Copy the rendered video to final output and purge the per-story cache dir.

        The story dir (temp/, renders/, uploaded originals) is pure working cache;
        the only thing that must survive is progress.json for status polling, and
        that gets rewritten right after this call by the "completed" progress update,
        which recreates the dir via _story_dir()'s makedirs.
        """
        safe_name = self.output_name.strip() if self.output_name else f"story_{self.story_id}"
        safe_name = Path(safe_name).stem
        if not safe_name:
            safe_name = f"story_{self.story_id}"

        final_path = os.path.join(_output_dir(), f"{safe_name}.mp4")

        counter = 1
        while os.path.isfile(final_path):
            final_path = os.path.join(_output_dir(), f"{safe_name}_{counter}.mp4")
            counter += 1

        shutil.copy2(current_video, final_path)
        logger.info(f"[StoryPipeline:{self.story_id}] Final output: {final_path}")

        story_dir = _story_dir(self.story_id)
        try:
            shutil.rmtree(story_dir, ignore_errors=True)
        except OSError:
            pass

        return final_path


class StoryVideoCancelled(RuntimeError):
    """Raised when a Story Video cancellation marker is observed."""
