"""Quote moments: a few key lines take over the screen — big, centred, over a blurred
and dimmed picture — instead of showing as a subtitle.

Lab idea 16 (1.18x). Quotes come from the chapter file (``>`` lines); in ``auto``
mode, missing ones are picked from subtitle lines ending in ! ? … or quoted.
"""

from __future__ import annotations

import re

from src.utils.edit_styles import assets
from src.utils.edit_styles.graph import T, windows_expr
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import H, W, drop_spans, esc, fit_font_size

_STRONG_END = re.compile(r"(!|\?|…|\.\.\.|[”\"」』])\s*$")


def pick_quotes(sub_cues: list[dict], total: float, count: int, gap: float, hold: float,
                taken: list[float]) -> list[dict]:
    """Subtitle lines that read like a punchline, spread out by ``gap`` seconds."""
    chosen = list(taken)
    out = []
    candidates = [c for c in sub_cues if _STRONG_END.search(str(c.get("text", "")).strip())
                  and len(str(c.get("text", ""))) >= 8]
    # longest first: a two-line exclamation is a better quote than "No!"
    candidates.sort(key=lambda c: -len(str(c["text"])))
    for c in candidates:
        t = float(c["start"])
        if t < 5 or t + hold > total:
            continue
        if any(abs(t - other) < gap for other in chosen):
            continue
        chosen.append(t)
        out.append({"time": t, "text": str(c["text"]), "end": float(c["end"])})
        if len(out) >= count:
            break
    return out


class QuoteMoments(Kind):
    type_id = "quote_moments"
    ass_layers_needed = True

    def prepare(self, plan):
        p = self.p
        hold = float(p["minHoldSeconds"])
        count = int(p["maxQuotes"])
        gap = float(p["minGapSeconds"])
        quotes = []
        for q in plan.quotes:
            if all(abs(q["time"] - o["time"]) >= gap for o in quotes):
                cue = next((c for c in plan.sub_cues if float(c["start"]) <= q["time"] < float(c["end"])), None)
                quotes.append({"time": q["time"], "text": q["text"], "end": float(cue["end"]) if cue else q["time"]})
        quotes = quotes[:count]
        if p["source"] == "auto" and len(quotes) < count:
            quotes += pick_quotes(plan.sub_cues, plan.total, count - len(quotes), gap, hold,
                                  [q["time"] for q in quotes])
        quotes.sort(key=lambda q: q["time"])
        self.quotes = [(q["time"], min(plan.total, max(q["end"], q["time"] + hold)), q["text"]) for q in quotes]
        self.spans = [(a, b) for a, b, _t in self.quotes]
        if float(p["bgDim"]) > 0:
            self.assets["dim"] = assets.solid("#000000", float(p["bgDim"]))

    def inputs(self, plan, ss):
        return [["-i", self.assets["dim"]]] if self.assets.get("dim") and self.spans else []

    def video_parts(self, plan, ops, chain, idx, ss):
        if not self.spans:
            return [], chain, idx
        n_inputs = 1 if self.assets.get("dim") else 0
        wins = windows_expr(self.spans, T(ss))
        parts = [ops.split(chain, ["qm_main", "qm_src"]),
                 ops.blur("[qm_src]", "[qm_blur]", self.p["bgBlur"]),
                 ops.overlay("[qm_main]", "[qm_blur]", "[qm_b]", enable=wins, repeat=False)]
        chain = "[qm_b]"
        if n_inputs:
            parts += [ops.upload(idx, "qm_dim"), ops.overlay(chain, "[qm_dim]", "[qm_out]", enable=wins)]
            chain = "[qm_out]"
        return parts, chain, idx + n_inputs

    def overlay_hidden(self, plan, tx):
        if not self.p.get("hideCtaWave", True) or not self.spans:
            return None
        return windows_expr(self.spans, tx)

    def transform_subtitles(self, plan, ass_text):
        return drop_spans(ass_text, self.spans) if self.spans else ass_text

    def ass_layers(self, plan):
        from src.utils.edit_styles.runtime import ass_colour, ass_event, ass_style

        p = self.p
        styles = [ass_style("QmText", p["font"], int(p["size"]), ass_colour(p["color"]), bold=1, shadow=4, align=5,
                            ml=120, mr=120),
                  ass_style("QmMark", "Georgia", 260, ass_colour(p["quoteMarkColor"]), align=5)]
        events = []
        for a, b, text in self.quotes:
            size = fit_font_size(text, p["font"], int(p["size"]), W - 240, 0.55)
            events += [
                ass_event(a, b, "QmMark", f"{{\\pos({W // 2},{H // 2 - 240})\\fad(350,350)\\1a&H40&}}“", 8),
                ass_event(a, b, "QmText", f"{{\\pos({W // 2},{H // 2 + 20})\\fs{size}\\fad(350,350)"
                                          f"\\fscx108\\fscy108\\t(0,600,\\fscx100\\fscy100)}}{esc(text)}", 8),
            ]
        return styles, events
