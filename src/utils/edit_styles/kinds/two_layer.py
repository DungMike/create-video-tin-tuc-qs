"""One clip, two layers: a sharp window over its own blurred, darkened self. The window
changes place chapter by chapter and opens to the full frame now and then.

Lab idea 23 (1.24x). Like tv_zoom this is a timeline: the states are GPU passes and only
the short moves between them run on the CPU chain (the window is resized every frame).
"""

from __future__ import annotations

from src.utils.edit_styles import assets
from src.utils.edit_styles.graph import ease
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import H, W, region_centre_offset, shift_subtitles

FULL = {"x": 0, "y": 0, "w": W, "h": H}


class TwoLayer(Kind):
    type_id = "two_layer"
    needs_chapters = True

    # ------------------------------------------------------------------ setup
    def _states(self) -> dict[str, dict]:
        p = self.p
        return {"center": p["centerRect"], "side": p["sideRect"], "full": FULL}

    def _order(self) -> list[str]:
        return {"center_side": ["center", "side"],
                "center_side_full": ["center", "side", "full"],
                "center_full": ["center", "full"]}.get(self.p["sequence"], ["center", "side"])

    def prepare(self, plan):
        p = self.p
        self.trans = float(p["transitionSeconds"])
        border = int(p["borderWidth"])
        self.frames = {}
        for name, rect in self._states().items():
            if name == "full":
                continue
            self.frames[name] = assets.card_frame({
                "rect": rect, "radius": 0, "borderWidth": border, "borderColor": p["borderColor"],
                "shadow": p.get("shadow", True), "shadowStrength": 170, "dim": float(p["bgDim"]),
            })
        order = self._order()
        starts = [float(c["start"]) for c in plan.chapters if c["start"] > self.trans * 2]
        self.switches = []
        for t in starts:
            if t + self.trans + 2 < plan.total and (not self.switches or t - self.switches[-1] > self.trans + 6):
                self.switches.append(t)
        self.sequence = [order[i % len(order)] for i in range(len(self.switches) + 1)]

    def timeline(self, plan):
        if not self.switches:
            return None
        pieces, cursor = [], 0.0
        for i, t in enumerate(self.switches):
            pieces.append({"start": cursor, "end": t, "state": self.sequence[i], "gpu": True})
            pieces.append({"start": t, "end": t + self.trans,
                           "state": f"{self.sequence[i]}->{self.sequence[i + 1]}", "gpu": False})
            cursor = t + self.trans
        pieces.append({"start": cursor, "end": plan.total, "state": self.sequence[-1], "gpu": True})
        return pieces

    def piece_uses_decor(self, plan) -> bool:
        return False

    def _state(self, plan) -> str:
        return (plan.piece or {}).get("state", self._order()[0])

    def _rect(self, name) -> dict:
        return self._states().get(name, FULL)

    # ------------------------------------------------------------------ overlay pass
    def inputs(self, plan, ss):
        state = self._state(plan)
        if "->" in state or state == "full":
            return []
        return [["-i", self.frames[state]]]

    def video_parts(self, plan, ops, chain, idx, ss):
        state = self._state(plan)
        if "->" in state:
            return self._transition_parts(plan, chain, idx, state)
        if state == "full":
            return [], chain, idx
        r = self._rect(state)
        parts = [
            ops.split(chain, ["tl_bg0", "tl_src"]),
            ops.blur("[tl_bg0]", "[tl_bg]", self.p["bgBlur"]),
            ops.scale("[tl_src]", r["w"], r["h"], "[tl_card]"),
            ops.overlay("[tl_bg]", "[tl_card]", "[tl_m]", str(r["x"]), str(r["y"]), repeat=False),
            ops.upload(idx, "tl_frm"),
            ops.overlay("[tl_m]", "[tl_frm]", "[tl_out]"),
        ]
        return parts, "[tl_out]", idx + 1

    def _transition_parts(self, plan, chain, idx, state):
        """CPU piece: the window travels and resizes; its border is a colour source
        scaled every frame (``pad`` has no per-frame size)."""
        a_name, b_name = state.split("->")
        a, b = self._rect(a_name), self._rect(b_name)
        dur = (plan.piece or {}).get("end", 0) - (plan.piece or {}).get("start", 0) or self.trans
        q = ease(f"clip(t/{dur:.3f},0,1)")

        def lerp(start, end):
            return f"({start}+({end - start})*{q})"

        rw, rh = lerp(a["w"], b["w"]), lerp(a["h"], b["h"])
        rx, ry = lerp(a["x"], b["x"]), lerp(a["y"], b["y"])
        border = int(self.p["borderWidth"])
        if "full" in (a_name, b_name) and border:
            bpx = f"({border}*(1-{q}))" if b_name == "full" else f"({border}*{q})"
        else:
            bpx = str(border)
        dim = 1 - float(self.p["bgDim"])
        colour = self.p["borderColor"].lstrip("#")
        parts = [
            f"{chain}split=2[tl_bg0][tl_src]",
            f"[tl_bg0]scale=240:136,gblur=sigma=6,scale={W}:{H}:flags=bicubic,"
            f"lutyuv=y='16+(val-16)*{dim:.3f}':u='128+(val-128)*{dim:.3f}':v='128+(val-128)*{dim:.3f}'[tl_bg]",
            f"[tl_src]scale=w='2*trunc({rw}/2)':h='2*trunc({rh}/2)':eval=frame[tl_card]",
        ]
        chain_label = "[tl_bg]"
        if border:
            parts += [
                f"color=c=0x{colour}:s=16x16:r=30,format=yuv420p,"
                f"scale=w='2*trunc(({rw}+2*{bpx})/2)':h='2*trunc(({rh}+2*{bpx})/2)':eval=frame[tl_brd]",
                f"[tl_bg][tl_brd]overlay=x='{rx}-{bpx}':y='{ry}-{bpx}':eval=frame:shortest=1:format=yuv420[tl_m0]",
            ]
            chain_label = "[tl_m0]"
        parts.append(f"{chain_label}[tl_card]overlay=x='{rx}':y='{ry}':eval=frame:format=yuv420[tl_out]")
        return parts, "[tl_out]", idx

    # ------------------------------------------------------------------ overlays
    def _wave_for(self, rect):
        return rect["x"] + 60, rect["y"] + rect["h"] - 250

    def _cta_for(self, rect):
        return rect["x"] + 30, rect["y"] + 20

    def _placed(self, plan, pick):
        state = self._state(plan)
        if "->" not in state:
            x, y = pick(self._rect(state))
            return str(x), str(y), False
        a_name, b_name = state.split("->")
        ax, ay = pick(self._rect(a_name))
        bx, by = pick(self._rect(b_name))
        dur = (plan.piece or {}).get("end", 0) - (plan.piece or {}).get("start", 0) or self.trans
        q = ease(f"clip(t/{dur:.3f},0,1)")
        return f"{ax}+({bx - ax})*{q}", f"{ay}+({by - ay})*{q}", True

    def wave_xy(self, plan, tx):
        return self._placed(plan, self._wave_for)

    def cta_xy(self, plan, tx):
        return self._placed(plan, self._cta_for)

    # ------------------------------------------------------------------ subtitles
    def subtitle_overrides(self, plan):
        # One width for the whole video (the ASS is built once): the narrowest window.
        rects = [self._rect(name) for name in self._order()]
        narrow = min(rects, key=lambda r: r["w"])
        return {"marginLR": max(10, (W - narrow["w"]) // 2 + 20)}

    def transform_subtitles(self, plan, ass_text):
        """Each stretch of subtitles follows the window it plays under."""
        if not getattr(self, "switches", None):
            return ass_text
        spans = []
        cursor = 0.0
        for i, t in enumerate(self.switches):
            spans.append((cursor, t + self.trans, self.sequence[i]))
            cursor = t + self.trans
        spans.append((cursor, plan.total, self.sequence[-1]))

        def dx_for(start, _end):
            for a, b, name in spans:
                if a <= start < b:
                    r = self._rect(name)
                    return region_centre_offset(r["x"], r["x"] + r["w"])
            return 0

        return shift_subtitles(ass_text, dx_for)

