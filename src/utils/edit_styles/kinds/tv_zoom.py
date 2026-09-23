"""TV opens to full screen: the video plays inside the decor's screen, then the whole
room scales about that screen until the picture fills the frame — and back again at the
next chapter.

Lab idea 22 (1.18x). Rendered as a timeline: the two resting states are ordinary GPU
passes, and only the ~1 second of zoom runs on the CPU chain, because it re-scales both
the video and the decor on every frame (``scale_cuda`` fixes its size at init).
"""

from __future__ import annotations

from src.utils.edit_styles.graph import ease
from src.utils.edit_styles.kinds import Kind
from src.utils.edit_styles.kinds.common import H, W


def _geometry(plan) -> dict:
    """Where the decor's screen sits, from the decor record the rotation dealt."""
    from src.utils.story_decor_images import decor_fit_geometry

    record = plan.decor_record_used or {"frame": {"x": 0, "y": 0, "w": W, "h": H}}
    return decor_fit_geometry(record)


class TvZoom(Kind):
    type_id = "tv_zoom"
    needs_chapters = True

    def prepare(self, plan):
        p = self.p
        self.trans = float(p["transitionSeconds"])
        if p["trigger"] == "interval":
            every = float(p["everyMinutes"]) * 60
            starts = [k * every for k in range(1, int(plan.total // every) + 1)]
        else:
            starts = [float(c["start"]) for c in plan.chapters if c["start"] > self.trans * 2]
        # Each switch needs room for the zoom plus a moment of stillness on both sides.
        self.switches = []
        for t in starts:
            if t + self.trans + 2 < plan.total and (not self.switches or t - self.switches[-1] > self.trans + 6):
                self.switches.append(t)

    # ------------------------------------------------------------------ timeline
    def timeline(self, plan):
        if not self.switches:
            return None
        state = self.p["startState"]
        pieces, cursor = [], 0.0
        for t in self.switches:
            pieces.append({"start": cursor, "end": t, "state": state, "gpu": True})
            nxt = "full" if state == "room" else "room"
            pieces.append({"start": t, "end": t + self.trans, "state": f"{state}->{nxt}", "gpu": False})
            cursor, state = t + self.trans, nxt
        pieces.append({"start": cursor, "end": plan.total, "state": state, "gpu": True})
        return pieces

    def piece_uses_decor(self, plan) -> bool:
        # The room state is the plain decor fit; full screen has none, and the zoom
        # scales the decor itself (so it takes the PNG as its own input instead).
        return (plan.piece or {}).get("state", self.p["startState"]) == "room"

    def _state(self, plan) -> str:
        return (plan.piece or {}).get("state", self.p["startState"])

    # ------------------------------------------------------------------ overlay pass
    def inputs(self, plan, ss):
        if "->" not in self._state(plan) or not plan.decor_png_used:
            return []
        return [["-loop", "1", "-framerate", "30", "-i", plan.decor_png_used]]

    def video_parts(self, plan, ops, chain, idx, ss):
        state = self._state(plan)
        if "->" not in state:
            return [], chain, idx  # resting state: the pipeline's decor fit (or nothing)
        if not plan.decor_png_used:
            return [], chain, idx
        geo = _geometry(plan)
        fx, fy, fw, fh = geo["fitX"], geo["fitY"], geo["fitW"], geo["fitH"]
        dur = (plan.piece or {}).get("end", 0) - (plan.piece or {}).get("start", 0) or self.trans
        # `t` restarts at 0 in every piece, so the progress is local to the zoom.
        q = ease(f"clip(t/{dur:.3f},0,1)")
        if state.startswith("full"):
            q = f"(1-{q})"
        rw, rh = f"({fw}+{W - fw}*{q})", f"({fh}+{H - fh}*{q})"
        rx, ry = f"({fx}*(1-{q}))", f"({fy}*(1-{q}))"
        scale = f"({rw}/{fw})"
        parts = [
            f"{chain}split=2[tz_bg][tz_src]",
            f"[tz_src]scale=w='2*trunc({rw}/2)':h='2*trunc({rh}/2)':eval=frame[tz_vid]",
            f"[{idx}:v]format=rgba,scale=w='2*trunc({W}*{scale}/2)':h='2*trunc({H}*{scale}/2)':eval=frame[tz_dec]",
            f"[tz_bg][tz_vid]overlay=x='{rx}':y='{ry}':eval=frame:format=yuv420[tz_m]",
            f"[tz_m][tz_dec]overlay=x='{rx}-{fx}*{scale}':y='{ry}-{fy}*{scale}':eval=frame:format=yuv420[tz_out]",
        ]
        return parts, "[tz_out]", idx + 1

    # ------------------------------------------------------------------ overlays
    def _wave_room(self, plan):
        geo = _geometry(plan)
        return geo["fitX"] + geo["fitW"] - 470, geo["fitY"] + geo["fitH"] - 250

    def wave_xy(self, plan, tx):
        state = self._state(plan)
        room = self._wave_room(plan)
        full = (W - 450, H - 250)
        if state == "room":
            return str(room[0]), str(room[1]), False
        if state == "full":
            return str(full[0]), str(full[1]), False
        dur = (plan.piece or {}).get("end", 0) - (plan.piece or {}).get("start", 0) or self.trans
        q = ease(f"clip(t/{dur:.3f},0,1)")
        a, b = (room, full) if state.startswith("room") else (full, room)
        return f"{a[0]}+({b[0] - a[0]})*{q}", f"{a[1]}+({b[1] - a[1]})*{q}", True

    def cta_xy(self, plan, tx):
        state = self._state(plan)
        if state == "room":
            geo = _geometry(plan)
            return str(geo["fitX"] + 20), str(geo["fitY"] + 20), False
        return None if "->" not in state else ("40", "30", False)
