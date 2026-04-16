import subprocess
import threading
import time
from src.utils.logger import logger
from src.config import Config

class FFmpegHelper:
    @staticmethod
    def run_command(
        cmd_list: list,
        timeout_seconds: int | None = None,
        progress_callback=None,
        progress_total_seconds: float | None = None,
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
    ) -> bool:
        progress_cmd = list(cmd_list)
        if progress_cmd and "ffmpeg" in progress_cmd[0].lower():
            progress_cmd = [progress_cmd[0], "-hide_banner", "-nostats", "-progress", "pipe:1"] + progress_cmd[1:]

        stderr_lines: list[str] = []
        start_time = time.monotonic()
        process = None

        try:
            process = subprocess.Popen(
                progress_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            def _drain_stderr():
                if not process or not process.stderr:
                    return
                for line in process.stderr:
                    stderr_lines.append(line)

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            if process.stdout:
                for line in process.stdout:
                    if timeout and time.monotonic() - start_time > timeout:
                        process.kill()
                        logger.error(f"FFmpeg timed out after {timeout}s: {' '.join(cmd_list)}")
                        return False

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

            return_code = process.wait(timeout=timeout)
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
