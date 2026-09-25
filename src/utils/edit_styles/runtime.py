"""What one render needs from its layout and modifiers.

``EditPlan`` is built once per story (after the audio is known) and then asked,
by both overlay builders and for every parallel segment, for:

- extra ``-i`` inputs and the filter fragment that follows the decor slot;
- where/when the waveform and the CTA are drawn (expressions + ``enable``);
- ASS styles/events to append, and subtitle placement for ``build_ass``;
- the decor to use (a layout can swap the PNG or supply a synthetic frame);
- whether clip selection should chain consecutive clips (long takes).

Everything time-dependent is written against ``T(ss)`` so a segment starting at
``ss`` draws exactly what a single pass would.
"""

from __future__ import annotations

import os
import random
import threading

from src.utils.edit_styles import assets, chapters as chapter_mod, kinds as kind_mod, spec
from src.utils.edit_styles.graph import Ops, T, sum_expr, windows_expr
from src.utils.logger import logger

W, H, FPS = 1920, 1080, 30


# --------------------------------------------------------------------------- #
# ASS helpers
# --------------------------------------------------------------------------- #
def ts(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_colour(hex_colour: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (style field)."""
    v = str(hex_colour or "#FFFFFF").lstrip("#")
    if len(v) != 6:
        v = "FFFFFF"
    return f"&H{alpha:02X}{v[4:6]}{v[2:4]}{v[0:2]}".upper()


def ass_inline(hex_colour: str) -> str:
    """#RRGGBB -> &HBBGGRR& (override tag)."""
    v = str(hex_colour or "#FFFFFF").lstrip("#")
    if len(v) != 6:
        v = "FFFFFF"
    return f"&H{v[4:6]}{v[2:4]}{v[0:2]}&".upper()


def ass_alpha(opacity: float) -> str:
    return f"&H{max(0, min(255, int(round(255 * (1 - float(opacity)))))):02X}&"


def ass_style(name, fontname, size, primary="&H00FFFFFF", outline_c="&H00000000", back="&H00000000",
              bold=0, border_style=1, outline=0, shadow=0, align=7, ml=0, mr=0, mv=0, spacing=0) -> str:
    return (f"Style: {name},{fontname},{size},{primary},&H00FFFFFF,{outline_c},{back},{bold},0,0,0,100,100,"
            f"{spacing},0,{border_style},{outline},{shadow},{align},{ml},{mr},{mv},1")


def ass_event(start, end, style_name, text, layer=0, ml=0, mr=0, mv=0) -> str:
    return f"Dialogue: {layer},{ts(start)},{ts(end)},{style_name},,{ml},{mr},{mv},,{text}"


def _even(v: float) -> int:
    i = int(round(v))
    return i - i % 2


# --------------------------------------------------------------------------- #
class EditPlan:
    """Resolved layout + modifiers for one story. Cheap to query, built once."""

    def __init__(self, layout: dict | None, modifiers: list[dict], *, story_id: str, total: float,
                 clip_seconds: float, temp_dir: str):
        self.layout = layout
        self.modifiers = {m["type"]: m for m in modifiers}
        self.story_id = story_id
        self.total = float(total)
        self.clip_seconds = float(clip_seconds or 3)
        self.temp_dir = temp_dir
        self.assets: dict = {}
        self.chapters: list[dict] = []
        self.paragraphs: list[float] = []
        self.quotes: list[dict] = []
        self.cut_times: list[float] = []
        # Real start time of every clip in the concatenated base. Clips come out
        # of the concat a little shorter than their nominal length (~2.983s for a
        # 3s clip), so cuts drift ~3s off k*3 by the end of a 10-minute video:
        # anything synced to cuts must use these, not multiples of clip_seconds.
        self.clip_starts: list[float] = []
        self.clip_period = float(clip_seconds or 3)
        # False once measured clips differ in length (render plays every clip full, so
        # 8s video cuts and 3-5s photo clips mix): a mean period no longer hits the cuts.
        self.clip_uniform = True
        self.run_starts: list[int] | None = None  # clip indices that open a shot (long takes)
        self.decor_png_override: str | None = None
        self.decor_record_override: dict | None = None
        # The decor image the rotation dealt, once the overlay pass resolved it.
        self.decor_png_used: str | None = None
        self.decor_record_used: dict | None = None
        # The piece being built, for layouts rendered as a timeline (tv_zoom, two_layer).
        self.piece: dict | None = None
        self.camera_label = ""
        self.skipped: list[str] = []
        self.sub_cues: list[dict] = []
        # Phase-2+ types are Kind objects (edit_styles/kinds); phase-1 ones stay inline.
        self.layout_kind = kind_mod.make(layout)
        order = {t["id"]: i for i, t in enumerate(spec.TYPES)}
        self.mod_kinds = sorted((k for k in (kind_mod.make(m) for m in modifiers) if k),
                                key=lambda k: order.get(k.type_id, 999))
        self._media_thread: threading.Thread | None = None

    @property
    def kinds(self) -> list:
        return ([self.layout_kind] if self.layout_kind else []) + list(self.mod_kinds)

    # ------------------------------------------------------------ properties
    @property
    def layout_type(self) -> str:
        return self.layout["type"] if self.layout else ""

    @property
    def p(self) -> dict:
        return self.layout["params"] if self.layout else {}

    def mod(self, type_id: str) -> dict | None:
        m = self.modifiers.get(type_id)
        return m["params"] if m else None

    @property
    def requires_decor(self) -> bool:
        t = spec.get_type(self.layout_type)
        return bool(t and t.get("requiresDecor"))

    @property
    def long_takes(self) -> dict | None:
        return self.mod("long_takes")

    @property
    def has_video_work(self) -> bool:
        """Anything that needs the overlay pass even with no waveform/CTA/noise."""
        video_layouts = {"card", "letterbox", "film_frame", "osd_cctv", "drift", "tv_drift"}
        return (self.layout_type in video_layouts or self.decor_record_override is not None
                or any(k in self.modifiers for k in ("cut_accents", "voice_bars"))
                or any(k.video_work for k in self.kinds))

    @property
    def has_ass_layers(self) -> bool:
        return (self.layout_type in ("letterbox", "osd_camcorder", "osd_cctv")
                or any(k.ass_layers_needed for k in self.kinds))

    @property
    def needs_clip_timing(self) -> bool:
        return "cut_accents" in self.modifiers or (
            self.layout_type == "drift" and bool(self.p.get("redirectEachClip", True))) or any(
            k.needs_clip_timing or k.needs_clip_media for k in self.kinds)

    @property
    def needs_clip_media(self) -> bool:
        return any(k.needs_clip_media for k in self.kinds)

    def set_clip_starts(self, starts: list[float]):
        """Measured clip start times of the base; derives visual cuts and the mean clip length."""
        self.clip_starts = [float(s) for s in starts]
        if len(self.clip_starts) > 1:
            self.clip_period = (self.clip_starts[-1] - self.clip_starts[0]) / (len(self.clip_starts) - 1)
            gaps = [b - a for a, b in zip(self.clip_starts, self.clip_starts[1:])]
            # Concat shaves a few frames off some clips (~2.983s for 3s): allow 15%.
            self.clip_uniform = max(abs(gap - self.clip_period) for gap in gaps) <= 0.15 * self.clip_period
        indices = self.run_starts if self.run_starts is not None else range(len(self.clip_starts))
        self.cut_times = [self.clip_starts[i] for i in indices if 0 < i < len(self.clip_starts)]

    @property
    def needs_chapters(self) -> bool:
        return self.layout_type == "letterbox" or (
            self.mod("cut_accents") is not None and self.mod("cut_accents").get("dipOn") == "chapter") or any(
            k.needs_chapters for k in self.kinds)

    # ------------------------------------------------------------ preparation
    def prepare(self, *, audio_path: str, subtitle_path: str = "", chapters_path: str = "",
                sub_cues: list[dict] | None = None, fully_baked: bool = False):
        """Build static assets, per-video media and timing. Runs once, before clip selection."""
        raw_cues = []
        if subtitle_path and os.path.isfile(subtitle_path):
            try:
                from src.utils.story_subtitles import parse_srt
                raw_cues = parse_srt(subtitle_path)
            except Exception as exc:  # noqa: BLE001 - chapters are best effort
                logger.warning(f"[EditStyles:{self.story_id}] SRT unreadable for chapters: {exc}")
        # Chapter settings live on the layout; a modifier that uses chapters (chapter
        # cards) carries its own copy for layouts that have none.
        chapter_p = self.p if "autoTitle" in self.p else next(
            (k.p for k in self.mod_kinds if "autoTitle" in k.p), {})
        auto_title = chapter_p.get("autoTitle", "Phần {n}")
        every = float(chapter_p.get("autoChapterMinutes", 2.0)) * 60
        self.sub_cues = list(sub_cues or [])
        timing = chapter_mod.resolve(self.total, chapters_path, raw_cues, self.sub_cues, every, auto_title)
        self.chapters, self.paragraphs, self.quotes = timing["chapters"], timing["paragraphs"], timing["quotes"]

        lt = self.layout_type
        p = self.p
        if lt == "card":
            self.assets["frame"] = assets.card_frame(p)
        elif lt == "letterbox":
            self.assets["bars"] = assets.letterbox_bars(p)
        elif lt == "film_frame":
            self.decor_png_override = assets.film_frame(p)
            self.decor_record_override = {"frame": dict(p["rect"]), "overscan": 0.01}
        elif lt == "osd_cctv":
            labels = p.get("cameraLabels") or ["CAM 01"]
            self.camera_label = random.Random(self.story_id).choice(labels)
            if p.get("scanBand"):
                self.assets["band"] = assets.solid("#FFFFFF", float(p["scanOpacity"]), (W, 90))

        cut = self.mod("cut_accents")
        if cut:
            if cut.get("flash"):
                self.assets["flash"] = assets.solid(cut["flashColor"], float(cut["flashOpacity"]))
            if cut.get("dip"):
                self.assets["dips"] = [assets.solid(cut["dipColor"], a) for a in (0.35, 0.65, 0.9)]

        vb = self.mod("voice_bars")
        if vb and fully_baked:
            self.skipped.append("voice_bars")
            vb = None
            self.modifiers.pop("voice_bars", None)
        if vb:
            out = os.path.join(self.temp_dir, f"voice_bars_{self.story_id}.mov")
            self.assets["voice"] = assets.voice_bars(audio_path, out, self.total, vb, FPS)
        if fully_baked and "cta_moments" in self.modifiers:
            self.skipped.append("cta_moments")
            self.modifiers.pop("cta_moments", None)
        for kind in self.kinds:
            kind.prepare(self)

    # ------------------------------------------------------------ per-clip media
    def start_clip_media(self, clips: list[str]):
        """Stills of the selected clips and the per-clip pictures built from them, in a
        background thread that runs while the base video is being concatenated."""
        if not self.needs_clip_media or not clips:
            return
        from src.utils.edit_styles.kinds.common import extract_stills

        def work():
            try:
                stills = extract_stills(clips, os.path.join(self.temp_dir, "edit_stills"))
                for kind in self.kinds:
                    if kind.needs_clip_media:
                        kind.clip_media(self, stills)
            except Exception as exc:  # noqa: BLE001 - the layout degrades, the render goes on
                logger.warning(f"[EditStyles:{self.story_id}] Clip stills failed: {exc}")

        self._media_thread = threading.Thread(target=work, name=f"edit-media-{self.story_id}", daemon=True)
        self._media_thread.start()

    def finish_clip_media(self):
        if self._media_thread is not None:
            self._media_thread.join()
            self._media_thread = None

    def use_decor(self, decor_record: dict, decor_png: str):
        """Remember the dealt decor (a timeline layout scales it itself) and let
        tv_glass swap the PNG for one with a reflection on the glass."""
        self.decor_record_used, self.decor_png_used = decor_record, decor_png
        if self.layout_type == "tv_glass" and decor_record and decor_png:
            key = {"decor": decor_record.get("id"), "at": decor_record.get("updatedAt"),
                   **{k: self.p[k] for k in ("glareStrength", "glareSecondary", "glareSlope", "glareWidth",
                                             "topSheen")}}
            self.decor_png_override = assets.glare_decor(decor_png, decor_record["frame"], self.p, key)

    # ------------------------------------------------------------ clip selection
    def run_length(self, rng: random.Random) -> int:
        lt = self.long_takes or {}
        weights = [max(0, int(lt.get(f"weight{i}", 0))) for i in (1, 2, 3)]
        if not any(weights):
            return 1
        return rng.choices([1, 2, 3], weights=weights)[0]

    # ------------------------------------------------------------ inputs + filters
    def video_inputs(self, ss: float | None) -> list[list[str]]:
        """Extra input args, in the order video_parts() indexes them:
        layout, cut accents, then modifier kinds."""
        out: list[list[str]] = []
        if self.layout_kind:
            out += self.layout_kind.inputs(self, ss)
        else:
            out += [["-i", path] for path in self._layout_input_paths()]
        out += [["-i", path] for path in self._cut_input_paths()]
        for kind in self.mod_kinds:
            out += kind.inputs(self, ss)
        return out

    def _layout_input_paths(self) -> list[str]:
        paths = []
        if self.layout_type == "card":
            paths.append(self.assets["frame"])
        elif self.layout_type == "letterbox":
            paths.append(self.assets["bars"])
        elif self.layout_type == "osd_cctv" and self.assets.get("band"):
            paths.append(self.assets["band"])
        return paths

    def _cut_input_paths(self) -> list[str]:
        paths = []
        if self.assets.get("flash"):
            paths.append(self.assets["flash"])
        for dip in self.assets.get("dips", []):
            paths.append(dip)
        return paths

    def video_parts(self, ops: Ops, chain: str, first_index: int, ss: float | None) -> tuple[list[str], str]:
        """Filter fragment inserted right after the decor slot."""
        parts: list[str] = []
        idx = first_index
        tx = T(ss)
        lt, p = self.layout_type, self.p

        if self.layout_kind:
            kind_parts, chain, idx = self.layout_kind.video_parts(self, ops, chain, idx, ss)
            parts += kind_parts
        elif lt == "card":
            r = p["rect"]
            bx, by = self._card_origin(tx)
            parts += [ops.split(chain, ["ec_bg", "ec_src"]),
                      ops.blur("[ec_bg]", "[ec_blur]", p.get("blur", "medium")),
                      ops.scale("[ec_src]", r["w"], r["h"], "[ec_card]"),
                      ops.overlay("[ec_blur]", "[ec_card]", "[ec_m]", bx, by, dynamic=bool(p.get("bob")), repeat=False),
                      ops.upload(idx, "ec_frm"),
                      ops.overlay("[ec_m]", "[ec_frm]", "[ec_out]", f"{bx}-{r['x']}", f"{by}-{r['y']}",
                                  dynamic=bool(p.get("bob")))]
            chain, idx = "[ec_out]", idx + 1
        elif lt == "letterbox":
            parts += [ops.upload(idx, "el_bars"), ops.overlay(chain, "[el_bars]", "[el_out]")]
            chain, idx = "[el_out]", idx + 1
        elif lt == "osd_cctv" and self.assets.get("band"):
            y = f"mod({tx}*{int(p['scanSpeed'])},{H + 90})-90"
            parts += [ops.upload(idx, "eb_band"),
                      ops.overlay(chain, "[eb_band]", "[eb_out]", "0", y, dynamic=True)]
            chain, idx = "[eb_out]", idx + 1
        elif lt in ("drift", "tv_drift"):
            zoom = float(p["zoom"])
            zw, zh = _even(W * zoom), _even(H * zoom)
            mx, my = (zw - W) / 2, (zh - H) / 2
            amp = float(p["amplitude"])
            if lt == "drift" and p.get("redirectEachClip", True) and self.clip_uniform:
                # Measured mean clip length: nominal 3s would drift off the real cuts.
                # Clips of mixed length fall through to the smooth drift below instead
                # of turning mid-shot.
                per = f"{self.clip_period:.5f}"
                k = f"floor({tx}/{per})"
                prog = f"(2*mod({tx},{per})/{per}-1)"
                x = f"-{mx:.1f}+{mx * amp:.1f}*cos({k}*2.39996)*{prog}"
                y = f"-{my:.1f}+{my * amp:.1f}*sin({k}*2.39996)*{prog}"
            else:
                px = float(p.get("periodX", 41))
                py = float(p.get("periodY", 53))
                x = f"-{mx:.1f}+{mx * amp:.1f}*sin(2*PI*{tx}/{px:g})"
                y = f"-{my:.1f}+{my * amp:.1f}*sin(2*PI*{tx}/{py:g})"
            parts += [ops.split(chain, ["ed_cv", "ed_z"]), ops.scale("[ed_z]", zw, zh, "[ed_zz]"),
                      ops.overlay("[ed_cv]", "[ed_zz]", "[ed_out]", x, y, dynamic=True, repeat=False)]
            chain = "[ed_out]"

        cut = self.mod("cut_accents")
        if cut:
            boundaries = self._dip_boundaries(cut)
            near = windows_expr([(b - 0.6, b + 0.6) for b in boundaries], tx)
            if self.assets.get("flash"):
                flash_on = self._flash_windows(cut)
                enable = f"({windows_expr(flash_on, tx)})*(1-({near}))"
                parts += [ops.upload(idx, "ef_fl"), ops.overlay(chain, "[ef_fl]", "[ef_f0]", enable=enable)]
                chain, idx = "[ef_f0]", idx + 1
            half = float(cut["dipSeconds"]) / 2
            for level, dip in enumerate(self.assets.get("dips", [])):
                # 35% -> 65% -> 90% -> 65% -> 35%, symmetric around each boundary
                edges = [(-half, -half * 0.6), (half * 0.6, half)] if level == 0 else (
                    [(-half * 0.6, -half * 0.2), (half * 0.2, half * 0.6)] if level == 1 else [(-half * 0.2, half * 0.2)])
                wins = [(b + a0, b + a1) for b in boundaries for (a0, a1) in edges]
                parts += [ops.upload(idx, f"ep_d{level}"),
                          ops.overlay(chain, f"[ep_d{level}]", f"[ep_o{level}]", enable=windows_expr(wins, tx))]
                chain, idx = f"[ep_o{level}]", idx + 1
        for kind in self.mod_kinds:
            kind_parts, chain, idx = kind.video_parts(self, ops, chain, idx, ss)
            parts += kind_parts
        return parts, chain

    def input_count(self) -> int:
        return len(self.video_inputs(0.0))

    def timeline(self) -> list[dict] | None:
        """Pieces this layout is rendered in, or None for one pass over the whole video."""
        return self.layout_kind.timeline(self) if self.layout_kind else None

    def piece_uses_decor(self) -> bool:
        """Whether the pipeline fits the video into the decor frame for the current piece."""
        if self.layout_kind:
            return self.layout_kind.piece_uses_decor(self)
        return True

    def _overlay_hidden(self, tx: str) -> str | None:
        """Non-zero while waveform and CTA must be off screen (a quote holds the frame)."""
        terms = [h for h in (k.overlay_hidden(self, tx) for k in self.kinds) if h]
        return sum_expr(f"({h})" for h in terms) if terms else None

    # ------------------------------------------------------------ waveform / CTA
    def wave_override(self, record: dict | None, default_xy: tuple[str, str], ss: float | None) -> dict | None:
        """Placement (and for voice bars, the input) of the waveform layer."""
        tx = T(ss)
        vb = self.mod("voice_bars")
        x, y = default_xy
        custom = self.p.get("wavePlacement")
        dynamic = False
        if custom:
            x, y = str(custom["x"]), str(custom["y"])
        elif self.layout_type == "card":
            bx, by = self._card_origin(tx)
            r = self.p["rect"]
            x, y, dynamic = f"{bx}+60", f"{by}+{r['h'] - 250}", bool(self.p.get("bob"))
        elif self.layout_type == "letterbox":
            bar = assets.letterbox_bar_height(self.p["aspect"])
            x, y = str(W - 450), str(H - bar - 250)
        elif self.layout_type == "film_frame":
            r = self.p["rect"]
            x, y = str(r["x"] + r["w"] - 470), str(r["y"] + r["h"] - 250)
        elif self.layout_type in ("osd_camcorder", "osd_cctv"):
            x, y = "60", "760"
        elif self.layout_kind:
            placed = self.layout_kind.wave_xy(self, tx)
            if placed:
                x, y, dynamic = placed
        out = {"x": x, "y": y, "dynamic": dynamic}
        hidden = self._overlay_hidden(tx)
        if hidden:
            out["enable"] = f"not({hidden})"
        if vb and self.assets.get("voice"):
            place = vb.get("placement")
            if place:
                out["x"], out["y"] = str(place["x"]), str(place["y"])
            elif record and not custom and self.layout_type in ("", "plain", "tv_frame", "tv_glass", "tv_drift"):
                rh = int(record.get("processedHeight") or 236)
                out["y"] = f"({out['y']})+{(rh - int(vb['height'])) // 2}"
            out["input"] = ["-ss", f"{float(ss or 0):.3f}", "-i", self.assets["voice"]]
        return out

    def cta_override(self, record: dict | None, default_xy: tuple[str, str], ss: float | None) -> dict:
        tx = T(ss)
        x, y = default_xy
        dynamic = False
        custom = self.p.get("ctaPlacement")
        if custom:
            x, y = str(custom["x"]), str(custom["y"])
        elif self.layout_type == "card":
            bx, by = self._card_origin(tx)
            x, y, dynamic = f"{bx}+30", f"{by}+20", bool(self.p.get("bob"))
        elif self.layout_type == "letterbox":
            x, y = "40", str(assets.letterbox_bar_height(self.p["aspect"]) + 12)
        elif self.layout_type == "film_frame":
            r = self.p["rect"]
            x, y = str(r["x"] + 20), str(r["y"] + 20)
        elif self.layout_type in ("osd_camcorder", "osd_cctv"):
            x, y = "90", "210"
        elif self.layout_kind:
            placed = self.layout_kind.cta_xy(self, tx)
            if placed:
                x, y, dynamic = placed
        out = {"x": x, "y": y, "dynamic": dynamic, "enable": None}
        cm = self.mod("cta_moments")
        if cm:
            wins = self._cta_windows(cm)
            out["enable"] = windows_expr(wins, tx)
            if cm["entry"] != "cut" and wins:
                width = int((record or {}).get("processedWidth") or 360)
                slide = 0.45
                terms = []
                for a, b in wins:
                    terms.append(f"between({tx},{a:.3f},{a + slide:.3f})*(1-({tx}-{a:.3f})/{slide})")
                    terms.append(f"between({tx},{b - slide:.3f},{b:.3f})*(({tx}-{b - slide:.3f})/{slide})")
                offset = sum_expr(terms)
                if cm["entry"] == "slide_left":
                    out["x"] = f"({x})-({x}+{width})*({offset})"
                else:
                    out["x"] = f"({x})+({W}-({x}))*({offset})"
                out["dynamic"] = True
        hidden = self._overlay_hidden(tx)
        if hidden:
            out["enable"] = f"({out['enable']})*not({hidden})" if out["enable"] else f"not({hidden})"
        return out

    # ------------------------------------------------------------ subtitles / ASS
    def subtitle_overrides(self) -> dict:
        p = self.p
        out: dict = {}
        if not self.layout:
            return out
        user_wins = self.layout_kind is not None
        if self.layout_kind:
            out.update(self.layout_kind.subtitle_overrides(self))
        if self.layout_type == "letterbox":
            bar = assets.letterbox_bar_height(p["aspect"])
            if bar >= 100:
                size = int(p.get("subFontSize") or 44)
                size = min(size, int((bar - 16) / 2.5))
                out["fontSize"] = size
                out["marginV"] = max(8, int((bar - 2.5 * size) / 2))
        if int(p.get("subFontSize") or 0) and ("fontSize" not in out or user_wins):
            out["fontSize"] = int(p["subFontSize"])
        if int(p.get("subMarginV") or 0) and ("marginV" not in out or user_wins):
            out["marginV"] = int(p["subMarginV"])
        if int(p.get("subMarginLR") or 0):
            out["marginLR"] = int(p["subMarginLR"])
        if p.get("subForceColors"):
            out["textColor"] = p.get("subTextColor")
            out["outlineColor"] = p.get("subOutlineColor")
            if int(p.get("subOutlineWidth", -1)) >= 0:
                out["outlineWidth"] = int(p["subOutlineWidth"])
        return out

    def transform_subtitles(self, ass_text: str) -> str:
        """Kind-specific edits of the built subtitle events (move into a region, hide quoted lines)."""
        for kind in self.kinds:
            ass_text = kind.transform_subtitles(self, ass_text)
        return ass_text

    def ass_layers(self) -> tuple[list[str], list[str]]:
        lt, p = self.layout_type, self.p
        styles: list[str] = []
        events: list[str] = []
        if lt == "letterbox":
            bar = assets.letterbox_bar_height(p["aspect"])
            if p.get("titleEnabled") and bar >= 50:
                styles.append(ass_style("EsTitle", p["titleFont"], int(p["titleSize"]), ass_colour(p["titleColor"]),
                                        align=8, spacing=int(p["titleSpacing"])))
                total = len(self.chapters)
                for c in self.chapters:
                    if not c["title"]:
                        continue  # no chapter file: the bar stays empty rather than inventing a title
                    label = chapter_mod.format_title(p["titleFormat"], c["n"], total, c["title"], c.get("label", ""))
                    events.append(ass_event(c["start"], c["end"], "EsTitle",
                                            f"{{\\an8\\pos({W // 2},{max(8, bar // 2 - int(p['titleSize']) // 2)})"
                                            f"\\fad(600,400)}}{label}", 6))
            if p.get("progressEnabled"):
                styles.append(ass_style("EsBar", "Arial", 10))
                events += self._progress_events(H - int(p["progressHeight"]) - 2, p["progressColor"],
                                                int(p["progressHeight"]))
        elif lt in ("osd_camcorder", "osd_cctv"):
            styles += [ass_style("EsOsd", p["font"], int(p["size"]), ass_colour(p["color"]), bold=1, outline=2,
                                 shadow=2, spacing=2),
                       ass_style("EsBox", "Arial", 10)]
            if p.get("corners"):
                events += self._corner_events(p)
            events += self._osd_events(lt, p)
        for kind in self.kinds:
            kind_styles, kind_events = kind.ass_layers(self)
            styles += [st for st in kind_styles if st.split(",", 1)[0] not in {x.split(",", 1)[0] for x in styles}]
            events += kind_events
        return styles, events

    # ------------------------------------------------------------ internals
    def _card_origin(self, tx: str) -> tuple[str, str]:
        r, p = self.p["rect"], self.p
        if not p.get("bob"):
            return str(r["x"]), str(r["y"])
        amp, period = int(p["bobAmplitude"]), int(p["bobPeriod"])
        return (f"{r['x']}+{amp}*sin(2*PI*{tx}/{period + 1})",
                f"{r['y']}+{max(1, int(amp * 0.8))}*sin(2*PI*{tx}/{period})")

    def _dip_boundaries(self, cut: dict) -> list[float]:
        if cut.get("dipOn") == "chapter":
            return [c["start"] for c in self.chapters if c["start"] > 0.5]
        return [b for b in self.paragraphs if b > 0.5]

    def _flash_windows(self, cut: dict) -> list[tuple[float, float]]:
        every = max(1, int(cut["flashEveryNCuts"]))
        length = max(1, int(cut["flashFrames"])) / FPS
        cuts = self.cut_times or [k * self.clip_period for k in range(1, int(self.total / self.clip_period) + 1)]
        return [(c, c + length) for i, c in enumerate(cuts, start=1) if i % every == 0 and c < self.total]

    def _cta_windows(self, cm: dict) -> list[tuple[float, float]]:
        show = float(cm["showSeconds"])
        wins = []
        for raw in cm.get("moments") or []:
            raw = str(raw).strip()
            try:
                t = float(raw[:-1]) / 100 * self.total if raw.endswith("%") else float(raw)
            except ValueError:
                continue
            if 0 <= t < self.total:
                wins.append((t, min(self.total, t + show)))
        end = float(cm.get("endSeconds") or 0)
        if end > 0:
            wins.append((max(0.0, self.total - end), self.total))
        wins.sort()
        merged: list[tuple[float, float]] = []
        for a, b in wins:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        return merged

    def _progress_events(self, y: int, colour: str, height: int) -> list[str]:
        """One static bar per second: safe to cut anywhere (no \\t spanning a segment)."""
        inline = ass_inline(colour)
        out = []
        steps = int(self.total)
        for s in range(steps):
            wpx = max(1, round(W * (s + 1) / self.total))
            out.append(ass_event(s, min(self.total, s + 1), "EsBar",
                                 f"{{\\an7\\pos(0,{y})\\1c{inline}\\p1}}m 0 0 l {wpx} 0 {wpx} {height} 0 {height}{{\\p0}}",
                                 6))
        return out

    def _corner_events(self, p: dict) -> list[str]:
        """Four small drawings. One drawing spanning all corners makes libass blend
        a 1920x1080 bitmap every frame: measured 3.3x slower than the whole render."""
        inset, a, t = int(p["cornerInset"]), int(p["cornerArm"]), int(p["cornerThickness"])
        b = a - t
        alpha = ass_alpha(float(p["cornerOpacity"]))
        colour = ass_inline(p["color"])
        shapes = [
            (inset, inset, f"m 0 {a} l 0 0 {a} 0 {a} {t} {t} {t} {t} {a}"),
            (W - inset - a, inset, f"m {a} {a} l {a} 0 0 0 0 {t} {b} {t} {b} {a}"),
            (inset, H - inset - a, f"m 0 0 l 0 {a} {a} {a} {a} {b} {t} {b} {t} 0"),
            (W - inset - a, H - inset - a, f"m {a} 0 l {a} {a} 0 {a} 0 {b} {b} {b} {b} 0"),
        ]
        return [ass_event(0, self.total, "EsBox",
                          f"{{\\an7\\pos({x},{y})\\1c{colour}\\1a{alpha}\\p1}}{shape}{{\\p0}}", 5)
                for x, y, shape in shapes]

    def _osd_events(self, lt: str, p: dict) -> list[str]:
        total = self.total
        events = []
        rec_colour = ass_inline(p["recColor"])
        white = ass_inline(p["color"])
        if lt == "osd_camcorder":
            if p.get("showPlay"):
                events.append(ass_event(0, total, "EsOsd", f"{{\\an7\\pos(100,92)}}{p['playText']}", 5))
            if p.get("showBattery"):
                events.append(ass_event(0, total, "EsOsd", "{\\an9\\pos(1820,92)}SP  ▮▮▮", 5))
        else:
            events.append(ass_event(0, total, "EsOsd", f"{{\\an7\\pos(100,92)}}{self.camera_label}", 5))
        blink = float(p.get("blinkPeriod", 1.0))
        show_rec = p.get("showRec", True)
        if show_rec:
            t = 0.0
            pos = "\\an9\\pos(1820,150)" if lt == "osd_camcorder" else "\\an7\\pos(100,150)"
            while t < total:
                events.append(ass_event(t, min(total, t + blink * 0.6), "EsOsd",
                                        f"{{{pos}\\1c{rec_colour}}}●{{\\1c{white}}} REC", 5))
                t += blink
        if lt == "osd_camcorder" and not p.get("showCounter", True):
            return events
        start = 0
        if lt == "osd_cctv" and p.get("clockMode") == "clock":
            parsed = chapter_mod.parse_time(p.get("startTime", "00:00:00"))
            start = int(parsed or 0)
        for s in range(int(total)):
            v = start + s
            hh, rem = divmod(v % 86400, 3600)
            mm, ss_ = divmod(rem, 60)
            if lt == "osd_camcorder":
                txt, pos = f"{hh}:{mm:02d}:{ss_:02d}", "\\an3\\pos(1820,1000)"
            else:
                txt, pos = f"{hh:02d}:{mm:02d}:{ss_:02d}", "\\an9\\pos(1820,92)"
            events.append(ass_event(s, min(total, s + 1), "EsOsd", f"{{{pos}}}{txt}", 5))
        return events


# --------------------------------------------------------------------------- #
def build_plan(layout_id: str, modifier_ids: list[str], *, story_id: str, total: float, clip_seconds: float,
               temp_dir: str) -> EditPlan | None:
    """None when the story asks for nothing (plain layout, no modifiers)."""
    from src.utils.edit_styles import store

    layout = store.get_edit_style(layout_id) if layout_id else None
    if layout and layout.get("group") != "layout":
        layout = None
    modifiers = store.resolve_modifiers(modifier_ids or [])
    if not layout and not modifiers:
        return None
    return EditPlan(layout, modifiers, story_id=story_id, total=total, clip_seconds=clip_seconds,
                    temp_dir=temp_dir)
