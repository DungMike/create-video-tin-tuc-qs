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
import shutil
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


def build_manual_mask_png(source_path: str, record: dict) -> str:
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
    base.putalpha(mask)

    os.makedirs(Config.STORY_DECOR_DIR, exist_ok=True)
    base.save(output_path, "PNG")
    logger.info(f"[DecorImage] Manual mask {frame} radius={radius} -> {output_path}")
    return processed_filename


def regenerate_decor_mask(source_path: str, record: dict) -> str:
    """Rebuild the RGBA PNG through whichever mask mode the record asks for."""
    if decor_mask_mode(record) == "manual":
        return build_manual_mask_png(source_path, record)
    return preprocess_decor_image(source_path, record)


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
    filepath = _absolute(filename)
    file_storage.save(filepath)

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
        "name": os.path.splitext(safe_name)[0] or image_id,
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
        "autoDetected": bool(detected),
        "enabled": True,
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
    }

    record["processedFilename"] = regenerate_decor_mask(filepath, record)
    record["processedRelativePath"] = _relative(record["processedFilename"])

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

    for key in ("name", "overscan"):
        if key in updates and updates[key] is not None:
            record[key] = updates[key]

    # "" is a meaningful value here (back to ungrouped), so this cannot ride
    # along with the is-not-None loop above.
    if "group" in updates:
        record["group"] = normalize_group(updates["group"])

    if "enabled" in updates and updates["enabled"] is not None:
        record["enabled"] = bool(updates["enabled"])

    if not processed_abs_path(record):
        regenerate = True

    if regenerate:
        source_path = _absolute(str(record["filename"]))
        old_processed = record.get("processedFilename")
        record["processedFilename"] = regenerate_decor_mask(source_path, record)
        record["processedRelativePath"] = _relative(record["processedFilename"])
        if old_processed and old_processed != record["processedFilename"]:
            try:
                os.remove(_absolute(str(old_processed)))
            except OSError:
                pass

    record["updatedAt"] = datetime.now().isoformat()
    save_decor_index(index)
    return record


def delete_decor_image_record(image_id: str) -> bool:
    index = load_decor_index()
    images = index.get("images", [])
    record = next((item for item in images if item.get("id") == image_id), None)
    if not record:
        return False

    for key in ("filename", "processedFilename"):
        filename = record.get(key)
        if filename:
            try:
                os.remove(_absolute(str(filename)))
            except OSError:
                pass

    # Alignment stills are written per image by the frame-preview endpoint; they
    # have no record of their own, so they only ever get cleaned up here.
    preview_dir = os.path.join(Config.STORY_DECOR_DIR, "previews")
    if os.path.isdir(preview_dir):
        for name in os.listdir(preview_dir):
            if name.startswith(f"{image_id}_"):
                try:
                    os.remove(os.path.join(preview_dir, name))
                except OSError:
                    pass

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


def get_enabled_decor_images(group: str | None = None) -> list[dict]:
    """Usable decor images; pass ``group`` to keep only one theme."""
    wanted = None if group is None else normalize_group(group)
    return [
        item
        for item in load_decor_index().get("images", [])
        if item.get("enabled", True)
        and processed_abs_path(item)
        and (wanted is None or decor_group_of(item) == wanted)
    ]


def build_decor_rotation(image_ids: list[str], count: int) -> list[str]:
    """Assign a decor image to each of ``count`` videos, shuffled without replacement.

    The deck is shuffled, dealt out, then reshuffled — so a batch of 50 over 7
    images uses all 7 in every run of 7, in a different order each time, and no
    image is starved the way independent random picks would allow.
    """
    from src.utils.asset_rotation import deal_rotation

    usable = [image_id for image_id in image_ids if resolve_decor_image(image_id)]
    return deal_rotation(usable, count)
