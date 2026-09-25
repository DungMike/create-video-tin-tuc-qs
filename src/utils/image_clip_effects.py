"""Anh tinh -> clip Ken Burns dung chuan clip thu vien.

Mot anh thanh dung 1 clip: zoom in/out, lia trai/phai/len/xuong, cheo, zoom + troi
ve mot phia. Clip ra phai ghep duoc voi clip cat tu video bang concat ``-c copy``
cua buoc render, nen lenh ffmpeg di qua dung ``clip_canonical`` + co encoder cua
``split_into_clips``. Nhung chi tiet da do (ffmpeg 8.1):

- Anh vao la **1 frame** (khong ``-loop``): scale supersample chi chay mot lan, roi
  ``zoompan d=N+1`` sinh ra cac frame. ``fps=`` o duoi chuoi canonical bo frame cuoi
  cua zoompan, nen can ``d=N+1`` + ``-frames:v N`` moi ra dung N frame.
- ``format=yuvj444p`` truoc zoompan: o 4:2:0 zoompan chi dich duoc buoc 2px (lia bi
  giat). Ban ``j`` giu full range nen duoi canonical van chuyen range dung.
- Anh duoc ``normalize_photo`` ve JPEG RGB truoc: PNG (``gbr``) lam scale cua
  canonical bao loi ``in_color_matrix``, anh xoay EXIF / CMYK thi sai hinh.
- Khong roi ve libx264 khi NVENC loi: SPS khac lam hong concat ``-c copy``.
"""

from __future__ import annotations

import os
import random
import time

from src.config import Config
from src.utils.clip_canonical import (
    canonical_output_args,
    canonical_video_filter,
    is_canonical,
    keyframe_args,
)
from src.utils.clip_spec_validation import expected_dimensions, probe_clip_spec
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

# Supersample truoc zoompan: toa do crop lam tron theo pixel cua anh da phong, nen
# S=3 dua sai so ve ~1/3 px cua khung ra.
SUPERSAMPLE = 3
# Anh stage duoc thu ve toi da chung nay (du cho zoom 1.3 tren khung 1920).
MAX_STAGED_WIDTH = 2880
THUMB_WIDTH = 480
JPEG_QUALITY = 92

ZOOM_MIN = 1.05
ZOOM_MAX = 1.4
DEFAULT_ZOOM = 1.2

RENDER_TIMEOUT_SECONDS = 180
RENDER_RETRY_DELAY_SECONDS = 3.0

# id -> (nhan, z_dau, z_cuoi, fx_dau, fy_dau, fx_cuoi, fy_cuoi).
# "Z" la muc zoom cua job; fx/fy la vi tri khung trong phan le (0 = trai/tren).
EFFECTS: dict[str, tuple] = {
    "zoom_in": ("Zoom vào giữa", 1, "Z", 0.5, 0.5, 0.5, 0.5),
    "zoom_out": ("Zoom ra từ giữa", "Z", 1, 0.5, 0.5, 0.5, 0.5),
    "zoom_in_left": ("Zoom vào, trôi sang trái", 1, "Z", 0.5, 0.5, 0.15, 0.45),
    "zoom_in_right": ("Zoom vào, trôi sang phải", 1, "Z", 0.5, 0.5, 0.85, 0.45),
    "zoom_out_left": ("Zoom ra từ bên trái", "Z", 1, 0.15, 0.45, 0.5, 0.5),
    "zoom_out_right": ("Zoom ra từ bên phải", "Z", 1, 0.85, 0.45, 0.5, 0.5),
    "pan_left": ("Lia sang trái", "Z", "Z", 1.0, 0.5, 0.0, 0.5),
    "pan_right": ("Lia sang phải", "Z", "Z", 0.0, 0.5, 1.0, 0.5),
    "pan_up": ("Lia lên", "Z", "Z", 0.5, 1.0, 0.5, 0.0),
    "pan_down": ("Lia xuống", "Z", "Z", 0.5, 0.0, 0.5, 1.0),
    "pan_diag_tl_br": ("Lia chéo ↘", "Z", "Z", 0.0, 0.0, 1.0, 1.0),
    "pan_diag_br_tl": ("Lia chéo ↖", "Z", "Z", 1.0, 1.0, 0.0, 0.0),
}
EFFECT_IDS = tuple(EFFECTS)


def list_effects() -> list[dict]:
    """Danh muc cho UI, kem hanh trinh de UI mo phong chuyen dong (khong chep bang so)."""
    return [
        {
            "id": effect_id,
            "label": label,
            "startZoomed": z0 == "Z",
            "endZoomed": z1 == "Z",
            "from": [fx0, fy0],
            "to": [fx1, fy1],
        }
        for effect_id, (label, z0, z1, fx0, fy0, fx1, fy1) in EFFECTS.items()
    ]


def clamp_zoom(zoom) -> float:
    try:
        value = float(zoom)
    except (TypeError, ValueError):
        value = DEFAULT_ZOOM
    return max(ZOOM_MIN, min(ZOOM_MAX, value))


def clean_effects(effects) -> list[str]:
    """Hieu ung hop le theo thu tu danh muc; rong/khong hop le -> tat ca."""
    wanted = {str(effect).strip() for effect in (effects or [])}
    chosen = [effect_id for effect_id in EFFECT_IDS if effect_id in wanted]
    return chosen or list(EFFECT_IDS)


def duration_range() -> tuple[float, float]:
    """``(min, max)`` giay cua clip tu anh -- STORY_IMAGE_CLIP_DURATION_MIN/MAX, doc lap
    voi STORY_CLIP_DURATION (luong cat video). Render phat du do dai tung clip."""
    try:
        low = float(Config.STORY_IMAGE_CLIP_DURATION_MIN)
        high = float(Config.STORY_IMAGE_CLIP_DURATION_MAX)
    except (TypeError, ValueError, AttributeError):
        low, high = 3.0, 5.0
    low = max(1.0, low)
    return low, max(low, high)


def pick_duration(rng: random.Random) -> float:
    """Mot do dai ngau nhien trong ``duration_range()``, lam tron 0.1 giay."""
    low, high = duration_range()
    return round(rng.uniform(low, high), 1)


def frame_count(seconds) -> int:
    fps = max(1, int(Config.TARGET_FPS))
    return max(2, int(round(float(seconds) * fps)))


def _fmt(value: float) -> str:
    return f"{float(value):.5f}".rstrip("0").rstrip(".") or "0"


def effect_filter(effect: str, seconds, zoom=DEFAULT_ZOOM) -> str:
    """Chuoi filter Ken Burns (ket thuc bang dau phay) lam ``prefix`` cho canonical."""
    if effect not in EFFECTS:
        raise ValueError(f"Hieu ung khong hop le: {effect}")
    _label, z0, z1, fx0, fy0, fx1, fy1 = EFFECTS[effect]
    zoom = clamp_zoom(zoom)
    z0 = zoom if z0 == "Z" else float(z0)
    z1 = zoom if z1 == "Z" else float(z1)

    width, height = expected_dimensions()
    fps = max(1, int(Config.TARGET_FPS))
    frames = frame_count(seconds)
    super_w = (width * SUPERSAMPLE) // 2 * 2
    super_h = (height * SUPERSAMPLE) // 2 * 2

    progress = f"min(on/{frames - 1},1)"
    eased = f"({progress})*({progress})*(3-2*({progress}))"
    if abs(z1 - z0) < 1e-6:
        z_expr = _fmt(z0)
    else:
        # Noi suy theo ham mu: toc do zoom deu voi mat, khong nhanh dan.
        z_expr = f"{_fmt(z0)}*pow({_fmt(z1 / z0)},{eased})"

    def position(start: float, end: float) -> str:
        if abs(end - start) < 1e-6:
            return _fmt(start)
        return f"{_fmt(start)}+({_fmt(end - start)})*{eased}"

    x_expr = f"(iw-iw/zoom)*({position(fx0, fx1)})"
    y_expr = f"(ih-ih/zoom)*({position(fy0, fy1)})"
    return (
        f"scale={super_w}:{super_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={super_w}:{super_h},format=yuvj444p,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
        f":d={frames + 1}:s={width}x{height}:fps={fps},"
    )


def build_render_command(image_path: str, out_path: str, effect: str, seconds, zoom=DEFAULT_ZOOM) -> list[str]:
    frames = frame_count(seconds)
    video_filter = canonical_video_filter(
        probe_clip_spec(image_path), prefix=effect_filter(effect, seconds, zoom)
    )
    cmd = ["ffmpeg", "-y", "-i", image_path, "-vf", video_filter, "-frames:v", str(frames), "-an"]
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(keyframe_args(seconds))
    cmd.extend(canonical_output_args())
    cmd.extend(["-movflags", "+faststart", out_path])
    return cmd


def _noop_progress(_data) -> None:
    return None


def _never() -> bool:
    return False


def render_image_clip(
    image_path: str,
    out_path: str,
    effect: str,
    seconds,
    zoom=DEFAULT_ZOOM,
    cancel_cb=None,
    retries: int = 1,
) -> bool:
    """Render 1 clip. True khi ``out_path`` ton tai va dung chuan canonical.

    ``progress_callback`` truyen vao chi de ``run_command`` di nhanh co huy duoc
    (``cancel_callback`` bi bo qua o nhanh khong progress).
    """
    cancel_cb = cancel_cb or _never
    cmd = build_render_command(image_path, out_path, effect, seconds, zoom)
    for attempt in range(max(0, int(retries)) + 1):
        if cancel_cb():
            return False
        ok = FFmpegHelper.run_command(
            cmd,
            timeout_seconds=RENDER_TIMEOUT_SECONDS,
            progress_callback=_noop_progress,
            cancel_callback=cancel_cb,
        )
        if ok and os.path.isfile(out_path) and is_canonical(out_path):
            return True
        try:
            os.remove(out_path)
        except OSError:
            pass
        if attempt < retries and not cancel_cb():
            logger.warning(f"[ImageClip] Render loi ({effect}) {image_path}, thu lai...")
            time.sleep(RENDER_RETRY_DELAY_SECONDS)
    logger.error(f"[ImageClip] Render that bai: {image_path} ({effect})")
    return False


class EffectRotation:
    """Chia deu cac hieu ung duoc bat: moi vong la mot hoan vi xao theo ``seed``."""

    def __init__(self, enabled, seed: str):
        self._pool = clean_effects(enabled)
        self._rng = random.Random(str(seed))
        self._bag: list[str] = []

    def next(self) -> str:
        if not self._bag:
            self._bag = list(self._pool)
            self._rng.shuffle(self._bag)
        return self._bag.pop()


def normalize_photo(src_path: str, dest_path: str, thumb_path: str | None = None) -> tuple[int, int]:
    """Anh tai ve -> JPEG RGB (xoay theo EXIF, rong <= MAX_STAGED_WIDTH) + thumbnail.

    Thumbnail la phan giua 16:9 -- dung khung ma clip se thay. Tra ve (w, h) cua
    anh da chuan hoa.
    """
    from PIL import Image, ImageOps

    with Image.open(src_path) as opened:
        if opened.format == "JPEG":
            # Giai ma o ty le nho hon ngay tu JPEG (nhanh hon nhieu voi anh 6000px).
            opened.draft("RGB", (MAX_STAGED_WIDTH, MAX_STAGED_WIDTH))
        image = ImageOps.exif_transpose(opened)
        if image.mode != "RGB":
            image = image.convert("RGB")
        if image.width > MAX_STAGED_WIDTH:
            height = max(1, round(image.height * MAX_STAGED_WIDTH / image.width))
            image = image.resize((MAX_STAGED_WIDTH, height), Image.LANCZOS)
        size = image.size

        tmp_path = f"{dest_path}.tmp.jpg"
        image.save(tmp_path, "JPEG", quality=JPEG_QUALITY, progressive=False)
        os.replace(tmp_path, dest_path)

        if thumb_path:
            width, height = size
            crop_w, crop_h = width, round(width * 9 / 16)
            if crop_h > height:
                crop_w, crop_h = round(height * 16 / 9), height
            left, top = (width - crop_w) // 2, (height - crop_h) // 2
            thumb = image.crop((left, top, left + crop_w, top + crop_h))
            thumb.thumbnail((THUMB_WIDTH, THUMB_WIDTH), Image.LANCZOS)
            thumb.save(thumb_path, "JPEG", quality=82)
    return size
