"""Documentary film strip: the picture sits in a frame on a dark grainy ground, with a
strip of thumbnails of the previous/next clips; the strip slides one notch at each cut.

Lab idea 27 (1.21x). The strip changes once per clip, so it is one PNG per clip
(concat demuxer, each shown until the next measured cut); the slide is an overlay
x expression keyed to the same measured cut times.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw

from src.utils.edit_styles import assets
from src.utils.edit_styles.graph import T, ease, sum_expr
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import (
    H, W, concat_input, region_centre_offset, shift_subtitles, write_ffconcat,
)

_GAP = 16
_SLOTS = 8  # k-3 .. k+4: one spare on the right feeds the slide


class DocStrip(Kind):
    type_id = "doc_strip"
    needs_clip_media = True
    needs_clip_timing = True

    def _thumb(self) -> tuple[int, int]:
        tw = int(self.p["thumbSize"])
        return tw, tw * 9 // 16

    def _strip_y(self) -> int:
        _tw, th = self._thumb()
        return 24 if self.p["stripPosition"] == "top" else H - th - 12 - 24

    def prepare(self, plan):
        p = self.p
        r = p["rect"]
        x, y, w, h = r["x"], r["y"], r["w"], r["h"]
        _tw, th = self._thumb()
        strip_y = self._strip_y()

        def ground():
            base = assets.rgb(p["bgColor"], (13, 12, 11))
            img = Image.new("RGBA", (W, H), (*base, 255))
            if p.get("bgGrain", True):
                arr = np.asarray(img).astype(np.int16)
                noise = np.random.default_rng(3).normal(0, 6, (H, W, 1)).astype(np.int16)
                arr[..., :3] = np.clip(arr[..., :3] + noise, 0, 255)
                img = Image.fromarray(arr.astype(np.uint8), "RGBA")
            d = ImageDraw.Draw(img)
            d.rectangle((x - 6, y - 6, x + w + 5, y + h + 5), fill=(*assets.rgb(p["thumbBorderColor"]), 255))
            for sx in range(10, W, 44):  # sprocket marks along the strip
                d.rectangle((sx, strip_y - 16, sx + 22, strip_y - 6), fill=(40, 36, 32, 255))
                d.rectangle((sx, strip_y + th + 18, sx + 22, strip_y + th + 28), fill=(40, 36, 32, 255))
            return assets.punch(img, (x, y, w, h))

        key = {k: p[k] for k in ("rect", "bgColor", "bgGrain", "thumbBorderColor", "thumbSize", "stripPosition")}
        self.assets["ground"] = assets.cached_png("docstrip_bg", key, ground)

    def clip_media(self, plan, stills):
        p = self.p
        tw, th = self._thumb()
        step = tw + _GAP
        out_dir = os.path.join(plan.temp_dir, "doc_strip")
        os.makedirs(out_dir, exist_ok=True)
        border = (*assets.rgb(p["thumbBorderColor"], (232, 220, 192)), 255)
        current = (*assets.rgb(p["currentColor"], (240, 169, 59)), 255)
        dim = 1 - float(p["dimOthers"])
        f = assets.font("ChakraPetch-Medium", max(14, th // 8))
        thumbs: dict[int, tuple[Image.Image, Image.Image]] = {}

        def thumb(j):
            if j not in thumbs:
                im = Image.open(stills[j]).convert("RGB").resize((tw, th))
                thumbs[j] = (im, Image.eval(im, lambda v: int(v * dim)))
            return thumbs[j]

        pictures = []
        for k in range(len(stills)):
            strip = Image.new("RGBA", (step * _SLOTS, th + 12), (0, 0, 0, 0))
            d = ImageDraw.Draw(strip)
            for slot, j in enumerate(range(k - 3, k + _SLOTS - 3)):
                if j < 0 or j >= len(stills):
                    continue
                sx = slot * step
                is_cur = j == k
                d.rectangle((sx, 0, sx + tw + 11, th + 11), fill=current if is_cur else border)
                bright, dark = thumb(j)
                strip.paste(bright if is_cur else dark, (sx + 6, 6))
                if p.get("showIndex", True):
                    d.text((sx + 12, th - 18), f"{j + 1:03d}", fill=(255, 255, 255, 230), font=f)
            path = os.path.join(out_dir, f"{k:04d}.png")
            strip.save(path)
            pictures.append(path)
        self.assets["strip"] = pictures
        self.step = step

    # ------------------------------------------------------------------ overlay pass
    def inputs(self, plan, ss):
        out = [["-i", self.assets["ground"]]]
        if self.assets.get("strip"):
            name = os.path.join(plan.temp_dir, f"doc_strip_{float(ss or 0):.3f}.ffconcat")
            write_ffconcat(self.assets["strip"], plan.clip_starts, plan.total, ss, name)
            out.append(concat_input(name))
        return out

    def video_parts(self, plan, ops, chain, idx, ss):
        tx = T(ss)
        p = self.p
        r = p["rect"]
        parts = [
            ops.split(chain, ["dt_cv", "dt_src"]),
            ops.scale("[dt_src]", r["w"], r["h"], "[dt_mv]"),
            ops.overlay("[dt_cv]", "[dt_mv]", "[dt_m]", str(r["x"]), str(r["y"]), repeat=False),
            ops.upload(idx, "dt_bg"),
            ops.overlay("[dt_m]", "[dt_bg]", "[dt_g]"),
        ]
        chain, idx = "[dt_g]", idx + 1
        if self.assets.get("strip"):
            tw, _th = self._thumb()
            step = self.step
            rest = W // 2 - (3 * step + (tw + 12) // 2)
            dur = float(p["slideSeconds"])
            # At each cut the new picture (already shifted a notch) starts one step to
            # the right and eases back: the strip appears to slide left by one frame.
            terms = [f"between({tx},{s:.3f},{s + dur:.3f})*(1-{ease(f'clip(({tx}-{s:.3f})/{dur},0,1)')})"
                     for s in plan.clip_starts[1:]]
            sx = f"{rest}+{step}*({sum_expr(terms)})"
            parts += [ops.upload(idx, "dt_st"),
                      ops.overlay(chain, "[dt_st]", "[dt_out]", sx, str(self._strip_y()), dynamic=True)]
            chain, idx = "[dt_out]", idx + 1
        return parts, chain, idx

    def wave_xy(self, plan, tx):
        r = self.p["rect"]
        return str(min(W - 440, r["x"] + r["w"] - 440)), str(min(H - 240, r["y"] + r["h"] - 250)), False

    def cta_xy(self, plan, tx):
        r = self.p["rect"]
        return str(max(10, r["x"] - 278)), str(r["y"] + 4), False

    def subtitle_overrides(self, plan):
        r = self.p["rect"]
        return {"marginLR": max(10, (W - r["w"]) // 2)}

    def transform_subtitles(self, plan, ass_text):
        r = self.p["rect"]
        dx = region_centre_offset(r["x"], r["x"] + r["w"])
        return shift_subtitles(ass_text, lambda s, e: dx)
