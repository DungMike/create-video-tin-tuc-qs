"""Helpers shared by the kind modules: ASS text surgery and per-clip media."""

from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

W, H = 1920, 1080

_POS = re.compile(r"\\pos\(\s*(-?[\d.]+)\s*,")
_MOVE = re.compile(r"\\move\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,")
_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[.](\d{2})")


def esc(text: str) -> str:
    """Plain text for an ASS event: braces would open override blocks; newlines become \\N."""
    return str(text or "").replace("{", "(").replace("}", ")").replace("\r", "").replace("\n", "\\N")


def fit_font_size(text: str, family: str, size: int, max_width: float, min_ratio: float = 0.6) -> int:
    """Shrink a size until the longest line fits ``max_width`` (measured with the real font).

    Column text also carries ``\\q0`` so what still does not fit wraps at spaces:
    the script header disables wrapping (WrapStyle 2) for the subtitles.
    """
    from src.utils.edit_styles import assets

    lines = [line for line in str(text or "").replace("\\N", "\n").split("\n") if line.strip()]
    if not lines:
        return size
    try:
        f = assets.font(family, size)
        widest = max(f.getlength(line) for line in lines)
    except Exception:  # noqa: BLE001 - measuring is a nicety
        return size
    if widest <= max_width:
        return size
    return max(int(size * min_ratio), int(size * max_width / widest))


def ass_seconds(stamp: str) -> float:
    m = _TIME.match(stamp.strip())
    if not m:
        return 0.0
    h, mi, s, cs = (int(v) for v in m.groups())
    return h * 3600 + mi * 60 + s + cs / 100


def _default_margin(lines: list[str]) -> int:
    for line in lines:
        if line.startswith("Style: Default,"):
            fields = line.split(",")
            try:
                return int(fields[19])
            except (IndexError, ValueError):
                break
    return 40


def map_default_events(ass_text: str, fn) -> str:
    """Rewrite the subtitle (``Default``) dialogues: ``fn(fields, start, end) -> fields | None``."""
    lines = ass_text.split("\n")
    out = []
    for line in lines:
        if line.startswith("Dialogue:"):
            head, _, rest = line.partition(":")
            fields = rest.strip().split(",", 9)
            if len(fields) == 10 and fields[3] == "Default":
                changed = fn(fields, ass_seconds(fields[1]), ass_seconds(fields[2]))
                if changed is None:
                    continue
                line = f"{head}: " + ",".join(changed)
        out.append(line)
    return "\n".join(out)


def shift_subtitles(ass_text: str, dx_for) -> str:
    """Move subtitle lines sideways into a picture region.

    ``dx_for(start, end)`` returns the horizontal offset for a line (0/None = leave it).
    Positioned presets (word pop, karaoke, drawn boxes) carry ``\\pos`` — shifted
    directly; plain lines get per-event margins around the same centre. An event
    margin of 0 means "use the style's", so shifted margins never go below 1.
    """
    base = _default_margin(ass_text.split("\n"))

    def fn(fields, start, end):
        dx = dx_for(start, end)
        if not dx:
            return fields
        dx = int(round(dx))
        text = fields[9]
        text = _POS.sub(lambda m: f"\\pos({float(m.group(1)) + dx:.1f},", text)
        text = _MOVE.sub(lambda m: f"\\move({float(m.group(1)) + dx:.1f},{m.group(2)},{float(m.group(3)) + dx:.1f},",
                         text)
        fields[9] = text
        fields[5] = str(max(1, base + dx))
        fields[6] = str(max(1, base - dx))
        return fields

    return map_default_events(ass_text, fn)


def region_centre_offset(x0: float, x1: float) -> float:
    """How far a region's centre sits from the frame's centre."""
    return (x0 + x1) / 2 - W / 2


def drop_spans(ass_text: str, spans: list[tuple[float, float]]) -> str:
    """Hide subtitle lines under ``spans`` (a big quote replaces them); lines that
    run past a span start again when it ends."""

    def fn(fields, start, end):
        for a, b in spans:
            if a - 0.01 <= start < b:
                if end <= b + 0.01:
                    return None
                from src.utils.edit_styles.runtime import ts

                fields[1] = ts(b)
        return fields

    return map_default_events(ass_text, fn)


# --------------------------------------------------------------------------- #
# Per-clip media
# --------------------------------------------------------------------------- #
def extract_stills(clips: list[str], out_dir: str, size=(480, 270), workers: int = 6) -> list[str]:
    """First frame of every selected clip (one JPG per distinct clip, reused on repeats)."""
    os.makedirs(out_dir, exist_ok=True)
    unique = list(dict.fromkeys(clips))
    names = {clip: os.path.join(out_dir, f"s{i:04d}.jpg") for i, clip in enumerate(unique)}

    def one(clip):
        out = names[clip]
        if not os.path.isfile(out):
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", clip, "-frames:v", "1",
                            "-vf", f"scale={size[0]}:{size[1]}", "-q:v", "3", out],
                           capture_output=True, timeout=60)
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, unique))
    return [names[clip] for clip in clips]


def _ffconcat_entry(pic: str) -> str:
    """One ``file`` entry, absolute so the concat demuxer resolves it correctly.

    The demuxer resolves relative entries against the .ffconcat's OWN directory,
    not the process CWD -- so a project-relative path (STORAGE_DIR=./storage)
    gets appended to the temp dir and ffmpeg opens temp/./storage/... instead.
    """
    return os.path.abspath(pic).replace("\\", "/").replace("'", "'\\''")


def write_ffconcat(pictures: list[str], starts: list[float], total: float, ss: float | None, path: str) -> str:
    """Picture k shown from ``starts[k]`` to the next start, as an image sequence for ``-f concat``.

    Written per segment: the list begins at ``ss`` so its first frame lines up with
    the segment's t=0 (the overlay input is ``setpts=PTS-STARTPTS``).
    """
    ss = float(ss or 0.0)
    ends = list(starts[1:]) + [total]
    lines = ["ffconcat version 1.0"]
    last = None
    for pic, a, b in zip(pictures, starts, ends):
        a = max(a, ss)
        if b <= a:
            continue
        safe = _ffconcat_entry(pic)
        lines += [f"file '{safe}'", f"duration {b - a:.3f}"]
        last = safe
    if last is None and pictures:
        last = _ffconcat_entry(pictures[-1])
        lines += [f"file '{last}'", "duration 1.000"]
    if last is not None:
        lines.append(f"file '{last}'")  # concat quirk: the last duration needs a trailing entry
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def concat_input(path: str) -> list[str]:
    return ["-f", "concat", "-safe", "0", "-i", path]


def first_frame_windows(starts: list[float], fps: int = 30) -> list[tuple[float, float]]:
    """Tiny windows that contain exactly the first frame of every clip."""
    return [(s - 0.004, s + 0.8 / fps) for s in starts]
