import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Material Limits
    MIN_VIDEO_CLIPS = int(os.getenv("MIN_VIDEO_CLIPS", "30"))
    MIN_IMAGE_CLIPS = int(os.getenv("MIN_IMAGE_CLIPS", "30"))

    # Timeline Control
    VID_CLIP_MIN_DURATION = int(os.getenv("VID_CLIP_MIN_DURATION", "3"))
    VID_CLIP_MAX_DURATION = int(os.getenv("VID_CLIP_MAX_DURATION", "10"))
    IMG_CLIP_DURATION = int(os.getenv("IMG_CLIP_DURATION", "6"))
    REVIEW_CLIP_DURATION = int(os.getenv("REVIEW_CLIP_DURATION", "6"))
    REVIEW_PAGE_SIZE = int(os.getenv("REVIEW_PAGE_SIZE", "30"))

    # FFmpeg / Render
    TARGET_RESOLUTION = os.getenv("TARGET_RESOLUTION", "1920x1080")
    TARGET_FPS = int(os.getenv("TARGET_FPS", "30"))
    USE_GPU_NVENC = os.getenv("USE_GPU_NVENC", "true").lower() == "true"
    FFMPEG_PRESET = os.getenv("FFMPEG_PRESET", "p4")
    VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "8M")

    # Crawler
    YOUTUBE_DOWNLOAD_LIMIT_PER_KEYWORD = int(os.getenv("YOUTUBE_DOWNLOAD_LIMIT_PER_KEYWORD", "3"))
    IMAGES_TO_DOWNLOAD_PER_KEYWORD = int(os.getenv("IMAGES_TO_DOWNLOAD_PER_KEYWORD", "10"))
    MAX_SOURCE_VIDEO_DURATION = int(os.getenv("MAX_SOURCE_VIDEO_DURATION", "600"))
    CLIPS_PER_SOURCE_VIDEO = int(os.getenv("CLIPS_PER_SOURCE_VIDEO", "4"))

    # Storage
    STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./storage/output")

    # Web UI
    WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
    WEB_PORT = int(os.getenv("WEB_PORT", "5000"))
    FRONTEND_HOST = os.getenv("FRONTEND_HOST", "127.0.0.1")
    FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "5173"))
    FRONTEND_DIST_DIR = os.getenv("FRONTEND_DIST_DIR", "./frontend/dist")
    WEB_SECRET_KEY = os.getenv("WEB_SECRET_KEY", "local-dev-secret")
    MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "4096"))

    # Upload types
    ALLOWED_AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "aac", "flac", "ogg"}
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "webm"}
