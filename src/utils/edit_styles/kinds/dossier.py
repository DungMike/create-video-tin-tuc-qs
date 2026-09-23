"""Case file: the video is a photo taped onto a paper desk, with a stamp, a file
number and handwritten notes beside it; a sheet of paper sweeps across at each
chapter change.

Lab idea 28 (0.97x — the paper hides most of the frame, so less to encode). The
desk is one full-frame PNG with the photo window punched out; stamp/notes are
small ASS events.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from src.utils.edit_styles import assets, chapters as chapter_mod
from src.utils.edit_styles.graph import T, ease, sum_expr, windows_expr
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import H, W, esc, fit_font_size, region_centre_offset, shift_subtitles

# where stickers sit on the photo: (corner x, corner y) as fractions of the photo, tilt
_STICKER_SLOTS = [((0.0, 0.0), -14), ((1.0, 0.0), 12), ((0.0, 1.0), 9), ((1.0, 1.0), -10)]


class Dossier(Kind):
    type_id = "dossier"
    ass_layers_needed = True
    needs_chapters = True

    def _notes_x(self) -> int:
        r = self.p["rect"]
        return min(W - 420, r["x"] + r["w"] + 68)

    def prepare(self, plan):
        p = self.p
        r = p["rect"]
        x, y, w, h = r["x"], r["y"], r["w"], r["h"]
        notes_x = self._notes_x()
        self.sweeps: list[float] = []

        def desk():
            pic = assets.user_image(p.get("paperImage"))
            if pic is not None:
                img = assets.cover(pic, (W, H)).convert("RGBA")
            else:
                img = assets.paper((W, H), assets.rgb(p["paperColor"], (216, 199, 161)), grain=int(p["paperGrain"]),
                                   seed=11)
            border = int(p["photoBorderWidth"])
            if p.get("photoShadow", True):
                img = Image.alpha_composite(img, assets.shadow((x - border, y - border, w + 2 * border,
                                                                h + 2 * border), 150, blur=10, grow=2, drop=6))
            d = ImageDraw.Draw(img)
            if border:
                d.rectangle((x - border, y - border, x + w + border - 1, y + h + border - 1),
                            fill=(*assets.rgb(p["photoBorderColor"], (250, 248, 240)), 255))
            stickers = [assets.user_image(ref) for ref in p.get("decorImages") or []]
            stickers = [s for s in stickers if s is not None]
            size = int(p["decorSize"])
            if stickers:
                for sticker, ((fx, fy), tilt) in zip(stickers, _STICKER_SLOTS):
                    s = sticker.copy()
                    s.thumbnail((size, size))
                    s = s.rotate(tilt, expand=True, resample=Image.BICUBIC)
                    cx, cy = int(x + fx * w), int(y + fy * h)
                    img.alpha_composite(s, (max(0, cx - s.width // 2), max(0, cy - s.height // 2)))
            elif p.get("autoTape", True):
                for tx, ty, ang in ((x - 40, y - 34, -32), (x + w - 70, y - 30, 28)):
                    tape = Image.new("RGBA", (size - 30 if size > 60 else size, 46), (238, 226, 190, 190))
                    tape = tape.rotate(ang, expand=True, resample=Image.BICUBIC)
                    img.alpha_composite(tape, (max(0, tx), max(0, ty)))
            d = ImageDraw.Draw(img)
            if p.get("notesEnabled", True) and p.get("ruledLines", True) and notes_x < W - 200:
                for yy in range(250, 820, 46):
                    d.line((notes_x - 10, yy, W - 80, yy), fill=(120, 110, 90, 90), width=2)
                d.line((notes_x - 10, 190, W - 80, 190), fill=(176, 42, 36, 200), width=3)
            return assets.punch(img, (x, y, w, h))

        key = {k: p[k] for k in ("paperColor", "paperGrain", "rect", "photoBorderColor", "photoBorderWidth",
                                 "photoShadow", "autoTape", "decorSize", "notesEnabled", "ruledLines")}
        key["paper"] = assets.image_key(p.get("paperImage"))
        key["decor"] = [assets.image_key(ref) for ref in p.get("decorImages") or []]
        self.assets["desk"] = assets.cached_png("dossier", key, desk)

        if p.get("sheetSweep", True):
            def sheet():
                img = assets.paper((W, H), (232, 222, 196), grain=6, seed=12)
                return img.rotate(-2.5, expand=False, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))

            self.assets["sheet"] = assets.cached_png("dossier_sheet", {"v": 1}, sheet)
            half = float(p["sheetSeconds"]) / 2
            self.sweeps = [float(c["start"]) for c in plan.chapters if c["start"] > 0.5]
            self.half = half

    def inputs(self, plan, ss):
        out = [["-i", self.assets["desk"]]]
        if self.assets.get("sheet") and self.sweeps:
            out.append(["-i", self.assets["sheet"]])
        return out

    def video_parts(self, plan, ops, chain, idx, ss):
        tx = T(ss)
        r = self.p["rect"]
        parts = [
            ops.split(chain, ["ds_cv", "ds_src"]),
            ops.scale("[ds_src]", r["w"], r["h"], "[ds_mv]"),
            ops.overlay("[ds_cv]", "[ds_mv]", "[ds_m]", str(r["x"]), str(r["y"]), repeat=False),
            ops.upload(idx, "ds_desk"),
            ops.overlay("[ds_m]", "[ds_desk]", "[ds_g]"),
        ]
        chain, idx = "[ds_g]", idx + 1
        if self.assets.get("sheet") and self.sweeps:
            half = self.half
            terms, wins = [], []
            for b in self.sweeps:
                a0, a1 = b - half, b + half
                u1 = f"clip(({tx}-{a0:.3f})/{half:.3f},0,1)"
                u2 = f"clip(({tx}-{b:.3f})/{half:.3f},0,1)"
                terms.append(f"between({tx},{a0:.3f},{a1:.3f})*if(lt({tx},{b:.3f}),{W}*(1-{ease(u1)}),-{W}*{ease(u2)})")
                wins.append((a0, a1))
            parts += [ops.upload(idx, "ds_sheet"),
                      ops.overlay(chain, "[ds_sheet]", "[ds_out]", sum_expr(terms), "0",
                                  enable=windows_expr(wins, tx), dynamic=True)]
            chain, idx = "[ds_out]", idx + 1
        return parts, chain, idx

    def wave_xy(self, plan, tx):
        return str(self._notes_x() + 60), str(H - 290), False

    def cta_xy(self, plan, tx):
        r = self.p["rect"]
        return str(r["x"] + 30), str(r["y"] + 20), False

    # ------------------------------------------------------------------ subtitles
    def subtitle_overrides(self, plan):
        r = self.p["rect"]
        out = {"marginLR": max(10, (W - r["w"]) // 2),
               "marginV": max(10, H - (r["y"] + r["h"]) - 150),
               "fontFamily": self.p.get("subFont") or "Sarabun"}
        return out

    def transform_subtitles(self, plan, ass_text):
        r = self.p["rect"]
        dx = region_centre_offset(r["x"], r["x"] + r["w"])
        return shift_subtitles(ass_text, lambda s, e: dx)

    def ass_layers(self, plan):
        from src.utils.edit_styles.runtime import ass_colour, ass_event, ass_inline, ass_style

        p = self.p
        styles, events = [], []
        total = len(plan.chapters)
        notes_x = self._notes_x()
        room = W - 80 - notes_x
        labels = p.get("stampLabels") or ["หลักฐาน"]
        stamp_colour = ass_colour(p["stampColor"])
        ink = ass_colour(p["inkColor"])
        if p.get("stampEnabled", True):
            styles += [ass_style("DsStamp", p["stampFont"], int(p["stampSize"]), stamp_colour, stamp_colour,
                                 bold=1, align=5, spacing=4),
                       ass_style("DsBox", "Arial", 10)]
        if p.get("notesEnabled", True):
            styles += [ass_style("DsFile", p["fileFont"], int(p["fileSize"]), ink, spacing=3),
                       ass_style("DsNote", p["noteFont"], int(p["noteSize"]), ink)]
        stamp_x, stamp_y = notes_x + min(220, room // 2), 122
        for i, c in enumerate(plan.chapters):
            a, b = float(c["start"]), float(c["end"])
            t0 = a + (0.3 if i else 0.0)
            if p.get("stampEnabled", True):
                label = c.get("label") or labels[i % len(labels)]
                size = fit_font_size(label, p["stampFont"], int(p["stampSize"]), 340)
                angle = int(p["stampAngle"])
                # A drawing is anchored by its own origin: positive coordinates keep the
                # box centred on the \an5 stamp text.
                events += [
                    ass_event(t0, b, "DsBox", f"{{\\an5\\pos({stamp_x},{stamp_y})\\frz{angle}\\1a&HFF&"
                                              f"\\3c{ass_inline(p['stampColor'])}\\bord5\\p1}}"
                                              f"m 0 0 l 380 0 380 88 0 88{{\\p0}}", 7),
                    ass_event(t0, b, "DsStamp", f"{{\\an5\\pos({stamp_x},{stamp_y})\\frz{angle}\\fs{size}}}{esc(label)}",
                              7),
                ]
            if p.get("notesEnabled", True) and c["title"]:
                number = chapter_mod.format_title(p["fileNumberFormat"], c["n"], total, c["title"], c.get("label", ""))
                title_fs = fit_font_size(c["title"], p["noteFont"], int(p["noteSize"]), room)
                events += [
                    ass_event(t0, b, "DsFile", f"{{\\an7\\pos({notes_x},210)}}{esc(number)}", 7),
                    ass_event(t0, b, "DsNote", f"{{\\an7\\pos({notes_x},262)\\q0\\fs{title_fs}\\fad(500,0)}}"
                                               f"{esc(c['title'])}", 7, notes_x, 70),
                ]
                quote = c.get("quote") if p.get("noteQuote", True) else ""
                if quote:
                    q_fs = fit_font_size(quote, p["noteFont"], int(int(p["noteSize"]) * 0.85), room, 0.7)
                    events.append(ass_event(t0 + 0.6, b, "DsNote", f"{{\\an7\\pos({notes_x},354)\\q0\\fs{q_fs}"
                                                                   f"\\fad(600,0)}}— {esc(quote)}", 7, notes_x, 70))
        return styles, events
