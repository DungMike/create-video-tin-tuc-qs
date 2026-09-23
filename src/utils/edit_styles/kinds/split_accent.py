"""Accent split: at chosen moments a close-up of the same clip slides in beside the
picture (65/35 by default), holds, and slides away.

Lab idea 26 (1.13x). The close-up is a GPU crop: the zoomed frame is overlaid onto
a panel-sized canvas at a negative offset, so nothing leaves the GPU.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from src.utils.edit_styles import assets
from src.utils.edit_styles.graph import T, sum_expr, win_factor, windows_expr
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import H, W

_RATIO_PANEL = {"60": 768, "65": 672, "70": 576}


def _even(v: float) -> int:
    i = int(round(v))
    return i - i % 2


def _spaced(times: list[float], gap: float, total: float, hold: float) -> list[float]:
    out: list[float] = []
    for t in sorted(times):
        if t + hold > total:
            continue
        if not out or t - out[-1] >= gap:
            out.append(t)
    return out


class SplitAccent(Kind):
    type_id = "split_accent"
    needs_chapters = True

    def prepare(self, plan):
        p = self.p
        self.pw = _RATIO_PANEL.get(str(p["ratio"]), 672)
        hold, slide = float(p["holdSeconds"]), float(p["slideSeconds"])
        trigger = p["trigger"]
        if trigger == "paragraph":
            starts = list(plan.paragraphs)
        elif trigger == "interval":
            every = float(p["everySeconds"])
            starts = [k * every for k in range(1, int(plan.total // every) + 1)]
        elif trigger == "quote":
            starts = [q["time"] for q in plan.quotes] or [c["start"] for c in plan.chapters]
        else:
            starts = [c["start"] for c in plan.chapters]
        starts = _spaced([s + 0.5 for s in starts], float(p["minGapSeconds"]), plan.total, hold)
        self.windows = [(a, a + hold) for a in starts]
        self.slide = slide

        dw = int(p["dividerWidth"])
        if dw:
            def build():
                img = Image.new("RGBA", (dw + 8, H), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.rectangle((0, 0, dw + 7, H), fill=(10, 10, 12, 255))
                d.rectangle((4, 0, 3 + dw, H), fill=(*assets.rgb(p["dividerColor"], (240, 169, 59)), 255))
                return img

            self.assets["divider"] = assets.cached_png("splitdiv", {"w": dw, "c": p["dividerColor"]}, build)

    def inputs(self, plan, ss):
        return [["-i", self.assets["divider"]]] if self.assets.get("divider") and self.windows else []

    def _factor(self, tx):
        return sum_expr(win_factor(a, b, tx, self.slide, self.slide) for a, b in self.windows)

    def video_parts(self, plan, ops, chain, idx, ss):
        if not self.windows:
            return [], chain, idx
        tx = T(ss)
        p = self.p
        pw = self.pw
        f = self._factor(tx)
        wins = windows_expr(self.windows, tx)
        right = p["panelSide"] == "right"
        zoom = float(p["zoom"])
        zw, zh = _even(W * zoom), _even(H * zoom)
        # which part of the zoomed frame the panel shows
        centre_x = {"left": zw / 3, "right": 2 * zw / 3}.get(p["focus"], zw / 2)
        ox = int(max(pw - zw, min(0, pw / 2 - centre_x)))
        oy = int((H - zh) / 2)
        main_x = f"-{pw // 2}*({f})" if right else f"{pw // 2}*({f})"
        panel_x = f"{W}-{pw}*({f})" if right else f"-{pw}+{pw}*({f})"
        parts = [
            ops.split(chain, ["sp_cv", "sp_mv", "sp_zs"]),
            ops.overlay("[sp_cv]", "[sp_mv]", "[sp_m1]", main_x, "0", dynamic=True, repeat=False),
            ops.split("[sp_zs]", ["sp_zc", "sp_zz"]),
            ops.scale("[sp_zc]", pw, H, "[sp_pc]"),
            ops.scale("[sp_zz]", zw, zh, "[sp_zoom]"),
            ops.overlay("[sp_pc]", "[sp_zoom]", "[sp_panel]", str(ox), str(oy), repeat=False),
            ops.overlay("[sp_m1]", "[sp_panel]", "[sp_m2]", panel_x, "0", enable=wins, dynamic=True, repeat=False),
        ]
        chain = "[sp_m2]"
        if self.assets.get("divider"):
            half = (int(p["dividerWidth"]) + 8) // 2
            div_x = f"({panel_x})-{half}" if right else f"({panel_x})+{pw}-{half}"
            parts += [ops.upload(idx, "sp_div"),
                      ops.overlay(chain, "[sp_div]", "[sp_out]", div_x, "0", enable=wins, dynamic=True)]
            chain, idx = "[sp_out]", idx + 1
        return parts, chain, idx

    def wave_xy(self, plan, tx):
        # keep the waveform on the main picture, away from the panel side
        return ("40", "690", False) if self.p["panelSide"] == "right" else (str(W - 460), "690", False)
