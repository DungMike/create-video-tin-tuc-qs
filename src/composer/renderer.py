import os
import random
from pathlib import Path

from src.config import Config
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
        self.transition_presets = list(transition_presets or load_active_transition_presets())
        if not self.transition_presets:
            self.transition_presets = [dict(DEFAULT_TRANSITION_FALLBACK, active=True)]

    def _emit_progress(self, payload: dict):
        if self.progress_callback:
            self.progress_callback(payload)

    def _choose_transition_preset(self) -> dict:
        return random.choice(self.transition_presets)

    def _build_base_video_filter(self, input_index: int) -> str:
        width, height = Config.TARGET_RESOLUTION.split("x")
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
        filter_parts = [self._build_base_video_filter(segment["input_index"]) for segment in segments]
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
        audio_input_index = len(segments)

        cmd = ["ffmpeg", "-y"]
        for segment in indexed_segments:
            cmd.extend(["-i", segment["path"]])

        cmd.extend(
            [
                "-i",
                audio_path,
                "-filter_complex",
                filter_complex,
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
        if Config.IMAGE_ONLY_FAST_CHUNK_CONCAT:
            return self._concat_chunks_with_audio_copy(chunk_paths, audio_path, audio_duration, output_file)

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
            chunk_paths.append(chunk_path)
            self._emit_progress(
                {
                    "stage": "render_chunks",
                    "currentChunk": chunk_index + 1,
                    "totalChunks": len(chunks),
                    "currentSegment": min(segment_start + len(chunk), len(segments)),
                    "totalSegments": len(segments),
                    "message": f"Da render xong chunk {chunk_index + 1}/{len(chunks)}.",
                }
            )

        return self._xfade_chunks_with_audio(chunk_paths, audio_path, audio_duration, output_file)

    def _render_image_only_fast(
        self,
        segments: list[dict],
        audio_path: str,
        audio_duration: float,
        output_file: str,
    ) -> bool:
        """Fast image-only render: concat all pre-rendered clips + optional overlays + audio mux.

        Each clip already contains fade-in / fade-out baked in during the
        motion-clip generation step, so no xfade re-encoding is needed.
        When overlays (PiP video / source text) are configured, a filter_complex
        with NVENC encode is used instead of stream-copy.
        """
        has_pip = self._has_overlay_video()
        has_text = self._has_source_text()
        has_overlays = has_pip or has_text

        mode_label = "stream-copy" if not has_overlays else "filter+nvenc (overlay)"
        logger.info(
            f"Fast image-only render: concat {len(segments)} clips via {mode_label} + audio mux."
        )
        self._emit_progress(
            {
                "stage": "render_video",
                "currentSegment": 0,
                "totalSegments": len(segments),
                "currentChunk": 0,
                "totalChunks": 1,
                "message": f"Fast render: noi {len(segments)} clip ({mode_label}).",
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

        if has_pip:
            cmd.extend(["-stream_loop", "-1", "-i", self.decor_video_path])

        cmd.extend(["-i", audio_path])
        audio_idx = 1 + (1 if has_pip else 0)

        if has_overlays:
            pip_input_idx = 1 if has_pip else None
            filter_str, video_label = self._build_overlay_filter(has_pip, has_text, pip_input_idx)
            logger.info(f"Overlay filter_complex: {filter_str}")
            cmd.extend(["-filter_complex", filter_str, "-map", video_label, "-map", f"{audio_idx}:a:0"])
            cmd.extend(FFmpegHelper.get_nvenc_flags())
            cmd.extend(["-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p"])
        else:
            cmd.extend(["-map", "0:v:0", "-map", f"{audio_idx}:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"])

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
        path = self.decor_video_path
        if not path:
            return False
        if not os.path.isfile(path):
            logger.warning(f"Decor video path not found: {path}")
            return False
        return True

    def _has_source_text(self) -> bool:
        return bool(Config.SOURCE_TEXT)

    def _has_overlays(self) -> bool:
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
    ) -> tuple[str, str]:
        """Build a ``filter_complex`` string for PiP overlay and/or source text.

        Returns ``(filter_string, output_video_label)``.
        """
        filter_statements: list[str] = []
        chain: list[str] = []

        # --- PiP overlay scaling + positioning ---
        if has_pip and pip_input_idx is not None:
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
            chain.append(f"overlay={x}:{y}:eof_action=pass")

        # --- Source text (drawtext) ---
        if has_text:
            escaped_text = self._escape_drawtext_value(Config.SOURCE_TEXT)
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

    def _apply_overlays(self, input_video: str, audio_duration: float, output_file: str) -> bool:
        """Post-process step: apply PiP overlay + source text to an already rendered video."""
        has_pip = self._has_overlay_video()
        has_text = self._has_source_text()

        logger.info("Applying overlay post-processing to rendered video.")
        self._emit_progress(
            {
                "stage": "overlay",
                "message": "Dang ap dung overlay (PiP / text watermark)...",
                "ffmpegPercent": 0,
            }
        )

        cmd = ["ffmpeg", "-y", "-i", input_video]
        pip_input_idx = None

        if has_pip:
            cmd.extend(["-stream_loop", "-1", "-i", self.decor_video_path])
            pip_input_idx = 1

        filter_str, video_label = self._build_overlay_filter(has_pip, has_text, pip_input_idx)
        logger.info(f"Overlay post-process filter_complex: {filter_str}")

        cmd.extend(["-filter_complex", filter_str, "-map", video_label, "-map", "0:a:0"])
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(
            ["-c:a", "copy", "-t", str(audio_duration), "-movflags", "+faststart", "-pix_fmt", "yuv420p", output_file]
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

        ok = FFmpegHelper.run_command(cmd, progress_callback=_progress, progress_total_seconds=audio_duration)
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
    ):
        self.progress_callback = progress_callback
        self.decor_video_path = decor_video_path
        output_file = self._output_filename(audio_path)
        segments = list(timeline_data.get("segments", []))

        if not segments:
            logger.error("Render requested with empty timeline.")
            return None

        logger.info(f"Starting final render with {len(segments)} segments: {output_file}")

        has_overlays = self._has_overlays()

        if self._is_image_only(segments) and Config.IMAGE_ONLY_SKIP_XFADE:
            # Fast path handles overlays inline (no extra step needed).
            render_ok = self._render_image_only_fast(segments, audio_path, audio_duration, output_file)
        else:
            # Other paths: render to temp if overlays are needed, then post-process.
            target = output_file
            if has_overlays:
                target = os.path.join(self.temp_dir, f"pre_overlay_{self.job_id}.mp4")
                os.makedirs(self.temp_dir, exist_ok=True)

            if self._is_image_only(segments) and len(segments) > self._chunk_limit(segments):
                render_ok = self._run_chunked_render(segments, audio_path, audio_duration, target)
            else:
                render_ok = self._run_single_pass_render(segments, audio_path, audio_duration, target)

            if render_ok and has_overlays:
                render_ok = self._apply_overlays(target, audio_duration, output_file)

        if render_ok:
            logger.info("Render completed successfully!")
            return output_file

        logger.error("Render failed!")
        return None

