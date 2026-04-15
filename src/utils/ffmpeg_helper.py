import subprocess
from src.utils.logger import logger
from src.config import Config

class FFmpegHelper:
    @staticmethod
    def run_command(cmd_list: list) -> bool:
        """Execute an ffmpeg command synchronously."""
        cmd_str = ' '.join(cmd_list)
        logger.debug(f"Running FFmpeg: {cmd_str}")
        try:
            result = subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                logger.error(f"FFmpeg error:\n{result.stderr}")
                return False
            return True
        except Exception as e:
            logger.error(f"Exception running FFmpeg: {e}")
            return False

    @staticmethod
    def get_nvenc_flags() -> list:
        """Return base flags for NVENC encoding if configured."""
        if Config.USE_GPU_NVENC:
            return ["-c:v", "h264_nvenc", "-preset", Config.FFMPEG_PRESET, "-b:v", Config.VIDEO_BITRATE]
        else:
            return ["-c:v", "libx264", "-preset", "fast", "-b:v", Config.VIDEO_BITRATE]
