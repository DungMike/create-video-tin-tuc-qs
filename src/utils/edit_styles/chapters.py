"""Chapter and quote timing for edit styles.

Source, in order:

1. ``<audio stem>.chapters.txt`` next to the audio/SRT (UTF-8, ``#`` = comment)::

       00:00 | หมู่บ้านริมโขง | บทนำ | ความเงียบสงบ...
       01:01 | ป้าบัว หญิงทอเสื่อ | เบื้องหลัง
       > 00:59 คดีของหญิงทอเสื่อ
       > 08:17

   A chapter line is ``time | title | label (optional) | key line (optional)``.
   A ``>`` line marks a quote; without text, the subtitle at that time is used.
   ``\\N`` inside a title is a line break (the ASS header uses WrapStyle 2).

2. Otherwise chapters start at the SRT paragraph (raw cue) closest to every
   ``every_seconds``, titled by the layout's ``autoTitle`` template.
"""

from __future__ import annotations

import re

_TIME = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?\s*$")


def parse_time(value: str) -> float | None:
    value = str(value or "").strip()
    m = _TIME.match(value)
    if m:
        h, mi, s, frac = m.groups()
        seconds = int(h or 0) * 3600 + int(mi) * 60 + int(s)
        return seconds + (int(frac.ljust(3, "0")) / 1000 if frac else 0.0)
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def parse_chapter_file(path: str) -> dict:
    """{"chapters": [{start, title, label, quote}], "quotes": [{time, text}]}; bad lines are skipped."""
    chapters, quotes = [], []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return {"chapters": [], "quotes": []}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            body = line[1:].strip()
            stamp, _, rest = body.partition(" ")
            t = parse_time(stamp)
            if t is not None:
                quotes.append({"time": t, "text": rest.strip()})
            continue
        parts = [part.strip() for part in line.split("|")]
        t = parse_time(parts[0])
        if t is None:
            continue
        chapters.append({
            "start": t,
            "title": parts[1] if len(parts) > 1 else "",
            "label": parts[2] if len(parts) > 2 else "",
            "quote": parts[3] if len(parts) > 3 else "",
        })
    chapters.sort(key=lambda c: c["start"])
    quotes.sort(key=lambda q: q["time"])
    return {"chapters": chapters, "quotes": quotes}


def auto_chapters(paragraph_starts: list[float], total: float, every_seconds: float) -> list[dict]:
    """One chapter at the paragraph start nearest each multiple of ``every_seconds``."""
    every = max(30.0, float(every_seconds))
    starts = sorted({round(float(s), 3) for s in paragraph_starts if 0 <= s < total}) or [0.0]
    chosen = [0.0]
    target = every
    while target < total - every / 3:
        candidate = min(starts, key=lambda s: abs(s - target))
        if candidate - chosen[-1] >= every / 2:
            chosen.append(candidate)
        target += every
    if len(starts) == 1:  # no SRT structure: plain even split
        chosen = [round(i * every, 3) for i in range(int(total // every) + 1) if i * every < total - every / 3]
    return [{"start": s, "title": "", "label": "", "quote": ""} for s in chosen]


def snap(time: float, cue_starts: list[float]) -> float:
    """Pull a hand-typed time onto the nearest subtitle start (cuts land between lines)."""
    if not cue_starts:
        return time
    return min(cue_starts, key=lambda s: abs(s - time))


def format_title(template: str, n: int, total: int, title: str, label: str = "") -> str:
    text = str(template or "{title}")
    text = text.replace("{n}", str(n)).replace("{total}", str(total))
    text = text.replace("{title}", title or "").replace("{label}", label or "")
    return " ".join(text.split()).strip(" ·|-") or title or str(n)


def resolve(total: float, chapters_path: str = "", raw_cues: list[dict] | None = None,
            sub_cues: list[dict] | None = None, every_seconds: float = 120.0,
            auto_title: str = "") -> dict:
    """Chapters (windows + display text), paragraph starts and quotes for one render.

    Without a chapter file the chapters still exist — layouts use their times to
    switch shape, dip or open a close-up — but they carry no text, so nothing
    prints an invented "Phần 2" over the video. Filling ``autoTitle`` brings a
    generated title back.
    """
    raw_cues = raw_cues or []
    sub_cues = sub_cues or []
    paragraph_starts = [float(c["start"]) for c in raw_cues if float(c["start"]) < total]
    cue_starts = [float(c["start"]) for c in sub_cues]
    parsed = parse_chapter_file(chapters_path) if chapters_path else {"chapters": [], "quotes": []}
    chapters = [dict(c, start=snap(c["start"], cue_starts)) for c in parsed["chapters"] if c["start"] < total]
    from_file = bool(chapters)
    if not chapters:
        chapters = auto_chapters(paragraph_starts, total, every_seconds)
    if chapters and chapters[0]["start"] > 0.5:
        chapters.insert(0, {"start": 0.0, "title": "", "label": "", "quote": ""})
    count = len(chapters)
    for i, c in enumerate(chapters):
        c["end"] = chapters[i + 1]["start"] if i + 1 < count else total
        c["n"] = i + 1
        if not c["title"] and auto_title:
            c["title"] = format_title(auto_title, i + 1, count, "")
    quotes = []
    for q in parsed["quotes"]:
        t = snap(q["time"], cue_starts)
        text = q["text"] or next((c["text"] for c in sub_cues if c["start"] <= t < c["end"]), "")
        if text and t < total:
            quotes.append({"time": t, "text": text})
    return {"chapters": chapters, "paragraphs": paragraph_starts, "quotes": quotes, "fromFile": from_file}
