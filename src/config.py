import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Material Limits
    MIN_VIDEO_CLIPS = int(os.getenv("MIN_VIDEO_CLIPS", "30"))
    MIN_IMAGE_CLIPS = int(os.getenv("MIN_IMAGE_CLIPS", "30"))

    # Timeline Control
    VID_CLIP_MIN_DURATION = float(os.getenv("VID_CLIP_MIN_DURATION", "5"))
    VID_CLIP_MAX_DURATION = float(os.getenv("VID_CLIP_MAX_DURATION", "5"))
    IMG_CLIP_DURATION = int(os.getenv("IMG_CLIP_DURATION", "6"))
    REVIEW_CLIP_DURATION = float(os.getenv("REVIEW_CLIP_DURATION", "6"))
    # News Bulletin: minimum display duration for resume (headline) segments.
    # Short TTS headlines are padded with silence to ensure the banner has time to animate fully.
    RESUME_MIN_DURATION = float(os.getenv("RESUME_MIN_DURATION", "4.5"))
    # Silence appended to the end of each resume segment for a clean banner fade-out before next segment.
    RESUME_SILENCE_PAD = float(os.getenv("RESUME_SILENCE_PAD", "0.4"))
    REVIEW_PAGE_SIZE = int(os.getenv("REVIEW_PAGE_SIZE", "30"))
    IMAGE_TRANSITION_DURATION = float(os.getenv("IMAGE_TRANSITION_DURATION", "0.75"))
    EFFECT_PREVIEW_CLIP_DURATION = float(os.getenv("EFFECT_PREVIEW_CLIP_DURATION", "2.25"))
    RENDER_CHUNK_SEGMENT_LIMIT = int(os.getenv("RENDER_CHUNK_SEGMENT_LIMIT", "40"))
    IMAGE_ONLY_CHUNK_SEGMENT_LIMIT = int(os.getenv("IMAGE_ONLY_CHUNK_SEGMENT_LIMIT", "80"))

    # FFmpeg / Render
    TARGET_RESOLUTION = os.getenv("TARGET_RESOLUTION", "1920x1080")
    TARGET_FPS = int(os.getenv("TARGET_FPS", "30"))
    USE_GPU_NVENC = os.getenv("USE_GPU_NVENC", "true").lower() == "true"
    FFMPEG_PRESET = os.getenv("FFMPEG_PRESET", "p2")
    VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "8M")
    FFMPEG_COMMAND_TIMEOUT_SECONDS = int(os.getenv("FFMPEG_COMMAND_TIMEOUT_SECONDS", "0"))
    FFMPEG_IMAGE_CLIP_TIMEOUT_SECONDS = int(os.getenv("FFMPEG_IMAGE_CLIP_TIMEOUT_SECONDS", "120"))
    IMAGE_MOTION_CACHE_ENABLED = os.getenv("IMAGE_MOTION_CACHE_ENABLED", "true").lower() == "true"
    IMAGE_MOTION_WORKERS = int(os.getenv("IMAGE_MOTION_WORKERS", "4"))
    IMAGE_ONLY_FAST_CHUNK_CONCAT = os.getenv("IMAGE_ONLY_FAST_CHUNK_CONCAT", "true").lower() == "true"
    IMAGE_ONLY_SKIP_XFADE = os.getenv("IMAGE_ONLY_SKIP_XFADE", "true").lower() == "true"
    IMAGE_CLIP_FADE_DURATION = float(os.getenv("IMAGE_CLIP_FADE_DURATION", "0.5"))
    # Zoom factor for video clips: 1.0 = no zoom, 1.23 = 123% (zoom in, crop edges)
    VID_CLIP_ZOOM_FACTOR = float(os.getenv("VID_CLIP_ZOOM_FACTOR", "1.23"))

    # Overlay / Watermark
    ENABLE_OVERLAY = os.getenv("ENABLE_OVERLAY", "true").lower() == "true"
    OVERLAY_VIDEO_POSITION = os.getenv("OVERLAY_VIDEO_POSITION", "top_right")
    OVERLAY_VIDEO_SCALE = float(os.getenv("OVERLAY_VIDEO_SCALE", "0.25"))
    OVERLAY_VIDEO_MARGIN = int(os.getenv("OVERLAY_VIDEO_MARGIN", "10"))
    SOURCE_TEXT = os.getenv("SOURCE_TEXT", "")
    SOURCE_TEXT_FONT_SIZE = int(os.getenv("SOURCE_TEXT_FONT_SIZE", "22"))

    @classmethod
    def get_source_text_options(cls) -> dict[str, str]:
        """Collect all SOURCE_TEXT_N options from environment.

        Scans for SOURCE_TEXT_1, SOURCE_TEXT_2, ... and returns a dict
        like {"SOURCE_TEXT_1": "Nguồn: Tổng hợp", "SOURCE_TEXT_2": "..."}.
        Falls back to the global SOURCE_TEXT if no numbered options exist.
        """
        options: dict[str, str] = {}
        for key, value in os.environ.items():
            if key.startswith("SOURCE_TEXT_") and key[len("SOURCE_TEXT_"):].isdigit() and value.strip():
                options[key] = value.strip()
        if not options and cls.SOURCE_TEXT:
            options["SOURCE_TEXT_1"] = cls.SOURCE_TEXT
        return dict(sorted(options.items()))
    SOURCE_TEXT_FONT = os.getenv("SOURCE_TEXT_FONT", "C:/Windows/Fonts/arial.ttf")
    SOURCE_TEXT_POSITION = os.getenv("SOURCE_TEXT_POSITION", "bottom_left")
    SOURCE_TEXT_MARGIN = int(os.getenv("SOURCE_TEXT_MARGIN", "20"))
    # Overlay pass performance tuning
    # Bitrate for overlay output (can be lower than main render bitrate - 4M is sufficient for news)
    OVERLAY_OUTPUT_BITRATE = os.getenv("OVERLAY_OUTPUT_BITRATE", "4M")
    # NVENC preset for overlay pass: p1=fastest, p4=balanced. Use p1 for max speed.
    OVERLAY_NVENC_PRESET = os.getenv("OVERLAY_NVENC_PRESET", "p1")
    # CPU threads for overlay filter pass (0=auto). More threads = faster overlay.
    OVERLAY_CPU_THREADS = int(os.getenv("OVERLAY_CPU_THREADS", "0"))
    # Use GPU full-pipeline (overlay_cuda): requires FFmpeg libnpp support
    # Set false if overlay_cuda returns 'Function not implemented'
    OVERLAY_USE_GPU_PIPELINE = os.getenv("OVERLAY_USE_GPU_PIPELINE", "false").lower() == "true"

    # Decor Image Overlay (banner phía dưới video kèm tiêu đề tin)
    DECOR_IMAGE_ENABLED = os.getenv("DECOR_IMAGE_ENABLED", "true").lower() == "true"
    DECOR_IMAGE_WIDTH = int(os.getenv("DECOR_IMAGE_WIDTH", "1920"))
    DECOR_IMAGE_HEIGHT = int(os.getenv("DECOR_IMAGE_HEIGHT", "300"))
    DECOR_IMAGE_FADE_DURATION = float(os.getenv("DECOR_IMAGE_FADE_DURATION", "0.3"))
    DECOR_IMAGE_ANIM_DURATION = float(os.getenv("DECOR_IMAGE_ANIM_DURATION", os.getenv("DECOR_IMAGE_FADE_DURATION", "0.3")))
    DECOR_IMAGE_ANIMATION = os.getenv("DECOR_IMAGE_ANIMATION", "slide_up_fade")
    DECOR_IMAGE_TITLE_FONT = os.getenv("DECOR_IMAGE_TITLE_FONT", os.getenv("SOURCE_TEXT_FONT", "C:/Windows/Fonts/arial.ttf"))
    DECOR_IMAGE_TITLE_FONT_SIZE = int(os.getenv("DECOR_IMAGE_TITLE_FONT_SIZE", "36"))
    DECOR_IMAGE_TITLE_COLOR = os.getenv("DECOR_IMAGE_TITLE_COLOR", "white")
    DECOR_IMAGE_TITLE_MAX_LENGTH = int(os.getenv("DECOR_IMAGE_TITLE_MAX_LENGTH", "80"))
    DECOR_IMAGE_DETAIL_GAP = float(os.getenv("DECOR_IMAGE_DETAIL_GAP", "0.2"))
    DECOR_IMAGE_GAP_SECONDS = float(os.getenv("DECOR_IMAGE_GAP_SECONDS", os.getenv("DECOR_IMAGE_DETAIL_GAP", "0.2")))
    DECOR_IMAGES_DIR = os.getenv("DECOR_IMAGES_DIR", "./storage/channels/decor_images")

    # Output
    OUTPUT_USE_AUDIO_FILENAME = os.getenv("OUTPUT_USE_AUDIO_FILENAME", "true").lower() == "true"

    # Crawler
    YOUTUBE_DOWNLOAD_LIMIT_PER_KEYWORD = int(os.getenv("YOUTUBE_DOWNLOAD_LIMIT_PER_KEYWORD", "3"))
    IMAGES_TO_DOWNLOAD_PER_KEYWORD = int(os.getenv("IMAGES_TO_DOWNLOAD_PER_KEYWORD", "10"))
    MAX_SOURCE_VIDEO_DURATION = int(os.getenv("MAX_SOURCE_VIDEO_DURATION", "600"))
    CLIPS_PER_SOURCE_VIDEO = int(os.getenv("CLIPS_PER_SOURCE_VIDEO", "4"))

    # Storage
    STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./storage/output")
    EFFECTS_LIBRARY_DIR = os.getenv("EFFECTS_LIBRARY_DIR", "./storage/effects_library")
    DECOR_VIDEOS_DIR = os.getenv("DECOR_VIDEOS_DIR", "./storage/decor_videos")
    BATCH_RETRY_RETENTION_SECONDS = int(os.getenv("BATCH_RETRY_RETENTION_SECONDS", "86400"))

    # Web UI
    WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
    WEB_PORT = int(os.getenv("WEB_PORT", "5000"))
    FRONTEND_HOST = os.getenv("FRONTEND_HOST", "127.0.0.1")
    FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "5173"))
    FRONTEND_DIST_DIR = os.getenv("FRONTEND_DIST_DIR", "./frontend/dist")
    WEB_SECRET_KEY = os.getenv("WEB_SECRET_KEY", "local-dev-secret")
    MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "4096"))

    # TTS / Voice API
    TTS_API_BASE_URL = os.getenv("TTS_API_BASE_URL", "https://thangtm.info")
    TTS_API_KEY = os.getenv("TTS_API_KEY", "")
    TTS_PLATFORM = os.getenv("TTS_PLATFORM", "minimax")
    TTS_DEFAULT_VOICE_ID = os.getenv("TTS_DEFAULT_VOICE_ID", "")
    TTS_MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "2000"))
    TTS_MAX_CONCURRENCY = int(os.getenv("TTS_MAX_CONCURRENCY", "5"))
    TTS_POLL_INTERVAL_SECONDS = float(os.getenv("TTS_POLL_INTERVAL_SECONDS", "2"))
    TTS_TASK_TIMEOUT_SECONDS = int(os.getenv("TTS_TASK_TIMEOUT_SECONDS", "600"))
    # Per-single-task poll timeout (seconds). If a task stays "running" longer
    # than this, the chunk retries with a fresh API call. Max retries = CHUNK_MAX_RETRY_CYCLES.
    TTS_SINGLE_TASK_TIMEOUT_SECONDS = int(os.getenv("TTS_SINGLE_TASK_TIMEOUT_SECONDS", "60"))
    TTS_SPEED = float(os.getenv("TTS_SPEED", "1"))
    TTS_VOLUME = float(os.getenv("TTS_VOLUME", "1"))

    # Upload types
    ALLOWED_AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "aac", "flac", "ogg"}
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "webm"}
