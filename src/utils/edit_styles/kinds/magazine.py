"""Motion magazine: at each chapter start the picture slides aside for a text column
(chapter number, title, a key line); the column swaps sides chapter to chapter.

Lab idea 25 (1.12x the plain render). The picture moves by half the column so it
stays centred in the space left; a blurred copy fills the strip it uncovers.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from src.utils.edit_styles import assets, chapters as chapter_mod
from src.utils.edit_styles.graph import T, sum_expr, win_factor, windows_expr
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import H, W, esc, fit_font_size, region_centre_offset, shift_subtitles


class Magazine(Kind):
    type_id = "magazine"
    ass_layers_needed = True
    needs_chapters = True

    def prepare(self, plan):
        p = self.p
        cw = int(p["columnWidth"])
        slide = float(p["slideSeconds"])
        side = p["firstSide"]
        self.windows = []  # (a, b, side, chapter)
        for c in plan.chapters:
            a = float(c["start"])
            b = min(float(c["end"]), a + float(p["columnSeconds"]))
            # The column is nothing but chapter text: without a chapter file it never opens.
            if b - a > 2 * slide + 1 and (c["title"] or self._quote(plan, c)):
                self.windows.append((a, b, side, c))
            if p.get("alternateSides", True):
                side = "left" if side == "right" else "right"
        self.cw = cw

        def build():
            img = Image.new("RGBA", (cw, H), (*assets.rgb(p["columnColor"], (18, 20, 24)), 255))
            pic = assets.user_image(p.get("columnImage"))
            if pic is not None:
                img = assets.cover(pic, (cw, H)).convert("RGBA")
                shade = Image.new("RGBA", (cw, H), (0, 0, 0, 90))  # keep the text readable
                img = Image.alpha_composite(img, shade)
            if p.get("columnGrain"):
                arr = np.asarray(img).astype(np.int16)
                noise = np.random.default_rng(5).normal(0, 3.5, (H, cw, 1)).astype(np.int16)
                arr[..., :3] = np.clip(arr[..., :3] + noise, 0, 255)
                img = Image.fromarray(arr.astype(np.uint8), "RGBA")
            d = ImageDraw.Draw(img)
            accent = assets.rgb(p["accentColor"], (201, 164, 92))
            d.rectangle((72, 250, 180, 254), fill=(*accent, 255))
            d.rectangle((72, 900, cw - 72, 901), fill=(*accent, 120))
            return img

        key = {k: p[k] for k in ("columnWidth", "columnColor", "columnGrain", "accentColor")}
        key["image"] = assets.image_key(p.get("columnImage"))
        self.assets["column"] = assets.cached_png("magcol", key, build)

    # ------------------------------------------------------------------ overlay pass
    def inputs(self, plan, ss):
        return [["-i", self.assets["column"]]]

    def _factors(self, tx):
        slide = float(self.p["slideSeconds"])
        fr = sum_expr(win_factor(a, b, tx, slide, slide) for a, b, s, _c in self.windows if s == "right")
        fl = sum_expr(win_factor(a, b, tx, slide, slide) for a, b, s, _c in self.windows if s == "left")
        return fr, fl

    def video_parts(self, plan, ops, chain, idx, ss):
        if not self.windows:
            return [], chain, idx + 1
        tx = T(ss)
        cw = self.cw
        fr, fl = self._factors(tx)
        half = cw // 2
        vx = f"(-{half}*({fr})+{half}*({fl}))"
        colr = f"({W}-{cw}*({fr}))"
        coll = f"(-{cw}+{cw}*({fl}))"
        wins_r = windows_expr([(a, b) for a, b, s, _c in self.windows if s == "right"], tx)
        wins_l = windows_expr([(a, b) for a, b, s, _c in self.windows if s == "left"], tx)
        parts = [
            ops.split(chain, ["mg_cv", "mg_mv"]),
            ops.blur("[mg_cv]", "[mg_cvb]", "strong"),
            ops.overlay("[mg_cvb]", "[mg_mv]", "[mg_m1]", vx, "0", dynamic=True, repeat=False),
            ops.upload(idx, "mg_col"),
            ops.split("[mg_col]", ["mg_colr", "mg_coll"]),
            ops.overlay("[mg_m1]", "[mg_colr]", "[mg_m2]", colr, "0", enable=wins_r, dynamic=True),
            ops.overlay("[mg_m2]", "[mg_coll]", "[mg_out]", coll, "0", enable=wins_l, dynamic=True),
        ]
        return parts, "[mg_out]", idx + 1

    def wave_xy(self, plan, tx):
        fr, fl = self._factors(tx)
        cw = self.cw
        # rides at the bottom of the column while it is out, else sits at the left
        x = f"40*(1-({fr})-({fl}))+({W}-{cw}+110)*({fr})+110*({fl})"
        return x, "820", True

    def cta_xy(self, plan, tx):
        _fr, fl = self._factors(tx)
        return f"40+{self.cw}*({fl})", "20", True

    # ------------------------------------------------------------------ subtitles
    def subtitle_overrides(self, plan):
        # Narrow enough to sit beside the column, centred in the picture while it is out.
        return {"marginLR": (self.cw + 80) // 2}

    def transform_subtitles(self, plan, ass_text):
        shift = self.cw // 2

        def dx_for(start, end):
            for a, b, side, _c in self.windows:
                if a - 0.01 <= start < b:
                    return -shift if side == "right" else shift
            return 0

        return shift_subtitles(ass_text, dx_for)

    def ass_layers(self, plan):
        from src.utils.edit_styles.runtime import ass_colour, ass_event, ass_style

        p = self.p
        styles = [
            ass_style("MgNum", p["numFont"], int(p["numSize"]), ass_colour(p["accentColor"]), spacing=10),
            ass_style("MgTitle", p["titleFont"], int(p["titleSize"]), ass_colour(p["titleColor"]), bold=1),
            ass_style("MgBody", p["quoteFont"], int(p["quoteSize"]), ass_colour(p["quoteColor"])),
        ]
        events = []
        slide = float(p["slideSeconds"])
        total = len(plan.chapters)
        for a, b, side, c in self.windows:
            cx = W - self.cw if side == "right" else 0
            ml, mr = cx + 72, W - (cx + self.cw - 72)
            t0, t1 = a + slide + 0.1, b - slide
            num = chapter_mod.format_title(p["numFormat"], c["n"], total, c["title"], c.get("label", ""))
            room = self.cw - 144
            title_fs = fit_font_size(c["title"], p["titleFont"], int(p["titleSize"]), room)
            events.append(ass_event(t0, t1, "MgNum", f"{{\\fad(400,300)}}{esc(num)}", 7, ml, mr, 200))
            if c["title"]:
                events.append(ass_event(t0 + 0.15, t1, "MgTitle",
                                        f"{{\\q0\\fs{title_fs}\\fad(400,300)}}{esc(c['title'])}", 7, ml, mr, 270))
            quote = self._quote(plan, c) if p.get("showQuote", True) else ""
            if quote:
                quote_fs = fit_font_size(quote, p["quoteFont"], int(p["quoteSize"]), room, 0.75)
                events.append(ass_event(t0 + 0.4, t1, "MgBody",
                                        f"{{\\q0\\fs{quote_fs}\\fad(500,300)}}“{esc(quote)}”", 7, ml, mr, 540))
        return styles, events

    @staticmethod
    def _quote(plan, c) -> str:
        if c.get("quote"):
            return c["quote"]
        cue = next((q for q in plan.sub_cues if c["start"] <= float(q["start"]) < c["end"]), None)
        return str(cue["text"]) if cue else ""


def column_region_offset(cw: int, side: str) -> float:
    """Horizontal offset of the picture centre while a column is out (used by tests)."""
    x0, x1 = (0, W - cw) if side == "right" else (cw, W)
    return region_centre_offset(x0, x1)
