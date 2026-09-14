import os
from dotenv import load_dotenv

load_dotenv()

def _numbered_env_keys(name: str, max_slots: int = 32) -> list[str]:
    """Doc NAME, NAME_2, NAME_3, ... NAME_<max_slots> thanh mot list key.

    Slot rong bi bo qua nen danh so thua (chi dien _2 va _5) van chay dung, va
    key trung nhau chi duoc tinh mot lan -- pool xoay vong coi hai key giong het
    nhau la mot quota, dem chung hai lan chi lam no tuong minh con quota.
    """
    values: list[str] = []
    seen: set[str] = set()
    for slot in range(1, max_slots + 1):
        env_name = name if slot == 1 else f"{name}_{slot}"
        value = (os.getenv(env_name) or "").strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values

class Config:
    # Storage root. Every other storage path below derives from this, so pointing
    # STORAGE_DIR at another drive moves the whole storage tree in one step (the
    # code box keeps the repo on C: and the media on D:). Defined first because
    # class-body attributes can only reference names defined above them.
    # Absolute paths are used as-is; a relative value stays relative to the CWD
    # the app is started from.
    STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")

    # FFmpeg / Render
    TARGET_RESOLUTION = os.getenv("TARGET_RESOLUTION", "1920x1080")
    TARGET_FPS = int(os.getenv("TARGET_FPS", "30"))
    USE_GPU_NVENC = os.getenv("USE_GPU_NVENC", "true").lower() == "true"
    FFMPEG_PRESET = os.getenv("FFMPEG_PRESET", "p2")
    VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "8M")
    FFMPEG_COMMAND_TIMEOUT_SECONDS = int(os.getenv("FFMPEG_COMMAND_TIMEOUT_SECONDS", "0"))
    # Sparkle sources are generated once and cached; the slowest preset
    # (shimmer_sweep) runs at ~0.25x realtime, so a 30s loop needs ~2 minutes.
    FFMPEG_STORY_SPARKLE_TIMEOUT_SECONDS = int(os.getenv("FFMPEG_STORY_SPARKLE_TIMEOUT_SECONDS", "900"))
    # Number of story videos to render concurrently within one batch. The render is
    # bound by a single-threaded libavfilter graph (~1.2 cores) with NVENC idle, so
    # running a few in parallel uses the otherwise-idle cores/encoder. Output is
    # identical to sequential. Keep modest on low-core machines (default 2).
    STORY_BATCH_MAX_WORKERS = max(1, int(os.getenv("STORY_BATCH_MAX_WORKERS", "2")))
    # Number of clips to bake (re-encode with a TV style) concurrently when
    # building a pre-styled library. Like the batch render it is filter-bound on
    # CPU with NVENC mostly idle, so a couple in parallel uses spare cores.
    STORY_BAKE_MAX_WORKERS = max(1, int(os.getenv("STORY_BAKE_MAX_WORKERS", "2")))

    # Overlay pass performance tuning
    # Bitrate for overlay output (can be lower than main render bitrate - 4M is sufficient for news)
    OVERLAY_OUTPUT_BITRATE = os.getenv("OVERLAY_OUTPUT_BITRATE", "4M")
    # NVENC preset for overlay pass: p1=fastest, p4=balanced. Use p1 for max speed.
    OVERLAY_NVENC_PRESET = os.getenv("OVERLAY_NVENC_PRESET", "p1")
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

    # Storage (STORAGE_DIR itself is defined at the top of the class)
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", os.path.join(STORAGE_DIR, "output"))

    # Web UI
    WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
    WEB_PORT = int(os.getenv("WEB_PORT", "5000"))
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
    # Per-single-task poll timeout (seconds). If a task stays "running" longer
    # than this, the chunk retries with a fresh API call. Max retries = CHUNK_MAX_RETRY_CYCLES.
    TTS_SINGLE_TASK_TIMEOUT_SECONDS = int(os.getenv("TTS_SINGLE_TASK_TIMEOUT_SECONDS", "60"))
    TTS_SPEED = float(os.getenv("TTS_SPEED", "1"))
    TTS_VOLUME = float(os.getenv("TTS_VOLUME", "1"))

    # Upload types
    ALLOWED_AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "aac", "flac", "ogg"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "webm"}

    # --- Story Video ---
    PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
    PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
    # Pexels tinh quota THEO KEY: 200 request/gio, 20.000/thang. Mot luot quet
    # het mot tu khoa (iter_all_provider_videos) ton toi 100 request, nen voi mot
    # key duy nhat chi hai tu khoa la het quota va moi request sau do tra 429 --
    # dung lam ket qua tai hang loat bi cat cut. Khai bao them key trong .env
    # duoi dang PEXELS_API_KEY_2, _3, ... (toi _32); pool o
    # src/utils/pexels_key_pool.py xoay vong qua chung, key nao dinh 429 thi cho
    # nghi den luc reset va request ke tiep nhay ngay sang key con quota.
    PEXELS_API_KEYS = _numbered_env_keys("PEXELS_API_KEY")
    # Thoi gian cho mot key nghi khi provider khong gui kem X-Ratelimit-Reset /
    # Retry-After. Quota Pexels reset theo gio nen mac dinh la mot gio.
    PEXELS_KEY_COOLDOWN_SECONDS = int(os.getenv("PEXELS_KEY_COOLDOWN_SECONDS", "3600"))
    # Khi CA pool dang nghi: chi ngu toi da bay nhieu giay roi bao loi len caller.
    # Cho ca tieng dong ho trong mot job tai hang loat la treo, khong phai retry.
    PEXELS_POOL_MAX_WAIT_SECONDS = float(os.getenv("PEXELS_POOL_MAX_WAIT_SECONDS", "60"))
    # Length of one library clip / one render segment. Downloads are cut into clips
    # of exactly this length, and the render trims each clip to it (concat outpoint),
    # so a library built at 3s and a render at 3s stay in lockstep. A library can
    # override it via its own `clipDuration` (see story_library.library_clip_duration).
    STORY_CLIP_DURATION = int(os.getenv("STORY_CLIP_DURATION", "3"))
    # Root of the clip libraries (story_library/<library_id>/clips/). This is the
    # hottest read path of a render AND the biggest thing under STORAGE_DIR, so the
    # two pull in opposite directions: it wants the fast disk, it does not fit on it.
    # Overridable on its own for that reason -- before this it was pinned to
    # STORAGE_DIR and the only way to split it off was an NTFS junction per library
    # (C:/storage/story_library/<id> -> E:/...), which still works and stays valid:
    # index.json stores relative_path, and both commonpath guards in
    # src/utils/story_library.py realpath() the root as well as the candidate, so a
    # junction AT LIBRARY LEVEL resolves on both sides. A junction at clips/ level
    # does not -- never place one there (see .env).
    STORY_LIBRARY_DIR = os.getenv("STORY_LIBRARY_DIR", os.path.join(STORAGE_DIR, "story_library"))
    STORY_VIDEO_DIR = os.path.join(STORAGE_DIR, "story_video")
    # Intro-video library: short opening clips prepended to each batch video.
    # Each intro is normalized to the pipeline's canonical output spec on upload.
    STORY_INTRO_DIR = os.path.join(STORY_VIDEO_DIR, "intros")
    STORY_OVERLAY_PACK_DIR = os.path.join(STORAGE_DIR, "story_overlay_packs")
    STORY_OVERLAY_PRECOMPOSE_ENABLED = os.getenv("STORY_OVERLAY_PRECOMPOSE_ENABLED", "true").lower() == "true"
    STORY_OVERLAY_PACK_DURATION_SECONDS = int(os.getenv("STORY_OVERLAY_PACK_DURATION_SECONDS", "80"))
    # Landing area for downloaded/uploaded SOURCE material, before it is cut into
    # library clips. Cold storage: nothing reads it during a render (the render reads
    # story_library/<id>/clips/), it only grows as sources are imported, and it is the
    # single largest thing under STORAGE_DIR. Overridable on its own so the hot render
    # working set can live on a fast small disk while this stays on a big slow one --
    # keeping it on the SSD buys no render speed and just consumes the space.
    STORY_RAW_DIR = os.getenv("STORY_RAW_DIR", os.path.join(STORAGE_DIR, "story_raw_videos"))
    # --- Prefetch branch (download-all first, review/prune, then cut) ---
    # The alternate import flow: instead of previewing provider results over the
    # provider CDN and only downloading what was picked, it pulls every page of a
    # keyword down to STORY_RAW_DIR first so the review happens on local files
    # (instant, and it spends no extra provider API quota).
    STORY_PREFETCH_DOWNLOAD_WORKERS = int(os.getenv("STORY_PREFETCH_DOWNLOAD_WORKERS", "4"))
    # Both 0 = unlimited: "download everything the keyword has" is the point of the
    # flow. They exist as a brake for when a keyword turns out to hold thousands of
    # 1080p videos; the UI's cancel button is the normal way to stop a run.
    STORY_PREFETCH_MAX_VIDEOS = int(os.getenv("STORY_PREFETCH_MAX_VIDEOS", "0"))
    STORY_PREFETCH_MAX_TOTAL_MB = int(os.getenv("STORY_PREFETCH_MAX_TOTAL_MB", "0"))
    # Finished/cancelled prefetch sessions are swept after this long. Nothing else
    # under STORY_RAW_DIR has ever been cleaned up, and this flow downloads whole
    # result sets, so its own staging dirs must not accumulate forever.
    STORY_PREFETCH_TTL_SECONDS = int(os.getenv("STORY_PREFETCH_TTL_SECONDS", "172800"))
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
    STORY_SUBTITLE_DEFAULT_FONT = os.getenv("STORY_SUBTITLE_DEFAULT_FONT", "Arial")

    # --- CRT Effect ---
    CRT_NOISE_STRENGTH = int(os.getenv("CRT_NOISE_STRENGTH", "15"))
    CRT_SCANLINE_OPACITY = float(os.getenv("CRT_SCANLINE_OPACITY", "0.06"))
    CRT_VIGNETTE = os.getenv("CRT_VIGNETTE", "PI/5")
    CRT_COLOR_BLEED = os.getenv("CRT_COLOR_BLEED", "true").lower() == "true"
    CRT_FLICKER = float(os.getenv("CRT_FLICKER", "0.02"))
    CRT_EFFECT_DIR = os.path.join(STORAGE_DIR, "crt_effect")

    # --- Waveform Overlay ---
    WAVEFORM_OVERLAY_DIR = os.path.join(STORAGE_DIR, "waveform_overlays")
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

    # --- Story Decor Image (khung TV: anh nen phu toan khung, video chay trong vung xanh) ---
    # Luu y: khac hoan toan block DECOR_IMAGE_* o tren (banner cua pipeline news-bulletin).
    STORY_DECOR_DIR = os.path.join(STORAGE_DIR, "story_decor_images")
    # Chroma green tieu chuan; moi anh tu do lai mau that lay tu vung xanh luc upload.
    STORY_DECOR_KEY_COLOR = os.getenv("STORY_DECOR_KEY_COLOR", "0x00b140")
    # Nguong hep hon CTA overlay: anh decor la anh chup that, co nhieu mau nam
    # cach mau xanh khong xa (go, da, la cay). similarity+blend > ~0.20 la bat dau
    # duc thung vao phong nen. Vung xanh la mang phang nen khong can nguong rong.
    STORY_DECOR_SIMILARITY = float(os.getenv("STORY_DECOR_SIMILARITY", "0.15"))
    STORY_DECOR_BLEND = float(os.getenv("STORY_DECOR_BLEND", "0.05"))
    # Noi video ra ngoai khung mot chut de vien xanh con sot khong bao gio ho ra.
    STORY_DECOR_OVERSCAN = float(os.getenv("STORY_DECOR_OVERSCAN", "0.01"))

    # --- Story TV Noise Overlay ---
    STORY_TV_NOISE_OVERLAY_DIR = os.path.join(STORAGE_DIR, "story_tv_noise_overlays")
    # Bo clip nguoi dung tai len de xem truoc ca chong hieu ung trong video that.
    STORY_EFFECT_PREVIEW_DIR = os.path.join(STORAGE_DIR, "story_effect_previews")
    STORY_TV_NOISE_TOLERANCE = float(os.getenv("STORY_TV_NOISE_TOLERANCE", "0.08"))
    STORY_TV_NOISE_SOFTNESS = float(os.getenv("STORY_TV_NOISE_SOFTNESS", "0.02"))
    STORY_TV_NOISE_OPACITY = float(os.getenv("STORY_TV_NOISE_OPACITY", "0.35"))
    # blendMode="luma": day mau lop sang ve trang va chuan hoa dinh alpha ve 1.0
    # truoc khi nhan opacity (xem preprocess_tv_noise_overlay).
    STORY_TV_NOISE_LUMA_GAIN = float(os.getenv("STORY_TV_NOISE_LUMA_GAIN", "2.0"))
    STORY_TV_NOISE_DEMO_SECONDS = float(os.getenv("STORY_TV_NOISE_DEMO_SECONDS", "3"))
