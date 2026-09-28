"""Procedural graphics for edit styles (PIL + numpy).

Static PNGs depend only on a record's params, so they are cached by a hash of
them under ``STORY_EDIT_STYLE_DIR/cache`` and built once. Per-video media (the
voice-reactive bars) is written into the story's own temp dir.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.config import Config
from src.utils.logger import logger

W, H = 1920, 1080
_LOCK = threading.Lock()


def _cache_dir() -> str:
    path = os.path.join(Config.STORY_EDIT_STYLE_DIR, "cache")
    os.makedirs(path, exist_ok=True)
    return path


def _cached_png(kind: str, key: dict, build) -> str:
    """Build ``build() -> PIL.Image`` once per distinct key; return the PNG path."""
    digest = hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:14]
    path = os.path.join(_cache_dir(), f"{kind}_{digest}.png")
    with _LOCK:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        tmp = f"{path}.{os.getpid()}.tmp.png"
        build().save(tmp, "PNG")
        os.replace(tmp, path)
    return path


def _rgb(hex_colour: str, default=(0, 0, 0)) -> tuple[int, int, int]:
    value = str(hex_colour or "").strip().lstrip("#")
    if len(value) != 6:
        return default
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return default


def _font(family: str, size: int):
    """A TrueType font for PIL text, from the story fonts dir or the bundled set."""
    wanted = str(family or "").lower().replace(" ", "").replace("-", "")
    for folder in (Config.STORY_FONTS_DIR, Config.STORY_BUNDLED_FONTS_DIR, "C:/Windows/Fonts"):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith((".ttf", ".otf")) and name.lower().replace("-", "").startswith(wanted):
                try:
                    return ImageFont.truetype(os.path.join(folder, name), size)
                except OSError:
                    continue
    return ImageFont.load_default()


def _punch(img: Image.Image, box, radius=0) -> Image.Image:
    """Make ``box`` (x, y, w, h) fully transparent: the video shows through there."""
    mask = Image.new("L", img.size, 255)
    x, y, w, h = box
    draw = ImageDraw.Draw(mask)
    if radius:
        draw.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=radius, fill=0)
    else:
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=0)
    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", img.size, 0), mask))
    return img


def _shadow(box, strength, blur=22, grow=10, drop=16, size=(W, H)) -> Image.Image:
    x, y, w, h = box
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rectangle((x - grow, y - grow + drop, x + w + grow, y + h + grow + drop), fill=int(strength))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    layer.putalpha(m.filter(ImageFilter.GaussianBlur(blur)))
    return layer


# --------------------------------------------------------------------------- #
def card_frame(p: dict) -> str:
    """Dim layer + soft shadow + border ring + rounded hole where the card sits."""
    r = p["rect"]
    box = (r["x"], r["y"], r["w"], r["h"])
    radius, border = int(p["radius"]), int(p["borderWidth"])

    def build():
        img = Image.new("RGBA", (W, H), (0, 0, 0, int(255 * float(p["dim"]))))
        if p["shadow"]:
            img = Image.alpha_composite(img, _shadow(box, p["shadowStrength"]))
        if border:
            ImageDraw.Draw(img).rounded_rectangle(
                (box[0] - border, box[1] - border, box[0] + box[2] + border - 1, box[1] + box[3] + border - 1),
                radius=(radius + border) if radius else 0, fill=(*_rgb(p["borderColor"]), 255))
        return _punch(img, box, radius)

    return _cached_png("card", {k: p[k] for k in ("rect", "radius", "borderWidth", "borderColor", "dim",
                                                   "shadow", "shadowStrength")}, build)


def letterbox_bar_height(aspect: str) -> int:
    try:
        ratio = float(aspect)
    except (TypeError, ValueError):
        ratio = 2.39
    return max(0, int(round((H - W / ratio) / 2)))


def letterbox_bars(p: dict) -> str:
    bar = letterbox_bar_height(p["aspect"])

    def build():
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        fill = (*_rgb(p["barColor"]), 255)
        d.rectangle((0, 0, W, bar), fill=fill)
        d.rectangle((0, H - bar, W, H), fill=fill)
        if int(p["ruleWidth"]):
            rule = (*_rgb(p["ruleColor"]), 170)
            d.rectangle((0, bar, W, bar + int(p["ruleWidth"]) - 1), fill=rule)
            d.rectangle((0, H - bar - int(p["ruleWidth"]), W, H - bar - 1), fill=rule)
        return img

    return _cached_png("letterbox", {k: p[k] for k in ("aspect", "barColor", "ruleColor", "ruleWidth")}, build)


def film_frame(p: dict) -> str:
    """Procedural frame around a transparent picture window: film strip, polaroid,
    notebook page or wooden picture frame (``variant``)."""
    variant = p.get("variant", "film")
    if variant == "polaroid":
        return _polaroid_frame(p)
    if variant == "notebook":
        return _notebook_frame(p)
    if variant == "wood":
        return _wood_frame(p)
    r = p["rect"]
    hole = (r["x"], r["y"], r["w"], r["h"])

    def build():
        img = Image.new("RGBA", (W, H), (*_rgb(p["stripColor"]), 255))
        d = ImageDraw.Draw(img)
        sprocket = (0, 0, 0, 0) if p["sprocketMode"] == "open" else (0, 0, 0, 255)
        top_y = max(8, hole[1] // 2 - 28)
        bottom_y = min(H - 64, hole[1] + hole[3] + (H - hole[1] - hole[3]) // 2 - 28)
        for row_y in (top_y, bottom_y):
            for sx in range(20, W, 72):
                d.rounded_rectangle((sx, row_y, sx + 40, row_y + 56), radius=8, fill=sprocket)
        f = _font("ChakraPetch-Medium", 22)
        ink = (*_rgb(p["edgeTextColor"]), 255)
        if p.get("edgeText"):
            d.text((hole[0] + 20, max(2, hole[1] - 32)), p["edgeText"], fill=ink, font=f)
        if p.get("edgeTextBottom"):
            d.text((hole[0] + hole[2] - 220, min(H - 30, hole[1] + hole[3] + 6)), p["edgeTextBottom"], fill=ink, font=f)
        # ImageDraw writes the fill's alpha as-is, so "open" sprockets are real
        # holes: the video shows through them like light through film.
        return _punch(img, hole, int(p["radius"]))

    key = {k: p[k] for k in ("rect", "radius", "stripColor", "sprocketMode", "edgeText", "edgeTextBottom",
                             "edgeTextColor")}
    return _cached_png("film", key, build)


def _frame_key(p: dict, *keys) -> dict:
    return {"variant": p.get("variant"), "rect": p["rect"], **{k: p.get(k) for k in keys}}


def _polaroid_frame(p: dict) -> str:
    """A polaroid card (thick bottom border, handwritten caption) lying on a table."""
    r = p["rect"]
    x, y, w, h = r["x"], r["y"], r["w"], r["h"]

    def build():
        img = paper((W, H), _rgb(p["stripColor"], (18, 16, 14)), grain=5, seed=21, fibres=6)
        side = max(18, int(w * 0.03))
        bottom = max(60, int(h * 0.16))
        card = (x - side, y - side, w + 2 * side, h + side + bottom)
        img = Image.alpha_composite(img, _shadow(card, 170, blur=16, grow=4, drop=12))
        d = ImageDraw.Draw(img)
        d.rectangle((card[0], card[1], card[0] + card[2] - 1, card[1] + card[3] - 1),
                    fill=(*_rgb(p["frameColor"], (244, 241, 234)), 255))
        if p.get("edgeText"):
            f = _font("Sriracha", max(24, bottom // 3))
            d.text((x + 12, y + h + bottom // 2 - bottom // 6), p["edgeText"],
                   fill=(*_rgb(p["edgeTextColor"]), 255), font=f)
        return _punch(img, (x, y, w, h))

    return _cached_png("polaroid", _frame_key(p, "stripColor", "frameColor", "edgeText", "edgeTextColor"), build)


def _notebook_frame(p: dict) -> str:
    """A ruled notebook page with a red margin, spiral holes and a taped photo window."""
    r = p["rect"]
    x, y, w, h = r["x"], r["y"], r["w"], r["h"]

    def build():
        img = paper((W, H), _rgb(p["frameColor"], (243, 238, 223)), grain=4, seed=31, fibres=8)
        d = ImageDraw.Draw(img)
        line = (*_rgb(p["lineColor"], (157, 180, 208)), 150)
        for yy in range(96, H, 44):
            d.line((0, yy, W, yy), fill=line, width=2)
        d.line((110, 0, 110, H), fill=(208, 80, 80, 170), width=3)
        for yy in range(60, H, 90):  # spiral binding holes down the left edge
            d.ellipse((34, yy, 62, yy + 28), fill=(40, 36, 32, 255))
        img = Image.alpha_composite(img, _shadow((x, y, w, h), 120, blur=10, grow=2, drop=6))
        d = ImageDraw.Draw(img)
        d.rectangle((x - 10, y - 10, x + w + 9, y + h + 9), fill=(252, 250, 244, 255))
        for tx, ty, ang in ((x - 50, y - 36, -30), (x + w - 90, y - 32, 26)):
            tape = Image.new("RGBA", (170, 48), (236, 222, 180, 185)).rotate(ang, expand=True,
                                                                           resample=Image.BICUBIC)
            img.alpha_composite(tape, (max(0, tx), max(0, ty)))
        return _punch(img, (x, y, w, h))

    return _cached_png("notebook", _frame_key(p, "frameColor", "lineColor"), build)


def _wood_frame(p: dict) -> str:
    """A wooden picture frame: grained moulding, a light mat and a bevel round the picture."""
    r = p["rect"]
    x, y, w, h = r["x"], r["y"], r["w"], r["h"]

    def build():
        wall = paper((W, H), _rgb(p["stripColor"], (18, 16, 14)), grain=4, seed=41, fibres=4)
        mat = 56
        moulding = 64
        outer = (x - mat - moulding, y - mat - moulding, w + 2 * (mat + moulding), h + 2 * (mat + moulding))
        img = Image.alpha_composite(wall, _shadow(outer, 190, blur=20, grow=6, drop=14))
        base = np.array(_rgb(p["woodColor"], (107, 66, 38)), np.float32)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        rng = np.random.default_rng(7)
        warp = np.asarray(Image.fromarray((rng.random((H // 16, W // 16)) * 255).astype(np.uint8))
                          .resize((W, H), Image.BICUBIC), np.float32) / 255
        grain = 0.5 + 0.5 * np.sin((yy * 0.12 + xx * 0.015) + warp * 9)
        wood = base[None, None, :] * (0.72 + 0.4 * grain[..., None])
        wood_img = Image.fromarray(wood.clip(0, 255).astype(np.uint8)).convert("RGBA")
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        md.rectangle((outer[0], outer[1], outer[0] + outer[2] - 1, outer[1] + outer[3] - 1), fill=255)
        img.paste(wood_img, (0, 0), mask)
        d = ImageDraw.Draw(img)
        d.rectangle((x - mat, y - mat, x + w + mat - 1, y + h + mat - 1),
                    fill=(*_rgb(p["frameColor"], (239, 232, 218)), 255))
        d.rectangle((x - 6, y - 6, x + w + 5, y + h + 5), fill=(205, 196, 178, 255))  # bevel
        return _punch(img, (x, y, w, h))

    return _cached_png("wood", _frame_key(p, "stripColor", "frameColor", "woodColor"), build)


# --------------------------------------------------------------------------- #
# Shared building blocks for the phase-2 layouts
# --------------------------------------------------------------------------- #
def paper(size, rgb, grain=7.0, seed=11, fibres=18, vignette=0.28) -> Image.Image:
    """Paper-like fill: flat colour + fine grain + soft fibres + a faint vignette."""
    w, h = size
    rng = np.random.default_rng(seed)
    arr = np.ones((h, w, 3), np.float32) * np.array(rgb, np.float32)
    if grain:
        arr += rng.normal(0, float(grain), (h, w, 1))
    if fibres:
        fib = Image.fromarray((rng.random((max(1, h // 4), max(1, w // 4))) * 255).astype(np.uint8))
        fib = np.asarray(fib.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(2)), np.float32)
        arr += (fib[..., None] / 255 - 0.5) * float(fibres)
    if vignette:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        arr *= (1 - vignette * (((xx - w / 2) / (w / 1.3)) ** 2 + ((yy - h / 2) / (h / 1.2)) ** 2))[..., None]
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8)).convert("RGBA")


def user_image(ref) -> Image.Image | None:
    """An uploaded picture (image field value), or None when unset/missing."""
    from src.utils.edit_styles.store import image_abspath

    path = image_abspath(ref) if ref else None
    if not path:
        return None
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


def image_key(ref) -> list:
    """Cache-key part for an uploaded picture: its name and modification time."""
    from src.utils.edit_styles.store import image_abspath

    path = image_abspath(ref) if ref else None
    return [ref, os.path.getmtime(path)] if path else [None]


def cover(img: Image.Image, size) -> Image.Image:
    from PIL import ImageOps

    return ImageOps.fit(img, tuple(size), method=Image.LANCZOS)


def cached_png(kind: str, key: dict, build) -> str:
    """Public alias of the hash cache for layout modules."""
    return _cached_png(kind, key, build)


def rgb(hex_colour: str, default=(0, 0, 0)) -> tuple[int, int, int]:
    return _rgb(hex_colour, default)


def font(family: str, size: int):
    return _font(family, size)


def punch(img: Image.Image, box, radius=0) -> Image.Image:
    return _punch(img, box, radius)


def shadow(box, strength, blur=22, grow=10, drop=16, size=(W, H)) -> Image.Image:
    return _shadow(box, strength, blur, grow, drop, size)


def solid(hex_colour: str, opacity: float, size=(W, H)) -> str:
    """A flat colour sheet with constant alpha (flash, dip, scan band)."""
    rgb = _rgb(hex_colour)
    alpha = max(0, min(255, int(round(255 * float(opacity)))))
    return _cached_png("solid", {"c": rgb, "a": alpha, "s": list(size)},
                       lambda: Image.new("RGBA", tuple(size), (*rgb, alpha)))


def styled_decor(keyed_png: str, frame: dict, radius: int, blur: float, border: dict, cache_key: dict) -> str:
    """The decor PNG blurred around the screen hole and/or ringed by a shadow +
    border (TV layouts).

    Same drawing as the decor library's own baked blur and border: the blur
    never pulls the hole's colour out into the room, it comes first so the ring
    stays crisp, and the hole is restored at the end so nothing covers the video.
    """
    from src.utils.story_decor_images import _blur_keeping_hole_out, _border_signature, _draw_border

    def build():
        img = Image.open(keyed_png).convert("RGBA")
        alpha = img.getchannel("A")
        hole = Image.new("L", img.size, 255)
        x, y, w, h = (int(frame[k]) for k in ("x", "y", "w", "h"))
        box = (x, y, x + w - 1, y + h - 1)
        if radius:
            ImageDraw.Draw(hole).rounded_rectangle(box, radius=radius, fill=0)
        else:
            ImageDraw.Draw(hole).rectangle(box, fill=0)
        if blur > 0:
            img = _blur_keeping_hole_out(img, hole, blur)
        if _border_signature(border) is not None:
            img = _draw_border(img, frame, radius, border)
        # Inside the hole: whatever the decor had (0 for a punched frame); outside: the drawn room.
        img.putalpha(Image.composite(img.getchannel("A"), alpha, hole))
        return img

    return _cached_png("tvdecor", cache_key, build)


def glare_decor(keyed_png: str, frame: dict, p: dict, cache_key: dict) -> str:
    """The decor PNG with a faint diagonal reflection inside the screen hole."""

    def build():
        img = Image.open(keyed_png).convert("RGBA")
        x, y, w, h = (int(frame[k]) for k in ("x", "y", "w", "h"))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        slope, width = float(p["glareSlope"]), float(p["glareWidth"])
        band = np.exp(-(((xx / w) - (yy / h) * slope - 0.18) / width) ** 2) * float(p["glareStrength"])
        band += np.exp(-(((xx / w) - (yy / h) * slope - 0.42) / (width / 2)) ** 2) * float(p["glareSecondary"])
        band += np.clip(1 - yy / (h * 0.18), 0, 1) * float(p["topSheen"])
        alpha = np.asarray(img.getchannel("A"), dtype=np.float32).copy()
        region = alpha[y:y + h, x:x + w]
        hole = region < 8
        region[hole] = np.clip(band[hole], 0, 60)
        alpha[y:y + h, x:x + w] = region
        rgb = np.asarray(img.convert("RGB"), dtype=np.uint8).copy()
        sub = rgb[y:y + h, x:x + w]
        sub[hole] = (235, 240, 255)
        rgb[y:y + h, x:x + w] = sub
        out = Image.fromarray(rgb, "RGB")
        out.putalpha(Image.fromarray(alpha.astype(np.uint8), "L"))
        return out

    return _cached_png("glass", cache_key, build)


# --------------------------------------------------------------------------- #
def voice_bars(audio: str, out: str, duration: float, p: dict, fps: int = 30) -> str:
    """Bars drawn from the narration: FFT per frame, log bands, fast attack / slow release.

    Measured on the 10-minute sample: ~10 s for 18 000 frames.
    """
    w, h, bars = int(p["width"]), int(p["height"]), int(p["bars"])
    sr = 16000
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-t", str(duration), "-i", audio, "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, dtype=np.float32)
    hop, win = sr // fps, 1024
    edges = (np.geomspace(90, 5000, bars + 1) / (sr / win)).astype(int).clip(1, win // 2)
    frames = int(round(duration * fps))
    window = np.hanning(win)
    colour = (*_rgb(p["color"], (242, 230, 200)), 235)
    fill = float(p["barFill"])
    release = float(p["release"])
    sens = 12.0 / float(p["sensitivity"])
    mirrored = p["style"] == "mirrored"
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                            "-r", str(fps), "-i", "-", "-c:v", "qtrle", out], stdin=subprocess.PIPE)
    level = np.zeros(bars)
    bw = w / bars
    try:
        for i in range(frames):
            seg = x[i * hop: i * hop + win]
            if len(seg) < win:
                seg = np.pad(seg, (0, win - len(seg)))
            mag = np.abs(np.fft.rfft(seg * window))
            bands = np.array([mag[edges[b]:max(edges[b] + 1, edges[b + 1])].mean() for b in range(bars)])
            bands = np.sqrt(bands / sens).clip(0, 1)
            level = np.where(bands > level, bands, level * release)
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            half = bw * fill / 2
            for b in range(bars):
                bh = max(4.0, level[b] * (h - 8))
                cx = b * bw + bw / 2
                top, bottom = ((h - bh) / 2, (h + bh) / 2) if mirrored else (h - bh, h)
                d.rounded_rectangle((cx - half, top, cx + half, bottom), radius=half, fill=colour)
            enc.stdin.write(img.tobytes())
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError("voice bars encode failed")
    logger.info(f"[EditStyles] Voice bars: {frames} frames -> {out}")
    return out
