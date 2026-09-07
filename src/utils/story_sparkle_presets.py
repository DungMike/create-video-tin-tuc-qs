"""Procedural "sparkle" (lap lanh) overlay sources for Story Video.

Cac preset o day KHONG chay luc render. Moi preset sinh ra mot file MP4 nen den
(loop lien mach), file do duoc dang ky nhu mot TV noise overlay voi blendMode
"luma" -> preprocess thanh alpha MOV -> gop vao overlay pack. Nho vay chi phi
luc render la ~0: van chi mot lop alpha duy nhat qua overlay_cuda.

Kien truc mirror crt_effect_processor: PARAM_SPEC + PRESETS + build_* +
sanitize_*, de UI dung lai duoc pattern slider/preview san co.

DO THUC TE (ffmpeg 8.1, may nay, do bang signalstats tren 1920x1080):
- star_dust     YAVG~1.6  YMAX~150  sinh o 0.77x realtime
- star_flare    YAVG~4.2  YMAX~255  sinh o 0.67x realtime
- shimmer_sweep YAVG~9.3  YMAX~139  sinh o 0.25x realtime (nen de loop ngan)

Ghi chu ky thuat: mat do hat duoc tao bang geq `random(0)` chu KHONG phai filter
`noise`. `noise=alls=100` tren nen den chi dat YMAX=65, nen nguong cat qua tho —
khong xuong duoc mat do thua (~0.05%) can thiet cho hat sao roi rac.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

_generate_lock = threading.Lock()

# Hat sang duoc sinh o 1/4 do phan giai roi scale len: geq la filter dat nhat
# trong chuoi, chay o 480x270 nhanh hon ~16 lan va sau khi gblur thi khong phan
# biet duoc voi ban sinh o full-res.
GEN_WIDTH = 480
GEN_HEIGHT = 270

SPARKLE_PARAM_SPEC = {
    # Xac suat mot pixel (o do phan giai sinh) thanh hat sang trong mot frame.
    "density": {"min": 0.00005, "max": 0.003, "default": 0.0006},
    # So frame tmix gop lai -> hat sang len/tat dan. Cang lon nhap nhay cang cham.
    "twinkle": {"min": 2, "max": 24, "default": 10},
    "size": {"min": 1.0, "max": 8.0, "default": 3.0},
    "gain": {"min": 2.0, "max": 20.0, "default": 10.0},
    # Do dai tia sao (gblur mot chieu). 0 = khong co tia.
    "spike": {"min": 0.0, "max": 90.0, "default": 45.0},
    "sweepSpeed": {"min": 300.0, "max": 2500.0, "default": 1200.0},
    "sweepAngle": {"min": 0.0, "max": 1.2, "default": 0.45},
    "sweepWidth": {"min": 30.0, "max": 200.0, "default": 70.0},
    "sweepGain": {"min": 0.1, "max": 1.5, "default": 0.55},
    "tintR": {"min": 0.3, "max": 1.0, "default": 1.0},
    "tintG": {"min": 0.3, "max": 1.0, "default": 1.0},
    "tintB": {"min": 0.3, "max": 1.0, "default": 1.0},
    "loopSeconds": {"min": 6.0, "max": 30.0, "default": 20.0},
}

# `paramsUsed` cho UI biet preset nay hien nhung slider nao.
_DUST_PARAMS = ["density", "twinkle", "size", "gain", "tintR", "tintG", "tintB", "loopSeconds"]
_FLARE_PARAMS = ["density", "twinkle", "spike", "gain", "tintR", "tintG", "tintB", "loopSeconds"]
_SWEEP_PARAMS = ["sweepSpeed", "sweepAngle", "sweepWidth", "sweepGain", "tintR", "tintG", "tintB", "loopSeconds"]

SPARKLE_PRESETS = [
    {
        "id": "star_dust",
        "name": "Bụi Sao Twinkle",
        "description": "Hạt sáng nhỏ rải rác, nhấp nháy ngẫu nhiên — hợp video kể chuyện, cảnh đêm.",
        "paramsUsed": _DUST_PARAMS,
        "params": {"density": 0.0006, "twinkle": 10, "size": 3.0, "gain": 10.0, "loopSeconds": 20.0},
    },
    {
        "id": "star_dust_gold",
        "name": "Bụi Sao Vàng Kim",
        "description": "Như Bụi Sao Twinkle nhưng ám vàng ấm, hạt to và thưa hơn.",
        "paramsUsed": _DUST_PARAMS,
        "params": {
            "density": 0.00035, "twinkle": 14, "size": 4.5, "gain": 18.0,
            "tintR": 1.0, "tintG": 0.85, "tintB": 0.55, "loopSeconds": 20.0,
        },
    },
    {
        "id": "star_flare",
        "name": "Tia Sao 4 Cánh",
        "description": "Điểm sáng có tia chữ thập như filter star trên ống kính — lấp lánh rõ, lung linh.",
        "paramsUsed": _FLARE_PARAMS,
        "params": {"density": 0.00025, "twinkle": 10, "spike": 45.0, "gain": 12.0, "loopSeconds": 20.0},
    },
    {
        "id": "star_flare_soft",
        "name": "Tia Sao Dịu",
        "description": "Tia sao ngắn và thưa, ánh vàng nhạt — nhấn nhẹ, không chiếm khung hình.",
        "paramsUsed": _FLARE_PARAMS,
        "params": {
            "density": 0.00012, "twinkle": 16, "spike": 28.0, "gain": 12.0,
            "tintR": 1.0, "tintG": 0.9, "tintB": 0.7, "loopSeconds": 20.0,
        },
    },
    {
        "id": "shimmer_sweep",
        "name": "Vệt Sáng Quét",
        "description": "Dải sáng chéo quét ngang khung hình theo chu kỳ — kiểu ánh kim loại.",
        "paramsUsed": _SWEEP_PARAMS,
        "params": {
            "sweepSpeed": 1200.0, "sweepAngle": 0.45, "sweepWidth": 70.0,
            "sweepGain": 0.55, "loopSeconds": 12.0,
        },
    },
]

_PRESET_IDS = {preset["id"] for preset in SPARKLE_PRESETS}


class SparkleError(Exception):
    """Raised when a sparkle source cannot be built or generated."""


def get_sparkle_preset(preset_id: str) -> dict | None:
    return next((preset for preset in SPARKLE_PRESETS if preset["id"] == preset_id), None)


def sanitize_sparkle_params(preset_id: str, params: dict | None) -> dict:
    """Clamp/normalize params against the spec, filling from the preset's defaults.

    Unknown keys are dropped; missing keys fall back to the preset's own value and
    then to the global spec default. Mirrors ``sanitize_tv_effect_params``.
    """
    preset = get_sparkle_preset(preset_id) or {}
    preset_params = preset.get("params", {})
    incoming = params if isinstance(params, dict) else {}

    cleaned: dict = {}
    for key, spec in SPARKLE_PARAM_SPEC.items():
        raw = incoming.get(key, preset_params.get(key, spec["default"]))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = float(preset_params.get(key, spec["default"]))
        cleaned[key] = min(spec["max"], max(spec["min"], value))
    return cleaned


def _target_size() -> tuple[int, int]:
    width, height = Config.TARGET_RESOLUTION.split("x", 1)
    return int(width), int(height)


def _even(value: float) -> int:
    """Nearest even int >= 2 — yuv420p refuses odd dimensions."""
    return max(2, int(round(value / 2)) * 2)


def _tint_suffix(params: dict) -> str:
    """Colour cast applied last; empty when the tint is neutral (saves a pass).

    The tint is written straight into the U/V planes rather than round-tripping
    through RGB (``format=gbrp,colorchannelmixer``): that round trip re-maps the
    limited/full range twice and lifts the black background off zero — measured
    YAVG jumped from 1.6 to 65, which would have made the whole frame glow once
    luma becomes alpha. Writing constant chroma leaves Y — and therefore the
    alpha and the brightness this module documents — exactly as designed.
    """
    r, g, b = params["tintR"], params["tintG"], params["tintB"]
    if abs(r - 1.0) < 0.01 and abs(g - 1.0) < 0.01 and abs(b - 1.0) < 0.01:
        return ""
    # `y='val'` phai viet ra: giong geq, lutyuv khong pass-through plane khong
    # duoc khai bao — bo trong thi nen den bi nang len (YAVG 1.0 -> 16.3).
    # BT.709 (the pipeline's canonical matrix, Config.CLIP_EXPECTED_COLOR_SPACE).
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    u = int(round(255 * (0.5 + (b - luma) / 1.8556)))
    v = int(round(255 * (0.5 + (r - luma) / 1.5748)))
    return f",lutyuv=y='val':u={min(255, max(0, u))}:v={min(255, max(0, v))}"


def _dot_field_chain(params: dict) -> str:
    """Sparse twinkling dot field at GEN_WIDTHxGEN_HEIGHT, upscaled to target.

    ``geq`` evaluates ``random(0)`` once per pixel, so the density is exactly the
    probability given (measured: p=0.0015 -> YAVG 0.394 ~= 255*p).
    """
    width, height = _target_size()
    return (
        # cb/cr phai set tuong minh: geq khong giu chroma cua nguon, de trong thi
        # hai plane nay tut ve ~0 -> hat sang bi am xanh la thay vi trang.
        "format=yuv420p,"
        f"geq=lum='if(lt(random(0)\\,{params['density']:.6g})\\,255\\,0)':cb=128:cr=128,"
        f"tmix=frames={int(round(params['twinkle']))},"
        f"scale={width}:{height}:flags=bicubic"
    )


def sweep_period_seconds(params: dict) -> float:
    """One full left-to-right traversal, in seconds."""
    width, height = _target_size()
    band_canvas = _even(height * 1.76)
    return (width + band_canvas) / params["sweepSpeed"]


def sparkle_loop_seconds(preset_id: str, params: dict) -> float:
    """Loop length of the generated source.

    The overlay pack feeds every layer with ``-stream_loop -1``, so the seam must
    be invisible. Random dot fields are seamless by construction; the sweep is
    not, so its duration is rounded UP to a whole number of traversals.
    """
    fps = max(1, int(Config.TARGET_FPS))
    if preset_id != "shimmer_sweep":
        return round(params["loopSeconds"], 3)
    period = sweep_period_seconds(params)
    cycles = max(1, math.ceil(params["loopSeconds"] / period))
    # Snap to a whole frame so the encoder lands on an exact frame count. Do NOT
    # round the result: at 30fps a 955-frame loop is 31.8333... and trimming that
    # to 4 decimals puts it back off the frame grid (954.999 frames).
    # The sub-frame drift left over (<=20px of band travel) is invisible anyway:
    # at the seam the band is off-screen.
    return round(cycles * period * fps) / fps


def build_sparkle_source_command(preset_id: str, params: dict, output_path: str) -> list[str]:
    """FFmpeg command producing the black-background sparkle loop for a preset.

    Output is a plain MP4 (no alpha); the alpha is derived later by the "luma"
    preprocess branch in ``story_tv_noise_overlays``.
    """
    if preset_id not in _PRESET_IDS:
        raise SparkleError(f"Unknown sparkle preset: {preset_id!r}")

    width, height = _target_size()
    fps = max(1, int(Config.TARGET_FPS))
    duration = sparkle_loop_seconds(preset_id, params)
    tint = _tint_suffix(params)

    inputs: list[str] = []
    if preset_id == "shimmer_sweep":
        band_canvas = _even(height * 1.76)
        band_width = _even(max(120.0, params["sweepWidth"] * 8.0))
        band_height = _even(band_canvas * 1.16)
        inputs = [
            f"color=c=black:s={width}x{height}:r={fps}:d={duration}",
            f"color=c=black:s={band_width}x{band_height}:r={fps}:d={duration}",
        ]
        filter_complex = (
            f"[1:v]format=yuv420p,"
            f"geq=lum='255*exp(-pow((X-{band_width // 2})/{params['sweepWidth']:.4g}\\,2))':cb=128:cr=128[shm_src];"
            f"[shm_src]rotate={params['sweepAngle']:.4g}:c=black:ow={band_canvas}:oh={band_canvas}[shm_band];"
            f"[0:v]format=yuv420p[shm_bg];"
            f"[shm_bg][shm_band]overlay="
            f"x='-w+mod(t*{params['sweepSpeed']:.6g}\\,W+w)':y='(H-h)/2':eval=frame,"
            f"gblur=sigma=8,"
            f"lutyuv=y='min(255,val*{params['sweepGain']:.3g})'"
            f"{tint},format=yuv420p[v]"
        )
    else:
        inputs = [f"color=c=black:s={GEN_WIDTH}x{GEN_HEIGHT}:r={fps}:d={duration}"]
        dots = _dot_field_chain(params)
        if preset_id.startswith("star_flare"):
            spike = params["spike"]
            filter_complex = (
                f"[0:v]{dots},"
                f"lutyuv=y='min(255,val*{params['gain']:.3g})',"
                f"split=3[spk_c][spk_h][spk_v];"
                f"[spk_h]gblur=sigma={spike:.4g}:sigmaV=0:steps=2[spk_hs];"
                f"[spk_v]gblur=sigma=0:sigmaV={spike:.4g}:steps=2[spk_vs];"
                f"[spk_c]gblur=sigma=2[spk_core];"
                f"[spk_hs][spk_vs]blend=all_mode=lighten[spk_cross];"
                f"[spk_core][spk_cross]blend=all_mode=lighten,"
                f"lutyuv=y='min(255,val*3)'"
                f"{tint},format=yuv420p[v]"
            )
        else:
            filter_complex = (
                f"[0:v]{dots},"
                f"gblur=sigma={params['size']:.3g},"
                f"lutyuv=y='min(255,val*{params['gain']:.3g})'"
                f"{tint},format=yuv420p[v]"
            )

    cmd = ["ffmpeg", "-y"]
    for source in inputs:
        cmd.extend(["-f", "lavfi", "-i", source])
    cmd.extend(
        [
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-t", str(duration),
            "-an",
            # Sinh mot lan roi cache: uu tien chat luong hon toc do. NVENC bi bo
            # qua o day vi x264 CRF 16 giu duoc hat sang nho ma NVENC hay lam nhoe.
            "-c:v", "libx264",
            "-crf", "16",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ]
    )
    return cmd


def sparkle_signature(preset_id: str, params: dict) -> dict:
    """Stable description of a generated source — the cache key input."""
    width, height = _target_size()
    preset = get_sparkle_preset(preset_id) or {}
    used = preset.get("paramsUsed") or list(SPARKLE_PARAM_SPEC)
    return {
        "presetId": preset_id,
        "target": {"width": width, "height": height, "fps": int(Config.TARGET_FPS)},
        "durationSeconds": sparkle_loop_seconds(preset_id, params),
        # Chi cac param preset thuc su dung moi vao hash, de doi mot slider khong
        # lien quan khong lam mat cache.
        "params": {key: round(params[key], 8) for key in sorted(used) if key in params},
    }


def sparkle_hash(signature: dict) -> str:
    payload = json.dumps(signature, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _source_dir() -> str:
    os.makedirs(Config.STORY_TV_NOISE_OVERLAY_DIR, exist_ok=True)
    return Config.STORY_TV_NOISE_OVERLAY_DIR


def generate_sparkle_source(preset_id: str, params: dict | None) -> str:
    """Render (or reuse) the black-background sparkle loop. Returns its path.

    Cached by ``sparkle_hash`` so re-creating a layer with identical settings is
    free — generation costs 25-90s depending on the preset.
    """
    if preset_id not in _PRESET_IDS:
        raise SparkleError(f"Unknown sparkle preset: {preset_id!r}")

    clean = sanitize_sparkle_params(preset_id, params)
    signature = sparkle_signature(preset_id, clean)
    digest = sparkle_hash(signature)
    output_path = os.path.join(_source_dir(), f"sparkle_{preset_id}_{digest}.mp4")

    with _generate_lock:
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
            logger.info(f"[Sparkle] Reusing cached source: {output_path}")
            return output_path

        # PID-unique temp: another process (web app vs. script) may be building the
        # same source; a shared temp name crashes the loser with WinError 32.
        tmp_path = f"{output_path}.tmp.{os.getpid()}.mp4"
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass

        duration = signature["durationSeconds"]
        logger.info(
            f"[Sparkle] Generating {preset_id} source ({duration}s, hash={digest}); this takes a while."
        )
        cmd = build_sparkle_source_command(preset_id, clean, tmp_path)
        ok = FFmpegHelper.run_command(cmd, timeout_seconds=Config.FFMPEG_STORY_SPARKLE_TIMEOUT_SECONDS)
        if not ok or not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) <= 0:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise SparkleError(f"FFmpeg failed to generate sparkle source for {preset_id!r}.")

        try:
            os.replace(tmp_path, output_path)
        except OSError as exc:
            raise SparkleError(f"Cannot finalize sparkle source: {exc}") from exc

    logger.info(f"[Sparkle] Generated source: {output_path}")
    return output_path


def list_sparkle_presets() -> list[dict]:
    """Preset catalogue + param spec, for the settings UI."""
    return [
        {
            "id": preset["id"],
            "name": preset["name"],
            "description": preset["description"],
            "paramsUsed": preset["paramsUsed"],
            "params": sanitize_sparkle_params(preset["id"], preset.get("params")),
        }
        for preset in SPARKLE_PRESETS
    ]
