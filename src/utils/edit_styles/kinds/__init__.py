"""Edit-style types that live in their own module (phase 2 onwards).

The phase-1 types are written inline in ``runtime.EditPlan``. Every later type is
a ``Kind``: a small object the plan asks the same questions at the same points —
assets to build, extra inputs, the filter fragment, where waveform/CTA go,
subtitle placement, ASS layers. A layout kind replaces the inline layout code; a
modifier kind is chained after the inline cut accents.

All times in expressions go through ``T(ss)`` (see ``graph``), so a kind draws
the same frame whether the overlay pass runs whole or in parallel segments.
"""

from __future__ import annotations

from src.utils.edit_styles.graph import Ops


class Kind:
    type_id = ""
    #: needs the overlay pass even with no waveform/CTA/noise
    video_work = True
    #: draws ASS layers (so an ASS file is built even without an SRT)
    ass_layers_needed = False
    needs_chapters = False
    #: measured clip starts (cuts) are used in expressions
    needs_clip_timing = False
    #: needs a still of every selected clip (album pile, film strip)
    needs_clip_media = False

    def __init__(self, record: dict):
        self.record = record
        self.p: dict = record["params"]
        self.assets: dict = {}

    # -- preparation --------------------------------------------------------
    def prepare(self, plan) -> None:
        """Static assets and timing; runs once before clip selection."""

    def clip_media(self, plan, stills: list[str]) -> None:
        """Per-clip pictures from the stills of the selected clips (background thread)."""

    # -- overlay pass ---------------------------------------------------------
    def inputs(self, plan, ss: float | None) -> list[list[str]]:
        """Extra ``-i`` groups, in the order ``video_parts`` indexes them. Same count for every ss."""
        return []

    def video_parts(self, plan, ops: Ops, chain: str, idx: int, ss: float | None) -> tuple[list[str], str, int]:
        return [], chain, idx

    def wave_xy(self, plan, tx: str):
        """(x, y, dynamic) for the waveform when the user left its position empty; None = unchanged."""
        return None

    def cta_xy(self, plan, tx: str):
        return None

    def overlay_hidden(self, plan, tx: str) -> str | None:
        """Expression that is non-zero while waveform and CTA must be hidden."""
        return None

    # -- timeline (layouts that change shape over time) -----------------------
    def timeline(self, plan) -> list[dict] | None:
        """Pieces ``[{start, end, state, gpu}]`` this layout is rendered in, or None for one pass.

        A piece is rendered by its own FFmpeg command and the pieces are concatenated:
        states that hold still run on the GPU, transitions that resize every frame run
        on the CPU chain (scale_cuda cannot re-evaluate its size per frame).
        """
        return None

    def piece_uses_decor(self, plan) -> bool:
        """False when the pipeline must not fit the video into the decor frame for this
        piece (the layout draws the decor itself, or the piece is full screen)."""
        return True

    # -- subtitles ------------------------------------------------------------
    def subtitle_overrides(self, plan) -> dict:
        return {}

    def transform_subtitles(self, plan, ass_text: str) -> str:
        return ass_text

    def ass_layers(self, plan) -> tuple[list[str], list[str]]:
        return [], []


def _registry() -> dict[str, type[Kind]]:
    from src.utils.edit_styles.kinds import (
        album, chapter_cards, doc_strip, dossier, light_fx, magazine, newsroom, quote_moments, split_accent,
        tv_zoom, two_layer,
    )

    return {
        "tv_zoom": tv_zoom.TvZoom,
        "two_layer": two_layer.TwoLayer,
        "magazine": magazine.Magazine,
        "split_accent": split_accent.SplitAccent,
        "dossier": dossier.Dossier,
        "newsroom": newsroom.Newsroom,
        "album": album.Album,
        "doc_strip": doc_strip.DocStrip,
        "chapter_cards": chapter_cards.ChapterCards,
        "quote_moments": quote_moments.QuoteMoments,
        "light_sweep": light_fx.LightSweep,
        "light_leak": light_fx.LightLeak,
        "light_rays": light_fx.LightRays,
        "spotlight": light_fx.Spotlight,
    }


def make(record: dict | None) -> Kind | None:
    if not record:
        return None
    cls = _registry().get(record.get("type", ""))
    return cls(record) if cls else None


def is_kind(type_id: str) -> bool:
    return type_id in _registry()
