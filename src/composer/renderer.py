import os
import random
import time
from pathlib import Path

from src.config import Config
from src.utils.decor_videos import get_long_decor_path_validated, get_prescaled_path
from src.utils.effects_library import DEFAULT_TRANSITION_FALLBACK, load_active_transition_presets
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger


class Renderer:
    def __init__(self, job_id: str, dirs: dict, transition_presets: list[dict] | None = None):
        self.job_id = job_id
        self.output_dir = dirs["output"]
        self.temp_dir = dirs["temp"]
        self.progress_callback = None
        self.decor_video_path: str | None = None
        self.source_text_override: str | None = None
        self.transition_presets = list(transition_presets or load_active_transition_presets())
        if not self.transition_presets:
            self.transition_presets = [dict(DEFAULT_TRANSITION_FALLBACK, active=True)]

    def _emit_progress(self, payload: dict):
        if self.progress_callback:
            self.progress_callback(payload)

    def _choose_transition_preset(self) -> dict:
        return random.choice(self.transition_presets)

    def _build_base_video_filter(self, input_index: int, is_prescaled: bool = False) -> str:
        """Build per-input video filter.

        When ``is_prescaled=True`` (e.g. video clips already normalized to
        1920x1080/30fps during clip extraction), we skip the heavy
        scale+crop+fps chain and only fix the timebase / SAR.  This avoids
        double-encoding on every chunk render and significantly reduces CPU/GPU
        load.
        """
        width, height = Config.TARGET_RESOLUTION.split("x")
        if is_prescaled:
            # Clip da duoc scale/crop/fps khi cat (video_processor.py)
            # Chi can fix timebase va SAR, khong can re-scale
            return (
                f"[{input_index}:v]settb=AVTB,setpts=PTS-STARTPTS,"
                f"format=yuv420p,setsar=1[v{input_index}]"
            )
        return (
            f"[{input_index}:v]settb=AVTB,setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={Config.TARGET_FPS},format=yuv420p,setsar=1[v{input_index}]"
        )

    def _group_segments(self, segments: list[dict]) -> list[list[dict]]:
        groups: list[list[dict]] = []
        current_image_group: list[dict] = []

        for segment in segments:
            if segment["kind"] == "image":
                current_image_group.append(segment)
                continue

            if current_image_group:
                groups.append(current_image_group)
                current_image_group = []

            groups.append([segment])

        if current_image_group:
            groups.append(current_image_group)

        return groups

    def _build_filter_complex(self, segments: list[dict]) -> tuple[str, str]:
        # Video clips (kind=video) da duoc pre-scaled khi cat ra, dung filter nhe hon
        filter_parts = [
            self._build_base_video_filter(
                segment["input_index"],
                is_prescaled=(segment.get("kind") == "video"),
            )
            for segment in segments
        ]
        group_output_labels: list[str] = []

        for group_index, group in enumerate(self._group_segments(segments)):
            if len(group) == 1 and group[0]["kind"] == "video":
                group_output_labels.append(f"[v{group[0]['input_index']}]")
                continue

            current_label = f"v{group[0]['input_index']}"
            current_duration = float(group[0]["duration"])

            for next_segment in group[1:]:
                next_label = f"v{next_segment['input_index']}"
                transition = self._choose_transition_preset()
                transition_duration = min(
                    Config.IMAGE_TRANSITION_DURATION,
                    max(current_duration - 0.05, 0.05),
                    max(float(next_segment["duration"]) - 0.05, 0.05),
                )
                offset = max(current_duration - transition_duration, 0.0)
                output_label = f"xfade_{group_index}_{next_segment['input_index']}"
                filter_parts.append(
                    f"[{current_label}][{next_label}]xfade=transition={transition['xfade_transition']}:duration={transition_duration}:offset={offset}[{output_label}]"
                )
                logger.info(
                    f"Applying image transition {transition['id']} between {group[0]['id']} and {next_segment['id']}"
                )
                current_label = output_label
                current_duration = current_duration + float(next_segment["duration"]) - transition_duration

            group_output_labels.append(f"[{current_label}]")

        if len(group_output_labels) == 1:
            final_video_label = group_output_labels[0]
        else:
            final_video_label = "[videoout]"
            filter_parts.append(
                f"{''.join(group_output_labels)}concat=n={len(group_output_labels)}:v=1:a=0{final_video_label}"
            )

        filter_complex = ";".join(filter_parts)
        return filter_complex, final_video_label

    def _filter_script_path(self, output_file: str) -> str:
        stem = Path(output_file).stem
        return os.path.join(self.temp_dir, f"{stem}_filter_complex.txt")

    def _run_video_filter_render(
        self,
        segments: list[dict],
        output_file: str,
        progress_context: dict | None = None,
    ) -> bool:
        indexed_segments = [dict(segment, input_index=index) for index, segment in enumerate(segments)]
        filter_complex, final_video_label = self._build_filter_complex(indexed_segments)
        os.makedirs(self.temp_dir, exist_ok=True)
        filter_script_path = self._filter_script_path(output_file)
        with open(filter_script_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(filter_complex)

        cmd = ["ffmpeg", "-y"]
        for segment in indexed_segments:
            cmd.extend(["-i", segment["path"]])

        cmd.extend(["-filter_complex_script", filter_script_path, "-map", final_video_label])
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(["-pix_fmt", "yuv420p", output_file])

        def _progress(payload: dict):
            if not progress_context:
                return
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._emit_progress(
                {
                    **progress_context,
                    "ffmpegPercent": round(float(ffmpeg_percent), 2),
                    "outTimeSeconds": payload.get("outTimeSeconds"),
                    "message": progress_context.get("message"),
                }
            )

        expected_duration = sum(float(segment["duration"]) for segment in segments)
        return FFmpegHelper.run_command(
            cmd,
            progress_callback=_progress if progress_context else None,
            progress_total_seconds=expected_duration,
        )

    def _run_single_pass_render(self, segments: list[dict], audio_path: str, audio_duration: float, output_file: str) -> bool:
        self._emit_progress(
            {
                "stage": "render_video",
                "currentSegment": 0,
                "totalSegments": len(segments),
                "currentChunk": 0,
                "totalChunks": 1,
                "message": f"Dang render video mot pass voi {len(segments)} segment.",
            }
        )
        indexed_segments = [dict(segment, input_index=index) for index, segment in enumerate(segments)]
        filter_complex, final_video_label = self._build_filter_complex(indexed_segments)
        os.makedirs(self.temp_dir, exist_ok=True)
        filter_script_path = self._filter_script_path(output_file)
        with open(filter_script_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(filter_complex)

        audio_input_index = len(segments)

        cmd = ["ffmpeg", "-y"]
        for segment in indexed_segments:
            cmd.extend(["-i", segment["path"]])

        cmd.extend(
            [
                "-i",
                audio_path,
                "-filter_complex_script",
                filter_script_path,
                "-map",
                final_video_label,
                "-map",
                f"{audio_input_index}:a:0",
            ]
        )
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-t",
                str(audio_duration),
                "-movflags",
                "+faststart",
                "-pix_fmt",
                "yuv420p",
                output_file,
            ]
        )
        ok = FFmpegHelper.run_command(cmd)
        if ok:
            self._emit_progress(
                {
                    "stage": "render_video",
                    "currentSegment": len(segments),
                    "totalSegments": len(segments),
                    "currentChunk": 1,
                    "totalChunks": 1,
                    "message": f"Da render xong {len(segments)}/{len(segments)} segment.",
                }
            )
        return ok

    def _is_image_only(self, segments: list[dict]) -> bool:
        return bool(segments) and all(segment.get("kind") == "image" for segment in segments)

    def _chunk_limit(self, segments: list[dict]) -> int:
        if self._is_image_only(segments):
            return max(1, Config.IMAGE_ONLY_CHUNK_SEGMENT_LIMIT)
        return max(1, Config.RENDER_CHUNK_SEGMENT_LIMIT)

    def _chunk_segments(self, segments: list[dict]) -> list[list[dict]]:
        chunk_size = self._chunk_limit(segments)
        return [segments[index : index + chunk_size] for index in range(0, len(segments), chunk_size)]

    def _concat_single_chunk_with_audio(
        self, chunk_path: str, audio_path: str, audio_duration: float, output_file: str
    ) -> bool:
        return self._concat_chunks_with_audio_copy([chunk_path], audio_path, audio_duration, output_file)

    def _concat_chunks_with_audio_copy(
        self, chunk_paths: list[str], audio_path: str, audio_duration: float, output_file: str
    ) -> bool:
        self._emit_progress(
            {
                "stage": "join_chunks",
                "message": f"Dang noi {len(chunk_paths)} chunk bang stream-copy va gan audio.",
                "currentChunk": len(chunk_paths),
                "totalChunks": len(chunk_paths),
                "ffmpegPercent": 0,
            }
        )
        concat_file = os.path.join(self.temp_dir, "render_chunks.txt")
        os.makedirs(self.temp_dir, exist_ok=True)

        with open(concat_file, "w", encoding="utf-8") as file_obj:
            for chunk_path in chunk_paths:
                clean_path = os.path.abspath(chunk_path).replace("\\", "/").replace("'", "'\\''")
                file_obj.write(f"file '{clean_path}'\n")

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
            output_file,
        ]

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._emit_progress(
                {
                    "stage": "join_chunks",
                    "message": f"Dang noi {len(chunk_paths)} chunk bang stream-copy va gan audio.",
                    "currentChunk": len(chunk_paths),
                    "totalChunks": len(chunk_paths),
                    "ffmpegPercent": round(float(ffmpeg_percent), 2),
                    "outTimeSeconds": payload.get("outTimeSeconds"),
                }
            )

        ok = FFmpegHelper.run_command(cmd, progress_callback=_progress, progress_total_seconds=audio_duration)
        if ok:
            self._emit_progress(
                {
                    "stage": "finalize",
                    "message": "Da noi chunk bang stream-copy va gan audio thanh cong.",
                    "currentChunk": len(chunk_paths),
                    "totalChunks": len(chunk_paths),
                    "ffmpegPercent": 100,
                }
            )
        return ok

    def _xfade_chunks_with_audio(
        self, chunk_paths: list[str], audio_path: str, audio_duration: float, output_file: str
    ) -> bool:
        self._emit_progress(
            {
                "stage": "join_chunks",
                "message": f"Dang noi {len(chunk_paths)} chunk va gan audio.",
                "currentChunk": len(chunk_paths),
                "totalChunks": len(chunk_paths),
            }
        )
        if len(chunk_paths) == 1:
            return self._concat_single_chunk_with_audio(chunk_paths[0], audio_path, audio_duration, output_file)

        # Fix B: Dung stream-copy cho ca mixed media chunks
        # Tat ca chunk da duoc render ra cung codec/resolution/fps (h264_nvenc 1920x1080 30fps)
        # -> stream-copy la an toan va nhanh hon re-encode xfade
        if Config.IMAGE_ONLY_FAST_CHUNK_CONCAT:
            logger.info(
                f"[Renderer] join_chunks: stream-copy {len(chunk_paths)} chunks (IMAGE_ONLY_FAST_CHUNK_CONCAT=true)"
            )
            return self._concat_chunks_with_audio_copy(chunk_paths, audio_path, audio_duration, output_file)

        logger.info(
            f"[Renderer] join_chunks: xfade re-encode {len(chunk_paths)} chunks "
            f"(IMAGE_ONLY_FAST_CHUNK_CONCAT=false)"
        )
        chunk_durations = [FFmpegHelper.probe_duration(path) for path in chunk_paths]
        if any(duration <= 0 for duration in chunk_durations):
            logger.error("Cannot join rendered chunks because one or more chunk durations are invalid.")
            return False

        filter_parts = []
        for index in range(len(chunk_paths)):
            filter_parts.append(f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[chunk{index}]")

        current_label = "chunk0"
        current_duration = chunk_durations[0]
        for index in range(1, len(chunk_paths)):
            transition = self._choose_transition_preset()
            transition_duration = min(
                Config.IMAGE_TRANSITION_DURATION,
                max(current_duration - 0.05, 0.05),
                max(chunk_durations[index] - 0.05, 0.05),
            )
            offset = max(current_duration - transition_duration, 0.0)
            output_label = f"chunk_xfade_{index}"
            filter_parts.append(
                f"[{current_label}][chunk{index}]xfade=transition={transition['xfade_transition']}:duration={transition_duration}:offset={offset}[{output_label}]"
            )
            logger.info(f"Applying chunk transition {transition['id']} before chunk {index + 1}.")
            current_label = output_label
            current_duration = current_duration + chunk_durations[index] - transition_duration

        audio_input_index = len(chunk_paths)
        cmd = ["ffmpeg", "-y"]
        for chunk_path in chunk_paths:
            cmd.extend(["-i", chunk_path])
        cmd.extend(
            [
                "-i",
                audio_path,
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                f"[{current_label}]",
                "-map",
                f"{audio_input_index}:a:0",
            ]
        )
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-t",
                str(audio_duration),
                "-movflags",
                "+faststart",
                "-pix_fmt",
                "yuv420p",
                output_file,
            ]
        )
        ok = FFmpegHelper.run_command(cmd)
        if ok:
            self._emit_progress(
                {
                    "stage": "finalize",
                    "message": "Da noi chunk va gan audio thanh cong.",
                    "currentChunk": len(chunk_paths),
                    "totalChunks": len(chunk_paths),
                }
            )
        return ok

    def _run_chunked_render(self, segments: list[dict], audio_path: str, audio_duration: float, output_file: str) -> bool:
        chunks = self._chunk_segments(segments)
        chunk_paths = []
        os.makedirs(self.temp_dir, exist_ok=True)
        chunk_limit = self._chunk_limit(segments)
        logger.info(
            f"Rendering {len(segments)} timeline segments in {len(chunks)} chunks "
            f"of up to {chunk_limit} segments."
        )

        for chunk_index, chunk in enumerate(chunks):
            chunk_path = os.path.join(self.temp_dir, f"render_chunk_{chunk_index:04d}.mp4")
            if Path(chunk_path).exists():
                os.remove(chunk_path)
            logger.info(f"Rendering chunk {chunk_index + 1}/{len(chunks)} with {len(chunk)} segments.")
            segment_start = sum(len(previous_chunk) for previous_chunk in chunks[:chunk_index])
            self._emit_progress(
                {
                    "stage": "render_chunks",
                    "currentChunk": chunk_index,
                    "totalChunks": len(chunks),
                    "currentSegment": segment_start,
                    "totalSegments": len(segments),
                    "message": f"Dang render chunk {chunk_index + 1}/{len(chunks)} ({len(chunk)} segment).",
                }
            )
            progress_context = {
                "stage": "render_chunks",
                "currentChunk": chunk_index,
                "totalChunks": len(chunks),
                "currentSegment": segment_start,
                "totalSegments": len(segments),
                "message": f"Dang render chunk {chunk_index + 1}/{len(chunks)} ({len(chunk)} segment).",
            }
            t_chunk = time.monotonic()
            if not self._run_video_filter_render(chunk, chunk_path, progress_context=progress_context):
                logger.error(f"Render failed while creating chunk {chunk_index + 1}/{len(chunks)}.")
                self._emit_progress(
                    {
                        "stage": "render_chunks",
                        "currentChunk": chunk_index + 1,
                        "totalChunks": len(chunks),
                        "currentSegment": min(segment_start + len(chunk), len(segments)),
                        "totalSegments": len(segments),
                        "message": f"Render chunk {chunk_index + 1}/{len(chunks)} that bai.",
                        "level": "error",
                    }
                )
                return False
            chunk_elapsed = (time.monotonic() - t_chunk) * 1000
            logger.info(
                f"[Renderer] chunk {chunk_index + 1}/{len(chunks)} done in {chunk_elapsed:.0f}ms "
                f"({len(chunk)} segments, path={chunk_path})"
            )
            chunk_paths.append(chunk_path)
            self._emit_progress(
                {
                    "stage": "render_chunks",
                    "currentChunk": chunk_index + 1,
                    "totalChunks": len(chunks),
                    "currentSegment": min(segment_start + len(chunk), len(segments)),
                    "totalSegments": len(segments),
                    "message": f"Da render xong chunk {chunk_index + 1}/{len(chunks)} ({chunk_elapsed:.0f}ms).",
                }
            )

        t_join = time.monotonic()
        ok = self._xfade_chunks_with_audio(chunk_paths, audio_path, audio_duration, output_file)
        logger.info(
            f"[Renderer] join_chunks done in {(time.monotonic()-t_join)*1000:.0f}ms, "
            f"total_chunks={len(chunk_paths)}"
        )
        return ok

    def _render_fast(
        self,
        segments: list[dict],
        audio_path: str,
        audio_duration: float,
        output_file: str,
    ) -> bool:
        """Fast render: concat all pre-rendered clips (image or video) via stream-copy + audio mux.

        Each clip is already configured with identical resolution, framerate, and codec.
        This method strictly performs a stream-copy to avoid FFmpeg buffering issues when
        concatenating hundreds of files into a complex filter graph.
        """
        logger.info(
            f"Fast render: concat {len(segments)} clips via stream-copy + audio mux."
        )
        self._emit_progress(
            {
                "stage": "render_video",
                "currentSegment": 0,
                "totalSegments": len(segments),
                "currentChunk": 0,
                "totalChunks": 1,
                "message": f"Fast render: noi {len(segments)} clip (stream-copy).",
            }
        )

        os.makedirs(self.temp_dir, exist_ok=True)
        concat_file = os.path.join(self.temp_dir, "render_fast_concat.txt")
        with open(concat_file, "w", encoding="utf-8") as file_obj:
            for segment in segments:
                clean_path = os.path.abspath(segment["path"]).replace("\\", "/").replace("'", "'\\''")
                file_obj.write(f"file '{clean_path}'\n")

        # --- Build the ffmpeg command ---
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file]
        cmd.extend(["-i", audio_path])
        
        # We exclusively use stream-copy here to avoid the massive slow-down 
        # caused by applying filter_complex directly on top of 500+ concat files.
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"])

        cmd.extend(["-t", str(audio_duration), "-movflags", "+faststart", output_file])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._emit_progress(
                {
                    "stage": "render_video",
                    "currentSegment": len(segments),
                    "totalSegments": len(segments),
                    "currentChunk": 1,
                    "totalChunks": 1,
                    "ffmpegPercent": round(float(ffmpeg_percent), 2),
                    "outTimeSeconds": payload.get("outTimeSeconds"),
                    "message": f"Fast render: dang noi clip va gan audio...",
                }
            )

        ok = FFmpegHelper.run_command(cmd, progress_callback=_progress, progress_total_seconds=audio_duration)
        if ok:
            self._emit_progress(
                {
                    "stage": "finalize",
                    "currentSegment": len(segments),
                    "totalSegments": len(segments),
                    "currentChunk": 1,
                    "totalChunks": 1,
                    "ffmpegPercent": 100,
                    "message": "Fast render hoan tat thanh cong.",
                }
            )
        return ok

    # ------------------------------------------------------------------ #
    #  Overlay helpers (PiP video + source text watermark)               #
    # ------------------------------------------------------------------ #

    def _has_overlay_video(self) -> bool:
        """Check if video decor (PiP) overlay is available AND enabled.

        ENABLE_OVERLAY only controls this PiP video overlay, not the source text.
        """
        if not Config.ENABLE_OVERLAY:
            return False
        path = self.decor_video_path
        if not path:
            return False
        if not os.path.isfile(path):
            logger.warning(f"Decor video path not found: {path}")
            return False
        return True

    def _has_source_text(self) -> bool:
        """Source text is always shown when SOURCE_TEXT is configured,
        regardless of ENABLE_OVERLAY setting.

        Uses per-item override if set, otherwise falls back to global config.
        """
        text = self.source_text_override if self.source_text_override is not None else Config.SOURCE_TEXT
        return bool(text)

    def _has_overlays(self) -> bool:
        """Returns True if any overlay (PiP video or source text) needs to be applied."""
        return self._has_overlay_video() or self._has_source_text()

    @staticmethod
    def _escape_drawtext_value(value: str) -> str:
        """Escape a string for use as a drawtext filter option value.

        FFmpeg drawtext requires escaping of ``:``, ``\\``, and ``'``
        within option values.
        """
        return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    def _prepare_font(self) -> str:
        """Copy the configured font file into the temp dir with a safe name.

        Returns the absolute path to the copy (no spaces, no Unicode, no
        drive-letter colons inside filter strings needed).
        """
        import shutil

        src = os.path.abspath(Config.SOURCE_TEXT_FONT)
        os.makedirs(self.temp_dir, exist_ok=True)
        dest = os.path.join(self.temp_dir, "font.ttf")
        if not os.path.isfile(dest):
            shutil.copy2(src, dest)
        return dest

    def _build_overlay_filter(
        self,
        has_pip: bool,
        has_text: bool,
        pip_input_idx: int | None = None,
        pip_is_prescaled: bool = False,
    ) -> tuple[str, str]:
        """Build a ``filter_complex`` string for PiP overlay and/or source text.

        Returns ``(filter_string, output_video_label)``.

        When ``pip_is_prescaled`` is True the PiP input has already been scaled
        to the target overlay width so the expensive per-frame ``scale`` filter
        is skipped.

        Static overlay and drawtext filters use ``eval=init`` so that FFmpeg
        computes position coordinates only once instead of per-frame.
        """
        filter_statements: list[str] = []
        chain: list[str] = []

        # --- PiP overlay scaling + positioning ---
        if has_pip and pip_input_idx is not None:
            if pip_is_prescaled:
                # Decor video already at correct size – only fix PTS
                filter_statements.append(
                    f"[{pip_input_idx}:v]setpts=PTS-STARTPTS[pip]"
                )
            else:
                width = int(Config.TARGET_RESOLUTION.split("x")[0])
                overlay_w = int(width * Config.OVERLAY_VIDEO_SCALE)
                overlay_w += overlay_w % 2  # ensure even for H.264
                filter_statements.append(
                    f"[{pip_input_idx}:v]scale={overlay_w}:-2,setpts=PTS-STARTPTS[pip]"
                )

            margin = Config.OVERLAY_VIDEO_MARGIN
            positions = {
                "top_left": (str(margin), str(margin)),
                "top_right": (f"W-w-{margin}", str(margin)),
                "bottom_left": (str(margin), f"H-h-{margin}"),
                "bottom_right": (f"W-w-{margin}", f"H-h-{margin}"),
            }
            x, y = positions.get(Config.OVERLAY_VIDEO_POSITION, positions["top_right"])
            # eval=init: position is static, compute once instead of per-frame
            chain.append(f"overlay={x}:{y}:eof_action=repeat:eval=init")

        # --- Source text (drawtext) ---
        if has_text:
            effective_source_text = self.source_text_override if self.source_text_override is not None else Config.SOURCE_TEXT
            escaped_text = self._escape_drawtext_value(effective_source_text)
            font_path = self._prepare_font()
            # Use forward slashes; the font copy lives in the temp dir
            # which is under the project root (no spaces in the temp subpath).
            font_path_fwd = font_path.replace("\\", "/")

            margin = Config.SOURCE_TEXT_MARGIN
            text_positions = {
                "bottom_left": (str(margin), f"h-th-{margin}"),
                "bottom_right": (f"w-tw-{margin}", f"h-th-{margin}"),
                "top_left": (str(margin), str(margin)),
                "top_right": (f"w-tw-{margin}", str(margin)),
            }
            tx, ty = text_positions.get(Config.SOURCE_TEXT_POSITION, text_positions["bottom_left"])
            chain.append(
                f"drawtext=text='{escaped_text}':"
                f"fontfile='{font_path_fwd}':"
                f"fontsize={Config.SOURCE_TEXT_FONT_SIZE}:fontcolor=white:"
                f"borderw=2:bordercolor=black:x={tx}:y={ty}"
            )

        # --- Assemble ---
        chain_str = ",".join(chain)
        if has_pip:
            filter_statements.append(f"[0:v][pip]{chain_str}[outv]")
        else:
            filter_statements.append(f"[0:v]{chain_str}[outv]")

        return ";".join(filter_statements), "[outv]"

    def _build_overlay_cmd(
        self,
        input_video: str,
        audio_duration: float,
        output_file: str,
        use_hwaccel: bool = False,
    ) -> list[str]:
        """Build the FFmpeg command list for overlay post-processing."""
        has_pip = self._has_overlay_video()
        has_text = self._has_source_text()

        cmd = ["ffmpeg", "-y"]

        if use_hwaccel:
            cmd.extend(["-hwaccel", "cuda"])

        cmd.extend(["-i", input_video])
        pip_input_idx = None
        pip_is_prescaled = False

        if has_pip:
            # Priority 1: long decor (61-min, pre-scaled 480p) – no loop, no scale
            #   Validated: must be >= audio_duration to avoid silent disappearance
            long_decor = get_long_decor_path_validated(
                self.decor_video_path, min_duration=audio_duration
            )
            # Priority 2: short pre-scaled copy – still needs loop
            prescaled = get_prescaled_path(self.decor_video_path)

            if long_decor:
                logger.info(f"Using long pre-scaled decor video (no loop): {long_decor}")
                cmd.extend(["-i", long_decor])
                pip_is_prescaled = True
            elif prescaled:
                logger.info(f"Using pre-scaled decor video (with loop): {prescaled}")
                cmd.extend(["-stream_loop", "-1", "-i", prescaled])
                pip_is_prescaled = True
            else:
                logger.info("Using original decor video (with loop + runtime scale)")
                cmd.extend(["-stream_loop", "-1", "-i", self.decor_video_path])
            pip_input_idx = 1

        filter_str, video_label = self._build_overlay_filter(
            has_pip, has_text, pip_input_idx, pip_is_prescaled=pip_is_prescaled,
        )
        logger.info(f"Overlay post-process filter_complex: {filter_str}")

        cmd.extend(["-filter_complex", filter_str, "-map", video_label, "-map", "0:a:0"])
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(
            ["-c:a", "copy", "-t", str(audio_duration), "-movflags", "+faststart", "-pix_fmt", "yuv420p", output_file]
        )
        return cmd

    def _apply_overlays(self, input_video: str, audio_duration: float, output_file: str) -> bool:
        """Post-process step: apply PiP overlay + source text to an already rendered video.

        Performance optimisations applied here:
        1. NVDEC hardware decoding (``-hwaccel cuda``) for the base video input
           to offload H.264 decode from CPU to GPU.
        2. Pre-scaled decor video is used when available, eliminating the
           per-frame ``scale`` filter for the PiP input.
        3. ``eval=init`` on overlay/drawtext filters (set in
           ``_build_overlay_filter``) so position coordinates are computed
           only once instead of per-frame.

        If ``-hwaccel cuda`` causes FFmpeg to fail (some GPU/driver combos
        are incompatible with CPU-only filters like overlay/drawtext), the
        method automatically retries without hardware decode acceleration.
        """
        logger.info("Applying overlay post-processing to rendered video.")
        self._emit_progress(
            {
                "stage": "overlay",
                "message": "Dang ap dung overlay (PiP / text watermark)...",
                "ffmpegPercent": 0,
            }
        )

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._emit_progress(
                {
                    "stage": "overlay",
                    "ffmpegPercent": round(float(ffmpeg_percent), 2),
                    "outTimeSeconds": payload.get("outTimeSeconds"),
                    "message": "Dang ap dung overlay...",
                }
            )

        # Attempt 1: try with NVDEC hardware decode if GPU is enabled
        if Config.USE_GPU_NVENC:
            cmd_hwaccel = self._build_overlay_cmd(input_video, audio_duration, output_file, use_hwaccel=True)
            logger.info("[Overlay] Attempt 1: with -hwaccel cuda")
            ok = FFmpegHelper.run_command(cmd_hwaccel, progress_callback=_progress, progress_total_seconds=audio_duration)
            if ok:
                self._emit_progress({"stage": "overlay", "ffmpegPercent": 100, "message": "Overlay hoan tat."})
                return True
            logger.warning("[Overlay] -hwaccel cuda failed. Retrying without hardware decode...")

        # Attempt 2 (fallback): CPU decode only
        cmd_cpu = self._build_overlay_cmd(input_video, audio_duration, output_file, use_hwaccel=False)
        logger.info("[Overlay] Attempt (fallback): CPU decode only")
        ok = FFmpegHelper.run_command(cmd_cpu, progress_callback=_progress, progress_total_seconds=audio_duration)
        if ok:
            self._emit_progress({"stage": "overlay", "ffmpegPercent": 100, "message": "Overlay hoan tat."})
        else:
            logger.error("Overlay post-processing failed.")
        return ok

    # ------------------------------------------------------------------ #
    #  Output filename helper                                            #
    # ------------------------------------------------------------------ #

    def _output_filename(self, audio_path: str) -> str:
        """Derive the output video filename.

        When ``OUTPUT_USE_AUDIO_FILENAME`` is enabled, the output file is named
        after the uploaded audio. Otherwise the legacy ``video_<job_id>.mp4``
        naming is used.
        """
        if Config.OUTPUT_USE_AUDIO_FILENAME and audio_path:
            audio_basename = os.path.splitext(os.path.basename(audio_path))[0]
            # Strip the "audio_" prefix added by the upload handler
            if audio_basename.startswith("audio_"):
                audio_basename = audio_basename[len("audio_"):]
            if audio_basename:
                return os.path.join(self.output_dir, f"{audio_basename}.mp4")
        return os.path.join(self.output_dir, f"video_{self.job_id}.mp4")

    # ------------------------------------------------------------------ #
    #  Public entry point                                                #
    # ------------------------------------------------------------------ #

    def render(
        self,
        timeline_data: dict,
        audio_path: str,
        audio_duration: float,
        progress_callback=None,
        decor_video_path: str | None = None,
        source_text_override: str | None = None,
        pre_overlay_callback=None,
    ):
        self.progress_callback = progress_callback
        self.decor_video_path = decor_video_path
        self.source_text_override = source_text_override
        output_file = self._output_filename(audio_path)
        segments = list(timeline_data.get("segments", []))

        if not segments:
            logger.error("Render requested with empty timeline.")
            return None

        logger.info(f"Starting final render with {len(segments)} segments: {output_file}")

        has_overlays = self._has_overlays()

        target = output_file
        if has_overlays:
            target = os.path.join(self.temp_dir, f"pre_overlay_{self.job_id}.mp4")
            os.makedirs(self.temp_dir, exist_ok=True)

        if Config.IMAGE_ONLY_SKIP_XFADE:
            # Fast path: stream-copy concat. Overlays are applied in a second pass if needed.
            render_ok = self._render_fast(segments, audio_path, audio_duration, target)
        else:
            # Chunked/Single pass path
            if len(segments) > self._chunk_limit(segments):
                render_ok = self._run_chunked_render(segments, audio_path, audio_duration, target)
            else:
                render_ok = self._run_single_pass_render(segments, audio_path, audio_duration, target)

        if render_ok and pre_overlay_callback:
            pre_overlay_callback(target)

        if render_ok and has_overlays:
            render_ok = self._apply_overlays(target, audio_duration, output_file)

        if render_ok:
            logger.info("Render completed successfully!")
            return output_file

        logger.error("Render failed!")
        return None
