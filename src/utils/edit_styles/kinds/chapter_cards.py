"""Chapter cards: at every chapter start the picture dims for a few seconds under a big
"chapter n" label and the chapter title.

Lab idea 14 (1.11x). The dim is a one-frame PNG overlaid only inside the card
windows (a full-screen ASS box would make libass blend a 1920x1080 bitmap).
"""

from __future__ import annotations

from src.utils.edit_styles import assets, chapters as chapter_mod
from src.utils.edit_styles.graph import T, windows_expr
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import H, W, esc, fit_font_size


class ChapterCards(Kind):
    type_id = "chapter_cards"
    ass_layers_needed = True
    needs_chapters = True

    def prepare(self, plan):
        p = self.p
        seconds = float(p["seconds"])
        # A card is a title card: without a chapter file there is nothing to put on it.
        self.cards = [c for c in plan.chapters
                      if c["title"]
                      and not (p.get("skipFirst") and float(c["start"]) < 0.5)
                      and float(c["start"]) + 1.0 < plan.total]
        self.windows = [(float(c["start"]), min(plan.total, float(c["start"]) + seconds)) for c in self.cards]
        self.assets["dim"] = assets.solid("#000000", float(p["dim"]))

    def inputs(self, plan, ss):
        return [["-i", self.assets["dim"]]] if self.windows else []

    def video_parts(self, plan, ops, chain, idx, ss):
        if not self.windows:
            return [], chain, idx
        parts = [ops.upload(idx, "cc_dim"),
                 ops.overlay(chain, "[cc_dim]", "[cc_out]", enable=windows_expr(self.windows, T(ss)))]
        return parts, "[cc_out]", idx + 1

    def ass_layers(self, plan):
        from src.utils.edit_styles.runtime import ass_colour, ass_event, ass_style

        p = self.p
        styles = [
            ass_style("CcNum", p["labelFont"], int(p["labelSize"]), ass_colour(p["labelColor"]), shadow=2, align=5,
                      spacing=int(p["labelSpacing"])),
            ass_style("CcTitle", p["titleFont"], int(p["titleSize"]), ass_colour(p["titleColor"]), bold=1, shadow=5,
                      align=5),
        ]
        events = []
        total = len(plan.chapters)
        zoom = "\\fscx118\\fscy118\\t(0,3000,\\fscx100\\fscy100)" if p.get("zoomOut", True) else ""
        for c, (a, b) in zip(self.cards, self.windows):
            label = chapter_mod.format_title(p["labelFormat"], c["n"], total, c["title"], c.get("label", ""))
            size = fit_font_size(c["title"], p["titleFont"], int(p["titleSize"]), W - 240, 0.45)
            events += [
                ass_event(a + 0.2, b, "CcNum", f"{{\\pos({W // 2},{H // 2 - 140})\\fad(400,500)}}{esc(label)}", 8),
                ass_event(a + 0.35, b, "CcTitle",
                          f"{{\\pos({W // 2},{H // 2 - 10})\\fs{size}\\fad(400,500){zoom}}}{esc(c['title'])}", 8),
            ]
        return styles, events
