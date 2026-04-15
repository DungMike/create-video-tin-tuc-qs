import os
import random
from src.utils.logger import logger
from src.utils.ffmpeg_helper import FFmpegHelper
from src.config import Config

class ImageProcessor:
    def __init__(self, job_id: str, dirs: dict):
        self.job_id = job_id
        self.output_dir = dirs['img_clips']
        self.effects = [
            "z='min(zoom+0.0015,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
            "z='max(1.5-0.0015*on,1.001)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
            "x='max(0,iw/2-(iw/zoom/2)-2*on)':y='ih/2-(ih/zoom/2)':z='1.2'",
            "x='min(iw-(iw/zoom),iw/2-(iw/zoom/2)+2*on)':y='ih/2-(ih/zoom/2)':z='1.2'"
        ]

    def process_images(self, image_paths: list) -> list:
        clip_paths = []
        for idx, img_path in enumerate(image_paths):
            logger.info(f"Processing image: {img_path}")
            clip_path = os.path.join(self.output_dir, f"img_clip_{idx}.mp4")
            
            effect = random.choice(self.effects)
            w, h = Config.TARGET_RESOLUTION.split("x")
            
            # Using 2000:-1 as input scale to give enough pixels for zoompan effect before outputting scaled res
            filter_str = f"scale=2000:-1,zoompan={effect}:d={Config.TARGET_FPS * Config.IMG_CLIP_DURATION}:s={Config.TARGET_RESOLUTION}:fps={Config.TARGET_FPS}"
            
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", img_path,
                "-vf", filter_str,
                "-t", str(Config.IMG_CLIP_DURATION)
            ]
            
            cmd.extend(FFmpegHelper.get_nvenc_flags())
            cmd.extend(["-pix_fmt", "yuv420p", clip_path])
            
            if FFmpegHelper.run_command(cmd):
                clip_paths.append(clip_path)
            else:
                logger.error(f"Failed to process image: {img_path}")
                
        return clip_paths
