import subprocess
import os
from src.utils.logger import logger

def get_audio_duration(audio_path: str) -> float:
    """Return the duration of the audio file using ffprobe."""
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path
    ]
    
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Failed to get audio duration for {audio_path}: {e}")
        return 0.0

def validate_audio(audio_path: str) -> bool:
    """Basic validation to check if the file can be read."""
    duration = get_audio_duration(audio_path)
    return duration > 0.0
