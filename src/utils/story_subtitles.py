"""Subtitle core helpers for the story-video pipeline (SRT parse, resegment, ASS build, font scan)."""

import functools
import hashlib
import json
import os
import re
import shutil
import struct
import unicodedata

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger


class SubtitleParseError(Exception):
    pass


# Default ASS style spec. Each preset in SUBTITLE_PRESETS carries a partial "style"
# dict that overrides these fields; build_ass / _render_dialogue_text read the merged
# result via get_preset_style() instead of branching on preset_id.
#   Colours are ASS &HAABBGGRR (alpha 00 = opaque -> FF = transparent).
#   border_style: 1 = outline + drop shadow, 3 = opaque box (box colour = outline_colour).
#   anim: "standard" -> use the static `inline` tag; "fade_dynamic" -> duration-aware
#         \fade; "karaoke" -> per-syllable \kf via _render_karaoke_text;
#         "word_pop" -> each word revealed + bounced at its allocated time;
#         "color_cycle" -> \1c animated through `cycle_colours` over the cue
#         (cycle_mode "pulse" beats base->accent->base instead of a sweep);
#         "karaoke_zoom" -> \kf fill plus per-word scale bounce.
#   block_tags: extra override tags re-declared after each per-word \r reset
#         (e.g. "\\blur5" for neon glows), used by word_pop / karaoke_zoom.
_DEFAULT_STYLE = {
    "primary": "&H00FFFFFF",
    "secondary": "&H00FFFFFF",
    "outline_colour": "&H00000000",
    "back_colour": "&H00000000",
    "bold": 0,
    "italic": 0,
    "border_style": 1,
    "outline": 3,
    "shadow": 1,
    "inline": "{\\fad(250,250)}",
    "anim": "standard",
}

SUBTITLE_PRESETS = [
    # --- Original four (output-preserving; guarded by tests) ---
    {"id": "clean", "name": "Clean", "description": "Chữ trắng viền đen, fade nhẹ hai đầu.",
     "style": {}},
    {"id": "fade_soft", "name": "Fade mềm", "description": "Mờ dần vào/ra theo độ dài từng câu.",
     "style": {"anim": "fade_dynamic"}},
    {"id": "karaoke_pop", "name": "Karaoke Pop", "description": "Tô màu vàng từng chữ theo nhịp thoại.",
     "style": {"anim": "karaoke", "primary": "&H0000FFFF", "secondary": "&H00FFFFFF"}},
    {"id": "emphasis_bold", "name": "Đậm nổi bật", "description": "Chữ đậm, viền dày, bóng đổ rõ.",
     "style": {"bold": 1,
               "inline": "{\\fad(200,200)\\bord4\\shad2\\3c&H000000&\\4c&H202020&}"}},

    # --- Màu chữ (text colour) ---
    {"id": "yellow_pop", "name": "Vàng nổi", "description": "Chữ vàng, viền đen, nổi bật trên nền tối.",
     "style": {"primary": "&H0000FFFF"}},
    {"id": "cyan_cool", "name": "Xanh cyan", "description": "Chữ xanh cyan mát, viền đen.",
     "style": {"primary": "&H00FFFF00"}},
    {"id": "pink_hot", "name": "Hồng nổi", "description": "Chữ hồng rực, viền đen.",
     "style": {"primary": "&H00B469FF"}},
    {"id": "green_mint", "name": "Xanh lá", "description": "Chữ xanh lá tươi, viền đen.",
     "style": {"primary": "&H0000FF00"}},
    {"id": "orange_warm", "name": "Cam ấm", "description": "Chữ cam ấm, viền đen.",
     "style": {"primary": "&H000080FF"}},

    # --- Viền & glow (outline / neon) ---
    {"id": "outline_bold", "name": "Viền dày", "description": "Chữ trắng viền đen dày, đọc rõ mọi nền.",
     "style": {"outline": 6}},
    {"id": "outline_gold", "name": "Viền vàng", "description": "Chữ trắng, viền vàng kim dày.",
     "style": {"outline_colour": "&H0000D7FF", "outline": 4}},
    {"id": "neon_cyan", "name": "Neon cyan", "description": "Chữ trắng phát quầng sáng xanh cyan.",
     "style": {"outline_colour": "&H00FFFF00", "outline": 3, "shadow": 0,
               "inline": "{\\fad(200,200)\\blur6}"}},
    {"id": "neon_pink", "name": "Neon hồng", "description": "Chữ trắng phát quầng sáng hồng.",
     "style": {"outline_colour": "&H00B469FF", "outline": 3, "shadow": 0,
               "inline": "{\\fad(200,200)\\blur6}"}},

    # --- Nền / khung chữ (background box) ---
    {"id": "box_dark", "name": "Hộp tối mờ", "description": "Chữ trắng trên hộp đen trong suốt nhẹ.",
     "style": {"border_style": 3, "outline_colour": "&H80000000", "outline": 8, "shadow": 0}},
    {"id": "box_solid", "name": "Hộp đen đặc", "description": "Chữ trắng trên hộp đen đặc.",
     "style": {"border_style": 3, "outline_colour": "&H00000000", "outline": 6, "shadow": 0}},
    {"id": "tiktok_yellow", "name": "TikTok vàng", "description": "Chữ vàng trên hộp đen kiểu TikTok.",
     "style": {"primary": "&H0000FFFF", "border_style": 3, "outline_colour": "&H80000000",
               "outline": 8, "shadow": 0}},
    {"id": "banner_red", "name": "Banner đỏ", "description": "Chữ trắng trên dải banner đỏ đặc.",
     "style": {"border_style": 3, "outline_colour": "&H000000C0", "outline": 8, "shadow": 0}},

    # --- Chuyển động (motion) ---
    {"id": "pop_in", "name": "Bật vào", "description": "Chữ trắng bật to nhẹ khi xuất hiện.",
     "style": {"inline": "{\\fad(120,120)\\fscx70\\fscy70\\t(0,220,\\fscx100\\fscy100)}"}},
    {"id": "karaoke_box", "name": "Karaoke nền", "description": "Karaoke vàng trên hộp đen mờ.",
     "style": {"anim": "karaoke", "primary": "&H0000FFFF", "secondary": "&H00FFFFFF",
               "border_style": 3, "outline_colour": "&H80000000", "outline": 8, "shadow": 0}},

    # --- Chữ nhảy theo từng từ (word pop) ---
    {"id": "word_bounce", "name": "Chữ nhảy", "description": "Từng từ bật to đúng lúc được đọc.",
     "style": {"anim": "word_pop"}},
    {"id": "word_bounce_box", "name": "Chữ nhảy nền", "description": "Chữ nhảy từng từ trên hộp đen mờ.",
     "style": {"anim": "word_pop", "border_style": 3, "outline_colour": "&H80000000",
               "outline": 8, "shadow": 0}},

    # --- Đổi màu động (color cycle) ---
    {"id": "rainbow_cycle", "name": "Đổi màu cầu vồng", "description": "Màu chữ chuyển dần qua dải màu trong lúc hiển thị.",
     "style": {"anim": "color_cycle",
               "cycle_colours": ["&H5D5DFF&", "&H00D7FF&", "&H79E3A5&", "&HFFB37D&", "&HE37DD4&"]}},
    {"id": "color_pulse", "name": "Nhịp màu vàng", "description": "Chữ trắng nhấn nhịp sang vàng rồi về trắng.",
     "style": {"anim": "color_cycle", "cycle_mode": "pulse",
               "cycle_colours": ["&HFFFFFF&", "&H00FFFF&"]}},

    # --- Karaoke nâng cao ---
    {"id": "karaoke_zoom", "name": "Karaoke phóng to", "description": "Từ đang đọc phóng to và tô màu vàng.",
     "style": {"anim": "karaoke_zoom", "primary": "&H0000FFFF", "secondary": "&H00FFFFFF"}},
    {"id": "karaoke_neon", "name": "Karaoke neon", "description": "Karaoke tô vàng với quầng sáng neon cyan.",
     "style": {"anim": "karaoke_zoom", "primary": "&H0000FFFF", "secondary": "&H00FFFFFF",
               "outline_colour": "&H00FFFF00", "shadow": 0, "block_tags": "\\blur5"}},
]

_SYSTEM_FONTS_DIR = "C:/Windows/Fonts"
_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")
_FONTS_INDEX_FILENAME = "fonts_index.json"
# Tăng khi đổi schema record font để cache cũ (vd. còn cờ supportsKorean) tự hết hạn.
_FONTS_INDEX_VERSION = 2
# Font chứa Hangul được coi là font Hàn và bị loại khỏi danh sách chọn.
_HANGUL_PROBE_CODEPOINT = 0xAC00
_VIETNAMESE_PROBE_CODEPOINT = 0x1EBF
_THAI_PROBE_CODEPOINT = 0x0E01  # THAI CHARACTER KO KAI
# Tiếng Indonesia chỉ dùng 26 chữ Latin (+ "é" trong vài từ vay mượn), nên probe
# A/z bảo đảm có Latin cơ bản còn é loại các font symbol (Wingdings, Webdings...).
_INDONESIAN_PROBE_CODEPOINTS = (0x0041, 0x007A, 0x00E9)

# (filename_prefix, family, has_hangul, supports_vietnamese, supports_thai, supports_indonesian)
_FALLBACK_FONT_WHITELIST = (
    ("arial", "Arial", False, True, False, True),
    ("segoeui", "Segoe UI", False, True, False, True),
    ("tahoma", "Tahoma", False, True, True, True),
    ("leelawadeeui", "Leelawadee UI", False, True, True, True),
    ("leelawadee", "Leelawadee", False, True, True, True),
    ("verdana", "Verdana", False, True, False, True),
    ("calibri", "Calibri", False, True, False, True),
    ("times", "Times New Roman", False, True, False, True),
    ("georgia", "Georgia", False, True, False, True),
    ("cambria", "Cambria", False, True, False, True),
    ("sarabun", "Sarabun", False, True, True, True),
    ("notosansthai", "Noto Sans Thai", False, False, True, True),
)

_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
_CJK_RE = re.compile(
    r"[ᄀ-ᇿ⺀-鿿　-〿가-힯豈-﫿＀-￯]"
)
_SENTENCE_ENDERS = ".!?…。！？"
_SENTENCE_TRAILERS = "\"'”’」』)]"


def get_subtitle_preset(preset_id: str) -> dict | None:
    return next((item for item in SUBTITLE_PRESETS if item["id"] == preset_id), None)


def get_preset_style(preset_id: str) -> dict:
    """Merged ASS style spec for a preset. Unknown ids fall back to `clean`."""
    preset = get_subtitle_preset(preset_id) or get_subtitle_preset("clean")
    merged = dict(_DEFAULT_STYLE)
    merged.update(preset.get("style") or {})
    return merged


def _decode_srt_bytes(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except (UnicodeDecodeError, UnicodeError):
            pass
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_srt(path: str) -> list[dict]:
    try:
        with open(path, "rb") as file_obj:
            raw = file_obj.read()
    except OSError as exc:
        raise SubtitleParseError(f"Cannot read subtitle file: {path}") from exc

    text = _decode_srt_bytes(raw).replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise SubtitleParseError(f"Subtitle file is empty: {path}")

    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        time_index = None
        match = None
        for idx, line in enumerate(lines[:2]):
            match = _TIMESTAMP_RE.search(line)
            if match:
                time_index = idx
                break
        if time_index is None or not match:
            continue
        groups = match.groups()
        start = (
            int(groups[0]) * 3600 + int(groups[1]) * 60 + int(groups[2])
            + int(groups[3].ljust(3, "0")) / 1000.0
        )
        end = (
            int(groups[4]) * 3600 + int(groups[5]) * 60 + int(groups[6])
            + int(groups[7].ljust(3, "0")) / 1000.0
        )
        cue_text = " ".join(lines[time_index + 1:]).strip()
        if not cue_text or end <= start:
            continue
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": cue_text})

    if not cues:
        raise SubtitleParseError(f"No valid cues found in subtitle file: {path}")
    cues.sort(key=lambda cue: (cue["start"], cue["end"]))
    return cues


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    sentences: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        if ch in _SENTENCE_ENDERS:
            j = i + 1
            while j < n and text[j] in _SENTENCE_ENDERS:
                buf.append(text[j])
                j += 1
            while j < n and text[j] in _SENTENCE_TRAILERS:
                buf.append(text[j])
                j += 1
            if j >= n or text[j].isspace():
                sentence = "".join(buf).strip()
                if sentence:
                    sentences.append(sentence)
                buf = []
                while j < n and text[j].isspace():
                    j += 1
            i = j
        else:
            i += 1
    rest = "".join(buf).strip()
    if rest:
        sentences.append(rest)
    return sentences


def _pack_units(text: str, cap: int) -> list[str]:
    cap = max(1, cap)
    words = [word for word in text.split(" ") if word]
    units: list[str] = []
    for word in words:
        if len(word) > cap and _has_cjk(word):
            units.extend(word[i:i + cap] for i in range(0, len(word), cap))
        else:
            units.append(word)
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else f"{current} {unit}"
        if current and len(candidate) > cap:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _merge_short_blocks(blocks: list[dict], line_cap: int, lines_cap: int, min_duration: float) -> list[dict]:
    blocks = [dict(block) for block in blocks]
    changed = True
    while changed and len(blocks) > 1:
        changed = False
        for i, block in enumerate(blocks):
            if block["end"] - block["start"] >= min_duration:
                continue
            for j in (i + 1, i - 1):
                if j < 0 or j >= len(blocks):
                    continue
                left, right = (block, blocks[j]) if j > i else (blocks[j], block)
                merged_plain = f"{left['plain']} {right['plain']}".strip()
                if len(_pack_units(merged_plain, line_cap)) > lines_cap:
                    continue
                merged = {
                    "plain": merged_plain,
                    "start": min(left["start"], right["start"]),
                    "end": max(left["end"], right["end"]),
                }
                lo, hi = min(i, j), max(i, j)
                blocks[lo:hi + 1] = [merged]
                changed = True
                break
            if changed:
                break
    return blocks


def _enforce_min_duration(blocks: list[dict], start: float, end: float, min_duration: float) -> list[dict]:
    if not blocks:
        return blocks
    total = max(0.0, end - start)
    count = len(blocks)
    durations = [max(0.0, block["end"] - block["start"]) for block in blocks]
    if total >= min_duration * count:
        deficit = sum(max(0.0, min_duration - value) for value in durations)
        if deficit > 1e-9:
            surplus = sum(max(0.0, value - min_duration) for value in durations)
            if surplus > 1e-9:
                factor = min(1.0, deficit / surplus)
                durations = [
                    min_duration if value < min_duration
                    else value - (value - min_duration) * factor
                    for value in durations
                ]
    else:
        durations = [total / count] * count
    current_total = sum(durations)
    if current_total > 0:
        durations = [value * total / current_total for value in durations]
    cursor = start
    for block, duration in zip(blocks, durations):
        block["start"] = cursor
        block["end"] = cursor + duration
        cursor = block["end"]
    blocks[-1]["end"] = end
    return blocks


def resegment(
    cues,
    max_chars_per_line: int | None = None,
    max_lines: int | None = None,
    min_cue_duration: float = 0.8,
) -> list[dict]:
    line_cap = max(4, int(max_chars_per_line or Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE))
    lines_cap = max(1, int(max_lines or Config.STORY_SUBTITLE_MAX_LINES))

    result: list[dict] = []
    for cue_index, cue in enumerate(cues):
        text = re.sub(r"\s+", " ", str(cue.get("text", ""))).strip()
        if not text:
            continue
        start = float(cue["start"])
        end = float(cue["end"])
        span = max(0.1, end - start)
        sentences = _split_sentences(text)
        if not sentences:
            continue

        weights = [max(1, len(sentence)) for sentence in sentences]
        total_weight = sum(weights)
        blocks: list[dict] = []
        cursor = start
        for sentence, weight in zip(sentences, weights):
            sentence_span = span * weight / total_weight
            sentence_start = cursor
            cursor += sentence_span
            lines = _pack_units(sentence, line_cap)
            groups = [lines[i:i + lines_cap] for i in range(0, len(lines), lines_cap)]
            group_weights = [max(1, sum(len(line) for line in group)) for group in groups]
            group_total = sum(group_weights)
            group_cursor = sentence_start
            for group, group_weight in zip(groups, group_weights):
                group_span = sentence_span * group_weight / group_total
                blocks.append({
                    "plain": " ".join(group),
                    "start": group_cursor,
                    "end": group_cursor + group_span,
                })
                group_cursor += group_span
            blocks[-1]["end"] = cursor
        blocks[-1]["end"] = end

        blocks = _merge_short_blocks(blocks, line_cap, lines_cap, min_cue_duration)
        blocks = _enforce_min_duration(blocks, start, end, min_cue_duration)
        for block in blocks:
            result.append({
                "start": block["start"],
                "end": block["end"],
                "text": "\n".join(_pack_units(block["plain"], line_cap)),
                "_cue": cue_index,
            })

    result.sort(key=lambda item: (item["start"], item["end"]))
    # Only clamp overlaps between blocks tiled from the SAME source cue (rounding slips);
    # overlapping source cues (e.g. dual-speaker captions) are legitimate in ASS.
    prev_end_by_cue: dict[int, float] = {}
    for item in result:
        item["start"] = round(item["start"], 3)
        item["end"] = round(item["end"], 3)
        prev_end = prev_end_by_cue.get(item["_cue"])
        if prev_end is not None and item["start"] < prev_end:
            item["start"] = prev_end
        if item["end"] <= item["start"]:
            item["end"] = round(item["start"] + 0.05, 3)
        prev_end_by_cue[item["_cue"]] = item["end"]
    for item in result:
        del item["_cue"]
    return result


def _format_ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


# Thai writes its vowels and tone marks as combining characters sitting on a base
# consonant, and writes four vowels *before* the consonant they belong to. Slicing
# such a line every two codepoints tears those apart — "รั้ง" came out as "รั" +
# "้ง", the second piece starting with a bare tone mark.
_THAI_LEADING_VOWELS = "เแโใไ"
_COMBINING_CATEGORIES = ("Mn", "Mc", "Me")


def _grapheme_clusters(text: str) -> list[str]:
    """Split into clusters that must never be rendered apart from each other.

    A combining mark joins the base before it; a Thai leading vowel joins the base
    after it. Scripts without marks (Latin, CJK) come back one character per
    cluster, exactly as before.
    """
    clusters: list[str] = []
    pending = ""  # a leading vowel waiting for the consonant it is written before
    for char in text:
        if unicodedata.category(char) in _COMBINING_CATEGORIES and (pending or clusters):
            if pending:
                pending += char
            else:
                clusters[-1] += char
        elif pending:
            clusters.append(pending + char)
            pending = ""
        elif char in _THAI_LEADING_VOWELS:
            pending = char
        else:
            clusters.append(char)
    if pending:
        clusters.append(pending)
    return clusters


def _karaoke_units(line: str) -> list[str]:
    line = line.strip()
    if not line:
        return []
    if " " in line:
        return [word for word in line.split(" ") if word]
    # No spaces: Thai, and CJK. Step two clusters at a time so the animation reads
    # at a sane rate, but never cut inside a cluster.
    clusters = _grapheme_clusters(line)
    units = ["".join(clusters[i:i + 2]) for i in range(0, len(clusters), 2)]
    if len(units) >= 2 and len(_grapheme_clusters(units[-1])) == 1:
        last = units.pop()
        units[-1] += last
    return units


def _allocate_centiseconds(total_cs: int, weights: list[int]) -> list[int]:
    total_weight = sum(weights)
    if total_weight <= 0:
        return [0] * len(weights)
    allocated = 0
    running = 0.0
    values: list[int] = []
    for weight in weights:
        running += weight * total_cs / total_weight
        value = int(round(running)) - allocated
        values.append(max(0, value))
        allocated += value
    return values


def _render_karaoke_text(lines: list[str], duration: float) -> str:
    line_units = [(line, _karaoke_units(line)) for line in lines]
    weights = [len(unit) for _, units in line_units for unit in units]
    if not weights:
        return "{\\fad(150,150)}" + "\\N".join(_escape_ass_text(line) for line in lines)
    total_cs = max(1, int(round(duration * 100)))
    allocation = iter(_allocate_centiseconds(total_cs, weights))
    rendered_lines: list[str] = []
    for line, units in line_units:
        spaced = " " in line.strip()
        parts: list[str] = []
        for idx, unit in enumerate(units):
            cs = next(allocation)
            segment = f"{{\\kf{cs}}}{_escape_ass_text(unit)}"
            if spaced and idx < len(units) - 1:
                segment += " "
            parts.append(segment)
        rendered_lines.append("".join(parts))
    return "{\\fad(150,150)}" + "\\N".join(rendered_lines)


def _word_time_blocks(lines: list[str], duration: float):
    """Per-word (unit, start_ms, duration_ms) allocation shared by word animations.

    Returns (line_units, iterator of (unit, t0_ms, dur_ms, cs)) or None when the
    text has no units (caller falls back to a static line).
    """
    line_units = [(line, _karaoke_units(line)) for line in lines]
    weights = [len(unit) for _, units in line_units for unit in units]
    if not weights:
        return None
    total_cs = max(1, int(round(duration * 100)))
    allocation = _allocate_centiseconds(total_cs, weights)
    return line_units, iter(allocation)


# Word-animation timing. Every window now spans several frames at 30 fps
# (33ms/frame): the old 10ms alpha window was shorter than a single frame, and the
# 140ms scale ramp only got four steps — both read as a hard flicker, not a pop.
_POP_FADE_IN_MS = 110
_POP_FADE_OUT_MS = 150
_POP_START_SCALE = 80
_POP_PEAK_SCALE = 112
_POP_RISE_MS = 150
_POP_SETTLE_MS = 360
# The 3rd argument of \t is the easing exponent: <1 eases out, >1 eases in. An
# ease-out rise paired with an ease-in settle brings the velocity to zero on both
# sides of the peak, so the turnaround has no visible corner. The old transforms
# passed no exponent at all — linear in, linear out, hard corner between them.
_POP_RISE_ACCEL = "0.5"
_POP_SETTLE_ACCEL = "1.3"


def _render_word_pop_text(lines: list[str], duration: float, style: dict) -> str:
    """Fallback word reveal, used when the font cannot be measured.

    Deliberately free of \\fscx/\\fscy. Scaling a word *inside* a line makes libass
    re-measure that line and re-centre it every frame, so every other word slides
    sideways while one word bounces — that reflow was the jitter. Here a word only
    fades in, with a border pulse for accent (an outline is drawn outside the glyph,
    so it never changes the advance width). `_word_pop_events` keeps the real bounce
    on the measured path, where every word owns an absolute position.
    """
    allocated = _word_time_blocks(lines, duration)
    if allocated is None:
        return "{\\fad(150,150)}" + "\\N".join(_escape_ass_text(line) for line in lines)
    line_units, allocation = allocated
    block_tags = str(style.get("block_tags") or "")
    # An opaque box (border_style 3) takes its size from the border width, so a
    # \bord pulse there would make the box breathe instead of the text pop.
    base_bord = int(style.get("outline") or 0)
    pulse = style.get("border_style") != 3 and base_bord > 0
    elapsed_cs = 0
    rendered_lines: list[str] = []
    for line, units in line_units:
        spaced = " " in line.strip()
        parts: list[str] = []
        for idx, unit in enumerate(units):
            cs = next(allocation)
            t0 = elapsed_cs * 10
            elapsed_cs += cs
            tags = (
                f"\\r{block_tags}\\alpha&HFF&"
                f"\\t({t0},{t0 + _POP_FADE_IN_MS},\\alpha&H00&)"
            )
            if pulse:
                tags += (
                    f"\\bord{base_bord}"
                    f"\\t({t0},{t0 + _POP_RISE_MS},{_POP_RISE_ACCEL},\\bord{base_bord + 2})"
                    f"\\t({t0 + _POP_RISE_MS},{t0 + _POP_SETTLE_MS},{_POP_SETTLE_ACCEL},\\bord{base_bord})"
                )
            segment = "{" + tags + "}" + _escape_ass_text(unit)
            if spaced and idx < len(units) - 1:
                segment += " "
            parts.append(segment)
        rendered_lines.append("".join(parts))
    return "{\\fad(150,150)}" + "\\N".join(rendered_lines)


def _render_karaoke_zoom_text(lines: list[str], duration: float, style: dict) -> str:
    """Fallback karaoke accent, used when the font cannot be measured.

    The zoom is dropped here for the same reason as in `_render_word_pop_text`:
    inline \\fscx reflows the line. The old shrink window was worse still — it
    collapsed to zero length whenever a word was shorter than twice the rise (very
    common for short Vietnamese words), so the word snapped 122%→100% inside a
    single frame. What remains is the plain \\kf fill, smooth by construction;
    `_karaoke_zoom_events` keeps the zoom on the measured path.
    """
    allocated = _word_time_blocks(lines, duration)
    if allocated is None:
        return "{\\fad(150,150)}" + "\\N".join(_escape_ass_text(line) for line in lines)
    line_units, allocation = allocated
    block_tags = str(style.get("block_tags") or "")
    rendered_lines: list[str] = []
    for line, units in line_units:
        spaced = " " in line.strip()
        parts: list[str] = []
        for idx, unit in enumerate(units):
            cs = next(allocation)
            segment = "{" + f"\\r{block_tags}\\kf{cs}" + "}" + _escape_ass_text(unit)
            if spaced and idx < len(units) - 1:
                segment += " "
            parts.append(segment)
        rendered_lines.append("".join(parts))
    return "{\\fad(150,150)}" + "\\N".join(rendered_lines)


def _render_color_cycle_text(lines: list[str], duration: float, style: dict) -> str:
    """Animate the fill colour over the cue: sweep through cycle_colours, or
    beat base->accent->base when cycle_mode is "pulse". Colours use the inline
    \\1c form (&HBBGGRR&)."""
    colours = [str(c) for c in (style.get("cycle_colours") or []) if str(c).strip()]
    if len(colours) < 2:
        colours = ["&HFFFFFF&", "&H00FFFF&"]
    escaped = "\\N".join(_escape_ass_text(line) for line in lines)
    duration_ms = max(1, int(round(duration * 1000)))
    parts = [f"\\1c{colours[0]}"]
    if str(style.get("cycle_mode") or "") == "pulse":
        base, accent = colours[0], colours[1]
        beats = max(1, duration_ms // 1200)
        seg = duration_ms / (beats * 2)
        t = 0.0
        for _ in range(beats):
            parts.append(f"\\t({int(t)},{int(t + seg)},\\1c{accent})")
            parts.append(f"\\t({int(t + seg)},{int(t + 2 * seg)},\\1c{base})")
            t += 2 * seg
    else:
        seg = duration_ms / (len(colours) - 1)
        t = 0.0
        for colour in colours[1:]:
            parts.append(f"\\t({int(t)},{int(t + seg)},\\1c{colour})")
            t += seg
    return "{" + "".join(parts) + "\\fad(150,150)}" + escaped


def _render_dialogue_text(text: str, style: dict, duration: float) -> str:
    """Single-event rendering: one Dialogue line carrying the whole cue.

    Takes the already-merged style (preset + user overrides) rather than a preset
    id, so a colour the user picked reaches the inline tags too.
    """
    lines = text.split("\n")
    anim = style["anim"]
    if anim == "karaoke":
        return _render_karaoke_text(lines, duration)
    if anim == "word_pop":
        return _render_word_pop_text(lines, duration, style)
    if anim == "karaoke_zoom":
        return _render_karaoke_zoom_text(lines, duration, style)
    if anim == "color_cycle":
        return _render_color_cycle_text(lines, duration, style)
    escaped = "\\N".join(_escape_ass_text(line) for line in lines)
    if anim == "fade_dynamic":
        duration_ms = int(round(duration * 1000))
        if duration_ms >= 900:
            tag = f"{{\\fade(255,0,255,0,400,{duration_ms - 400},{duration_ms})}}"
        else:
            tag = "{\\fad(200,200)}"
    else:
        tag = style["inline"]
    return tag + escaped


def _coerce_style_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_style_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return max(minimum, min(maximum, parsed))


def _sanitize_font_family(font_family) -> str:
    # Commas shift Style fields, newlines inject arbitrary ASS lines.
    cleaned = re.sub(r"[,\r\n]+", " ", str(font_family))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or Config.STORY_SUBTITLE_DEFAULT_FONT


_HEX_COLOUR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_ASS_COLOUR_RE = re.compile(r"^&H([0-9a-fA-F]{2})([0-9a-fA-F]{6})&?$")


def _hex_to_ass_colour(value, alpha: int = 0) -> str | None:
    """`#RRGGBB` -> ASS `&HAABBGGRR` (bytes reversed; alpha 00 = opaque).

    Returns None for anything that is not exactly six hex digits. That check is
    load-bearing rather than cosmetic: the result is interpolated straight into the
    comma-separated `Style:` line, so an unvalidated string could shift the style
    fields along or inject whole ASS lines — the same reason `_sanitize_font_family`
    exists.
    """
    if not isinstance(value, str):
        return None
    match = _HEX_COLOUR_RE.match(value.strip())
    if not match:
        return None
    digits = match.group(1).upper()
    return f"&H{max(0, min(255, int(alpha))):02X}{digits[4:6]}{digits[2:4]}{digits[0:2]}"


def _split_ass_colour(colour) -> tuple[str, str]:
    """`&HAABBGGRR` -> (inline `\\1c` value, inline `\\1a` value)."""
    match = _ASS_COLOUR_RE.match(str(colour).strip())
    if not match:
        return "&H000000&", "&H00&"
    return f"&H{match.group(2).upper()}&", f"&H{match.group(1).upper()}&"


def _opacity_to_alpha_tag(opacity) -> str | None:
    """0..1 opacity -> inline ASS alpha (`&H00&` opaque ... `&HFF&` invisible)."""
    try:
        parsed = float(opacity)
    except (TypeError, ValueError):
        return None
    parsed = max(0.0, min(1.0, parsed))
    return f"&H{int(round((1.0 - parsed) * 255)):02X}&"


# ---------------------------------------------------------------------------
# Text measurement — the basis of jitter-free word animation
# ---------------------------------------------------------------------------
# libass lays a Dialogue line out from the glyph metrics current at that frame,
# and \t re-evaluates those every frame. So an animated \fscx changes one word's
# advance width, which changes the line width, which (Alignment 2 = centred)
# moves every other word on the line. That is the jitter. The fix is to stop
# letting libass lay the line out: measure the words here and give each one its
# own \pos, after which a word can scale without any other word noticing.
#
# Pillow and libass both measure through FreeType, so the widths agree closely —
# but exact agreement is not required. Every word on a line is placed from the
# same measurements, so a systematic error only shifts word spacing a little. It
# cannot produce movement, because nothing is re-measured at render time.

# Vertical advance between stacked lines, as a multiple of the font size.
_LINE_SPACING = 1.18
# Background box padding, as a multiple of the font size.
_BOX_PAD_X = 0.34
_BOX_PAD_Y = 0.16
# Text outline kept once the box became a drawn shape instead of BorderStyle 3.
_BOX_TEXT_OUTLINE = 2
# BorderStyle 3 padding used when nothing can be measured and the built-in opaque
# box has to stand in for the drawn one.
_BOX_FALLBACK_PADDING = 8

_FONT_FILE_CACHE: dict[str, str | None] = {}


@functools.lru_cache(maxsize=256)
def _face_style(font_path: str) -> str:
    """Subfamily of a font file ("regular", "bold", ...), or "" when unreadable."""
    try:
        from PIL import ImageFont

        return str(ImageFont.truetype(font_path, 16).font.style or "").strip().lower()
    except (ImportError, OSError, ValueError, AttributeError):
        return ""


def _resolve_font_file(family: str) -> str | None:
    """Path to a font file for `family`, or None when it cannot be resolved."""
    key = str(family or "").strip().lower()
    if not key:
        return None
    if key in _FONT_FILE_CACHE:
        return _FONT_FILE_CACHE[key]
    resolved: str | None = None
    try:
        for record in scan_fonts():
            if str(record.get("family", "")).strip().lower() != key:
                continue
            files = [str(path) for path in (record.get("files") or [])]
            # A .ttc holds several faces and index 0 need not be the one this
            # family names, so prefer a single-face file when one exists.
            files.sort(key=lambda path: path.lower().endswith(".ttc"))
            files = [path for path in files if os.path.isfile(path)]
            # A family often indexes several weights under one name (Leelawadee UI
            # lists both LeelawUI.ttf and LeelaUIb.ttf). libass draws the regular
            # face unless the style asks for bold, and the bold face is wider, so
            # measuring the wrong one spaces every word too far apart.
            resolved = next(
                (path for path in files if _face_style(path) == "regular"),
                files[0] if files else None,
            )
            break
    except OSError:
        resolved = None
    _FONT_FILE_CACHE[key] = resolved
    return resolved


# Probe size for the metric ratio below; large enough that Pillow's integer pixel
# metrics quantise to a negligible error.
_METRIC_PROBE_SIZE = 512


@functools.lru_cache(maxsize=64)
def _ass_size_scale(font_path: str) -> float:
    """Ratio between an ASS Fontsize and the em size Pillow wants for it.

    ASS sizes a font by its *height* — ascender to descender — while Pillow's `size`
    is the em square. The two differ per font: 12% for Arial, 33% for Leelawadee UI.
    Ignoring it stretched every line by that much. Latin hid the error inside its
    word spaces; Thai, which has no spaces for the slack to disappear into, showed it
    as characters drifting far apart.
    """
    from PIL import ImageFont

    probe = ImageFont.truetype(font_path, _METRIC_PROBE_SIZE)
    ascent, descent = probe.getmetrics()
    height = ascent + descent
    return _METRIC_PROBE_SIZE / height if height > 0 else 1.0


@functools.lru_cache(maxsize=64)
def _load_measure_font(font_path: str, size: int):
    """Pillow font whose advances match what libass renders at ASS Fontsize `size`."""
    try:
        from PIL import ImageFont
    except ImportError:
        logger.warning("[StorySubtitles] Pillow unavailable; falling back to inline subtitle layout.")
        return None
    try:
        return ImageFont.truetype(font_path, size * _ass_size_scale(font_path))
    except (OSError, ValueError) as exc:
        logger.warning(f"[StorySubtitles] Cannot measure font {font_path}: {exc}")
        return None


@functools.lru_cache(maxsize=4096)
def _measure_width(font_path: str, size: int, text: str) -> float:
    """Advance width of `text`, or -1.0 when it cannot be measured."""
    font = _load_measure_font(font_path, size)
    if font is None:
        return -1.0
    try:
        return float(font.getlength(text))
    except (OSError, ValueError):
        return -1.0


def _can_measure(font_family: str, font_size: int) -> str | None:
    """Font file path when this family/size can be measured, else None."""
    font_path = _resolve_font_file(font_family)
    if not font_path:
        return None
    return font_path if _measure_width(font_path, font_size, " ") >= 0 else None


def _layout_lines(
    line_units: list[tuple[str, list[str]]],
    font_path: str,
    font_size: int,
    play_res: tuple[int, int],
    margin_v: int,
    margin_lr: int,
    alignment: int,
) -> list[dict] | None:
    """Absolute placement for every word of one cue.

    Returns one dict per line — ``{"left", "y", "width", "height", "words":
    [(unit, centre_x), ...]}`` — or None when a word cannot be measured, in which
    case the caller falls back to inline (single-event) rendering.
    """
    play_x, play_y = play_res
    line_height = font_size * _LINE_SPACING
    laid: list[dict] = []
    for line, units in line_units:
        if not units:
            continue
        # Positions come from prefixes of the whole line rather than from measuring
        # each unit on its own. Measuring in isolation breaks scripts with combining
        # marks: a Thai cluster handed to the shaper alone gets a dotted-circle base,
        # whose width then pushed every following character further right.
        separator = " " if " " in line.strip() else ""
        text = separator.join(units)
        spans: list[tuple[int, int]] = []
        cursor = 0
        for unit in units:
            spans.append((cursor, cursor + len(unit)))
            cursor += len(unit) + len(separator)

        def prefix(index: int) -> float:
            return _measure_width(font_path, font_size, text[:index])

        total = prefix(len(text))
        if total < 0:
            return None
        if alignment in (1, 4, 7):
            left = float(margin_lr)
        elif alignment in (3, 6, 9):
            left = play_x - margin_lr - total
        else:
            left = (play_x - total) / 2.0
        words: list[tuple[str, float]] = []
        for unit, (begin, finish) in zip(units, spans):
            start_x, end_x = prefix(begin), prefix(finish)
            if start_x < 0 or end_x < 0:
                return None
            words.append((unit, left + (start_x + end_x) / 2.0))
        laid.append({"left": left, "width": total, "height": line_height, "words": words})
    if not laid:
        return None
    block_height = line_height * len(laid)
    if alignment in (7, 8, 9):
        top = float(margin_v)
    elif alignment in (4, 5, 6):
        top = (play_y - block_height) / 2.0
    else:
        top = play_y - margin_v - block_height
    for index, entry in enumerate(laid):
        entry["y"] = top + line_height * (index + 0.5)
    return laid


def _dialogue(start: float, end: float, text: str, layer: int = 0) -> str:
    return (
        f"Dialogue: {layer},{_format_ass_time(start)},{_format_ass_time(end)},"
        f"Default,,0,0,0,,{text}"
    )


def _background_events(layout, start, end, colour, alpha_tag, font_size) -> list[str]:
    """One opaque rectangle per line, drawn under the text on layer 0.

    A \\p1 shape rather than ASS's own opaque box (BorderStyle 3), for two reasons:
    the built-in box reuses the OutlineColour field, so box and text outline can
    never be different colours, and its size follows the glyphs — so it breathes
    whenever a word scales. A shape sized once from the measured line does neither.
    """
    pad_x = font_size * _BOX_PAD_X
    pad_y = font_size * _BOX_PAD_Y
    events: list[str] = []
    for line in layout:
        width = line["width"] + pad_x * 2
        height = line["height"] + pad_y * 2
        left = line["left"] - pad_x
        top = line["y"] - line["height"] / 2.0 - pad_y
        # \an7 puts the drawing origin exactly at \pos, so the shape needs no offset.
        tags = (
            f"\\an7\\pos({left:.1f},{top:.1f})\\bord0\\shad0"
            f"\\1c{colour}\\1a{alpha_tag}\\p1"
        )
        shape = f"m 0 0 l {width:.0f} 0 l {width:.0f} {height:.0f} l 0 {height:.0f}"
        events.append(_dialogue(start, end, "{" + tags + "}" + shape))
    return events


def _word_pop_events(layout, start, end, duration, style, layer) -> list[str]:
    """"Chữ nhảy": one Dialogue per word, each pinned to an absolute position.

    Every word carries its own \\pos, so scaling one cannot move any other — the
    reflow that made the line shudder is gone by construction. Each event also
    starts at its own word's time, which retires the old trick of laying the whole
    line out transparent and un-hiding words with a 10ms \\alpha transform.
    """
    weights = [len(unit) for line in layout for unit, _ in line["words"]]
    if not weights:
        return []
    allocation = iter(_allocate_centiseconds(max(1, int(round(duration * 100))), weights))
    block_tags = str(style.get("block_tags") or "")
    events: list[str] = []
    elapsed_cs = 0
    for line in layout:
        for unit, centre_x in line["words"]:
            cs = next(allocation)
            word_start = min(start + elapsed_cs / 100.0, end - 0.01)
            elapsed_cs += cs
            tags = (
                f"\\an5\\pos({centre_x:.1f},{line['y']:.1f}){block_tags}"
                f"\\fad({_POP_FADE_IN_MS},{_POP_FADE_OUT_MS})"
                f"\\fscx{_POP_START_SCALE}\\fscy{_POP_START_SCALE}"
                f"\\t(0,{_POP_RISE_MS},{_POP_RISE_ACCEL},"
                f"\\fscx{_POP_PEAK_SCALE}\\fscy{_POP_PEAK_SCALE})"
                f"\\t({_POP_RISE_MS},{_POP_SETTLE_MS},{_POP_SETTLE_ACCEL},\\fscx100\\fscy100)"
            )
            events.append(
                _dialogue(word_start, end, "{" + tags + "}" + _escape_ass_text(unit), layer)
            )
    return events


def _karaoke_zoom_events(layout, start, end, duration, style, layer) -> list[str]:
    """"Karaoke phóng to": \\kf fill plus a zoom on the word being read.

    Unlike the word pop, every event spans the whole cue — karaoke needs the words
    it has not reached yet to be on screen in the secondary colour. A word waits its
    turn behind a leading zero-width `\\k`, then fills with `\\kf`.
    """
    weights = [len(unit) for line in layout for unit, _ in line["words"]]
    if not weights:
        return []
    allocation = iter(_allocate_centiseconds(max(1, int(round(duration * 100))), weights))
    block_tags = str(style.get("block_tags") or "")
    events: list[str] = []
    elapsed_cs = 0
    for line in layout:
        for unit, centre_x in line["words"]:
            cs = next(allocation)
            lead_cs = elapsed_cs
            t0 = elapsed_cs * 10
            elapsed_cs += cs
            dur_ms = max(1, cs * 10)
            # Grow, hold while the word is actually being read, then ease back at
            # its end. Both windows are clamped to a few frames: the old code let
            # the shrink window reach zero length whenever a word was shorter than
            # twice the rise, which snapped the scale back inside a single frame.
            rise = max(80, min(_POP_RISE_MS, dur_ms // 3))
            fall = max(120, min(200, dur_ms // 3))
            fall_start = max(t0 + rise, t0 + dur_ms - fall)
            tags = (
                f"\\an5\\pos({centre_x:.1f},{line['y']:.1f}){block_tags}"
                f"\\fad({_POP_FADE_IN_MS},{_POP_FADE_OUT_MS})"
                f"\\t({t0},{t0 + rise},{_POP_RISE_ACCEL},"
                f"\\fscx{_POP_PEAK_SCALE}\\fscy{_POP_PEAK_SCALE})"
                f"\\t({fall_start},{fall_start + fall},{_POP_SETTLE_ACCEL},\\fscx100\\fscy100)"
            )
            karaoke = (f"{{\\k{lead_cs}}}" if lead_cs else "") + f"{{\\kf{cs}}}"
            events.append(
                _dialogue(start, end, "{" + tags + "}" + karaoke + _escape_ass_text(unit), layer)
            )
    return events


# Overrides that mean the user took manual control of the text decoration; any of
# them switches a preset's built-in opaque box over to the drawn rectangle, which
# is what lets outline colour and background colour differ at all.
_DECORATION_KEYS = ("backgroundEnabled", "backColor", "backOpacity", "outlineColor", "outlineWidth")


def _resolve_render_style(preset_id: str, overrides: dict, measured: bool):
    """Merge preset + user overrides into `(style, background)`.

    `background` is `(inline colour, inline alpha)` when the cue gets a drawn
    rectangle behind it, else None (the style may then still carry BorderStyle 3).

    ASS makes this fiddlier than it looks: under BorderStyle 3 the OutlineColour
    field *is* the box colour, so the built-in box and a text outline can never be
    two colours at once. Drawing the box ourselves frees that field — but that needs
    measured text, so without measurement we fall back to the built-in box and the
    outline colour goes back to meaning the box colour.
    """
    style = dict(get_preset_style(preset_id))

    primary = _hex_to_ass_colour(overrides.get("textColor"))
    if primary:
        style["primary"] = primary
    outline_colour = _hex_to_ass_colour(overrides.get("outlineColor"))
    outline_width = overrides.get("outlineWidth")
    parsed_width: int | None = None
    if outline_width is not None:
        try:
            parsed_width = max(0, min(20, int(outline_width)))
        except (TypeError, ValueError):
            parsed_width = None

    preset_box = style.get("border_style") == 3
    enabled = overrides.get("backgroundEnabled")
    want_box = preset_box if enabled is None else bool(enabled)
    box_colour = _hex_to_ass_colour(overrides.get("backColor"))
    box_alpha = _opacity_to_alpha_tag(overrides.get("backOpacity"))

    # Which model draws the background. The drawn rectangle is used when the user
    # touched any decoration control, and for the word animations whose scaling
    # would otherwise make the built-in box breathe.
    touched = any(overrides.get(key) is not None for key in _DECORATION_KEYS)
    animated = style.get("anim") in ("word_pop", "karaoke_zoom")
    draw_box = measured and want_box and (touched or animated)

    if draw_box or not want_box:
        # The Outline fields belong to the text from here on.
        style["border_style"] = 1
        if preset_box:
            style["outline_colour"] = _DEFAULT_STYLE["outline_colour"]
            style["outline"] = _BOX_TEXT_OUTLINE if draw_box else _DEFAULT_STYLE["outline"]
    if outline_colour and style["border_style"] != 3:
        style["outline_colour"] = outline_colour
    if parsed_width is not None and style["border_style"] != 3:
        style["outline"] = parsed_width

    if not want_box:
        return style, None

    if draw_box:
        if box_colour:
            colour, alpha = _split_ass_colour(box_colour)
        elif preset_box:
            colour, alpha = _split_ass_colour(get_preset_style(preset_id)["outline_colour"])
        else:
            colour, alpha = "&H000000&", "&H40&"
        return style, (colour, box_alpha or alpha)

    # No measurement: keep ASS's own opaque box, where OutlineColour is the box.
    style["border_style"] = 3
    style["shadow"] = 0
    if box_colour or box_alpha:
        base = box_colour or style["outline_colour"]
        colour, alpha = _split_ass_colour(base)
        alpha_hex = (box_alpha or alpha).strip("&H&") or "00"
        style["outline_colour"] = f"&H{alpha_hex.upper()}{colour.strip('&H&')}"
        style["outline"] = parsed_width if parsed_width is not None else _BOX_FALLBACK_PADDING
    return style, None


def _cue_events(
    text: str,
    start: float,
    end: float,
    style: dict,
    background,
    font_path: str | None,
    font_size: int,
    play_res: tuple[int, int],
    margin_v: int,
    margin_lr: int,
    alignment: int,
) -> list[str]:
    """Every Dialogue line one cue expands into.

    Static presets still produce exactly one event. The word animations produce one
    per word (plus one per line for a drawn background), because that is what pins
    each word to a fixed position.
    """
    lines = text.split("\n")
    anim = style["anim"]
    duration = end - start
    positioned = anim in ("word_pop", "karaoke_zoom")

    layout = None
    if font_path and (positioned or background is not None):
        layout = _layout_lines(
            [(line, _karaoke_units(line)) for line in lines],
            font_path, font_size, play_res, margin_v, margin_lr, alignment,
        )
    if layout is None and positioned:
        logger.debug("[StorySubtitles] Word layout unavailable; using inline fallback.")

    events: list[str] = []
    text_layer = 0
    if background is not None and layout is not None:
        events.extend(_background_events(layout, start, end, background[0], background[1], font_size))
        text_layer = 1

    if layout is not None and anim == "word_pop":
        events.extend(_word_pop_events(layout, start, end, duration, style, text_layer))
    elif layout is not None and anim == "karaoke_zoom":
        events.extend(_karaoke_zoom_events(layout, start, end, duration, style, text_layer))
    else:
        events.append(
            _dialogue(start, end, _render_dialogue_text(text, style, duration), text_layer)
        )
    return events


def build_ass(
    cues,
    font_family: str,
    preset_id: str = "clean",
    play_res: tuple[int, int] = (1920, 1080),
    style_overrides: dict | None = None,
) -> str:
    play_x, play_y = int(play_res[0]), int(play_res[1])
    scale = play_y / 1080.0
    overrides = style_overrides if isinstance(style_overrides, dict) else {}
    font_family = _sanitize_font_family(font_family)
    # `fontScale` multiplies the resolution-aware default so a chosen size looks the
    # same in the 720p preview and the 1080p render; `fontSize` (absolute px) still
    # wins when provided, for backward compatibility.
    font_scale = _coerce_style_float(overrides.get("fontScale"), 1.0, 0.3, 4.0)
    scaled_default = max(1, int(round(54 * scale * font_scale)))
    font_size = _coerce_style_int(overrides.get("fontSize"), scaled_default)
    margin_v = _coerce_style_int(overrides.get("marginV"), int(round(60 * scale)))
    alignment = _coerce_style_int(overrides.get("alignment"), 2)
    margin_lr = max(10, int(round(40 * scale)))

    font_path = _can_measure(font_family, font_size)
    style, background = _resolve_render_style(preset_id, overrides, measured=font_path is not None)
    primary = style["primary"]
    secondary = style["secondary"]
    outline_colour = style["outline_colour"]
    back_colour = style["back_colour"]
    bold = style["bold"]
    italic = style["italic"]
    border_style = style["border_style"]
    outline = style["outline"]
    shadow = style["shadow"]

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_x}",
        f"PlayResY: {play_y}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_family},{font_size},{primary},{secondary},{outline_colour},{back_colour},"
        f"{bold},{italic},0,0,100,100,0,0,{border_style},{outline},{shadow},"
        f"{alignment},{margin_lr},{margin_lr},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events: list[str] = []
    for cue in cues:
        start = float(cue["start"])
        end = float(cue["end"])
        text = str(cue.get("text", "")).strip()
        if not text or end <= start:
            continue
        events.extend(
            _cue_events(
                text, start, end, style, background,
                font_path, font_size, (play_x, play_y), margin_v, margin_lr, alignment,
            )
        )

    return "\n".join(header + events) + "\n"


def write_ass_file(ass_text: str, output_path: str) -> str:
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(ass_text)
    return output_path


def ass_filter_path(ass_path: str) -> str:
    ass_path = os.path.abspath(ass_path)
    relative = None
    try:
        relative = os.path.relpath(ass_path, os.getcwd())
    except ValueError:
        relative = None
    if relative is not None and ":" not in relative:
        return relative.replace("\\", "/")
    escaped = ass_path.replace("\\", "/").replace(":", "\\:")
    return f"'{escaped}'"


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _parse_name_table(data: bytes) -> str | None:
    if len(data) < 6:
        return None
    count = _u16(data, 2)
    string_offset = _u16(data, 4)
    best = None
    best_rank = -1
    for i in range(min(count, 512)):
        record_offset = 6 + i * 12
        if record_offset + 12 > len(data):
            break
        platform_id, encoding_id, language_id, name_id, length, offset = struct.unpack_from(
            ">6H", data, record_offset
        )
        if name_id != 1:
            continue
        raw_start = string_offset + offset
        raw = data[raw_start:raw_start + length]
        if len(raw) < length:
            continue
        if platform_id == 3:
            value = raw.decode("utf-16-be", errors="ignore")
            rank = 3 if (encoding_id in (1, 10) and language_id == 0x409) else 2
        elif platform_id == 0:
            value = raw.decode("utf-16-be", errors="ignore")
            rank = 1
        elif platform_id == 1:
            value = raw.decode("latin-1", errors="ignore")
            rank = 1 if (encoding_id == 0 and language_id == 0) else 0
        else:
            continue
        value = value.replace("\x00", "").strip()
        if value and rank > best_rank:
            best = value
            best_rank = rank
    return best


def _cmap_format4_maps(data: bytes, offset: int, codepoint: int) -> bool:
    if codepoint > 0xFFFF:
        return False
    seg_count_x2 = _u16(data, offset + 6)
    seg_count = seg_count_x2 // 2
    if seg_count == 0:
        return False
    end_codes_offset = offset + 14
    start_codes_offset = end_codes_offset + seg_count_x2 + 2
    id_delta_offset = start_codes_offset + seg_count_x2
    id_range_offset = id_delta_offset + seg_count_x2
    for i in range(seg_count):
        end_code = _u16(data, end_codes_offset + i * 2)
        if end_code < codepoint:
            continue
        start_code = _u16(data, start_codes_offset + i * 2)
        if start_code > codepoint:
            return False
        delta = struct.unpack_from(">h", data, id_delta_offset + i * 2)[0]
        range_offset_pos = id_range_offset + i * 2
        range_offset = _u16(data, range_offset_pos)
        if range_offset == 0:
            return ((codepoint + delta) & 0xFFFF) != 0
        glyph_pos = range_offset_pos + range_offset + (codepoint - start_code) * 2
        if glyph_pos + 2 > len(data):
            return False
        glyph = _u16(data, glyph_pos)
        if glyph == 0:
            return False
        return ((glyph + delta) & 0xFFFF) != 0
    return False


def _cmap_format12_maps(data: bytes, offset: int, codepoint: int) -> bool:
    n_groups = _u32(data, offset + 12)
    groups_offset = offset + 16
    for i in range(min(n_groups, 200000)):
        group_offset = groups_offset + i * 12
        if group_offset + 12 > len(data):
            return False
        start_char = _u32(data, group_offset)
        end_char = _u32(data, group_offset + 4)
        if start_char <= codepoint <= end_char:
            return True
        if start_char > codepoint:
            return False
    return False


def _cmap_maps_codepoint(data: bytes, codepoint: int) -> bool:
    if len(data) < 4:
        return False
    num_tables = _u16(data, 2)
    for i in range(min(num_tables, 64)):
        record_offset = 4 + i * 8
        if record_offset + 8 > len(data):
            break
        platform_id = _u16(data, record_offset)
        encoding_id = _u16(data, record_offset + 2)
        subtable_offset = _u32(data, record_offset + 4)
        if not (platform_id == 0 or (platform_id == 3 and encoding_id in (1, 10))):
            continue
        if subtable_offset + 4 > len(data):
            continue
        try:
            subtable_format = _u16(data, subtable_offset)
            if subtable_format == 4 and _cmap_format4_maps(data, subtable_offset, codepoint):
                return True
            if subtable_format == 12 and _cmap_format12_maps(data, subtable_offset, codepoint):
                return True
        except struct.error:
            continue
    return False


def _parse_sfnt_at(file_obj, base_offset: int) -> tuple[str, bool, bool, bool, bool] | None:
    file_obj.seek(base_offset)
    header = file_obj.read(12)
    if len(header) < 12 or header[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return None
    num_tables = struct.unpack(">H", header[4:6])[0]
    if num_tables == 0 or num_tables > 512:
        return None
    records = file_obj.read(num_tables * 16)
    if len(records) < num_tables * 16:
        return None
    name_loc = None
    cmap_loc = None
    for i in range(num_tables):
        tag = records[i * 16:i * 16 + 4]
        table_offset, table_length = struct.unpack_from(">II", records, i * 16 + 8)
        if table_length == 0 or table_length > 50_000_000:
            continue
        if tag == b"name":
            name_loc = (table_offset, table_length)
        elif tag == b"cmap":
            cmap_loc = (table_offset, table_length)
    if not name_loc:
        return None
    file_obj.seek(name_loc[0])
    family = _parse_name_table(file_obj.read(name_loc[1]))
    if not family:
        return None
    has_hangul = False
    supports_vietnamese = False
    supports_thai = False
    supports_indonesian = False
    if cmap_loc:
        file_obj.seek(cmap_loc[0])
        cmap_data = file_obj.read(cmap_loc[1])
        has_hangul = _cmap_maps_codepoint(cmap_data, _HANGUL_PROBE_CODEPOINT)
        supports_vietnamese = _cmap_maps_codepoint(cmap_data, _VIETNAMESE_PROBE_CODEPOINT)
        supports_thai = _cmap_maps_codepoint(cmap_data, _THAI_PROBE_CODEPOINT)
        supports_indonesian = all(
            _cmap_maps_codepoint(cmap_data, codepoint)
            for codepoint in _INDONESIAN_PROBE_CODEPOINTS
        )
    return family, has_hangul, supports_vietnamese, supports_thai, supports_indonesian


def _probe_font_file(path: str) -> list[tuple[str, bool, bool, bool, bool]]:
    results: list[tuple[str, bool, bool, bool, bool]] = []
    try:
        with open(path, "rb") as file_obj:
            head = file_obj.read(4)
            if head == b"ttcf":
                file_obj.seek(8)
                count_raw = file_obj.read(4)
                if len(count_raw) < 4:
                    return []
                num_fonts = min(struct.unpack(">I", count_raw)[0], 32)
                offsets_raw = file_obj.read(num_fonts * 4)
                if len(offsets_raw) < num_fonts * 4:
                    return []
                offsets = struct.unpack(f">{num_fonts}I", offsets_raw)
                for offset in offsets:
                    parsed = _parse_sfnt_at(file_obj, offset)
                    if parsed:
                        results.append(parsed)
            elif head in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
                parsed = _parse_sfnt_at(file_obj, 0)
                if parsed:
                    results.append(parsed)
    except (OSError, struct.error, ValueError):
        return []
    return results


def _fallback_font_entries(filename: str) -> list[tuple[str, bool, bool, bool, bool]]:
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    for prefix, family, has_hangul, supports_vietnamese, supports_thai, supports_indonesian in _FALLBACK_FONT_WHITELIST:
        if stem.startswith(prefix):
            return [(family, has_hangul, supports_vietnamese, supports_thai, supports_indonesian)]
    return []


def _list_font_files(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(
        os.path.join(directory, name)
        for name in names
        if name.lower().endswith(_FONT_EXTENSIONS) and os.path.isfile(os.path.join(directory, name))
    )


def _fonts_scan_signature(paths: list[str]) -> str:
    max_mtime = 0.0
    for path in paths:
        try:
            max_mtime = max(max_mtime, os.path.getmtime(path))
        except OSError:
            continue
    return f"{len(paths)}:{int(max_mtime)}"


def _fonts_index_path() -> str:
    os.makedirs(Config.STORY_FONTS_DIR, exist_ok=True)
    return os.path.join(Config.STORY_FONTS_DIR, _FONTS_INDEX_FILENAME)


def scan_fonts(force_refresh: bool = False) -> list[dict]:
    index_path = _fonts_index_path()
    system_files = _list_font_files(_SYSTEM_FONTS_DIR)
    user_files = _list_font_files(Config.STORY_FONTS_DIR)
    signature = _fonts_scan_signature(system_files + user_files)

    if not force_refresh and os.path.isfile(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as file_obj:
                cached = json.load(file_obj)
            if (
                isinstance(cached, dict)
                and cached.get("version") == _FONTS_INDEX_VERSION
                and cached.get("signature") == signature
                and isinstance(cached.get("fonts"), list)
            ):
                return cached["fonts"]
        except (OSError, json.JSONDecodeError):
            pass

    registry: dict[str, dict] = {}
    for source, files in (("system", system_files), ("user", user_files)):
        for path in files:
            normalized = path.replace("\\", "/")
            entries = _probe_font_file(path)
            if not entries:
                entries = _fallback_font_entries(path)
            for family, has_hangul, supports_vietnamese, supports_thai, supports_indonesian in entries:
                record = registry.setdefault(family, {
                    "family": family,
                    "files": [],
                    "supportsVietnamese": False,
                    "supportsThai": False,
                    "supportsIndonesian": False,
                    "source": source,
                })
                if normalized not in record["files"]:
                    record["files"].append(normalized)
                record["_hasHangul"] = record.get("_hasHangul", False) or has_hangul
                record["supportsVietnamese"] = record["supportsVietnamese"] or supports_vietnamese
                record["supportsThai"] = record["supportsThai"] or supports_thai
                record["supportsIndonesian"] = record["supportsIndonesian"] or supports_indonesian
                if source == "user":
                    record["source"] = "user"

    # Font Hàn không còn dùng trong pipeline nên bị loại hẳn khỏi danh sách chọn.
    fonts = sorted(
        (
            {key: value for key, value in record.items() if key != "_hasHangul"}
            for record in registry.values()
            if not record.get("_hasHangul")
        ),
        key=lambda item: item["family"].lower(),
    )
    try:
        tmp_path = index_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump(
                {"version": _FONTS_INDEX_VERSION, "signature": signature, "fonts": fonts},
                file_obj, indent=2, ensure_ascii=False,
            )
        shutil.move(tmp_path, index_path)
    except OSError as exc:
        logger.warning(f"[StorySubtitles] Could not write fonts index: {exc}")
    return fonts


_PREVIEW_SAMPLE_LINES = [
    "Malam itu, kebenaran akhirnya terungkap di ruangan kecil.",
    "Đêm đó, sự thật đã thức tỉnh trong căn phòng nhỏ.",
    "คืนนั้น ความจริงได้ตื่นขึ้นในห้องเล็กๆ",
    "The truth finally came out.",
]
_PREVIEW_DURATION_SECONDS = 4.5


def render_subtitle_preview(
    font_family,
    preset_id,
    max_chars_per_line,
    max_lines,
    sample_clip_path: str | None = None,
    style_overrides: dict | None = None,
) -> str:
    os.makedirs(Config.STORY_SUBTITLE_PREVIEW_DIR, exist_ok=True)
    if sample_clip_path and not os.path.isfile(sample_clip_path):
        sample_clip_path = None
    cache_key = "|".join([
        str(font_family),
        str(preset_id),
        str(max_chars_per_line),
        str(max_lines),
        os.path.basename(sample_clip_path or "bg"),
        json.dumps(style_overrides or {}, sort_keys=True, ensure_ascii=False),
    ])
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:16]
    output_path = os.path.abspath(os.path.join(Config.STORY_SUBTITLE_PREVIEW_DIR, f"{digest}.mp4"))
    if os.path.isfile(output_path):
        return output_path

    slot = _PREVIEW_DURATION_SECONDS / len(_PREVIEW_SAMPLE_LINES)
    sample_cues = [
        {"start": round(i * slot, 3), "end": round((i + 1) * slot, 3), "text": line}
        for i, line in enumerate(_PREVIEW_SAMPLE_LINES)
    ]
    blocks = resegment(sample_cues, max_chars_per_line, max_lines, min_cue_duration=0.5)
    ass_text = build_ass(
        blocks,
        font_family,
        preset_id=preset_id,
        play_res=(1280, 720),
        style_overrides=style_overrides,
    )
    ass_path = write_ass_file(
        ass_text, os.path.join(Config.STORY_SUBTITLE_PREVIEW_DIR, f"{digest}.ass")
    )

    ass_value = f"ass={ass_filter_path(ass_path)}"
    if _list_font_files(Config.STORY_FONTS_DIR):
        ass_value += f":fontsdir={ass_filter_path(Config.STORY_FONTS_DIR)}"

    if sample_clip_path:
        input_args = ["-stream_loop", "-1", "-i", sample_clip_path]
        filter_str = f"scale=1280:-2,{ass_value},format=yuv420p"
    else:
        input_args = ["-f", "lavfi", "-i", "color=c=0x202030:s=1280x720:r=30"]
        filter_str = f"{ass_value},format=yuv420p"

    cmd = [
        "ffmpeg",
        "-y",
        *input_args,
        "-t",
        str(_PREVIEW_DURATION_SECONDS),
        "-an",
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        output_path,
    ]
    logger.info(f"[StorySubtitles] Rendering subtitle preview: {output_path}")
    if not FFmpegHelper.run_command(cmd, timeout_seconds=120):
        return ""
    return output_path if os.path.isfile(output_path) else ""
