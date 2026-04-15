import os
from src.utils.logger import logger
from src.utils.ffmpeg_helper import FFmpegHelper
from src.config import Config

class Renderer:
    def __init__(self, job_id: str, dirs: dict):
        self.job_id = job_id
        self.output_dir = dirs['output']

    def render(self, concat_file: str, audio_path: str, audio_duration: float):
        output_file = os.path.join(self.output_dir, f"video_{self.job_id}.mp4")
        logger.info(f"Starting final render: {output_file}")
        
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", str(audio_duration),
            "-movflags", "+faststart",
            output_file
        ]
        
        if FFmpegHelper.run_command(cmd):
            logger.info("Render completed successfully!")
            return output_file
        else:
            logger.error("Render failed!")
            return None
