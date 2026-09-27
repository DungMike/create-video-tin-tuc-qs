"""Story decor images: a full-frame photo the story video plays *inside* of.

A decor image is a still picture — a living room with a TV, a phone on a desk,
a cinema screen — with a "screen" area the video plays inside. That area is made
transparent once at upload time, producing an RGBA PNG at the output resolution;
the render then scales the story video into the screen rectangle and lays that
PNG on top, so everything opaque in the photo covers the video.

There are two ways to get that transparent area (``maskMode``):

``chroma``
    The photo already has the screen painted chroma green; ``colorkey`` keys it
    out. This is the original path and stays the default.
``manual``
    No green anywhere in the photo — the user drags a 16:9 rectangle over the
    screen in the editor and :func:`build_manual_mask_png` punches exactly that
    rectangle into the alpha. Chosen automatically when detection finds no
    green, because keying such a photo yields a fully opaque PNG that would
    hide the video entirely.

Both modes write the same ``<id>_keyed.png``, so nothing downstream has to know
which one produced it.

Layer order in the render (see ``story_video_pipeline._apply_story_overlays``)::

    clip -> TV style -> TV noise      # inside the screen
         -> scale/crop/pad into frame
         -> decor PNG                 # full frame, on top
         -> waveform -> CTA -> subtitles

Structurally this mirrors :mod:`src.utils.story_cta_overlay` (index.json +
colorkey preprocess + CRUD), with two additions specific to a *frame*: the
``frame`` rectangle the video is fitted into, and ``detect_green_frame`` which
finds that rectangle automatically on upload.
"""

import json
import math
import os
import random
import re
import shutil
import threading
import uuid
from datetime import datetime

from werkzeug.utils import secure_filename

from src.config import Config
from src.utils.logger import logger

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


# --------------------------------------------------------------------------- #
# Index storage
# --------------------------------------------------------------------------- #
def _index_path() -> str:
    os.makedirs(Config.STORY_DECOR_DIR, exist_ok=True)
    return os.path.join(Config.STORY_DECOR_DIR, "index.json")


def load_decor_index() -> dict:
    path = _index_path()
    if not os.path.isfile(path):
        return {"images": []}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else {"images": []}
    except (OSError, json.JSONDecodeError):
        return {"images": []}


def save_decor_index(data: dict):
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2, ensure_ascii=False)
    shutil.move(tmp, path)


# --------------------------------------------------------------------------- #
# Shared settings (mac dinh chung cho moi anh decor)
# --------------------------------------------------------------------------- #
# Ban kinh Gaussian toi da, tinh theo pixel cua khung 1920x1080. Tren 40 thi
# anh chi con la mang mau, khong con nhan ra bo cuc goc nua.
DECOR_BLUR_MAX = 40.0


def _clamp_blur(value) -> float:
    """Ban kinh blur hop le, lam tron 1 chu so; gia tri rac coi nhu 0 (tat)."""
    try:
        radius = float(value)
    except (TypeError, ValueError):
        return 0.0
    if radius != radius:  # NaN
        return 0.0
    return round(max(0.0, min(radius, DECOR_BLUR_MAX)), 1)


# Vien quanh o cua video, cung khoang 0..20 va cung mau mac dinh voi kieu dung
# "Hai lop cung nguon". Mac dinh la TAT (0px, khong bong): PNG ve ra y het nhu
# truoc khi co tinh nang nay.
DECOR_BORDER_MAX = 20
DECOR_BORDER_COLOR = "#F5F0E6"
# Cung do dam bong ma two_layer dung.
_DECOR_SHADOW_STRENGTH = 170
_HEX_COLOR = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _clamp_border_width(value) -> int:
    try:
        width = float(value)
    except (TypeError, ValueError):
        return 0
    if width != width:  # NaN
        return 0
    return int(round(max(0.0, min(width, float(DECOR_BORDER_MAX)))))


def _clamp_border_color(value) -> str:
    if isinstance(value, str) and _HEX_COLOR.match(value.strip()):
        return "#" + value.strip().lstrip("#").upper()
    return DECOR_BORDER_COLOR


def _clamp_border(raw) -> dict:
    """Vien hop le ``{"width", "color", "shadow"}``; thu gi hong thi ve mac dinh tat."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "width": _clamp_border_width(raw.get("width")),
        "color": _clamp_border_color(raw.get("color")),
        "shadow": bool(raw.get("shadow")),
    }


def _border_signature(border: dict | None) -> dict | None:
    """Phan cua vien thuc su lo ra trong PNG; ``None`` = khong ve gi.

    Day la thu ghi vao ``borderBaked`` va dem ra so. Mau bi bo khi day 0 de
    mot lan doi mau luc vien dang tat khong ve lai ca thu vien vo ich.
    """
    border = _clamp_border(border)
    if not border["width"] and not border["shadow"]:
        return None
    return {
        "width": border["width"],
        "color": border["color"] if border["width"] else None,
        "shadow": border["shadow"],
    }


def load_decor_settings(index: dict | None = None) -> dict:
    """Cai dat dung chung cho ca thu vien anh decor.

    Nam trong chinh ``index.json`` duoi khoa ``settings`` de khong de them mot
    file trang thai thu hai co the lech nhip voi danh sach anh.
    """
    data = index if index is not None else load_decor_index()
    stored = data.get("settings")
    stored = stored if isinstance(stored, dict) else {}
    # Config chi la gia tri khoi tao: khi nguoi dung da luu mot lan thi ban da
    # luu thang, doi bien moi truong khong am tham ghi de lua chon cua ho.
    raw = stored.get("backgroundBlur", Config.STORY_DECOR_BLUR)
    return {
        "backgroundBlur": _clamp_blur(raw),
        "borderWidth": _clamp_border_width(stored.get("borderWidth", 0)),
        "borderColor": _clamp_border_color(stored.get("borderColor", DECOR_BORDER_COLOR)),
        "borderShadow": bool(stored.get("borderShadow", False)),
    }


def _relative(filename: str) -> str:
    return f"story_decor_images/{filename}"


def _absolute(filename: str) -> str:
    return os.path.join(Config.STORY_DECOR_DIR, filename)


# --------------------------------------------------------------------------- #
# Theme groups
# --------------------------------------------------------------------------- #
# Each kind of story video has its own setting ("den chua", "lang que", "dieu
# tra pha an"...), so decor images are tagged with a free-text group and the
# render page rotates within one theme instead of across the whole library.
# An empty group means "not sorted yet" and is never a hard error.
MAX_GROUP_LEN = 60


def normalize_group(value) -> str:
    """Trim a group label; anything falsy or blank collapses to '' (ungrouped)."""
    if value is None:
        return ""
    return " ".join(str(value).split())[:MAX_GROUP_LEN]


def decor_group_of(record: dict) -> str:
    return normalize_group(record.get("group"))


def list_decor_groups() -> list[str]:
    """Distinct non-empty group labels, in first-use order."""
    groups: list[str] = []
    for record in load_decor_index().get("images", []):
        group = decor_group_of(record)
        if group and group not in groups:
            groups.append(group)
    return groups


def rename_decor_group(old: str, new: str) -> int:
    """Move every image in group ``old`` to ``new``. Returns how many moved.

    Renaming through the records themselves keeps the group list derived from a
    single source of truth — there is no separate group registry to drift.
    """
    old_group = normalize_group(old)
    new_group = normalize_group(new)
    if old_group == new_group:
        return 0

    index = load_decor_index()
    moved = 0
    for record in index.get("images", []):
        if decor_group_of(record) == old_group:
            record["group"] = new_group
            record["updatedAt"] = datetime.now().isoformat()
            moved += 1
    if moved:
        save_decor_index(index)
    return moved


def target_size() -> tuple[int, int]:
    width, height = Config.TARGET_RESOLUTION.split("x", 1)
    return int(width), int(height)


def processed_abs_path(record: dict) -> str | None:
    """The keyed RGBA PNG the render composites, or None when it is missing."""
    filename = record.get("processedFilename")
    if not filename:
        return None
    path = _absolute(str(filename))
    return path if os.path.isfile(path) else None


def decor_source_abs_path(record: dict) -> str | None:
    """The original upload, which re-detection and re-keying both read from."""
    filename = record.get("filename")
    if not filename:
        return None
    path = _absolute(str(filename))
    return path if os.path.isfile(path) else None


# --------------------------------------------------------------------------- #
# Green-screen detection
# --------------------------------------------------------------------------- #
def _contiguous_run(counts, threshold: float) -> tuple[int, int]:
    """Widest contiguous run of indexes above ``threshold``, around the peak.

    The screen is one big solid block, so its rows/columns all sit far above the
    threshold; stray green elsewhere in the photo (house plants, a green mug)
    contributes a handful of pixels per column and is cut off. Growing outward
    from the peak instead of taking the global bbox is what keeps the plant in
    the corner of the frame from stretching the rectangle across the whole image.
    """
    peak = int(counts.argmax())
    start = peak
    while start > 0 and counts[start - 1] >= threshold:
        start -= 1
    end = peak
    last = len(counts) - 1
    while end < last and counts[end + 1] >= threshold:
        end += 1
    return start, end


def detect_green_frame(image_path: str) -> tuple[dict, str] | None:
    """Locate the chroma-green screen in a decor image.

    Returns ``({"x","y","w","h"}, key_color)`` in output-resolution coordinates,
    or ``None`` when no plausible green region is found. The key colour is the
    median of the detected pixels, which tracks the actual green of the photo
    (lighting, compression) far better than a fixed constant.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:  # noqa: BLE001 - optional dependency guard
        logger.warning(f"[DecorImage] Green detection unavailable: {exc}")
        return None

    width, height = target_size()
    try:
        with Image.open(image_path) as img:
            # Resize to the output grid so the detected rectangle is already in
            # the same coordinate space the render's filter uses.
            rgb = img.convert("RGB").resize((width, height), Image.LANCZOS)
            arr = np.asarray(rgb).astype(np.int16)
    except Exception as exc:  # noqa: BLE001 - any decode failure is non-fatal
        logger.warning(f"[DecorImage] Could not read {image_path}: {exc}")
        return None

    red, green, blue = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    # Chroma green is bright AND strongly dominant. The margin of 60 is what
    # separates a keying backdrop from foliage, which is dark and only mildly
    # green-dominant.
    mask = (green > 80) & ((green - np.maximum(red, blue)) > 60)
    total = int(mask.sum())
    if total < (width * height) * 0.005:
        logger.info(f"[DecorImage] No green region found in {os.path.basename(image_path)}")
        return None

    col_counts = mask.sum(axis=0)
    row_counts = mask.sum(axis=1)
    x0, x1 = _contiguous_run(col_counts, col_counts.max() * 0.25)
    y0, y1 = _contiguous_run(row_counts, row_counts.max() * 0.25)

    frame = {"x": int(x0), "y": int(y0), "w": int(x1 - x0 + 1), "h": int(y1 - y0 + 1)}
    if frame["w"] < 32 or frame["h"] < 32:
        return None

    inner = mask[y0 : y1 + 1, x0 : x1 + 1]
    region = arr[y0 : y1 + 1, x0 : x1 + 1]
    picked = region[inner]
    if picked.size:
        med = np.median(picked, axis=0).astype(int)
        key_color = "0x{:02x}{:02x}{:02x}".format(*(int(c) for c in med))
    else:
        key_color = Config.STORY_DECOR_KEY_COLOR

    logger.info(
        f"[DecorImage] Detected frame {frame} key={key_color} "
        f"in {os.path.basename(image_path)}"
    )
    return frame, key_color


# --------------------------------------------------------------------------- #
# Fit geometry: how the 1920x1080 story video lands inside the frame rectangle
# --------------------------------------------------------------------------- #
def _even(value: float) -> int:
    """Round up to an even integer — yuv420p chroma subsampling needs both dims even."""
    return int(math.ceil(value / 2.0) * 2)


def _even_down(value: float) -> int:
    """Round down to an even integer.

    Offsets need this as much as sizes do: `pad` and `crop` reject an odd x/y on a
    chroma-subsampled format outright ("Invalid argument"), and overlay_cuda wants
    the same. Rounding down keeps the rectangle inside the frame it was clamped to.
    """
    result = int(value)
    return result - (result % 2)


def _clamp_frame(record: dict) -> dict:
    width, height = target_size()
    frame = record.get("frame") or {}
    try:
        x = int(frame.get("x", 0))
        y = int(frame.get("y", 0))
        w = int(frame.get("w", width))
        h = int(frame.get("h", height))
    except (TypeError, ValueError):
        x, y, w, h = 0, 0, width, height
    w = max(16, min(w, width))
    h = max(16, min(h, height))
    x = max(0, min(x, width - w))
    y = max(0, min(y, height - h))
    return {"x": x, "y": y, "w": w, "h": h}


def _place(ideal: float, size: int, f_pos: int, f_size: int, canvas: int) -> int:
    """Even offset that keeps the frame covered and the video inside the canvas.

    Both constraints are intervals: the video covers the frame while its offset
    is in ``[f_pos + f_size - size, f_pos]``, and it stays on the canvas while
    the offset is in ``[0, canvas - size]``. Those always overlap when the video
    fits the canvas (the frame is itself inside the canvas), so a legal position
    exists; we take the one nearest to centred on the frame.
    """
    lo = max(0, f_pos + f_size - size)
    hi = min(canvas - size, f_pos)
    if lo > hi:                      # video larger than the canvas; caller crops
        lo, hi = 0, max(0, canvas - size)
    pos = _even_down(max(lo, min(hi, ideal)))
    if pos < lo:
        pos += 2
    return max(0, min(pos, hi if hi >= lo else canvas - size))


def decor_fit_geometry(record: dict) -> dict:
    """Cover-fit numbers for the video inside the decor frame.

    The video always keeps the source aspect — it is never squeezed to the shape
    of the frame. It is scaled until it covers the frame, then placed; whatever
    spills past the frame is simply left on the canvas, because the decor PNG is
    composited *on top* and its opaque pixels hide the spill. That is why no crop
    is needed for an off-aspect frame, which matters a lot: `crop` and `pad` have
    no CUDA counterpart, so cropping would drag the entire overlay pass onto the
    CPU chain (measured ~3x slower than the GPU chain on this box).

    ``needsCrop`` is therefore True only in the degenerate case where overscan
    blows the video up past the 1920x1080 canvas itself, which no real
    screen-in-a-photo frame does.

    Everything is computed here in Python rather than as ffmpeg expressions: the
    source is always the canonical output resolution and the rectangle is fixed
    per decor image, so every value is a constant.
    """
    src_w, src_h = target_size()
    frame = _clamp_frame(record)

    try:
        overscan = float(record.get("overscan"))
    except (TypeError, ValueError):
        overscan = Config.STORY_DECOR_OVERSCAN
    overscan = max(0.0, min(0.25, overscan))

    # Cover the frame, keeping the source aspect exactly (one scale factor for
    # both axes). The +2px floor guarantees a sliver of slack, so rounding the
    # offset down to even can never re-expose a line of green at the edge.
    want_w = max(frame["w"] + 2.0, frame["w"] * (1.0 + overscan))
    want_h = max(frame["h"] + 2.0, frame["h"] * (1.0 + overscan))
    scale = max(want_w / src_w, want_h / src_h)
    scaled_w = _even(src_w * scale)
    scaled_h = _even(src_h * scale)

    # Only when overscan pushes the video off the canvas is a crop unavoidable.
    needs_crop = scaled_w > src_w or scaled_h > src_h
    fit_w = min(scaled_w, src_w)
    fit_h = min(scaled_h, src_h)
    crop_x = _even_down((scaled_w - fit_w) // 2)
    crop_y = _even_down((scaled_h - fit_h) // 2)

    fit_x = _place(frame["x"] + frame["w"] / 2 - fit_w / 2, fit_w,
                   frame["x"], frame["w"], src_w)
    fit_y = _place(frame["y"] + frame["h"] / 2 - fit_h / 2, fit_h,
                   frame["y"], frame["h"], src_h)

    return {
        "frame": frame,
        "fitX": fit_x,
        "fitY": fit_y,
        "fitW": fit_w,
        "fitH": fit_h,
        "scaledW": scaled_w,
        "scaledH": scaled_h,
        "cropX": crop_x,
        "cropY": crop_y,
        "needsCrop": needs_crop,
        "targetW": src_w,
        "targetH": src_h,
        # How far the video spills past the frame; the decor hides this much.
        "bleedX": (fit_w - frame["w"]) // 2,
        "bleedY": (fit_h - frame["h"]) // 2,
    }


def decor_filter_parts(
    chain_label: str,
    record: dict,
    decor_input_index: int,
    *,
    prefix: str = "decor",
) -> tuple[list[str], str]:
    """CPU filter_complex parts that fit the video into the frame and lay the PNG on top.

    Shared verbatim by the render pipeline and the effect preview so a preview
    can never disagree with what actually gets rendered, and deliberately the
    same *shape* as the CUDA version in ``_build_story_overlays_gpu_cmd`` so both
    paths produce the same picture.

    The shrunk video is composited back onto a copy of the un-shrunk chain rather
    than onto a black `pad`. That copy is invisible in a correct setup — the decor
    PNG is opaque everywhere except the screen, and the screen is exactly what the
    shrunk video covers — but it is what lets the CUDA version keep a single hw
    frames context (see the GPU builder for why that matters).
    """
    geo = decor_fit_geometry(record)
    bg_label = f"{prefix}bg"
    src_label = f"{prefix}src"
    fit_label = f"{prefix}fit"
    framed_label = f"{prefix}framed"
    out_label = f"{prefix}out"

    fit_chain = f"scale={geo['scaledW']}:{geo['scaledH']}"
    if geo["needsCrop"]:
        fit_chain += f",crop={geo['fitW']}:{geo['fitH']}:{geo['cropX']}:{geo['cropY']}"

    parts = [
        f"{chain_label}split=2[{bg_label}][{src_label}]",
        f"[{src_label}]{fit_chain}[{fit_label}]",
        # Pin the format here: overlay=format=auto would otherwise negotiate the
        # decor PNG's rgba all the way back up the chain, and the waveform/CTA
        # overlays downstream then fail on an rgba main input ("Error while
        # filtering: Invalid argument"). Pinning leaves everything after the decor
        # byte-identical to the no-decor chain.
        f"[{bg_label}][{fit_label}]overlay={geo['fitX']}:{geo['fitY']}"
        f":format=auto:eof_action=repeat:eval=init,format=yuv420p[{framed_label}]",
        f"[{decor_input_index}:v]setpts=PTS-STARTPTS,format=rgba[{prefix}img]",
        f"[{framed_label}][{prefix}img]"
        f"overlay=0:0:format=auto:eof_action=repeat:eval=init[{out_label}]",
    ]
    return parts, f"[{out_label}]"


def decor_input_args(decor_path: str) -> list[str]:
    """ffmpeg input args for the decor PNG.

    Deliberately a SINGLE frame, not `-loop 1 -framerate N`. The decor never
    changes, so looping it makes ffmpeg synthesise a fresh 1920x1080 RGBA frame
    every output frame and push each one through a format conversion and (on the
    GPU path) `hwupload_cuda` — ~124 MB/s of PCIe traffic to re-send an identical
    image. One frame plus `eof_action=repeat` on the overlay holds it instead, and
    measured 4.4x faster on the overlay pass (2.24x -> 9.86x realtime) with the
    composited decor bit-identical; only the phase of the looping waveform/CTA
    animations shifts, which is arbitrary anyway.
    """
    return ["-i", decor_path]


# --------------------------------------------------------------------------- #
# Preprocess: key the green out once, at upload time
# --------------------------------------------------------------------------- #
def _key_is_blue(key_color: str) -> bool:
    """Whether the keyed screen is blue rather than green.

    Almost every decor photo uses a green screen, but the key colour is a
    per-image setting, so a blue one has to pick the matching despill type.
    """
    digits = str(key_color or "").strip().lower()
    for prefix in ("0x", "#"):
        if digits.startswith(prefix):
            digits = digits[len(prefix):]
            break
    # Anything that is not exactly RRGGBB is not a colour we can reason about;
    # default to green, which is what every real decor photo uses.
    if len(digits) != 6 or any(c not in "0123456789abcdef" for c in digits):
        return False
    red, green, blue = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
    return blue > green and blue > red


def preprocess_decor_image(source_path: str, record: dict) -> str:
    from src.utils.ffmpeg_helper import FFmpegHelper

    image_id = str(record["id"])
    processed_filename = f"{image_id}_keyed.png"
    output_path = _absolute(processed_filename)
    width, height = target_size()
    key_color = str(record.get("keyColor") or Config.STORY_DECOR_KEY_COLOR)
    similarity = float(record.get("similarity") or Config.STORY_DECOR_SIMILARITY)
    blend = float(record.get("blend") or Config.STORY_DECOR_BLEND)

    # `colorkey` only clears pixels close enough to the key colour. The
    # anti-aliased boundary pixels — a blend of the subject and the screen — sit
    # too far from it to be cleared, so they stay fully opaque while still
    # carrying the screen's tint, drawing a bright outline around anything in
    # front of the screen (a person's hair, the bezel). `despill` neutralises
    # exactly that tint; measured on a real decor photo it removed 100% of the
    # fringe pixels while leaving the opaque-pixel count unchanged, i.e. without
    # eating into the subject. Preprocessing runs once at upload, so this costs
    # nothing at render time.
    spill_type = "blue" if _key_is_blue(key_color) else "green"

    filter_str = (
        f"scale={width}:{height},"
        f"format=rgba,"
        f"colorkey={key_color}:{similarity}:{blend},"
        f"despill=type={spill_type}:mix=0.5,"
        "format=rgba"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        source_path,
        "-vf",
        filter_str,
        "-frames:v",
        "1",
        output_path,
    ]

    logger.info(f"[DecorImage] Keying green -> RGBA PNG: {output_path}")
    if not FFmpegHelper.run_command(cmd):
        raise RuntimeError("FFmpeg failed to preprocess decor image.")
    if not os.path.isfile(output_path):
        raise RuntimeError("Processed decor image was not created.")
    return processed_filename


# --------------------------------------------------------------------------- #
# Manual mask: draw the screen area instead of keying one out
# --------------------------------------------------------------------------- #
def decor_mask_mode(record: dict) -> str:
    """How this image's transparent screen area is produced.

    ``chroma`` keys a green screen that is already painted in the photo;
    ``manual`` punches the ``frame`` rectangle straight into the alpha channel,
    so a photo that never had a green screen still works.
    """
    return "manual" if str(record.get("maskMode") or "chroma") == "manual" else "chroma"


def corner_radius_of(record: dict, frame: dict | None = None) -> int:
    """Rounded-corner radius in output pixels, clamped to what the rect allows."""
    frame = frame or _clamp_frame(record)
    try:
        radius = int(record.get("cornerRadius") or 0)
    except (TypeError, ValueError):
        radius = 0
    return max(0, min(radius, min(frame["w"], frame["h"]) // 2))


def _blur_keeping_hole_out(base, mask, radius: float):
    """Lam mo anh ma khong keo vung man hinh loang ra ngoai o cua.

    Mo thang ca tam anh la sai: Gaussian lay trung binh moi pixel quanh no, ke
    ca nhung pixel sap bi duc thanh lo. Vung man hinh trong anh thuong rat sang
    (hoac rat toi), nen no bi keo *ra ngoai* va ve mot quang sang om lay cua so
    video, rong toi 3 lan ban kinh. Do duoc voi phong=60 / man hinh=255 /
    sigma=20: pixel ngay sat mep o cua doc ra **156** thay vi 60.

    Cach chua la chuan hoa theo mask: mo ``rgb * keep`` roi chia cho ``keep``
    cung da mo. Moi pixel ket qua thanh trung binh cua *rieng* nhung pixel duoc
    giu lai, con vung lo khong dong gop gi. Do lai cung canh tren: 60.8 -- dung
    nhu chua mo bao gio.
    """
    from PIL import Image, ImageFilter

    import numpy as np

    def _blur_plane(plane):
        """Gaussian tren mot kenh. PIL khong mo duoc anh float nen di qua uint8:
        ca tu so lan mau so deu nam gon trong 0..255 nen khong mat gi dang ke."""
        img = Image.fromarray(np.clip(plane, 0, 255).astype(np.uint8), "L")
        return np.asarray(img.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)

    keep = np.asarray(mask, dtype=np.float32) / 255.0
    # Sat mep o cua mau so tut ve ~0.5, sau trong long lo thi ve 0; kep lai de
    # khong chia cho 0. Nhung pixel do deu nam trong lo va se bi alpha 0 xoa di.
    weight = np.maximum(_blur_plane(keep * 255.0) / 255.0, 1.0 / 255.0)

    rgb = np.asarray(base.convert("RGB"), dtype=np.float32)
    out = np.empty_like(rgb)
    for channel in range(3):
        out[:, :, channel] = _blur_plane(rgb[:, :, channel] * keep) / weight

    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def blur_radius_of(record: dict, settings: dict | None = None) -> float:
    """Do mo cua anh nen quanh o cua, tinh bang pixel cua khung 1920x1080.

    Chi co nghia o che do ``manual``: o do anh la mot mang mau dac bao quanh o
    cua da duc, lam mo no khien cua so video sac net noi han len. Che do
    ``chroma`` tra ve 0 -- ``colorkey`` chay trong cung mot luot ffmpeg nen mot
    ``gblur`` dat truoc se lam mau xanh loang ra ca phong, con dat sau thi lam
    nhoe chinh mep o cua vua key ra.

    ``blurRadius`` cua rieng anh: ``None``/thieu = theo mac dinh chung, so =
    tu dat rieng. Do do 0 la mot lua chon that ("anh nay khong mo"), khac han
    voi "chua chon gi".
    """
    if decor_mask_mode(record) != "manual":
        return 0.0
    own = record.get("blurRadius")
    if own is None:
        # ``settings`` la ban da giai quyet san, truyen vao de mot luot ap hang
        # loat khong phai doc lai index.json cho tung anh.
        resolved = settings if settings is not None else load_decor_settings()
        return _clamp_blur(resolved.get("backgroundBlur"))
    return _clamp_blur(own)


def border_of(record: dict, settings: dict | None = None) -> dict:
    """Vien (va bong) quanh o cua, cung luat voi ``blur_radius_of``.

    Chi co o che do ``manual``: anh ``chroma`` la anh TV that, da co san vien
    cua chinh cai TV. ``border`` cua rieng anh: ``None``/thieu = theo mac dinh
    chung, dict = tu dat rieng.
    """
    if decor_mask_mode(record) != "manual":
        return _clamp_border(None)
    own = record.get("border")
    if own is None:
        resolved = settings if settings is not None else load_decor_settings()
        return _clamp_border({
            "width": resolved.get("borderWidth"),
            "color": resolved.get("borderColor"),
            "shadow": resolved.get("borderShadow"),
        })
    return _clamp_border(own)


def _draw_border(base, frame: dict, radius: int, border: dict):
    """Bong + vong vien ve *ngoai* o cua, len chinh anh nen.

    Cung cach ``edit_styles.assets.card_frame`` ve cho "Hai lop cung nguon":
    vong vien om sat mep lo, ban kinh ngoai = bo goc + do day. Lo van giu
    nguyen kich thuoc nen video khong lech di dau; phan vong vien chui vao
    trong lo se bi alpha 0 xoa khi dap mask len sau.
    """
    from PIL import Image, ImageDraw

    from src.utils.edit_styles import assets as edit_assets

    x, y, w, h = frame["x"], frame["y"], frame["w"], frame["h"]
    if border["shadow"]:
        base = Image.alpha_composite(
            base, edit_assets.shadow((x, y, w, h), _DECOR_SHADOW_STRENGTH, size=base.size)
        )
    width = border["width"]
    if width:
        ImageDraw.Draw(base).rounded_rectangle(
            (x - width, y - width, x + w + width - 1, y + h + width - 1),
            radius=(radius + width) if radius else 0,
            fill=(*edit_assets.rgb(border["color"], (245, 240, 230)), 255),
        )
    return base


def build_manual_mask_png(source_path: str, record: dict, settings: dict | None = None) -> str:
    """Punch the frame rectangle into the photo's alpha; no green screen needed.

    ``colorkey`` can only clear pixels close to the key colour, so a photo with
    no green screen comes out fully opaque and hides the video completely. Here
    the rectangle *is* the alpha: exact, deterministic, and with no tolerance
    that could eat into the picture the way a widened ``similarity`` does.

    Writes the same ``<id>_keyed.png`` the chroma path writes, so everything
    downstream is unchanged -- the render, the frame preview, deletion, and the
    thumbnail's ``updatedAt`` cache-bust all keep working untouched.
    """
    from PIL import Image, ImageDraw, ImageOps

    image_id = str(record["id"])
    processed_filename = f"{image_id}_keyed.png"
    output_path = _absolute(processed_filename)
    width, height = target_size()
    frame = _clamp_frame(record)
    radius = corner_radius_of(record, frame)
    blur = blur_radius_of(record, settings)
    border = border_of(record, settings)

    with Image.open(source_path) as img:
        # The browser rotates by EXIF when it displays the photo, so the
        # rectangle the user dragged is in rotated coordinates. Match that here
        # or the hole lands somewhere else entirely on a phone photo.
        base = ImageOps.exif_transpose(img).convert("RGBA").resize(
            (width, height), Image.LANCZOS
        )

    # 255 keeps the photo, 0 lets the video through.
    mask = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(mask)
    box = (
        frame["x"],
        frame["y"],
        frame["x"] + frame["w"] - 1,
        frame["y"] + frame["h"] - 1,
    )
    if radius > 0:
        draw.rounded_rectangle(box, radius=radius, fill=0)
    else:
        draw.rectangle(box, fill=0)

    # Blur the photo *through* the same mask, so the screen area never bleeds
    # out and haloes the video window; then punch the crisp alpha on top, which
    # keeps the one edge that has to stay exact razor sharp. Skipped entirely
    # at 0 so the no-blur path is byte-for-byte what it has always been.
    if blur > 0:
        try:
            base = _blur_keeping_hole_out(base, mask, blur)
        except ImportError as exc:  # noqa: BLE001 - optional dependency guard
            logger.warning(f"[DecorImage] Blur unavailable, giu anh net: {exc}")

    # Ve sau blur de vien van sac. Tat (0px, khong bong) thi bo qua han, giu
    # nguyen duong cu tung byte.
    if _border_signature(border) is not None:
        base = _draw_border(base, frame, radius, border)

    base.putalpha(mask)

    os.makedirs(Config.STORY_DECOR_DIR, exist_ok=True)
    # Ghi ra file tam roi thay cho nhanh, khong ghi de thang len ban dang song:
    # mot luot ap do mo hang loat co the ve lai hang chuc PNG trong luc mot
    # render chay nen dang dua dung file do cho ffmpeg doc.
    tmp_path = output_path + ".tmp"
    base.save(tmp_path, "PNG")
    os.replace(tmp_path, output_path)
    logger.info(
        f"[DecorImage] Manual mask {frame} radius={radius} blur={blur} "
        f"border={_border_signature(border)} -> {output_path}"
    )
    return processed_filename


def regenerate_decor_mask(source_path: str, record: dict, settings: dict | None = None) -> str:
    """Rebuild the RGBA PNG through whichever mask mode the record asks for."""
    if decor_mask_mode(record) == "manual":
        return build_manual_mask_png(source_path, record, settings)
    return preprocess_decor_image(source_path, record)


def _baked_look_is_stale(record: dict, settings: dict | None = None) -> bool:
    """Do mo hoac vien dang yeu cau khac voi thu that su nam trong PNG."""
    if blur_radius_of(record, settings) != _clamp_blur(record.get("blurBaked") or 0):
        return True
    return _border_signature(border_of(record, settings)) != record.get("borderBaked")


def _rebuild_processed(record: dict, settings: dict | None = None):
    """Ve lai PNG cho record va ghi nho do mo vua nuong vao no.

    ``blurBaked`` la do mo that su dang nam trong file tren dia. Nho no ma
    viec "PNG co con dung khong" tro thanh mot phep so sanh, thay vi phai doan
    tu lich su: mot anh bi bo sot trong luot ap hang loat (file goc mat, dia
    day) se tu sua lai o lan PATCH ke tiep.
    """
    source_path = _absolute(str(record["filename"]))
    old_processed = record.get("processedFilename")
    # ``settings`` phai di kem suot ca duong: luc ap hang loat, gia tri moi chua
    # duoc ghi xuong dia, nen doc lai tu index se ra dung ban cu va ta se nuong
    # nham do mo roi con ghi lai "da nuong" mot con so khong dung su that.
    record["processedFilename"] = regenerate_decor_mask(source_path, record, settings)
    record["processedRelativePath"] = _relative(record["processedFilename"])
    record["blurBaked"] = blur_radius_of(record, settings)
    record["borderBaked"] = _border_signature(border_of(record, settings))
    if old_processed and old_processed != record["processedFilename"]:
        try:
            os.remove(_absolute(str(old_processed)))
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def create_decor_image(file_storage, group: str = "", mode: str = "") -> dict:
    if not file_storage or not file_storage.filename:
        raise ValueError("Chua chon file anh decor.")

    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise ValueError("Anh decor phai la PNG/JPG/WEBP.")

    os.makedirs(Config.STORY_DECOR_DIR, exist_ok=True)
    image_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(file_storage.filename)
    filename = f"{image_id}_{safe_name}"
    file_storage.save(_absolute(filename))
    return _register_decor_image(image_id, filename, os.path.splitext(safe_name)[0], group, mode)


_import_lock = threading.Lock()


def find_decor_by_source(provider: str, source_id: str) -> dict | None:
    """The decor image already imported from this provider photo, if any."""
    for item in load_decor_index().get("images", []):
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        if source.get("provider") == provider and str(source.get("id")) == str(source_id):
            return item
    return None


def _source_key(record: dict) -> str:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    if source.get("provider") and source.get("id"):
        return f"{source['provider']}:{source['id']}"
    return ""


def _retired_source_keys(index: dict) -> list[str]:
    """Provider photos whose record was purged after it was used (see ``purge_used_decor_images``)."""
    keys = index.get("retiredSources")
    return [str(key) for key in keys if key] if isinstance(keys, list) else []


def imported_source_keys() -> list[str]:
    """``provider:id`` of every decor image that came from a provider search.

    Purged photos stay listed: the search panel hides these, and a spent photo
    must not come back through a re-import.
    """
    index = load_decor_index()
    keys = [key for key in map(_source_key, index.get("images", [])) if key]
    return keys + _retired_source_keys(index)


def import_decor_image(item: dict, group: str = "") -> dict:
    """Download a Pexels/Pixabay search hit and register it like an upload.

    Raises ``FileExistsError`` when that photo was imported before.
    """
    from src.utils.decor_image_search import download_provider_image

    provider = str(item.get("provider") or "").strip().lower()
    source_id = str(item.get("id") or "").strip()
    download_url = str(item.get("downloadUrl") or "").strip()
    if provider not in {"pexels", "pixabay"} or not source_id or not download_url:
        raise ValueError("Anh tu provider khong hop le.")
    if find_decor_by_source(provider, source_id):
        raise FileExistsError(f"Anh {provider} {source_id} da co trong thu vien decor.")
    if f"{provider}:{source_id}" in _retired_source_keys(load_decor_index()):
        raise FileExistsError(f"Anh {provider} {source_id} da dung cho 1 video va da bi don khoi thu vien.")

    image_id = str(uuid.uuid4())[:8]
    # The download (seconds to over a minute on the Pexels CDN) runs outside the
    # lock so the UI can import several photos in parallel; only the index
    # read-modify-write below is serialised.
    path = download_provider_image(download_url, Config.STORY_DECOR_DIR, f"{image_id}_{provider}_{source_id}")
    display_name = secure_filename(str(item.get("title") or ""))[:60] or f"{provider}_{source_id}"
    source = {
        "provider": provider,
        "id": source_id,
        "pageUrl": str(item.get("pageUrl") or ""),
        "author": str(item.get("author") or ""),
    }
    try:
        with _import_lock:
            if find_decor_by_source(provider, source_id):
                raise FileExistsError(f"Anh {provider} {source_id} da co trong thu vien decor.")
            # Stock photos never carry a painted green screen, and detection can
            # still latch onto green scenery (grass, walls) — always start manual.
            return _register_decor_image(image_id, os.path.basename(path), display_name, group, "manual", source)
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


def _register_decor_image(
    image_id: str,
    filename: str,
    display_name: str,
    group: str = "",
    mode: str = "",
    source: dict | None = None,
) -> dict:
    """Detect/mask a source file already saved as ``filename`` and index it."""
    filepath = _absolute(filename)
    width, height = target_size()
    # An explicit "manual" skips detection entirely: the user wants to draw the
    # screen area themselves even on a photo that does have some green in it.
    forced_manual = str(mode or "").strip().lower() == "manual"
    detected = None if forced_manual else detect_green_frame(filepath)
    if detected:
        frame, key_color = detected
        mask_mode = "chroma"
    else:
        # No green to key: start with a centred 16:9 rectangle the user drags
        # onto the screen in the photo, and punch that rectangle directly.
        # Keying this photo would only ever produce a fully opaque PNG that
        # hides the video, so manual is the only mode that can work here.
        frame = {
            "x": width // 8,
            "y": height // 8,
            "w": width * 3 // 4,
            "h": (width * 3 // 4) * 9 // 16,
        }
        key_color = Config.STORY_DECOR_KEY_COLOR
        mask_mode = "manual"

    record = {
        "id": image_id,
        "name": display_name or image_id,
        "group": normalize_group(group),
        "filename": filename,
        "relativePath": _relative(filename),
        "keyColor": key_color,
        "similarity": Config.STORY_DECOR_SIMILARITY,
        "blend": Config.STORY_DECOR_BLEND,
        "frame": frame,
        "maskMode": mask_mode,
        "cornerRadius": 0,
        "overscan": Config.STORY_DECOR_OVERSCAN,
        # None = theo mac dinh chung. Anh moi upload di theo thanh truot chung
        # cho toi khi nguoi dung tu dat rieng cho no.
        "blurRadius": None,
        "autoDetected": bool(detected),
        "enabled": True,
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
    }
    if source:
        record["source"] = source

    _rebuild_processed(record)

    index = load_decor_index()
    images = index.get("images", [])
    images.append(record)
    index["images"] = images
    save_decor_index(index)
    return record


def get_decor_image(image_id: str) -> dict | None:
    images = load_decor_index().get("images", [])
    return next((item for item in images if item.get("id") == image_id), None)


def update_decor_image(image_id: str, updates: dict) -> dict | None:
    index = load_decor_index()
    images = index.get("images", [])
    record = next((item for item in images if item.get("id") == image_id), None)
    if not record:
        return None

    regenerate = False
    for key in ("keyColor", "similarity", "blend"):
        if key in updates and updates[key] is not None and record.get(key) != updates[key]:
            record[key] = updates[key]
            regenerate = True

    if "maskMode" in updates and updates["maskMode"] is not None:
        next_mode = "manual" if str(updates["maskMode"]) == "manual" else "chroma"
        if next_mode != decor_mask_mode(record):
            record["maskMode"] = next_mode
            regenerate = True

    # In manual mode the rectangle *is* the mask, so moving or reshaping it has
    # to redraw the PNG. In chroma mode the rectangle only says where the video
    # gets fitted and the PNG does not depend on it, so nothing is regenerated.
    manual = decor_mask_mode(record) == "manual"

    if isinstance(updates.get("frame"), dict):
        next_frame = _clamp_frame({"frame": updates["frame"]})
        if manual and next_frame != record.get("frame"):
            regenerate = True
        record["frame"] = next_frame

    if "cornerRadius" in updates and updates["cornerRadius"] is not None:
        try:
            next_radius = max(0, int(updates["cornerRadius"]))
        except (TypeError, ValueError):
            next_radius = 0
        if manual and next_radius != int(record.get("cornerRadius") or 0):
            regenerate = True
        record["cornerRadius"] = next_radius

    # Membership, not is-not-None: an explicit ``null`` means "back to the
    # shared default", which is a different state from "not sent at all".
    if "blurRadius" in updates:
        raw = updates["blurRadius"]
        record["blurRadius"] = None if raw is None else _clamp_blur(raw)

    # Cung luat voi blurRadius: ``null`` = quay ve mac dinh chung.
    if "border" in updates:
        raw = updates["border"]
        record["border"] = None if raw is None else _clamp_border(raw)

    # So muc do mo dang yeu cau voi muc that su nam trong PNG tren dia, thay vi
    # rinh tung thay doi. Nho vay mot anh lech nhip vi bat ky ly do nao -- doi
    # mac dinh chung luc no dang loi, ban ghi cu chua tung co blur -- deu tu
    # sua lai o lan luu ke tiep. Vien cung vay; record cu chua co
    # ``borderBaked`` la ``None``, khop voi vien tat nen khong bi ve lai.
    if manual and _baked_look_is_stale(record):
        regenerate = True

    for key in ("name", "overscan"):
        if key in updates and updates[key] is not None:
            record[key] = updates[key]

    # "" is a meaningful value here (back to ungrouped), so this cannot ride
    # along with the is-not-None loop above.
    if "group" in updates:
        record["group"] = normalize_group(updates["group"])

    if "enabled" in updates and updates["enabled"] is not None:
        record["enabled"] = bool(updates["enabled"])

    # Hand-editing the used mark is the way back: an image spent on a batch that
    # was cancelled, or one the user simply wants to run again, is freed here.
    if "used" in updates and updates["used"] is not None:
        record["used"] = bool(updates["used"])
        record["usedAt"] = datetime.now().isoformat() if record["used"] else None

    if not processed_abs_path(record):
        regenerate = True

    if regenerate:
        _rebuild_processed(record)

    record["updatedAt"] = datetime.now().isoformat()
    save_decor_index(index)
    return record


def apply_decor_blur_default(value) -> tuple[dict, list]:
    """Doi do mo mac dinh chung, va ve lai nhung anh dang di theo no."""
    return apply_decor_settings({"backgroundBlur": value})


def apply_decor_settings(updates: dict) -> tuple[dict, list]:
    """Doi mac dinh chung (do mo va/hoac vien), va ve lai nhung anh di theo no.

    Nhan bat ky khoa nao trong ``backgroundBlur``, ``borderWidth``,
    ``borderColor``, ``borderShadow``; khoa khong gui thi giu nguyen. Chi dung
    toi record ``manual`` ma PNG tren dia khong con khop: anh da tu dat rieng
    thi ban nuong da khop san, con anh ``chroma`` khong lien quan, nen deu
    khong bi dong vao. Ghi ``index.json`` dung mot lan o cuoi -- ghi sau moi
    anh vua thua vua de lai mot file nua vo neu co su co giua chung.
    """
    # Mot cap load/save duy nhat cho ca cai dat lan moi anh vua ve lai. Neu
    # luu cai dat truoc roi moi ve tung anh, mot su co giua chung se de lai
    # index noi "mo 20px" trong khi moi PNG tren dia van dang net.
    index = load_decor_index()
    images = index.get("images", [])
    settings = load_decor_settings(index)

    stored = dict(index.get("settings") or {})
    if "backgroundBlur" in updates:
        stored["backgroundBlur"] = _clamp_blur(updates["backgroundBlur"])
    if "borderWidth" in updates:
        stored["borderWidth"] = _clamp_border_width(updates["borderWidth"])
    if "borderColor" in updates:
        stored["borderColor"] = _clamp_border_color(updates["borderColor"])
    if "borderShadow" in updates:
        stored["borderShadow"] = bool(updates["borderShadow"])
    index["settings"] = stored
    resolved = load_decor_settings(index)
    now = datetime.now().isoformat()
    rebuilt = 0
    failed = 0

    for record in images:
        if decor_mask_mode(record) != "manual":
            continue
        if not _baked_look_is_stale(record, resolved):
            continue
        try:
            _rebuild_processed(record, resolved)
        except Exception as exc:  # noqa: BLE001 - mot anh hong khong duoc chan ca luot
            # Anh goc bi xoa ngoai app, dia day... Bo qua anh do: ``blurBaked``
            # cua no khong nhich nen lan luu sau se tu thu lai.
            failed += 1
            logger.warning(f"[DecorImage] Khong ve lai duoc {record.get('id')}: {exc}")
            continue
        # updatedAt la thu frontend dung de pha cache thumbnail, va cung la khoa
        # cache cua lop kinh trong tv_glass -- khong nhich len thi ca hai deu
        # tiep tuc phuc vu ban truoc khi mo.
        record["updatedAt"] = now
        rebuilt += 1

    index["images"] = images
    save_decor_index(index)
    logger.info(
        f"[DecorImage] Mac dinh chung {settings} -> {resolved}, "
        f"ve lai {rebuilt} anh, loi {failed}."
    )
    return resolved, images


def _remove_file(path: str) -> tuple[int, bool]:
    """``(bytes freed, gone)``; a file that was never there counts as gone."""
    try:
        size = os.path.getsize(path)
        os.remove(path)
    except FileNotFoundError:
        return 0, True
    except OSError as exc:
        logger.warning(f"[DecorImage] Khong xoa duoc {path}: {exc}")
        return 0, False
    return size, True


def _remove_decor_files(record: dict) -> tuple[int, bool]:
    """Delete an image's source, keyed PNG and alignment stills.

    Returns ``(bytes freed, every file gone)``. Only removals that succeeded
    are counted, so the reported size is what really left the disk.
    """
    image_id = str(record.get("id") or "")
    paths = [_absolute(str(record[key])) for key in ("filename", "processedFilename") if record.get(key)]
    # Alignment stills are written per image by the frame-preview endpoint; they
    # have no record of their own, so they only ever get cleaned up here.
    preview_dir = os.path.join(Config.STORY_DECOR_DIR, "previews")
    if image_id and os.path.isdir(preview_dir):
        paths += [os.path.join(preview_dir, name) for name in os.listdir(preview_dir)
                  if name.startswith(f"{image_id}_")]

    freed, all_gone = 0, True
    for path in paths:
        size, gone = _remove_file(path)
        freed += size
        all_gone = all_gone and gone
    return freed, all_gone


def delete_decor_image_record(image_id: str) -> bool:
    index = load_decor_index()
    images = index.get("images", [])
    record = next((item for item in images if item.get("id") == image_id), None)
    if not record:
        return False

    _remove_decor_files(record)

    index["images"] = [item for item in images if item.get("id") != image_id]
    save_decor_index(index)
    return True


# --------------------------------------------------------------------------- #
# Render-side resolution + batch rotation
# --------------------------------------------------------------------------- #
def resolve_decor_image(image_id: str) -> tuple[dict, str] | None:
    """Record + on-disk keyed PNG for a decor image, or None when unusable."""
    if not image_id:
        return None
    record = get_decor_image(image_id)
    if not record:
        return None
    path = processed_abs_path(record)
    if not path:
        logger.warning(f"[DecorImage] Processed PNG missing for {image_id}.")
        return None
    return record, path


def get_enabled_decor_images(group: str | None = None, *, unused_only: bool = False) -> list[dict]:
    """Usable decor images; pass ``group`` to keep only one theme.

    ``unused_only`` drops the ones already spent — what a render needs. A
    preview does not consume anything, so it leaves the flag off and can still
    show a spent image.
    """
    wanted = None if group is None else normalize_group(group)
    return [
        item
        for item in load_decor_index().get("images", [])
        if item.get("enabled", True)
        and processed_abs_path(item)
        and (wanted is None or decor_group_of(item) == wanted)
        and not (unused_only and decor_is_used(item))
    ]


# --------------------------------------------------------------------------- #
# One-time use
# --------------------------------------------------------------------------- #
# A decor image is a recognisable photo, not a neutral effect: the same living
# room behind two videos reads as the same video. So an image is dealt to
# exactly one render, marked ``used`` the moment it is dealt, and never enters a
# rotation again. Running out is a normal state, not an error — the caller
# swaps the video over to a layout that needs no decor image (see
# ``story_video_routes._resolve_edit_selection``).
#
# The flag gates *allocation* only. ``resolve_decor_image`` ignores it on
# purpose, so a retried or resumed batch still renders the image it was already
# dealt.


def decor_is_used(record: dict) -> bool:
    return bool(record.get("used"))


def claim_decor_images(image_ids: list[str], count: int) -> tuple[list[str], int]:
    """Take up to ``count`` unused decor images and mark them used, in one write.

    Returns ``(claimed, usable_total)``: the ids dealt out — shuffled, all
    distinct, and possibly fewer than ``count`` or empty — plus how many of
    ``image_ids`` exist with a keyed PNG at all, spent or not. That second number
    is what tells the two shortfalls apart: ``usable_total == 0`` means the
    selection itself is broken (deleted, or never keyed) and deserves an error,
    while a short ``claimed`` list only means the library ran out.

    Picking and marking under a single index load/save is what keeps two batches
    queued in the same moment from being dealt the same image.
    """
    wanted = list(dict.fromkeys(
        str(item or "").strip() for item in (image_ids or []) if str(item or "").strip()
    ))
    if not wanted or count <= 0:
        return [], 0

    index = load_decor_index()
    by_id = {str(item.get("id")): item for item in index.get("images", [])}

    usable = [image_id for image_id in wanted
              if by_id.get(image_id) and processed_abs_path(by_id[image_id])]
    free = [image_id for image_id in usable if not decor_is_used(by_id[image_id])]
    random.shuffle(free)
    claimed = free[:count]

    if claimed:
        now = datetime.now().isoformat()
        for image_id in claimed:
            record = by_id[image_id]
            record["used"] = True
            record["usedAt"] = now
            record["updatedAt"] = now
        save_decor_index(index)

    logger.info(
        f"[DecorImage] Claimed {len(claimed)}/{count} image(s) from {len(usable)} usable; "
        f"{len(free) - len(claimed)} unused left in this selection."
    )
    return claimed, len(usable)


def release_decor_images(image_ids) -> int:
    """Put claimed images back in the pool. Returns how many were freed.

    For the abort paths only: a batch that fails to be *created* after its
    images were dealt would otherwise spend them on a batch that never runs.
    """
    wanted = {str(item or "").strip() for item in (image_ids or []) if str(item or "").strip()}
    if not wanted:
        return 0

    index = load_decor_index()
    freed = 0
    for record in index.get("images", []):
        if str(record.get("id")) in wanted and decor_is_used(record):
            record["used"] = False
            record["usedAt"] = None
            record["updatedAt"] = datetime.now().isoformat()
            freed += 1
    if freed:
        save_decor_index(index)
        logger.info(f"[DecorImage] Released {freed} claimed image(s) back to the pool.")
    return freed


def reset_decor_used(group: str | None = None, image_ids=None) -> int:
    """Clear the used mark so a set can be rotated again. Returns how many were cleared.

    ``image_ids`` wins when given; otherwise ``group`` clears one theme and
    ``None`` clears the whole library.
    """
    wanted = None
    if image_ids is not None:
        wanted = {str(item or "").strip() for item in image_ids if str(item or "").strip()}
        if not wanted:
            return 0
    target_group = None if group is None else normalize_group(group)

    index = load_decor_index()
    cleared = 0
    for record in index.get("images", []):
        if not decor_is_used(record):
            continue
        if wanted is not None and str(record.get("id")) not in wanted:
            continue
        if wanted is None and target_group is not None and decor_group_of(record) != target_group:
            continue
        record["used"] = False
        record["usedAt"] = None
        record["updatedAt"] = datetime.now().isoformat()
        cleared += 1
    if cleared:
        save_decor_index(index)
    return cleared


# --------------------------------------------------------------------------- #
# Purging spent images
# --------------------------------------------------------------------------- #
# A used image never enters a rotation again, so its files only take up disk.
# But "used" means "dealt", not "rendered": a queued batch, a failed item
# waiting for retry, or a single render still in flight reads the PNG later
# (resolve_decor_image ignores the flag on purpose). The caller passes those
# ids as ``keep_ids``. Batches are found on disk; a single render has no batch
# file, so it holds its image in this process for as long as it runs.
_held_lock = threading.Lock()
_held: dict[str, int] = {}


def hold_decor_image(image_id: str):
    image_id = str(image_id or "").strip()
    if image_id:
        with _held_lock:
            _held[image_id] = _held.get(image_id, 0) + 1


def release_decor_hold(image_id: str):
    image_id = str(image_id or "").strip()
    with _held_lock:
        if _held.get(image_id, 0) > 1:
            _held[image_id] -= 1
        else:
            _held.pop(image_id, None)


def held_decor_ids() -> set[str]:
    with _held_lock:
        return set(_held)


def purge_used_decor_images(group: str | None = None, keep_ids=None) -> dict:
    """Delete the files and records of used decor images. ``group=None`` = whole library.

    Returns ``{"deleted", "freedBytes", "kept", "failed"}``: ``kept`` were in
    ``keep_ids``; ``failed`` had a file Windows would not let go of, so their
    record stays and the next purge tries again instead of orphaning the file.
    A purged provider photo is remembered in ``retiredSources`` so the search
    panel keeps hiding it.
    """
    keep = {str(item) for item in (keep_ids or []) if item}
    target_group = None if group is None else normalize_group(group)

    candidates = [
        record for record in load_decor_index().get("images", [])
        if decor_is_used(record)
        and (target_group is None or decor_group_of(record) == target_group)
    ]
    kept = [record for record in candidates if str(record.get("id")) in keep]

    # Files go first, outside the index write, so the index is only held for a
    # quick load/save below rather than for the whole deletion.
    deleted: dict[str, dict] = {}
    freed_bytes, failed = 0, 0
    for record in candidates:
        if str(record.get("id")) in keep:
            continue
        freed, gone = _remove_decor_files(record)
        freed_bytes += freed
        if gone:
            deleted[str(record.get("id"))] = record
        else:
            failed += 1

    if deleted:
        index = load_decor_index()
        index["images"] = [item for item in index.get("images", []) if str(item.get("id")) not in deleted]
        retired = _retired_source_keys(index)
        for key in map(_source_key, deleted.values()):
            if key and key not in retired:
                retired.append(key)
        if retired:
            index["retiredSources"] = retired
        save_decor_index(index)

    logger.info(
        f"[DecorImage] Purged {len(deleted)} used image(s), {freed_bytes / 1048576:.1f} MB freed; "
        f"kept {len(kept)} still needed by a render, {failed} failed (group={group!r})."
    )
    return {"deleted": len(deleted), "freedBytes": freed_bytes, "kept": len(kept), "failed": failed}
