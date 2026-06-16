"""Single Story Video pipeline runner.

Processes one story video: audio -> random 5-second clip sequence -> audio mux -> finalize.
"""

import json
import os
import random
import shutil
import threading
from datetime import datetime
from pathlib import Path

from src.config import Config
from src.processors.audio_utils import get_audio_duration, validate_audio
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import storage_absolute_path, storage_relative_path
from src.utils.logger import logger
from src.utils.story_library import load_story_library_index, story_library_root
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


def load_story_progress(story_id: str) -> dict | None:
    return _load_json(_progress_path(story_id))


def _load_story_library_index(library_id=None) -> dict:
    return load_story_library_index(library_id)


class StoryVideoPipelineRunner:
    """Runs the simple story video pipeline for a single story."""

    def __init__(self, story_id: str, config_dict: dict):
        self.story_id = story_id
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
        self._subtitle_ass_path = ""

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

        if not valid_clips:
            logger.error(f"[StoryPipeline:{self.story_id}] No valid clips found in story library.")
            return []

        segment_duration = max(1, int(Config.STORY_CLIP_DURATION))
        min_duration = max(0.5, segment_duration - 0.25)
        pool = [item for item in valid_clips if item[1] >= min_duration] or valid_clips
        target_duration = audio_duration + 0.25
        selected: list[str] = []
        selected_duration = 0.0

        while selected_duration < target_duration:
            self._raise_if_cancel_requested()
            shuffled = list(pool)
            random.shuffle(shuffled)
            for clip_path, duration in shuffled:
                selected.append(clip_path)
                selected_duration += min(duration, float(segment_duration))
                if selected_duration >= target_duration:
                    break

        logger.info(
            f"[StoryPipeline:{self.story_id}] Selected {len(selected)} clips, "
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
        segment_duration = max(1, int(Config.STORY_CLIP_DURATION))

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

        ok = FFmpegHelper.run_command(
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
        """Filter chain for the selected 1990s TV effect style (empty when disabled)."""
        from src.processors.crt_effect_processor import get_tv_effect_filter

        return get_tv_effect_filter(self.tv_effect_style_id or None)

    @staticmethod
    def _hwaccel_flags() -> list:
        """NVDEC decode flags for the base video input (CPU fallback handled by caller)."""
        return ["-hwaccel", "cuda"] if Config.USE_GPU_NVENC else []

    def _ass_filter_suffix(self) -> str:
        """Subtitle burn-in snippet ("ass=<path>,") chained right before the final format=yuv420p."""
        if not self._subtitle_ass_path:
            return ""
        from src.utils.story_subtitles import _list_font_files, ass_filter_path

        ass_value = f"ass={ass_filter_path(self._subtitle_ass_path)}"
        if _list_font_files(Config.STORY_FONTS_DIR):
            ass_value += f":fontsdir={ass_filter_path(Config.STORY_FONTS_DIR)}"
        return f"{ass_value},"

    def _apply_story_overlays(self, current_video: str, audio_duration: float) -> str | None:
        """Overlay TV noise layers and the configured waveform in a single FFmpeg pass."""
        from src.utils.story_cta_overlay import (
            get_active_cta_overlay,
            overlay_position_expr as cta_position_expr,
            processed_abs_path as cta_processed_abs_path,
        )
        from src.utils.story_overlay_packs import get_or_create_story_overlay_pack
        from src.utils.story_tv_noise_overlays import (
            get_active_tv_noise_overlays,
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
        pack_path = None
        if tv_noise_paths:
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
        for index, (_record, _overlay_path) in enumerate(tv_noise_paths):
            input_index = index + 1
            noise_label = f"tvnoise{index}"
            out_label = f"tvnoiseout{index}"
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

        ok = FFmpegHelper.run_command(
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
            ok = FFmpegHelper.run_command(
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

        ok = FFmpegHelper.run_command(
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
            ok = FFmpegHelper.run_command(
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

        ok = FFmpegHelper.run_command(
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
            ok = FFmpegHelper.run_command(
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

        ok = FFmpegHelper.run_command(
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
            ok = FFmpegHelper.run_command(
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
        """Copy the rendered video to final output and clean temp files."""
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

        temp = _temp_dir(self.story_id)
        try:
            shutil.rmtree(temp, ignore_errors=True)
        except OSError:
            pass

        return final_path


class StoryVideoCancelled(RuntimeError):
    """Raised when a Story Video cancellation marker is observed."""
