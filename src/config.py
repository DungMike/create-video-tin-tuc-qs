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
    # Number of story videos to render concurrently within one batch. The render is
    # bound by a single-threaded libavfilter graph (~1.2 cores) with NVENC idle, so
    # running a few in parallel uses the otherwise-idle cores/encoder. Output is
    # identical to sequential. Keep modest on low-core machines (default 2).
    STORY_BATCH_MAX_WORKERS = max(1, int(os.getenv("STORY_BATCH_MAX_WORKERS", "2")))
    # Number of clips to bake (re-encode with a TV style) concurrently when
    # building a pre-styled library. Like the batch render it is filter-bound on
    # CPU with NVENC mostly idle, so a couple in parallel uses spare cores.
    STORY_BAKE_MAX_WORKERS = max(1, int(os.getenv("STORY_BAKE_MAX_WORKERS", "2")))
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
    # Use GPU overlay pipeline (overlay_cuda) for the story-video overlay pass.
    # Needs an FFmpeg build exposing overlay_cuda/scale_cuda/hwupload_cuda; this is
    # auto-detected at runtime (FFmpegHelper.cuda_overlay_available). Each render
    # tries the GPU command and falls back to the CPU overlay path if it fails, so
    # enabling this is safe even on builds/machines without working CUDA filters.
    OVERLAY_USE_GPU_PIPELINE = os.getenv("OVERLAY_USE_GPU_PIPELINE", "false").lower() == "true"
    # Split the GPU overlay+subtitle pass into N parallel time-segments (concatenated
    # afterwards). The subtitle burn (libass) is single-threaded and CPU-bound while the
    # GPU sits mostly idle; running several segments at once parallelises libass across
    # cores and fills the GPU. Measured ~2x on a 10-min render at 3 segments (GTX 1060).
    # 1 disables segmentation. Only applied to the GPU overlay path when a subtitle is
    # present and the audio is at least OVERLAY_SEGMENT_MIN_SECONDS long; any failure
    # falls back to the single-pass overlay.
    OVERLAY_PARALLEL_SEGMENTS = int(os.getenv("OVERLAY_PARALLEL_SEGMENTS", "3"))
    OVERLAY_SEGMENT_MIN_SECONDS = float(os.getenv("OVERLAY_SEGMENT_MIN_SECONDS", "90"))
    # Global cap on concurrent overlay-pass ffmpeg processes across the whole app
    # (batch workers x segments). The overlay libass burn is CPU-heavy and single-
    # threaded; more concurrent overlay processes than CPU cores saturates the CPU and
    # starves the GPU. 0 = auto (physical cores - 1). Segments/workers beyond the cap
    # queue rather than thrash.
    OVERLAY_MAX_CONCURRENT = int(os.getenv("OVERLAY_MAX_CONCURRENT", "0"))

    # Comma-separated process names (e.g. anti-detect browser farm tools) that a batch
    # render may suspend to reclaim the CPU. Only takes effect when a batch is started
    # with optimize mode ON (per-batch toggle); they are resumed as soon as that batch
    # ends (success, failure, or cancel). Empty = nothing to suspend even in optimize
    # mode. Chrome Remote Desktop's remoting_host.exe is always excluded regardless of
    # this list, since suspending a remote-access channel could strand a remote operator.
    # See src/utils/render_priority.py; escape hatch: tests/benchmarks/resume_all.py.
    RENDER_SUSPEND_PROCESS_NAMES = os.getenv("RENDER_SUSPEND_PROCESS_NAMES", "")
    # In an optimize-mode batch render, bump spawned ffmpeg processes to Above-Normal
    # OS scheduling priority so the render is preferred over any process that wasn't
    # suspended (e.g. one spawned after the last suspend-scan). Windows-only; no-op
    # elsewhere.
    RENDER_BOOST_FFMPEG_PRIORITY = os.getenv("RENDER_BOOST_FFMPEG_PRIORITY", "true").lower() == "true"

    # Story-library clips must match ALL of TARGET_RESOLUTION + these exactly (pix_fmt,
    # color_range, color_space) to be selected for a render. A concatenated base with
    # mismatched clips can crash the GPU overlay pass mid-stream when NVDEC hits the
    # boundary (filter-graph "Reconfiguring..." event the static CUDA-only filter chain
    # can't bridge) -- excluded clips are simply skipped in favor of another from the
    # pool. Strict by design: defaults are the dominant combo measured on the production
    # library; clips tagged "unknown" or anything else are excluded, not assumed OK.
    # See src/utils/clip_spec_validation.py.
    CLIP_EXPECTED_PIX_FMT = os.getenv("CLIP_EXPECTED_PIX_FMT", "yuv420p")
    CLIP_EXPECTED_COLOR_RANGE = os.getenv("CLIP_EXPECTED_COLOR_RANGE", "tv")
    CLIP_EXPECTED_COLOR_SPACE = os.getenv("CLIP_EXPECTED_COLOR_SPACE", "bt709")
    # Primaries/transfer aren't part of the render's exclusion check, but every clip
    # written by src/utils/clip_canonical.py is tagged with them so the whole library
    # carries one identical VUI. See src/utils/clip_canonical.py.
    CLIP_EXPECTED_COLOR_PRIMARIES = os.getenv("CLIP_EXPECTED_COLOR_PRIMARIES", "bt709")
    CLIP_EXPECTED_COLOR_TRC = os.getenv("CLIP_EXPECTED_COLOR_TRC", "bt709")

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

    # --- Story Video ---
    PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
    PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
    # Length of one library clip / one render segment. Downloads are cut into clips
    # of exactly this length, and the render trims each clip to it (concat outpoint),
    # so a library built at 3s and a render at 3s stay in lockstep. A library can
    # override it via its own `clipDuration` (see story_library.library_clip_duration).
    STORY_CLIP_DURATION = int(os.getenv("STORY_CLIP_DURATION", "3"))
    STORY_LIBRARY_DIR = os.path.join(STORAGE_DIR, "story_library")
    STORY_VIDEO_DIR = os.path.join(STORAGE_DIR, "story_video")
    # Intro-video library: short opening clips prepended to each batch video.
    # Each intro is normalized to the pipeline's canonical output spec on upload.
    STORY_INTRO_DIR = os.path.join(STORY_VIDEO_DIR, "intros")
    STORY_OVERLAY_PACK_DIR = os.path.join(STORAGE_DIR, "story_overlay_packs")
    STORY_OVERLAY_PRECOMPOSE_ENABLED = os.getenv("STORY_OVERLAY_PRECOMPOSE_ENABLED", "true").lower() == "true"
    STORY_OVERLAY_PACK_DURATION_SECONDS = int(os.getenv("STORY_OVERLAY_PACK_DURATION_SECONDS", "80"))
    STORY_RAW_DIR = os.path.join(STORAGE_DIR, "story_raw_videos")
    STORY_LIBRARY_PAGE_SIZE = int(os.getenv("STORY_LIBRARY_PAGE_SIZE", "20"))
    # Multiple named clip libraries ("folders"). The Default library's root IS
    # STORY_LIBRARY_DIR itself (no file migration); other libraries live under
    # STORY_LIBRARY_DIR/<library_id>/. The registry file (libraries.json) is
    # resolved dynamically from STORY_LIBRARY_DIR in src/utils/story_library.py.
    STORY_LIBRARY_DEFAULT_ID = "default"
    STORY_LIBRARY_DEFAULT_NAME = os.getenv("STORY_LIBRARY_DEFAULT_NAME", "Mặc định")
    STORY_DRIVE_AUDIO_IMPORT_DIR = os.path.join(STORY_VIDEO_DIR, "drive_audio_imports")
    STORY_DRIVE_AUDIO_IMPORT_TTL_SECONDS = int(os.getenv("STORY_DRIVE_AUDIO_IMPORT_TTL_SECONDS", "86400"))
    STORY_DRIVE_AUDIO_MAX_FILES = int(os.getenv("STORY_DRIVE_AUDIO_MAX_FILES", "100"))
    STORY_DRIVE_AUDIO_MAX_TOTAL_MB = int(os.getenv("STORY_DRIVE_AUDIO_MAX_TOTAL_MB", "4096"))
    STORY_FONTS_DIR = os.path.join(STORAGE_DIR, "story_fonts")
    STORY_SUBTITLE_PREVIEW_DIR = os.path.join(STORAGE_DIR, "story_subtitle_previews")
    STORY_SUBTITLE_MAX_CHARS_PER_LINE = int(os.getenv("STORY_SUBTITLE_MAX_CHARS_PER_LINE", "42"))
    STORY_SUBTITLE_MAX_LINES = int(os.getenv("STORY_SUBTITLE_MAX_LINES", "2"))
    STORY_SUBTITLE_DEFAULT_FONT = os.getenv("STORY_SUBTITLE_DEFAULT_FONT", "Malgun Gothic")

    # --- CRT Effect ---
    CRT_NOISE_STRENGTH = int(os.getenv("CRT_NOISE_STRENGTH", "15"))
    CRT_SCANLINE_OPACITY = float(os.getenv("CRT_SCANLINE_OPACITY", "0.06"))
    CRT_VIGNETTE = os.getenv("CRT_VIGNETTE", "PI/5")
    CRT_COLOR_BLEED = os.getenv("CRT_COLOR_BLEED", "true").lower() == "true"
    CRT_FLICKER = float(os.getenv("CRT_FLICKER", "0.02"))
    CRT_EFFECT_DIR = os.path.join(STORAGE_DIR, "crt_effect")

    # --- Waveform Overlay ---
    WAVEFORM_OVERLAY_DIR = os.path.join(STORAGE_DIR, "waveform_overlays")
    WAVEFORM_OVERLAY_SCALE = float(os.getenv("WAVEFORM_OVERLAY_SCALE", "0.2"))
    WAVEFORM_OVERLAY_POSITION = os.getenv("WAVEFORM_OVERLAY_POSITION", "bottom_right")
    WAVEFORM_OVERLAY_MARGIN = int(os.getenv("WAVEFORM_OVERLAY_MARGIN", "15"))
    WAVEFORM_OVERLAY_KEY_COLOR = os.getenv("WAVEFORM_OVERLAY_KEY_COLOR", "0x2baa40")
    WAVEFORM_OVERLAY_KEY_SIMILARITY = float(os.getenv("WAVEFORM_OVERLAY_KEY_SIMILARITY", "0.12"))
    WAVEFORM_OVERLAY_KEY_BLEND = float(os.getenv("WAVEFORM_OVERLAY_KEY_BLEND", "0.03"))
    WAVEFORM_OVERLAY_WIDTH = int(os.getenv("WAVEFORM_OVERLAY_WIDTH", "420"))

    # --- Story CTA Overlay (Like/Subscribe/Notification corner decoration) ---
    STORY_CTA_OVERLAY_DIR = os.path.join(STORAGE_DIR, "story_cta_overlays")
    # Bundled default seed video (green-screen buttons). Lives under src/ because storage/ is gitignored.
    STORY_CTA_DEFAULT_VIDEO = os.getenv(
        "STORY_CTA_DEFAULT_VIDEO",
        os.path.join(os.path.dirname(__file__), "assets", "story_cta_default.mp4"),
    )
    STORY_CTA_OVERLAY_POSITION = os.getenv("STORY_CTA_OVERLAY_POSITION", "top_left")
    STORY_CTA_OVERLAY_MARGIN = int(os.getenv("STORY_CTA_OVERLAY_MARGIN", "24"))
    # Actual green of the bundled buttons clip sampled at ~0x1abe26 (R26 G190 B38).
    STORY_CTA_OVERLAY_KEY_COLOR = os.getenv("STORY_CTA_OVERLAY_KEY_COLOR", "0x1abe26")
    STORY_CTA_OVERLAY_KEY_SIMILARITY = float(os.getenv("STORY_CTA_OVERLAY_KEY_SIMILARITY", "0.20"))
    STORY_CTA_OVERLAY_KEY_BLEND = float(os.getenv("STORY_CTA_OVERLAY_KEY_BLEND", "0.10"))
    STORY_CTA_OVERLAY_WIDTH = int(os.getenv("STORY_CTA_OVERLAY_WIDTH", "360"))
    STORY_CTA_OVERLAY_DEFAULT_ENABLED = os.getenv("STORY_CTA_OVERLAY_DEFAULT_ENABLED", "true").lower() == "true"

    # --- Story TV Noise Overlay ---
    STORY_TV_NOISE_OVERLAY_DIR = os.path.join(STORAGE_DIR, "story_tv_noise_overlays")
    STORY_TV_NOISE_TOLERANCE = float(os.getenv("STORY_TV_NOISE_TOLERANCE", "0.08"))
    STORY_TV_NOISE_SOFTNESS = float(os.getenv("STORY_TV_NOISE_SOFTNESS", "0.02"))
    STORY_TV_NOISE_OPACITY = float(os.getenv("STORY_TV_NOISE_OPACITY", "0.35"))
    STORY_TV_NOISE_DEMO_SECONDS = float(os.getenv("STORY_TV_NOISE_DEMO_SECONDS", "3"))
