"""Special report: a news lower third (tag + chapter title), two picture boxes at each
chapter start (the second a close-up), a wipe card between chapters and a progress bar.

Lab idea 29 (1.08x). Bars and boxes are PNGs; the text is small ASS events.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from src.utils.edit_styles import assets, chapters as chapter_mod
from src.utils.edit_styles.graph import T, ease, sum_expr, windows_expr
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import H, W, esc, fit_font_size


def _even(v: float) -> int:
    i = int(round(v))
    return i - i % 2


class Newsroom(Kind):
    type_id = "newsroom"
    ass_layers_needed = True
    needs_chapters = True

    def prepare(self, plan):
        p = self.p
        lt_y, lt_h, lt_w = int(p["ltY"]), int(p["ltHeight"]), int(p["ltWidth"])
        tag_w = min(int(p["tagWidth"]), lt_w)
        rule = int(p["ruleHeight"])

        def lower_third():
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rectangle((0, lt_y, lt_w - 1, lt_y + lt_h - 1),
                        fill=(*assets.rgb(p["ltColor"], (14, 29, 58)), int(255 * float(p["ltOpacity"]))))
            d.rectangle((0, lt_y, tag_w - 1, lt_y + lt_h - 1), fill=(*assets.rgb(p["tagColor"], (200, 16, 46)), 255))
            if rule:
                d.rectangle((0, lt_y + lt_h, lt_w - 1, lt_y + lt_h + rule - 1),
                            fill=(*assets.rgb(p["ruleColor"], (200, 16, 46)), 255))
            return img

        self.assets["lt"] = assets.cached_png(
            "news_lt", {k: p[k] for k in ("ltY", "ltHeight", "ltWidth", "ltColor", "ltOpacity", "tagWidth",
                                          "tagColor", "ruleColor", "ruleHeight")}, lower_third)

        starts = [float(c["start"]) for c in plan.chapters]
        self.box_windows = []
        if p.get("twoBox", True):
            b1, b2 = p["box1Rect"], p["box2Rect"]

            def two_box():
                img = Image.new("RGBA", (W, H), (*assets.rgb(p["bgColor"], (14, 29, 58)), 255))
                d = ImageDraw.Draw(img)
                border = (*assets.rgb(p["boxBorderColor"], (255, 255, 255)), 255)
                for r in (b1, b2):
                    d.rectangle((r["x"] - 5, r["y"] - 5, r["x"] + r["w"] + 4, r["y"] + r["h"] + 4), fill=border)
                if p.get("box2Label"):
                    d.rectangle((b2["x"] - 5, b2["y"] - 5, b2["x"] + 150, b2["y"] + 34),
                                fill=(*assets.rgb(p["tagColor"], (200, 16, 46)), 255))
                assets.punch(img, (b1["x"], b1["y"], b1["w"], b1["h"]))
                return assets.punch(img, (b2["x"], b2["y"], b2["w"], b2["h"]))

            self.assets["two"] = assets.cached_png(
                "news_two", {k: p[k] for k in ("box1Rect", "box2Rect", "boxBorderColor", "bgColor", "tagColor",
                                               "box2Label")}, two_box)
            hold = float(p["twoBoxSeconds"])
            self.box_windows = [(a + 1.0, min(plan.total, a + 1.0 + hold)) for a in starts
                                if a + 1.0 + hold <= plan.total]

        self.wipes = []
        titled = [float(c["start"]) for c in plan.chapters if c["title"]]
        if p.get("wipe", True) and titled:
            def wipe():
                img = Image.new("RGBA", (W, H), (*assets.rgb(p["wipeColor"], (14, 29, 58)), 255))
                d = ImageDraw.Draw(img)
                edge = (*assets.rgb(p["tagColor"], (200, 16, 46)), 255)
                d.rectangle((0, 0, 40, H), fill=edge)
                d.rectangle((W - 40, 0, W, H), fill=edge)
                return img

            self.assets["wipe"] = assets.cached_png("news_wipe", {"c": p["wipeColor"], "e": p["tagColor"]}, wipe)
            self.wipes = [a for a in starts[1:] if a in titled]
            self.wipe_len = float(p["wipeSeconds"])

    def inputs(self, plan, ss):
        out = [["-i", self.assets["lt"]]]
        if self.assets.get("two") and self.box_windows:
            out.append(["-i", self.assets["two"]])
        if self.assets.get("wipe") and self.wipes:
            out.append(["-i", self.assets["wipe"]])
        return out

    def video_parts(self, plan, ops, chain, idx, ss):
        tx = T(ss)
        p = self.p
        parts: list[str] = []
        lt_idx = idx
        idx += 1
        if self.assets.get("two") and self.box_windows:
            b1, b2 = p["box1Rect"], p["box2Rect"]
            zoom = float(p["box2Zoom"])
            zw, zh = _even(W * zoom), _even(H * zoom)
            wins = windows_expr(self.box_windows, tx)
            parts += [
                ops.split(chain, ["nw_cv", "nw_b1", "nw_zc", "nw_zz"]),
                ops.scale("[nw_zc]", b2["w"], b2["h"], "[nw_pc]"),
                ops.scale("[nw_zz]", zw, zh, "[nw_zoom]"),
                ops.overlay("[nw_pc]", "[nw_zoom]", "[nw_box2]", str(b2["w"] // 2 - zw // 2),
                            str(b2["h"] // 2 - zh // 2), repeat=False),
                ops.scale("[nw_b1]", b1["w"], b1["h"], "[nw_box1]"),
                ops.upload(idx, "nw_two"),
                ops.overlay("[nw_cv]", "[nw_two]", "[nw_t0]", enable=wins),
                ops.overlay("[nw_t0]", "[nw_box1]", "[nw_t1]", str(b1["x"]), str(b1["y"]), enable=wins,
                            repeat=False),
                ops.overlay("[nw_t1]", "[nw_box2]", "[nw_t2]", str(b2["x"]), str(b2["y"]), enable=wins,
                            repeat=False),
            ]
            chain = "[nw_t2]"
            idx += 1
        parts += [ops.upload(lt_idx, "nw_lt"), ops.overlay(chain, "[nw_lt]", "[nw_t3]")]
        chain = "[nw_t3]"
        if self.assets.get("wipe") and self.wipes:
            d = self.wipe_len
            a_in, a_out = d * 0.3, d * 0.35
            terms, wins = [], []
            for a in self.wipes:
                u1 = f"clip(({tx}-{a - a_in:.3f})/{a_in:.3f},0,1)"
                u2 = f"clip(({tx}-{a + d - a_in - a_out:.3f})/{a_out:.3f},0,1)"
                terms.append(f"between({tx},{a - a_in:.3f},{a + d - a_in:.3f})*"
                             f"if(lt({tx},{a:.3f}),-{W}*(1-{ease(u1)}),{W}*{ease(u2)})")
                wins.append((a - a_in, a + d - a_in))
            parts += [ops.upload(idx, "nw_wp"),
                      ops.overlay(chain, "[nw_wp]", "[nw_t4]", sum_expr(terms), "0",
                                  enable=windows_expr(wins, tx), dynamic=True)]
            chain = "[nw_t4]"
            idx += 1
        return parts, chain, idx

    def wave_xy(self, plan, tx):
        return str(W - 450), str(max(40, int(self.p["ltY"]) - 250)), False

    def cta_xy(self, plan, tx):
        return str(W - 420), "20", False

    def subtitle_overrides(self, plan):
        return {"marginV": max(10, H - int(self.p["ltY"]) + 16)}

    def ass_layers(self, plan):
        from src.utils.edit_styles.runtime import ass_colour, ass_event, ass_style

        p = self.p
        lt_y, lt_h = int(p["ltY"]), int(p["ltHeight"])
        tag_w = min(int(p["tagWidth"]), int(p["ltWidth"]))
        mid = lt_y + lt_h // 2
        styles = [
            ass_style("NwTag", p["tagFont"], int(p["tagSize"]), ass_colour(p["tagTextColor"]), bold=1, align=5),
            ass_style("NwTitle", p["titleFont"], int(p["titleSize"]), ass_colour(p["titleColor"]), align=4),
            ass_style("NwWipe1", p["titleFont"], 96, ass_colour(p["titleColor"]), bold=1, align=5),
            ass_style("NwWipe2", p["titleFont"], 56, ass_colour("#E6E6E6"), align=5),
            ass_style("NwLabel", p["tagFont"], 24, ass_colour(p["tagTextColor"]), align=7),
        ]
        events = []
        if p.get("tagText"):
            tag_fs = fit_font_size(p["tagText"], p["tagFont"], int(p["tagSize"]), tag_w - 24)
            events.append(ass_event(0, plan.total, "NwTag", f"{{\\pos({tag_w // 2},{mid})\\fs{tag_fs}}}{esc(p['tagText'])}",
                                    7))
        total = len(plan.chapters)
        room = int(p["ltWidth"]) - tag_w - 60
        for i, c in enumerate(plan.chapters):
            a, b = float(c["start"]), float(c["end"])
            if not c["title"]:
                continue  # no chapter file: the bar carries the tag only, and no wipe card
            title = chapter_mod.format_title(p["titleFormat"], c["n"], total, c["title"], c.get("label", ""))
            fs = fit_font_size(title, p["titleFont"], int(p["titleSize"]), room)
            events.append(ass_event(a + (1.0 if i else 0.0), b, "NwTitle",
                                    f"{{\\pos({tag_w + 30},{mid})\\fs{fs}\\fad(300,0)}}{esc(title)}", 7))
            if i and p.get("wipe", True):
                d = float(p["wipeSeconds"])
                label = chapter_mod.format_title(p["wipeLabelFormat"], c["n"], total, c["title"], c.get("label", ""))
                w2 = fit_font_size(c["title"], p["titleFont"], 56, W - 200)
                events += [ass_event(a - 0.1, a + d * 0.35, "NwWipe1", f"{{\\pos({W // 2},470)\\fad(150,150)}}{esc(label)}",
                                     9),
                           ass_event(a - 0.1, a + d * 0.35, "NwWipe2",
                                     f"{{\\pos({W // 2},590)\\fs{w2}\\fad(150,150)}}{esc(c['title'])}", 9)]
        if p.get("twoBox", True) and p.get("box2Label"):
            b2 = p["box2Rect"]
            for a, b in self.box_windows:
                events.append(ass_event(a, b, "NwLabel", f"{{\\pos({b2['x'] + 8},{b2['y'] - 1})}}{esc(p['box2Label'])}",
                                        8))
        if p.get("progressEnabled", True):
            styles.append(ass_style("EsBar", "Arial", 10))
            y = lt_y + lt_h + int(p["ruleHeight"]) + 2
            events += plan._progress_events(min(H - 4, y), p["progressColor"], 4)
        return styles, events
