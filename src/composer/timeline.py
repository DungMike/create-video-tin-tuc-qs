import os
import random
from src.utils.logger import logger
from src.config import Config

class TimelineComposer:
    def __init__(self, job_id: str, dirs: dict):
        self.job_id = job_id
        self.temp_dir = dirs['temp']

    def create_timeline(self, vid_clips: list, img_clips: list, audio_duration: float) -> str:
        """Arrange clips alternately to match audio duration and generate concat input file."""
        random.shuffle(vid_clips)
        random.shuffle(img_clips)
        
        timeline_clips = []
        current_duration = 0.0
        
        def _get_clip_duration(clip_path):
            import subprocess
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", clip_path]
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
                return float(res.stdout.strip())
            except:
                return 0.0

        v_idx, i_idx = 0, 0
        use_video = True
        
        while current_duration < audio_duration:
            clip = None
            if use_video and v_idx < len(vid_clips):
                clip = vid_clips[v_idx]
                v_idx += 1
            elif not use_video and i_idx < len(img_clips):
                clip = img_clips[i_idx]
                i_idx += 1
            else:
                if v_idx < len(vid_clips):
                    clip = vid_clips[v_idx]
                    v_idx += 1
                elif i_idx < len(img_clips):
                    clip = img_clips[i_idx]
                    i_idx += 1
                else:
                    logger.warning("Ran out of unique clips! Re-using clips to fill timeline.")
                    v_idx, i_idx = 0, 0 
                    continue
            
            if clip:
                dur = _get_clip_duration(clip)
                if dur > 0:
                    timeline_clips.append(clip)
                    current_duration += dur
            
            use_video = not use_video
            
        logger.info(f"Generated timeline with {len(timeline_clips)} clips. Total duration: {current_duration}s (target: {audio_duration}s)")
        
        concat_file = os.path.join(self.temp_dir, "concat_input.txt")
        with open(concat_file, "w", encoding="utf-8") as f:
            for clip_path in timeline_clips:
                clean_path = os.path.abspath(clip_path).replace("\\", "/")
                f.write(f"file '{clean_path}'\n")
                
        return concat_file
