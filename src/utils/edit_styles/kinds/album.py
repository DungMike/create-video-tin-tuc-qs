"""Memory album: the video is a polaroid on a warm blurred background; every clip
opens on a short freeze, and a small pile of the previous clips' photos sits beside it.

Lab idea 24 (1.13x). The pile changes once per clip, so it is one PNG per clip fed
through the concat demuxer with each picture lasting until the next measured cut —
no per-frame video to encode.
"""

from __future__ import annotations

import os
import random

import numpy as np
from PIL import Image, ImageFilter

from src.utils.edit_styles import assets
from src.utils.edit_styles.graph import T, sum_expr, windows_expr
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import (
    H, W, concat_input, first_frame_windows, region_centre_offset, shift_subtitles, write_ffconcat,
)

PILE_SIZE = (440, 700)


class Album(Kind):
    type_id = "album"
    needs_clip_media = True
    needs_clip_timing = True

    def prepare(self, plan):
        p = self.p
        r = p["rect"]
        side, bottom = int(p["borderSides"]), int(p["borderBottom"])
        ow, oh = r["w"] + 2 * side, r["h"] + side + bottom

        def polaroid():
            img = Image.new("RGBA", (ow + 60, oh + 60), (0, 0, 0, 0))
            img = Image.alpha_composite(img, assets.shadow((30, 30, ow, oh), 170, blur=14, grow=4, drop=10,
                                                           size=(ow + 60, oh + 60)))
            img.paste(Image.new("RGBA", (ow, oh), (*assets.rgb(p["frameColor"], (246, 243, 236)), 255)), (30, 30))
            return assets.punch(img, (30 + side, 30 + side, r["w"], r["h"]))

        self.assets["polaroid"] = assets.cached_png(
            "album_pol", {k: p[k] for k in ("rect", "frameColor", "borderSides", "borderBottom")}, polaroid)
        self.pol_offset = (-(30 + side), -(30 + side))
        # the pile goes on whichever side has room
        right_room = W - (r["x"] + r["w"] + side)
        self.pile_xy = ((r["x"] + r["w"] + side + 20, 150) if right_room >= PILE_SIZE[0] - 40
                        else (max(0, r["x"] - side - PILE_SIZE[0] - 20), 150))
        if p.get("backgroundImage"):
            self.assets["bg"] = self._background(assets.user_image(p["backgroundImage"]),
                                                 assets.image_key(p["backgroundImage"]))
        self.slides = [b for b in plan.paragraphs if b > 0.5] if p.get("slideInOnParagraph", True) else []

    def _background(self, picture: Image.Image | None, source_key) -> str:
        p = self.p

        def build():
            src = picture if picture is not None else Image.new("RGB", (W, H), (60, 50, 40))
            im = assets.cover(src.convert("RGB"), (W, H))
            if int(p["bgBlur"]):
                im = im.filter(ImageFilter.GaussianBlur(int(p["bgBlur"])))
            arr = np.asarray(im, dtype=np.float32)
            warm = float(p["bgWarmth"])
            arr = arr * np.array([1.0, 1 - 0.12 * warm, 1 - 0.3 * warm]) * float(p["bgBrightness"])
            arr += np.array([18, 12, 4]) * warm
            if p.get("bgVignette", True):
                yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
                vig = 1 - 0.55 * (((xx - W / 2) / (W / 1.6)) ** 2 + ((yy - H / 2) / (H / 1.4)) ** 2)
                arr = arr * np.clip(vig, 0.35, 1)[..., None]
            return Image.fromarray(arr.clip(0, 255).astype(np.uint8)).convert("RGBA")

        key = {k: p[k] for k in ("bgBlur", "bgBrightness", "bgWarmth", "bgVignette")}
        key["src"] = source_key
        return assets.cached_png("album_bg", key, build)

    def clip_media(self, plan, stills):
        p = self.p
        if not self.assets.get("bg"):
            first = Image.open(stills[0]).convert("RGBA") if stills else None
            self.assets["bg"] = self._background(first, [stills[0], os.path.getsize(stills[0])] if stills else None)
        if not p.get("pile", True) or not stills:
            return
        out_dir = os.path.join(plan.temp_dir, "album_pile")
        os.makedirs(out_dir, exist_ok=True)
        rng = random.Random(plan.story_id)
        max_angle = int(p["pileMaxAngle"])
        angles = [rng.uniform(-max_angle, max_angle) for _ in stills]
        count = int(p["pileCount"])
        cards: dict[int, Image.Image] = {}

        def card(j):
            if j not in cards:
                still = Image.open(stills[j]).convert("RGB")
                c = Image.new("RGBA", (384, 262), (246, 243, 236, 255))
                c.paste(still.resize((360, 202)), (12, 12))
                cards[j] = c.rotate(angles[j], resample=Image.BICUBIC, expand=True)
            return cards[j]

        pictures = []
        for k in range(len(stills)):
            canvas = Image.new("RGBA", PILE_SIZE, (0, 0, 0, 0))
            for slot in range(count):
                j = k - (count - slot)
                if j >= 0:
                    canvas.alpha_composite(card(j), (20 + slot * 10, 20 + slot * 220))
            path = os.path.join(out_dir, f"{k:04d}.png")
            canvas.save(path)
            pictures.append(path)
        self.assets["pile"] = pictures

    # ------------------------------------------------------------------ overlay pass
    def inputs(self, plan, ss):
        if not self.assets.get("bg"):  # stills failed: a plain warm backdrop instead
            self.assets["bg"] = self._background(None, None)
        out = [["-i", self.assets["bg"]], ["-i", self.assets["polaroid"]]]
        if self.assets.get("pile"):
            name = os.path.join(plan.temp_dir, f"album_pile_{float(ss or 0):.3f}.ffconcat")
            write_ffconcat(self.assets["pile"], plan.clip_starts, plan.total, ss, name)
            out.append(concat_input(name))
        return out

    def video_parts(self, plan, ops, chain, idx, ss):
        tx = T(ss)
        p = self.p
        r = p["rect"]
        slide = sum_expr(f"between({tx},{b:.3f},{b + 0.7:.3f})*1800*pow(1-({tx}-{b:.3f})/0.7,2)"
                         for b in self.slides) if self.slides else ""
        xv = f"{r['x']}+({slide})" if slide else str(r["x"])
        moving = bool(slide)
        parts = [ops.split(chain, ["al_cv", "al_src"]), ops.scale("[al_src]", r["w"], r["h"], "[al_mv0]")]
        mv = "[al_mv0]"
        still = float(p["stillSeconds"])
        if still > 0 and plan.clip_starts:
            select = windows_expr(first_frame_windows(plan.clip_starts), tx)
            hold = windows_expr([(s, s + still) for s in plan.clip_starts], tx)
            parts += [ops.split(mv, ["al_mv", "al_ms"]),
                      f"[al_ms]select='{select}',fps=30[al_still]",
                      ops.overlay("[al_mv]", "[al_still]", "[al_mvf]", enable=hold)]
            mv = "[al_mvf]"
        parts += [ops.upload(idx, "al_bg"), ops.overlay("[al_cv]", "[al_bg]", "[al_g0]")]
        chain = "[al_g0]"
        pol_idx = idx + 1
        idx += 2
        if self.assets.get("pile"):
            px, py = self.pile_xy
            parts += [ops.upload(idx, "al_pile"), ops.overlay(chain, "[al_pile]", "[al_g1]", str(px), str(py))]
            chain, idx = "[al_g1]", idx + 1
        ox, oy = self.pol_offset
        parts += [ops.overlay(chain, mv, "[al_g2]", xv, str(r["y"]), dynamic=moving, repeat=False),
                  ops.upload(pol_idx, "al_pol"),
                  ops.overlay("[al_g2]", "[al_pol]", "[al_out]", f"({xv})+({ox})", str(r["y"] + oy), dynamic=moving)]
        return parts, "[al_out]", idx

    def wave_xy(self, plan, tx):
        return str(W - 440), str(H - 230), False

    def cta_xy(self, plan, tx):
        return str(W - 380), "6", False

    # ------------------------------------------------------------------ subtitles
    def subtitle_overrides(self, plan):
        p = self.p
        r = p["rect"]
        bottom = int(p["borderBottom"])
        return {"marginLR": max(10, (W - r["w"]) // 2 + 24),
                "marginV": max(8, H - (r["y"] + r["h"] + bottom) + 22),
                "fontFamily": p.get("subFont") or "Sriracha"}

    def transform_subtitles(self, plan, ass_text):
        r = self.p["rect"]
        dx = region_centre_offset(r["x"], r["x"] + r["w"])
        return shift_subtitles(ass_text, lambda s, e: dx)
