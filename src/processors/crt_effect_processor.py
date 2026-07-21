import json
import os
import threading

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

# ---------------------------------------------------------------------------
# TV effect styles — moi style la mot bo THAM SO, chuoi filter duoc sinh ra
# boi build_tv_effect_chain(). Nho do nguoi dung co the tuy chinh tung thong
# so (custom) va preview truoc khi dung.
#
# HIEU NANG (do thuc te tren GTX 1060 + Xeon E3, 1080p30):
# - eq/chromashift/hue/noise gan nhu mien phi (>10x realtime).
# - drawgrid (~1.3x) va vignette (~3x) la hai filter dat nhat o full-res.
# - Vi vay chuoi style duoc chay o 960x540 roi scale len TARGET_RESOLUTION
#   (build_tv_effect_filter): nhanh ~3 lan, do mem khi upscale lai dung
#   chat anh analog/VHS. Thong so dich pixel (chromaShift, scanlines) tinh
#   theo khung 960x540.
# ---------------------------------------------------------------------------
TV_EFFECT_PARAM_SPEC = {
    "tone": {"type": "choice", "options": ["none", "warm", "cool", "vintage", "sepia", "bw", "fade"], "default": "none"},
    "saturation": {"min": 0.0, "max": 2.0, "default": 1.0},
    "contrast": {"min": 0.5, "max": 1.5, "default": 1.0},
    "brightness": {"min": -0.3, "max": 0.3, "default": 0.0},
    "gamma": {"min": 0.5, "max": 1.5, "default": 1.0},
    "noise": {"min": 0, "max": 30, "default": 0},
    "chromaShift": {"min": 0, "max": 8, "default": 0},
    "scanlines": {"min": 0.0, "max": 0.3, "default": 0.0},
    "vignette": {"min": 0.0, "max": 1.0, "default": 0.0},
    "flicker": {"min": 0.0, "max": 0.08, "default": 0.0},
    "flickerSpeed": {"min": 0.5, "max": 15.0, "default": 3.0},
    "soften": {"min": 0.0, "max": 1.0, "default": 0.0},
}

# Flicker mac dinh duoc giu thap (<=0.02, toc do cham 2.5-5Hz) — bien do lon /
# tan so cao gay kho chiu khi xem video dai (phan hoi nguoi dung).
TV_EFFECT_STYLES = [
    {
        "id": "none",
        "name": "Không hiệu ứng",
        "description": "Giữ nguyên màu sắc video gốc, chỉ áp dụng overlay.",
        "params": {},
    },
    # --- Nhom nhieu TV / analog ---
    {
        "id": "vhs_1990",
        "name": "VHS 1990",
        "description": "Băng từ VHS: màu nhạt, chroma bleed đỏ/xanh, hạt nhiễu, scanline mờ và viền tối.",
        "params": {"saturation": 0.85, "contrast": 1.05, "brightness": 0.01, "chromaShift": 2, "noise": 8, "scanlines": 0.05, "vignette": 0.2},
    },
    {
        "id": "crt_arcade",
        "name": "CRT Arcade",
        "description": "Màn hình CRT bóng đèn: scanline rõ, màu đậm, nhấp nháy chậm rất nhẹ.",
        "params": {"saturation": 1.12, "contrast": 1.1, "gamma": 0.97, "noise": 5, "scanlines": 0.12, "vignette": 0.5, "flicker": 0.012, "flickerSpeed": 4},
    },
    {
        "id": "vhs_tracking",
        "name": "VHS Tracking Hỏng",
        "description": "Băng VHS lỗi tracking: chroma lệch mạnh, nhiễu dày, sáng tối dao động chậm.",
        "params": {"saturation": 0.72, "contrast": 1.1, "chromaShift": 4, "noise": 13, "scanlines": 0.08, "vignette": 0.5, "flicker": 0.02, "flickerSpeed": 2.5},
    },
    {
        "id": "broadcast_90s",
        "name": "Truyền Hình 90s",
        "description": "Tín hiệu truyền hình analog nhẹ: màu hơi rực, chroma bleed mảnh, nhiễu kín đáo.",
        "params": {"saturation": 1.08, "contrast": 1.05, "chromaShift": 1, "noise": 4, "scanlines": 0.03, "vignette": 0.15},
    },
    {
        "id": "bw_static",
        "name": "Đen Trắng Nhiễu",
        "description": "TV đen trắng sóng yếu: khử màu, tương phản cao, nhiễu hạt dày.",
        "params": {"tone": "bw", "contrast": 1.18, "brightness": 0.02, "noise": 12, "vignette": 0.5, "flicker": 0.015, "flickerSpeed": 5},
    },
    # --- Nhom ke chuyen / doi song (nhe nhang, khong flicker) ---
    {
        "id": "vhs_soft",
        "name": "VHS Êm",
        "description": "Chất VHS rất nhẹ cho video kể chuyện: hạt mịn, màu trầm vừa phải, không nhấp nháy.",
        "params": {"saturation": 0.9, "contrast": 1.03, "chromaShift": 1, "noise": 5, "scanlines": 0.03, "vignette": 0.25},
    },
    {
        "id": "retro_film",
        "name": "Retro Film Ấm",
        "description": "Phim cũ thập niên 90: tông màu vintage ấm, hạt film nhẹ, viền tối mềm.",
        "params": {"tone": "vintage", "saturation": 0.9, "noise": 6, "vignette": 0.5},
    },
    {
        "id": "golden_memories",
        "name": "Ký Ức Vàng",
        "description": "Hoài niệm tông vàng ấm: sáng nhẹ, hạt mịn, hơi mềm — hợp chuyện gia đình, tuổi thơ.",
        "params": {"tone": "warm", "saturation": 1.05, "contrast": 1.04, "brightness": 0.02, "noise": 5, "vignette": 0.4, "soften": 0.3},
    },
    {
        "id": "dreamy_haze",
        "name": "Hồi Ức Mơ Màng",
        "description": "Mềm và sáng như giấc mơ: blur nhẹ toàn khung, tông ấm nhạt — hợp đoạn hồi tưởng.",
        "params": {"tone": "warm", "saturation": 0.92, "contrast": 0.97, "brightness": 0.05, "noise": 4, "vignette": 0.3, "soften": 0.7},
    },
    {
        "id": "cold_night",
        "name": "Đêm Lạnh",
        "description": "Tông xanh lạnh, tương phản nhỉnh, viền tối sâu — hợp chuyện buồn, kịch tính, bí ẩn.",
        "params": {"tone": "cool", "saturation": 0.85, "contrast": 1.08, "noise": 7, "vignette": 0.6},
    },
    {
        "id": "old_documentary",
        "name": "Tài Liệu Cũ",
        "description": "Sepia kiểu tư liệu xưa: nâu trầm, hạt rõ, scanline thoáng — hợp chuyện quá khứ, lịch sử.",
        "params": {"tone": "sepia", "contrast": 1.06, "noise": 9, "scanlines": 0.04, "vignette": 0.5},
    },
    {
        "id": "film_noir",
        "name": "Noir Tương Phản",
        "description": "Đen trắng điện ảnh tương phản cao, viền tối đậm — nhấn mạnh cảm xúc, gay cấn.",
        "params": {"tone": "bw", "contrast": 1.3, "brightness": -0.02, "noise": 8, "vignette": 0.7},
    },
    {
        "id": "cinematic_fade",
        "name": "Điện Ảnh Trầm",
        "description": "Màu fade kiểu phim hiện đại: đen không sâu hẳn, màu dịu, hạt cực mịn — trung tính, dễ xem dài.",
        "params": {"tone": "fade", "saturation": 0.9, "noise": 4, "vignette": 0.35},
    },
    # --- Nhom moods ke chuyen mo rong (2026-07) ---
    {
        "id": "mystic_fog",
        "name": "Sương Khói Bí Ẩn",
        "description": "Lạnh, mềm và mờ ảo như phủ sương — hợp chuyện bí ẩn, creepypasta, tâm linh.",
        "params": {"tone": "cool", "saturation": 0.8, "contrast": 1.02, "noise": 5, "vignette": 0.6, "soften": 0.5},
    },
    {
        "id": "horror_night",
        "name": "Kinh Dị Đêm Tối",
        "description": "Tối, tương phản gắt, viền đen sâu, nhiễu rõ và nhấp nháy chậm — hợp chuyện kinh dị.",
        "params": {"saturation": 0.75, "contrast": 1.15, "brightness": -0.06, "noise": 10, "vignette": 0.85, "flicker": 0.015, "flickerSpeed": 2.5},
    },
    {
        "id": "pastel_dream",
        "name": "Giấc Mơ Pastel",
        "description": "Sáng, màu nhạt và mềm mại — hợp chuyện nhẹ nhàng, chữa lành, thiếu nhi.",
        "params": {"saturation": 0.8, "contrast": 0.92, "brightness": 0.06, "gamma": 1.05, "noise": 3, "vignette": 0.2, "soften": 0.6},
    },
    {
        "id": "film_8mm",
        "name": "Phim 8mm",
        "description": "Phim gia đình 8mm: màu vintage, hạt dày, viền tối và chớp sáng chậm — hợp hồi ký, ký ức xa.",
        "params": {"tone": "vintage", "saturation": 0.85, "contrast": 1.04, "noise": 14, "vignette": 0.6, "flicker": 0.018, "flickerSpeed": 2},
    },
    {
        "id": "digital_glitch",
        "name": "Glitch Kỹ Thuật Số",
        "description": "Chroma lệch mạnh, scanline dày, nhấp nháy nhanh — hợp sci-fi, chuyện công nghệ, analog horror.",
        "params": {"saturation": 1.05, "contrast": 1.08, "chromaShift": 6, "noise": 6, "scanlines": 0.15, "vignette": 0.3, "flicker": 0.05, "flickerSpeed": 12},
    },
    {
        "id": "sunset_nostalgia",
        "name": "Hoàng Hôn Hoài Niệm",
        "description": "Ấm rực như nắng chiều, viền tối nhẹ — hợp chuyện tình, thanh xuân, tiếc nuối.",
        "params": {"tone": "warm", "saturation": 1.1, "contrast": 1.03, "brightness": 0.02, "noise": 4, "vignette": 0.45},
    },
    {
        "id": "frozen_blue",
        "name": "Băng Giá",
        "description": "Xanh lạnh sâu, màu rút bớt, tương phản nhỉnh — hợp chuyện buồn, cô đơn, mùa đông.",
        "params": {"tone": "cool", "saturation": 0.7, "contrast": 1.1, "brightness": -0.02, "noise": 5, "vignette": 0.55},
    },
    {
        "id": "sepia_letter",
        "name": "Sepia Thư Cũ",
        "description": "Nâu sepia mềm như trang thư ố vàng — hợp đọc thư, nhật ký, chuyện kể lại.",
        "params": {"tone": "sepia", "contrast": 1.02, "brightness": 0.02, "noise": 5, "vignette": 0.45, "soften": 0.4},
    },
]

_STYLE_PROCESS_WIDTH = 960
_STYLE_PROCESS_HEIGHT = 540

_TONE_FILTERS = {
    "warm": "colortemperature=temperature=5000",
    "cool": "colortemperature=temperature=9500,colorbalance=bs=0.12:bm=0.06",
    "vintage": "curves=preset=vintage",
    "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
    "bw": "hue=s=0",
    "fade": "curves=all='0/0.06 0.5/0.5 1/0.96'",
}


def sanitize_tv_effect_params(params: dict | None) -> dict:
    """Clamp/normalize a params dict against TV_EFFECT_PARAM_SPEC."""
    params = params if isinstance(params, dict) else {}
    cleaned: dict = {}
    for key, spec in TV_EFFECT_PARAM_SPEC.items():
        raw = params.get(key, spec["default"])
        if spec.get("type") == "choice":
            cleaned[key] = raw if raw in spec["options"] else spec["default"]
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = float(spec["default"])
        cleaned[key] = min(spec["max"], max(spec["min"], value))
    return cleaned


def build_tv_effect_chain(params: dict | None) -> str:
    """Build the core filter chain (no scaling) from effect params."""
    p = sanitize_tv_effect_params(params)
    parts: list[str] = []

    tone_filter = _TONE_FILTERS.get(p["tone"])
    if tone_filter:
        parts.append(tone_filter)

    eq_opts: list[str] = []
    if abs(p["saturation"] - 1.0) > 0.001:
        eq_opts.append(f"saturation={p['saturation']:.3g}")
    if abs(p["contrast"] - 1.0) > 0.001:
        eq_opts.append(f"contrast={p['contrast']:.3g}")
    if abs(p["gamma"] - 1.0) > 0.001:
        eq_opts.append(f"gamma={p['gamma']:.3g}")
    if p["flicker"] > 0.0005:
        expr = f"{p['brightness']:.3g}+{p['flicker']:.3g}*sin(2*PI*t*{p['flickerSpeed']:.3g})"
        eq_opts.append(f"brightness='{expr}'")
        eq_opts.append("eval=frame")
    elif abs(p["brightness"]) > 0.001:
        eq_opts.append(f"brightness={p['brightness']:.3g}")
    if eq_opts:
        parts.append("eq=" + ":".join(eq_opts))

    chroma = int(round(p["chromaShift"]))
    if chroma > 0:
        parts.append(f"chromashift=cbh={chroma}:crh=-{chroma}")

    noise = int(round(p["noise"]))
    if noise > 0:
        parts.append(f"noise=alls={noise}:allf=t")

    if p["soften"] > 0.05:
        amount = 0.3 + 0.7 * p["soften"]
        parts.append(f"unsharp=5:5:-{amount:.2f}:5:5:0")

    if p["scanlines"] > 0.005:
        parts.append(f"drawgrid=w=iw:h=2:t=1:c=black@{p['scanlines']:.3g}")

    if p["vignette"] > 0.01:
        # 0..1 -> PI/6 (nhe) .. PI/3 (dam)
        angle = 0.5236 + 0.5236 * p["vignette"]
        parts.append(f"vignette=angle={angle:.4f}")

    return ",".join(parts)


def _wrap_fast_chain(chain: str, out_width: int, out_height: int) -> str:
    if not chain:
        return ""
    return (
        f"scale={_STYLE_PROCESS_WIDTH}:{_STYLE_PROCESS_HEIGHT},"
        f"{chain},"
        f"scale={out_width}:{out_height}:flags=bilinear"
    )


def build_tv_effect_filter(style_id: str, out_width: int, out_height: int) -> str:
    """Render-ready chain for a builtin style (half-res fast path)."""
    style = get_tv_effect_style(style_id)
    if not style:
        return ""
    return _wrap_fast_chain(build_tv_effect_chain(style["params"]), out_width, out_height)


def build_custom_tv_effect_filter(params: dict | None, out_width: int, out_height: int) -> str:
    """Render-ready chain for user-customized params (half-res fast path)."""
    return _wrap_fast_chain(build_tv_effect_chain(params), out_width, out_height)

_TV_EFFECT_CONFIG_LOCK = threading.Lock()


def _tv_effect_config_path() -> str:
    return os.path.join(Config.CRT_EFFECT_DIR, "tv_effect_config.json")


def tv_effect_previews_dir() -> str:
    path = os.path.join(Config.CRT_EFFECT_DIR, "tv_effect_previews")
    os.makedirs(path, exist_ok=True)
    return path


def get_tv_effect_style(style_id: str) -> dict | None:
    return next((style for style in TV_EFFECT_STYLES if style["id"] == style_id), None)


CUSTOM_STYLE_ID = "custom"


def _load_tv_effect_config_unlocked() -> dict:
    path = _tv_effect_config_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Cannot read TV effect config: {exc}")
    return {}


def _save_tv_effect_config_unlocked(data: dict):
    os.makedirs(Config.CRT_EFFECT_DIR, exist_ok=True)
    with open(_tv_effect_config_path(), "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)


def _is_valid_style_id(style_id: str) -> bool:
    return style_id == CUSTOM_STYLE_ID or get_tv_effect_style(style_id) is not None


def get_selected_tv_effect_style_id() -> str:
    with _TV_EFFECT_CONFIG_LOCK:
        style_id = str(_load_tv_effect_config_unlocked().get("selectedStyleId", "")).strip()
    return style_id if _is_valid_style_id(style_id) else "none"


def set_selected_tv_effect_style_id(style_id: str) -> bool:
    if not _is_valid_style_id(style_id):
        return False
    with _TV_EFFECT_CONFIG_LOCK:
        data = _load_tv_effect_config_unlocked()
        data["selectedStyleId"] = style_id
        _save_tv_effect_config_unlocked(data)
    return True


def get_custom_tv_effect_params() -> dict:
    with _TV_EFFECT_CONFIG_LOCK:
        params = _load_tv_effect_config_unlocked().get("customParams")
    return sanitize_tv_effect_params(params)


def set_custom_tv_effect_params(params: dict | None) -> dict:
    cleaned = sanitize_tv_effect_params(params)
    with _TV_EFFECT_CONFIG_LOCK:
        data = _load_tv_effect_config_unlocked()
        data["customParams"] = cleaned
        _save_tv_effect_config_unlocked(data)
    return cleaned


def _target_resolution() -> tuple[int, int]:
    try:
        width, height = (int(value) for value in Config.TARGET_RESOLUTION.split("x", 1))
        return width, height
    except (ValueError, AttributeError):
        return 1920, 1080


def get_tv_effect_filter(style_id: str | None = None) -> str:
    """Return the render-ready filter chain for a style id (or the selected style).

    Supports the special id ``custom`` (uses the saved custom params). The
    chain ends with a scale back to ``Config.TARGET_RESOLUTION`` so it can be
    inserted before overlay steps without changing frame geometry.
    """
    if style_id is None or not _is_valid_style_id(style_id):
        style_id = get_selected_tv_effect_style_id()
    width, height = _target_resolution()
    if style_id == CUSTOM_STYLE_ID:
        return build_custom_tv_effect_filter(get_custom_tv_effect_params(), width, height)
    return build_tv_effect_filter(style_id, width, height)


def generate_tv_effect_style_preview(
    style_id: str,
    sample_video_path: str,
    duration: float = 4.0,
    custom_params: dict | None = None,
) -> str:
    """Render a short preview clip for a style (NVDEC decode + NVENC encode).

    When ``custom_params`` is given the preview uses those params instead of a
    builtin style (output file ``custom.mp4``). Returns the absolute output
    path, or "" on failure.
    """
    if custom_params is not None:
        style_id = CUSTOM_STYLE_ID
    elif style_id != CUSTOM_STYLE_ID and not get_tv_effect_style(style_id):
        logger.error(f"Unknown TV effect style: {style_id}")
        return ""
    if not os.path.isfile(sample_video_path):
        logger.error(f"TV effect preview sample not found: {sample_video_path}")
        return ""

    duration = min(5.0, max(3.0, float(duration)))
    output_path = os.path.join(tv_effect_previews_dir(), f"{style_id}.mp4")

    # Preview at 720p so the render stays in the 2-4 second range on a GTX 1060.
    if style_id == CUSTOM_STYLE_ID:
        params = custom_params if custom_params is not None else get_custom_tv_effect_params()
        chain = build_custom_tv_effect_filter(params, 1280, 720)
    else:
        chain = build_tv_effect_filter(style_id, 1280, 720)
    vf = f"{chain},format=yuv420p" if chain else "scale=1280:-2,format=yuv420p"

    def _build_cmd(use_hwaccel: bool) -> list:
        cmd = ["ffmpeg", "-y"]
        if use_hwaccel:
            cmd.extend(["-hwaccel", "cuda"])
        cmd.extend(["-stream_loop", "-1", "-i", sample_video_path, "-t", str(duration), "-an", "-vf", vf])
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(["-movflags", "+faststart", output_path])
        return cmd

    use_hwaccel = bool(Config.USE_GPU_NVENC)
    ok = FFmpegHelper.run_command(_build_cmd(use_hwaccel), timeout_seconds=120)
    if not ok and use_hwaccel:
        logger.warning("TV effect preview with CUDA decode failed; retrying with CPU decode.")
        ok = FFmpegHelper.run_command(_build_cmd(False), timeout_seconds=120)

    if ok and os.path.isfile(output_path):
        return output_path
    logger.error(f"Failed to render TV effect preview for style {style_id}")
    return ""


class CRTEffectProcessor:
    PRESETS = {
        "subtle": {
            "noise_strength": 5,
            "scan_opacity": 0.02,
            "vignette": "PI/4",
            "color_bleed": False,
            "flicker": 0.005,
        },
        "light": {
            "noise_strength": 10,
            "scan_opacity": 0.04,
            "vignette": "PI/5",
            "color_bleed": False,
            "flicker": 0.01,
        },
        "medium": {
            "noise_strength": 15,
            "scan_opacity": 0.06,
            "vignette": "PI/5",
            "color_bleed": True,
            "flicker": 0.02,
        },
        "heavy": {
            "noise_strength": 25,
            "scan_opacity": 0.10,
            "vignette": "PI/3",
            "color_bleed": True,
            "flicker": 0.04,
        },
    }

    @staticmethod
    def _get_settings(settings: dict | None = None) -> dict:
        defaults = {
            "noise_strength": Config.CRT_NOISE_STRENGTH,
            "scan_opacity": Config.CRT_SCANLINE_OPACITY,
            "vignette": Config.CRT_VIGNETTE,
            "color_bleed": Config.CRT_COLOR_BLEED,
            "flicker": Config.CRT_FLICKER,
        }
        if settings:
            defaults.update(settings)
        return defaults

    @staticmethod
    def build_crt_filter(settings: dict | None = None, *, for_image: bool = False) -> str:
        s = CRTEffectProcessor._get_settings(settings)

        filters = []

        noise_strength = min(int(s["noise_strength"]), 10)  # cap for perf
        if noise_strength > 0:
            # Use 'allf=t' (temporal only) instead of 't+u' — 3-5x faster
            filters.append(f"noise=alls={noise_strength}:allf=t")

        vignette = s["vignette"]
        if vignette:
            filters.append(f"vignette=angle={vignette}")

        # Skip time-dependent effects for still images
        if not for_image:
            flicker = float(s["flicker"])
            if flicker > 0:
                filters.append(f"eq=brightness='{flicker}*sin(2*PI*t*8)':eval=frame")

            scan_opacity = float(s["scan_opacity"])
            if scan_opacity > 0:
                filters.append(
                    f"drawgrid=w=0:h=2:t=1:c=black@{scan_opacity}"
                )

        color_bleed = s.get("color_bleed", False)
        if color_bleed:
            filters.append("chromashift=cbh=2:crh=-2")

        if not filters:
            return "null"

        return ",".join(filters)

    @staticmethod
    def apply_to_video(
        input_video: str,
        output_video: str,
        settings: dict | None = None,
        progress_callback=None,
    ) -> bool:
        if not os.path.isfile(input_video):
            logger.error(f"CRT input video not found: {input_video}")
            return False

        os.makedirs(os.path.dirname(output_video) or ".", exist_ok=True)
        duration = FFmpegHelper.probe_duration(input_video) or None

        # For long videos (>120s), skip noise filter — it's extremely CPU-heavy
        effective_settings = dict(settings) if settings else {}
        if duration and duration > 120:
            effective_settings["noise_strength"] = 0
            logger.info(f"CRT: skipping noise filter for long video ({duration:.0f}s)")

        filter_str = CRTEffectProcessor.build_crt_filter(effective_settings)

        cmd = [
            "ffmpeg", "-y",
            "-hwaccel", "cuda",
            "-i", input_video,
            "-filter_threads", str(os.cpu_count() or 4),
            "-vf", filter_str,
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend([
            "-c:a", "copy",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            output_video,
        ])

        logger.info(f"Applying CRT effect: {input_video} -> {output_video}")
        logger.debug(f"CRT filter: {filter_str}")

        success = FFmpegHelper.run_command(
            cmd,
            progress_callback=progress_callback,
            progress_total_seconds=duration,
        )

        if success:
            logger.info(f"CRT effect applied successfully: {output_video}")
        else:
            logger.error(f"CRT effect failed for: {input_video}")

        return success

    @staticmethod
    def generate_demo_image(
        sample_image_path: str,
        settings: dict | None = None,
        output_path: str | None = None,
    ) -> str:
        if not os.path.isfile(sample_image_path):
            logger.error(f"CRT demo sample image not found: {sample_image_path}")
            return ""

        if not output_path:
            os.makedirs(Config.CRT_EFFECT_DIR, exist_ok=True)
            output_path = os.path.join(Config.CRT_EFFECT_DIR, "crt_demo_preview.jpg")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        filter_str = CRTEffectProcessor.build_crt_filter(settings, for_image=True)

        cmd = [
            "ffmpeg", "-y",
            "-i", sample_image_path,
            "-vf", filter_str,
            "-frames:v", "1",
            "-q:v", "2",
            output_path,
        ]

        logger.info(f"Generating CRT demo image: {sample_image_path} -> {output_path}")

        if FFmpegHelper.run_command(cmd):
            logger.info(f"CRT demo image generated: {output_path}")
            return output_path

        logger.error(f"Failed to generate CRT demo image from {sample_image_path}")
        return ""

    @staticmethod
    def generate_demo_video(
        sample_video_path: str,
        settings: dict | None = None,
        output_path: str | None = None,
        duration: float = 3.0,
    ) -> str:
        if not os.path.isfile(sample_video_path):
            logger.error(f"CRT demo sample video not found: {sample_video_path}")
            return ""

        if not output_path:
            os.makedirs(Config.CRT_EFFECT_DIR, exist_ok=True)
            output_path = os.path.join(Config.CRT_EFFECT_DIR, "crt_demo_preview.mp4")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        filter_str = CRTEffectProcessor.build_crt_filter(settings)

        cmd = [
            "ffmpeg", "-y",
            "-i", sample_video_path,
            "-t", str(duration),
            "-vf", filter_str,
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(["-pix_fmt", "yuv420p", output_path])

        logger.info(f"Generating CRT demo video ({duration}s): {sample_video_path} -> {output_path}")

        if FFmpegHelper.run_command(cmd):
            logger.info(f"CRT demo video generated: {output_path}")
            return output_path

        logger.error(f"Failed to generate CRT demo video from {sample_video_path}")
        return ""
