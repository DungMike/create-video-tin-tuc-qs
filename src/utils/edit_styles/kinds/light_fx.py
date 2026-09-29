"""Light effects (phase 4 modifiers): a sweep of light, film light leaks, slanted
sun rays and a drifting spotlight. Being modifiers, they chain on top of any layout.

Each effect is one single-frame PNG, built once per params and cached, overlaid
with a moving x/y (``eval=frame``): the cost is one overlay, the motion lives in
the expressions. Effects that come and go (a sweep on every 4th cut, a leak at
each paragraph) also carry ``enable`` so the overlay is skipped between passes.
Everything is written against ``T(ss)`` so parallel segments line up.
"""

from __future__ import annotations

import colorsys
import math
import random

import numpy as np
from PIL import Image

from src.utils.edit_styles import assets
from src.utils.edit_styles.graph import T, sum_expr, windows_expr
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import H, W

# What "random" draws from: the one-way directions (back-and-forth stays a deliberate pick).
_ONE_WAY = ["down", "up", "right", "left", "diag_right", "diag_left"]


def _even_up(value: float) -> int:
    i = int(math.ceil(value))
    return i + i % 2


def _rgba(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    """Float rgb (h, w, 3) in 0..255 and alpha (h, w) in 0..1 -> an RGBA picture."""
    out = np.dstack([np.clip(rgb, 0, 255), np.clip(alpha, 0, 1) * 255]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _smoothstep(u: np.ndarray) -> np.ndarray:
    u = np.clip(u, 0, 1)
    return u * u * (3 - 2 * u)


def _rng(plan, record: dict, salt: str = "") -> random.Random:
    """Per-video randomness: the same story always draws the same, neighbours differ."""
    return random.Random(f"{plan.story_id}:{record.get('id', '')}:{salt}")


# --------------------------------------------------------------------------- #
# Timing shared by the effects that come and go
# --------------------------------------------------------------------------- #
def event_times(plan, p: dict) -> list[float]:
    """Start times for ``rhythm`` = interval / cuts / paragraph / chapter."""
    rhythm, total = p.get("rhythm"), plan.total
    if rhythm == "cuts":
        # Measured cuts; nominal multiples only when the base was never measured.
        cuts = plan.cut_times or [k * plan.clip_period for k in range(1, int(total / plan.clip_period) + 1)]
        every = max(1, int(p.get("everyNCuts", 3)))
        return [c for i, c in enumerate(cuts, start=1) if i % every == 0]
    if rhythm == "paragraph":
        return [b for b in plan.paragraphs if b > 0.5]
    if rhythm == "chapter":
        return [float(c["start"]) for c in plan.chapters if float(c["start"]) > 0.5]
    every = max(2.0, float(p.get("everySeconds", 8)))
    first = min(1.0, every / 2)
    return [first + k * every for k in range(int(max(0.0, total - first) / every) + 1)]


def event_windows(times: list[float], duration: float, total: float) -> list[tuple[float, float]]:
    """``[start, start + duration]`` per time, never overlapping: a start that falls
    inside the previous pass is dropped (the expressions add one term per window)."""
    out: list[tuple[float, float]] = []
    for t in sorted(times):
        if t >= total - 0.1 or (out and t < out[-1][1] + 0.05):
            continue
        out.append((t, min(total, t + duration)))
    return out


# --------------------------------------------------------------------------- #
# Light sweep
# --------------------------------------------------------------------------- #
def _sweep_profile(profile: str, width: int, opacity: float):
    """Alpha across the band, from its back edge to its leading edge, and for the
    prism a colour per sample."""
    w, op = float(width), float(opacity)
    n = _even_up({"flat": w + 4, "laser": w, "double": 1.8 * w, "trail": 1.4 * w}.get(profile, 1.6 * w))
    s = np.arange(n, dtype=np.float32) + 0.5
    c = n / 2
    if profile == "flat":
        a = np.clip(np.minimum(s, n - s) / 2.0, 0, 1) * op
    elif profile == "laser":
        halo = 0.55 * op * np.exp(-0.5 * ((s - c) / (w / 6)) ** 2)
        half = max(1.5, w * 0.025)
        core = min(0.92, op * 3.2) * np.clip(half + 0.5 - np.abs(s - c), 0, 1)
        a = np.maximum(halo, core)
    elif profile == "double":
        a = np.maximum(op * np.exp(-0.5 * ((s - 0.6 * w) / (w / 5)) ** 2),
                       0.7 * op * np.exp(-0.5 * ((s - 1.4 * w) / (w / 14)) ** 2))
    elif profile == "trail":
        lead = n - 0.1 * w
        a = np.where(s <= lead, op * np.exp(-(lead - s) / (w * 0.28)),
                     op * np.exp(-0.5 * ((s - lead) / (0.03 * w + 1)) ** 2))
    else:  # soft, prism
        a = op * np.exp(-0.5 * ((s - c) / (w / 4)) ** 2)
    rgb = None
    if profile == "prism":
        rgb = np.array([colorsys.hsv_to_rgb(0.85 * i / max(1, n - 1), 0.6, 1.0) for i in range(n)],
                       np.float32) * 255
    return a.astype(np.float32), rgb


def sweep_png(profile: str, colour: str, opacity: float, width: int, axis: str, slant: int, angle: int,
              reverse: bool = False) -> tuple[str, int]:
    """The band as a PNG, and how long it is along the axis it moves on.

    ``axis`` "y": a full-width strip moving up/down. "x": a full-height strip moving
    sideways, slanted when ``slant`` is 1 ("/") or -1 ("\\"). ``reverse`` turns the
    profile round for bands that travel towards smaller x/y (the comet's head leads).
    """
    a, rgb = _sweep_profile(profile, width, opacity)
    if reverse:
        a = a[::-1].copy()
        rgb = rgb[::-1].copy() if rgb is not None else None
    n = len(a)
    shift = _even_up(H * math.tan(math.radians(angle))) if slant else 0
    extent = n + shift

    def build():
        if axis == "y":
            alpha = np.repeat(a[:, None], W, axis=1)
            col = np.repeat(rgb[:, None, :], W, axis=1) if rgb is not None else None
        elif not slant:
            alpha = np.repeat(a[None, :], H, axis=0)
            col = np.repeat(rgb[None, :, :], H, axis=0) if rgb is not None else None
        else:
            yy = np.arange(H, dtype=np.float32)[:, None]
            xx = np.arange(extent, dtype=np.float32)[None, :] + 0.5
            # "/": the top row sits `shift` further right; "\": the bottom row does.
            offset = (H - 1 - yy) * (shift / H) if slant > 0 else yy * (shift / H)
            pos = (xx - offset).ravel()
            grid = np.arange(n, dtype=np.float32) + 0.5
            alpha = np.interp(pos, grid, a, left=0, right=0).reshape(H, extent)
            col = None
            if rgb is not None:
                col = np.stack([np.interp(pos, grid, rgb[:, ch]).reshape(H, extent) for ch in range(3)], axis=-1)
        if col is None:
            col = np.broadcast_to(np.array(assets.rgb(colour, (255, 255, 255)), np.float32), alpha.shape + (3,))
        return _rgba(col, alpha)

    key = {"profile": profile, "colour": None if profile == "prism" else colour, "opacity": round(opacity, 4),
           "width": int(width), "axis": axis, "slant": slant, "angle": int(angle) if slant else 0,
           "reverse": bool(reverse)}
    return assets.cached_png("sweep", key, build), extent


class LightSweep(Kind):
    """A band of light crossing the frame: straight, slanted or back and forth,
    continuously, every N seconds, or on cuts / paragraphs / chapters."""

    type_id = "light_sweep"

    def __init__(self, record):
        super().__init__(record)
        self.rhythm = self.p.get("rhythm", "loop")
        self.needs_clip_timing = self.rhythm == "cuts"
        self.needs_chapters = self.rhythm == "chapter"
        self.direction = self.p.get("direction", "down")

    def prepare(self, plan):
        p = self.p
        if self.direction == "random":
            self.direction = _rng(plan, self.record, "direction").choice(_ONE_WAY)
        self.bounce = self.direction in ("bounce_v", "bounce_h")
        self.backward = self.direction in ("up", "left", "diag_left")
        profile = p.get("profile", "soft")
        if profile == "trail" and self.bounce:
            profile = "soft"  # a comet's head would lead on only half of the passes
        self.axis = "y" if self.direction in ("down", "up", "bounce_v") else "x"
        slant = {"diag_right": 1, "diag_left": -1}.get(self.direction, 0)
        self.assets["band"], self.extent = sweep_png(profile, p["color"], float(p["opacity"]), int(p["width"]),
                                                     self.axis, slant, int(p["angle"]), reverse=self.backward)
        self.span = H if self.axis == "y" else W
        self.travel = self.span + self.extent  # from just outside one edge to just outside the other
        self.speed = max(1.0, float(p["speed"]))

    def _windows(self, plan):
        return event_windows(event_times(plan, self.p), self.travel / self.speed, plan.total)

    def _active(self, plan) -> bool:
        return self.rhythm in ("loop", "interval") or bool(self._windows(plan))

    def _pos(self, u: str, back: bool) -> str:
        """Band offset along its axis once it has travelled ``u`` px."""
        return f"{self.span}-({u})" if back else f"({u})-{self.extent}"

    def inputs(self, plan, ss):
        return [["-i", self.assets["band"]]] if self._active(plan) else []

    def video_parts(self, plan, ops, chain, idx, ss):
        if not self._active(plan):
            return [], chain, idx
        tx = T(ss)
        speed, travel = f"{self.speed:g}", self.travel
        enable = None
        if self.rhythm == "loop":
            if self.bounce:
                pos = f"{travel}-abs(mod({tx}*{speed},{2 * travel})-{travel})-{self.extent}"
            else:
                pos = self._pos(f"mod({tx}*{speed},{travel})", self.backward)
        elif self.rhythm == "interval":
            duration = travel / self.speed
            period = max(float(self.p["everySeconds"]), duration + 0.2)
            lead = f"({tx}+{period - min(1.0, period / 2):.3f})"  # first pass starts at <= 1s
            phase = f"mod({lead},{period:.3f})"
            u = f"{phase}*{speed}"
            if self.bounce:
                pos = f"if(mod(floor({lead}/{period:.3f}),2),{self._pos(u, True)},{self._pos(u, False)})"
            else:
                pos = self._pos(u, self.backward)
            enable = f"lt({phase},{duration:.3f})"
        else:
            wins = self._windows(plan)
            terms = []
            for i, (a, b) in enumerate(wins):
                back = (i % 2 == 1) if self.bounce else self.backward
                terms.append(f"between({tx},{a:.3f},{b:.3f})*({self._pos(f'({tx}-{a:.3f})*{speed}', back)})")
            pos = sum_expr(terms)
            enable = windows_expr(wins, tx)
        x, y = ("0", pos) if self.axis == "y" else (pos, "0")
        parts = [ops.upload(idx, "ls_band"),
                 ops.overlay(chain, "[ls_band]", "[ls_out]", x, y, enable=enable, dynamic=True)]
        return parts, "[ls_out]", idx + 1


# --------------------------------------------------------------------------- #
# Film light leak
# --------------------------------------------------------------------------- #
LEAK_PALETTES = {
    "amber": [(255, 138, 0), (255, 196, 107), (255, 94, 58)],
    "rose": [(255, 79, 139), (255, 154, 118), (255, 209, 220)],
    "gold": [(255, 211, 110), (255, 241, 193), (255, 179, 71)],
    "fire": [(255, 61, 0), (255, 145, 0), (255, 234, 0)],
    "teal": [(41, 211, 195), (122, 231, 255), (58, 123, 255)],
    "violet": [(155, 92, 255), (255, 106, 213), (106, 139, 255)],
}
# (centre, sigma, peak) of the three glows in the square, as fractions of its side.
_LEAK_BLOBS = [((0.5, 0.5), 0.2, 1.0), ((0.36, 0.62), 0.15, 0.8), ((0.64, 0.38), 0.11, 0.7)]


def leak_png(palette: str, intensity: float, size: int) -> str:
    colours = LEAK_PALETTES.get(palette) or LEAK_PALETTES["amber"]
    n = _even_up(size)

    def build():
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) / n
        acc = np.zeros((n, n, 3), np.float32)
        weight = np.zeros((n, n), np.float32)
        clear = np.ones((n, n), np.float32)
        for ((cx, cy), sigma, peak), colour in zip(_LEAK_BLOBS, colours):
            a = peak * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma ** 2))
            acc += a[..., None] * np.array(colour, np.float32)
            weight += a
            clear *= 1 - a
        # Fade to nothing well before the square's edge, or its outline would show.
        edge = _smoothstep((0.5 - np.hypot(xx - 0.5, yy - 0.5)) / 0.16)
        return _rgba(acc / np.maximum(weight, 1e-6)[..., None], (1 - clear) * edge * float(intensity))

    return assets.cached_png("leak", {"palette": palette, "i": round(float(intensity), 4), "n": n}, build)


class LightLeak(Kind):
    """A soft coloured glow that drifts in from an edge and melts away, like light
    leaking onto film; one pass per interval / cut / paragraph / chapter."""

    type_id = "light_leak"

    def __init__(self, record):
        super().__init__(record)
        self.needs_clip_timing = self.p.get("rhythm") == "cuts"
        self.needs_chapters = self.p.get("rhythm") == "chapter"

    def prepare(self, plan):
        p = self.p
        palette = p.get("palette", "amber")
        if palette == "random":
            palette = _rng(plan, self.record, "palette").choice(sorted(LEAK_PALETTES))
        self.size = _even_up(int(p["size"]))
        self.assets["leak"] = leak_png(palette, float(p["intensity"]), self.size)

    def _windows(self, plan):
        return event_windows(event_times(plan, self.p), float(self.p["seconds"]), plan.total)

    def inputs(self, plan, ss):
        return [["-i", self.assets["leak"]]] if self._windows(plan) else []

    def video_parts(self, plan, ops, chain, idx, ss):
        wins = self._windows(plan)
        if not wins:
            return [], chain, idx
        tx, s, reach = T(ss), self.size, int(self.p["reach"])
        rng = _rng(plan, self.record, "sides")  # same draw in every segment: seeded, walked in order
        side_mode = self.p.get("side", "alternate")
        xs, ys = [], []
        for i, (a, b) in enumerate(wins):
            side = {"alternate": ("left", "right")[i % 2],
                    "random": rng.choice(["left", "right", "top"])}.get(side_mode, side_mode)
            across = rng.uniform(0.2, 0.8)
            inside = f"between({tx},{a:.3f},{b:.3f})"
            # 0 -> 1 -> 0 over the pass: the glow slides in, peaks, slides back out.
            env = f"sin(PI*({tx}-{a:.3f})/{max(0.1, b - a):.3f})"
            move = f"{reach + s / 2:g}*{env}"
            if side == "left":
                x, y = f"-{s}+{move}", f"{H * across - s / 2:.0f}"
            elif side == "right":
                x, y = f"{W}-{move}", f"{H * across - s / 2:.0f}"
            else:
                x, y = f"{W * across - s / 2:.0f}", f"-{s}+{move}"
            xs.append(f"{inside}*({x})")
            ys.append(f"{inside}*({y})")
        parts = [ops.upload(idx, "lk_img"),
                 ops.overlay(chain, "[lk_img]", "[lk_out]", sum_expr(xs), sum_expr(ys),
                             enable=windows_expr(wins, tx), dynamic=True)]
        return parts, "[lk_out]", idx + 1


# --------------------------------------------------------------------------- #
# Slanted sun rays
# --------------------------------------------------------------------------- #
def rays_png(p: dict, corner: str, margin: int) -> str:
    """A fan of soft rays from a source above the frame, ``margin`` px wider on each
    side so the sway never uncovers an edge."""
    width = W + 2 * margin
    rays, spread, length = int(p["rays"]), float(p["spread"]), float(p["length"])
    intensity, colour = float(p["intensity"]), p["color"]

    def build():
        yy, xx = np.mgrid[0:H, 0:width].astype(np.float32)
        if corner == "top":
            sx, sy, ax, ay = margin + W * 0.5, -H * 0.45, margin + W * 0.5, H
        else:  # top_left; top_right is its mirror image
            sx, sy, ax, ay = margin - W * 0.12, -H * 0.3, margin + W * 0.6, H * 0.9
        dx, dy = xx - sx, yy - sy
        rel = np.degrees(np.arctan2(dy, dx) - math.atan2(ay - sy, ax - sx))
        half = spread / 2
        gen = np.random.default_rng(7 + rays)
        angles = gen.uniform(-half * 0.92, half * 0.92, rays)
        widths = gen.uniform(0.6, 2.6, rays) * (spread / 60)
        gains = gen.uniform(0.45, 1.0, rays)
        clear = np.ones_like(rel)
        for ang, wid, gain in zip(angles, widths, gains):
            clear *= 1 - gain * np.exp(-0.5 * ((rel - ang) / wid) ** 2)
        fan = _smoothstep((half - np.abs(rel)) / (half * 0.25))
        # The source sits above the frame, so a steep falloff would spend the whole
        # ray before it gets on screen: fade gently over 1.3x the frame diagonal.
        radial = np.clip(1 - np.hypot(dx, dy) / (1.3 * length * math.hypot(W, H)), 0, 1) ** 0.9
        glow = 0.35 * np.exp(-(dx ** 2 + dy ** 2) / (2 * (0.35 * H) ** 2))
        alpha = intensity * np.clip((0.85 * (1 - clear) + 0.15) * fan * radial + glow, 0, 1)
        if corner == "top_right":
            alpha = alpha[:, ::-1]
        col = np.broadcast_to(np.array(assets.rgb(colour, (255, 241, 208)), np.float32), alpha.shape + (3,))
        return _rgba(col, alpha)

    key = {"corner": corner, "margin": margin, "rays": rays, "spread": spread, "length": length,
           "i": round(intensity, 4), "c": colour}
    return assets.cached_png("rays", key, build)


class LightRays(Kind):
    """God rays from a corner above the frame, swaying very slowly."""

    type_id = "light_rays"

    def prepare(self, plan):
        corner = self.p.get("corner", "top_left")
        if corner == "random":
            corner = _rng(plan, self.record, "corner").choice(["top_left", "top_right", "top"])
        self.margin = _even_up(int(self.p["sway"]))
        self.assets["rays"] = rays_png(self.p, corner, self.margin)

    def inputs(self, plan, ss):
        return [["-i", self.assets["rays"]]]

    def video_parts(self, plan, ops, chain, idx, ss):
        m = self.margin
        x = f"-{m}+{m}*sin(2*PI*{T(ss)}/{int(self.p['period'])})" if m else "0"
        parts = [ops.upload(idx, "lr_img"), ops.overlay(chain, "[lr_img]", "[lr_out]", x, "0", dynamic=bool(m))]
        return parts, "[lr_out]", idx + 1


# --------------------------------------------------------------------------- #
# Drifting spotlight
# --------------------------------------------------------------------------- #
def spotlight_png(p: dict, margin: int) -> str:
    """Darkness with a soft clear hole in the middle, ``margin`` px larger than the
    frame on every side so the hole can wander without uncovering an edge."""
    width, height = W + 2 * margin, H + 2 * margin
    darkness, softness, radius = float(p["darkness"]), float(p["softness"]), float(p["radius"])
    circle = p.get("shape") == "circle"

    def build():
        yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
        ry = radius * H
        rx = ry if circle else ry * W / H
        d = np.sqrt(((xx - width / 2) / rx) ** 2 + ((yy - height / 2) / ry) ** 2)
        lo, hi = 1 - 0.9 * softness, 1 + 0.6 * softness
        alpha = darkness * _smoothstep((d - lo) / (hi - lo))
        col = np.broadcast_to(np.array(assets.rgb(p["color"], (0, 0, 0)), np.float32), alpha.shape + (3,))
        return _rgba(col, alpha)

    key = {"m": margin, "d": round(darkness, 4), "s": round(softness, 4), "r": round(radius, 4),
           "circle": circle, "c": p["color"]}
    return assets.cached_png("spot", key, build)


class Spotlight(Kind):
    """A vignette whose clear centre drifts slowly around the frame (Lissajous path)."""

    type_id = "spotlight"

    def prepare(self, plan):
        self.margin = _even_up(int(self.p["drift"]))
        self.assets["spot"] = spotlight_png(self.p, self.margin)

    def inputs(self, plan, ss):
        return [["-i", self.assets["spot"]]]

    def video_parts(self, plan, ops, chain, idx, ss):
        m, tx = self.margin, T(ss)
        if m:
            x = f"-{m}+{m}*sin(2*PI*{tx}/{int(self.p['periodX'])})"
            y = f"-{m}+{m}*sin(2*PI*{tx}/{int(self.p['periodY'])})"
        else:
            x = y = "0"
        parts = [ops.upload(idx, "sp_img"), ops.overlay(chain, "[sp_img]", "[sp_out]", x, y, dynamic=bool(m))]
        return parts, "[sp_out]", idx + 1
