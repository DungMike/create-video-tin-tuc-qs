import subprocess
import threading
import time
from src.utils.logger import logger
from src.config import Config


def _boost_priority_if_batch_active(pid: int):
    """Bump a freshly-spawned ffmpeg process to Above-Normal OS priority while a
    batch render is active, so it's preferred over any competing process that
    wasn't (or couldn't be) suspended by RenderResourcePriority. Windows-only;
    silently no-ops elsewhere or if anything about this fails -- never worth
    breaking a render over a scheduling hint."""
    if not Config.RENDER_BOOST_FFMPEG_PRIORITY:
        return
    try:
        from src.utils.render_priority import is_batch_render_active

        if not is_batch_render_active():
            return
        import psutil

        psutil.Process(pid).nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass


class FFmpegHelper:
    @staticmethod
    def run_command(
        cmd_list: list,
        timeout_seconds: int | None = None,
        progress_callback=None,
        progress_total_seconds: float | None = None,
        cancel_callback=None,
    ) -> bool:
        """Execute an ffmpeg command synchronously."""
        cmd_str = ' '.join(cmd_list)
        logger.debug(f"Running FFmpeg: {cmd_str}")
        timeout = Config.FFMPEG_COMMAND_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        if timeout is not None and timeout <= 0:
            timeout = None
        if progress_callback:
            return FFmpegHelper._run_command_with_progress(
                cmd_list,
                timeout,
                progress_callback,
                progress_total_seconds,
                cancel_callback,
            )
        try:
            result = subprocess.run(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                logger.error(f"FFmpeg error:\n{result.stderr}")
                return False
            return True
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr or ""
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            logger.error(f"FFmpeg timed out after {timeout}s: {cmd_str}\n{stderr}")
            return False
        except Exception as e:
            logger.error(f"Exception running FFmpeg: {e}")
            return False

    @staticmethod
    def _run_command_with_progress(
        cmd_list: list,
        timeout: int | None,
        progress_callback,
        progress_total_seconds: float | None,
        cancel_callback=None,
    ) -> bool:
        progress_cmd = list(cmd_list)
        if progress_cmd and "ffmpeg" in progress_cmd[0].lower():
            progress_cmd = [progress_cmd[0], "-hide_banner", "-nostats", "-progress", "pipe:1"] + progress_cmd[1:]

        stderr_lines: list[str] = []
        start_time = time.monotonic()
        process = None

        try:
            if cancel_callback and cancel_callback():
                logger.info(f"FFmpeg cancelled before start: {' '.join(cmd_list)}")
                return False

            process = subprocess.Popen(
                progress_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            _boost_priority_if_batch_active(process.pid)

            def _drain_stderr():
                if not process or not process.stderr:
                    return
                for line in process.stderr:
                    stderr_lines.append(line)

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            if process.stdout:
                while True:
                    if cancel_callback and cancel_callback():
                        process.kill()
                        logger.info(f"FFmpeg cancelled: {' '.join(cmd_list)}")
                        return False

                    # Check timeout before each readline
                    if timeout and time.monotonic() - start_time > timeout:
                        process.kill()
                        logger.error(f"FFmpeg timed out after {timeout}s: {' '.join(cmd_list)}")
                        return False

                    line = process.stdout.readline()
                    if not line:
                        # EOF on stdout - process has finished writing
                        break

                    key, separator, value = line.strip().partition("=")
                    if not separator:
                        continue
                    if key == "out_time_ms":
                        try:
                            seconds = max(0.0, int(value) / 1_000_000)
                        except ValueError:
                            continue
                        percent = None
                        if progress_total_seconds and progress_total_seconds > 0:
                            percent = min(100.0, seconds * 100 / progress_total_seconds)
                        progress_callback({"outTimeSeconds": round(seconds, 2), "ffmpegPercent": percent})
                    elif key == "progress" and value == "end":
                        progress_callback({"ffmpegPercent": 100})
                        # Drain remaining stdout without blocking
                        try:
                            process.stdout.read()
                        except Exception:
                            pass
                        break

            # Wait with a reasonable cap to avoid infinite hang
            effective_wait = min(timeout, 300) if timeout else 300
            return_code = process.wait(timeout=effective_wait)
            stderr_thread.join(timeout=2)
            if return_code != 0:
                logger.error(f"FFmpeg error:\n{''.join(stderr_lines)}")
                return False
            return True
        except subprocess.TimeoutExpired:
            if process:
                process.kill()
            logger.error(f"FFmpeg timed out after {timeout}s: {' '.join(cmd_list)}")
            return False
        except Exception as e:
            if process:
                process.kill()
            logger.error(f"Exception running FFmpeg: {e}")
            return False

    @staticmethod
    def get_nvenc_flags() -> list:
        """Return base flags for NVENC encoding if configured."""
        if Config.USE_GPU_NVENC:
            return ["-c:v", "h264_nvenc", "-preset", Config.FFMPEG_PRESET, "-b:v", Config.VIDEO_BITRATE]
        else:
            return ["-c:v", "libx264", "-preset", "fast", "-b:v", Config.VIDEO_BITRATE]

    _cuda_overlay_available: bool | None = None

    @classmethod
    def cuda_overlay_available(cls) -> bool:
        """True when this FFmpeg build exposes the CUDA overlay filters the GPU
        overlay pipeline needs (overlay_cuda/scale_cuda/hwupload_cuda). Probed once
        via `ffmpeg -filters` and cached; safe fallback to False on any error."""
        if cls._cuda_overlay_available is None:
            required = {"overlay_cuda", "scale_cuda", "hwupload_cuda"}
            try:
                result = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-filters"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=15,
                )
                text = f"{result.stdout}\n{result.stderr}"
                cls._cuda_overlay_available = all(name in text for name in required)
            except Exception as exc:
                logger.warning(f"Could not probe ffmpeg for CUDA overlay filters: {exc}")
                cls._cuda_overlay_available = False
        return cls._cuda_overlay_available

    @staticmethod
    def probe_duration(media_path: str) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            media_path,
        ]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return float(result.stdout.strip())
        except Exception as exc:
            logger.warning(f"Could not probe duration for {media_path}: {exc}")
            return 0.0
