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


def _load_story_library_index() -> dict:
    index_path = os.path.join(Config.STORY_LIBRARY_DIR, "index.json")
    data = _load_json(index_path)
    if not data:
        return {"assets": []}
    return data


class StoryVideoPipelineRunner:
    """Runs the simple story video pipeline for a single story."""

    def __init__(self, story_id: str, config_dict: dict):
        self.story_id = story_id
        self.input_type = config_dict.get("input_type", "script_url")
        self.input_value = config_dict.get("input_value", "")
        self.output_name = config_dict.get("output_name", "")
        self.clip_tags = config_dict.get("clip_tags", [])
        self.voice_id = config_dict.get("voice_id", "")
        self.waveform_overlay_id = str(config_dict.get("waveform_overlay_id", "") or "").strip()

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
            self._update_progress("prepare_audio", 5, "Dang chuan bi audio...")

            audio_path = self._prepare_audio()
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
            if not clips:
                self._update_progress(
                    "select_clips",
                    15,
                    "Khong tim thay clip nao trong thu vien.",
                    status="failed",
                    error="No clips available in story library.",
                )
                return None

            self._update_progress("render_video", 35, "Dang ghep clip voi audio...")
            rendered_video = self._render_simple_video(clips, audio_path, audio_duration)
            if not rendered_video:
                self._update_progress(
                    "render_video",
                    35,
                    "Ghep video voi audio that bai.",
                    status="failed",
                    error="Video render failed.",
                )
                return None

            self._update_progress("story_overlays", 90, "Dang ap dung TV noise va song am...")
            output_video = self._apply_story_overlays(rendered_video, audio_duration)
            if not output_video:
                return None

            self._update_progress("finalize", 98, "Dang hoan tat...")
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
        index = _load_story_library_index()
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
            clip_path = os.path.join(Config.STORY_LIBRARY_DIR, rel_path)
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
        )
        if ok and os.path.isfile(output_path):
            return output_path
        return None

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
        )
        if ok and os.path.isfile(output_path):
            return output_path

        logger.error(f"[StoryPipeline:{self.story_id}] Failed to apply waveform overlay.")
        self._update_progress(
            "waveform_overlay",
            90,
            "Ap dung song am that bai.",
            status="failed",
            error="Waveform overlay failed.",
        )
        return None

    def _apply_story_overlays(self, current_video: str, audio_duration: float) -> str | None:
        """Overlay TV noise layers and the configured waveform in a single FFmpeg pass."""
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

        tv_noise_paths: list[tuple[dict, str]] = []
        for record in tv_noise_records:
            processed_path = tv_noise_processed_abs_path(record)
            if processed_path:
                tv_noise_paths.append((record, processed_path))

        if not tv_noise_paths and not waveform_path:
            logger.info(f"[StoryPipeline:{self.story_id}] No story overlays configured.")
            return current_video

        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"story_overlays_{self.story_id}.mp4")
        cmd = ["ffmpeg", "-y", "-i", current_video]

        for _record, overlay_path in tv_noise_paths:
            cmd.extend(["-stream_loop", "-1", "-i", overlay_path])

        waveform_input_index = None
        if waveform_path:
            waveform_input_index = 1 + len(tv_noise_paths)
            cmd.extend(["-stream_loop", "-1", "-i", waveform_path])

        filter_parts: list[str] = []
        chain_label = "[0:v]"
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

        filter_parts.append(f"{chain_label}format=yuv420p[v]")

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
        )
        if ok and os.path.isfile(output_path):
            return output_path

        logger.error(f"[StoryPipeline:{self.story_id}] Failed to apply story overlays.")
        self._update_progress(
            "story_overlays",
            90,
            "Ap dung TV noise/song am that bai.",
            status="failed",
            error="Story overlay pass failed.",
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
