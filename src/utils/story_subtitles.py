"""Subtitle core helpers for the story-video pipeline (SRT parse, resegment, ASS build, font scan)."""

import hashlib
import json
import os
import re
import shutil
import struct

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
#         \fade; "karaoke" -> per-syllable \kf via _render_karaoke_text.
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
]

_SYSTEM_FONTS_DIR = "C:/Windows/Fonts"
_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")
_FONTS_INDEX_FILENAME = "fonts_index.json"
_KOREAN_PROBE_CODEPOINT = 0xAC00
_VIETNAMESE_PROBE_CODEPOINT = 0x1EBF
_THAI_PROBE_CODEPOINT = 0x0E01  # THAI CHARACTER KO KAI

# (filename_prefix, family, supports_korean, supports_vietnamese, supports_thai)
_FALLBACK_FONT_WHITELIST = (
    ("malgun", "Malgun Gothic", True, False, False),
    ("arial", "Arial", False, True, False),
    ("segoeui", "Segoe UI", False, True, False),
    ("tahoma", "Tahoma", False, True, True),
    ("leelawadeeui", "Leelawadee UI", False, True, True),
    ("leelawadee", "Leelawadee", False, True, True),
    ("verdana", "Verdana", False, True, False),
    ("calibri", "Calibri", False, True, False),
    ("times", "Times New Roman", False, True, False),
    ("georgia", "Georgia", False, True, False),
    ("cambria", "Cambria", False, True, False),
    ("sarabun", "Sarabun", False, True, True),
    ("notosansthai", "Noto Sans Thai", False, False, True),
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


def _karaoke_units(line: str) -> list[str]:
    line = line.strip()
    if not line:
        return []
    if " " in line:
        return [word for word in line.split(" ") if word]
    units = [line[i:i + 2] for i in range(0, len(line), 2)]
    if len(units) >= 2 and len(units[-1]) == 1:
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


def _render_dialogue_text(text: str, preset_id: str, duration: float) -> str:
    lines = text.split("\n")
    style = get_preset_style(preset_id)
    anim = style["anim"]
    if anim == "karaoke":
        return _render_karaoke_text(lines, duration)
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


def _sanitize_font_family(font_family) -> str:
    # Commas shift Style fields, newlines inject arbitrary ASS lines.
    cleaned = re.sub(r"[,\r\n]+", " ", str(font_family))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or Config.STORY_SUBTITLE_DEFAULT_FONT


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
    font_size = _coerce_style_int(overrides.get("fontSize"), int(round(54 * scale)))
    margin_v = _coerce_style_int(overrides.get("marginV"), int(round(60 * scale)))
    alignment = _coerce_style_int(overrides.get("alignment"), 2)
    margin_lr = max(10, int(round(40 * scale)))

    style = get_preset_style(preset_id)
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
        rendered = _render_dialogue_text(text, preset_id, end - start)
        events.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},Default,,0,0,0,,{rendered}"
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


def _parse_sfnt_at(file_obj, base_offset: int) -> tuple[str, bool, bool, bool] | None:
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
    supports_korean = False
    supports_vietnamese = False
    supports_thai = False
    if cmap_loc:
        file_obj.seek(cmap_loc[0])
        cmap_data = file_obj.read(cmap_loc[1])
        supports_korean = _cmap_maps_codepoint(cmap_data, _KOREAN_PROBE_CODEPOINT)
        supports_vietnamese = _cmap_maps_codepoint(cmap_data, _VIETNAMESE_PROBE_CODEPOINT)
        supports_thai = _cmap_maps_codepoint(cmap_data, _THAI_PROBE_CODEPOINT)
    return family, supports_korean, supports_vietnamese, supports_thai


def _probe_font_file(path: str) -> list[tuple[str, bool, bool, bool]]:
    results: list[tuple[str, bool, bool, bool]] = []
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


def _fallback_font_entries(filename: str) -> list[tuple[str, bool, bool, bool]]:
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    for prefix, family, supports_korean, supports_vietnamese, supports_thai in _FALLBACK_FONT_WHITELIST:
        if stem.startswith(prefix):
            return [(family, supports_korean, supports_vietnamese, supports_thai)]
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
            for family, supports_korean, supports_vietnamese, supports_thai in entries:
                record = registry.setdefault(family, {
                    "family": family,
                    "files": [],
                    "supportsKorean": False,
                    "supportsVietnamese": False,
                    "supportsThai": False,
                    "source": source,
                })
                if normalized not in record["files"]:
                    record["files"].append(normalized)
                record["supportsKorean"] = record["supportsKorean"] or supports_korean
                record["supportsVietnamese"] = record["supportsVietnamese"] or supports_vietnamese
                record["supportsThai"] = record["supportsThai"] or supports_thai
                if source == "user":
                    record["source"] = "user"

    fonts = sorted(registry.values(), key=lambda item: item["family"].lower())
    try:
        tmp_path = index_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump({"signature": signature, "fonts": fonts}, file_obj, indent=2, ensure_ascii=False)
        shutil.move(tmp_path, index_path)
    except OSError as exc:
        logger.warning(f"[StorySubtitles] Could not write fonts index: {exc}")
    return fonts


_PREVIEW_SAMPLE_LINES = [
    "그날 밤, 진실이 깨어났다. 라디오에서 목소리가 흘러나왔다.",
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
