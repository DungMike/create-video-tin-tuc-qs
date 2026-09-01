import os
import re
import json

import pytest

from src.config import Config
from src.utils import story_subtitles as ss

SRT_PATH = os.path.join(os.path.dirname(__file__), "test-transcript", "test.srt")


def _dialogue_lines(ass_text: str) -> list[str]:
    return [line for line in ass_text.splitlines() if line.startswith("Dialogue:")]


def _style_line(ass_text: str) -> str:
    return next(line for line in ass_text.splitlines() if line.startswith("Style:"))


def test_subtitle_presets_registry():
    ids = [item["id"] for item in ss.SUBTITLE_PRESETS]
    # The four originals must remain present (their exact ASS output is guarded below).
    assert {"clean", "fade_soft", "karaoke_pop", "emphasis_bold"}.issubset(set(ids))
    assert len(ids) == len(set(ids)), "preset ids must be unique"
    for item in ss.SUBTITLE_PRESETS:
        assert item["name"] and item["description"]
    assert ss.get_subtitle_preset("clean")["id"] == "clean"
    assert ss.get_subtitle_preset("does_not_exist") is None


def test_every_preset_builds_valid_ass():
    # Every registered preset must round-trip through build_ass without error and
    # produce a Style row plus a Dialogue line.
    cues = [{"start": 0.0, "end": 2.0, "text": "xin chào thế giới"}]
    for item in ss.SUBTITLE_PRESETS:
        ass_text = ss.build_ass(cues, "Arial", item["id"])
        style_fields = _style_line(ass_text)[len("Style: "):].split(",")
        # 23 comma-separated fields per the [V4+ Styles] Format row.
        assert len(style_fields) == 23
        assert _dialogue_lines(ass_text)


def test_get_preset_style_unknown_falls_back_to_clean():
    assert ss.get_preset_style("does_not_exist") == ss.get_preset_style("clean")


def test_build_ass_preset_yellow_pop_primary_colour():
    ass_text = ss.build_ass([{"start": 0.0, "end": 2.0, "text": "abc"}], "Arial", "yellow_pop")
    fields = _style_line(ass_text)[len("Style: "):].split(",")
    assert fields[3] == "&H0000FFFF"  # PrimaryColour = yellow


def test_build_ass_preset_box_dark_uses_opaque_box():
    ass_text = ss.build_ass([{"start": 0.0, "end": 2.0, "text": "abc"}], "Arial", "box_dark")
    fields = _style_line(ass_text)[len("Style: "):].split(",")
    assert fields[5] == "&H80000000"  # OutlineColour = translucent black box fill
    assert fields[15] == "3"          # BorderStyle = opaque box


def test_build_ass_preset_neon_cyan_has_blur():
    ass_text = ss.build_ass([{"start": 0.0, "end": 2.0, "text": "abc"}], "Arial", "neon_cyan")
    line = _dialogue_lines(ass_text)[0]
    assert "\\blur" in line
    fields = _style_line(ass_text)[len("Style: "):].split(",")
    assert fields[5] == "&H00FFFF00"  # OutlineColour = cyan glow


def test_build_ass_preset_pop_in_has_scale_transform():
    ass_text = ss.build_ass([{"start": 0.0, "end": 2.0, "text": "abc"}], "Arial", "pop_in")
    line = _dialogue_lines(ass_text)[0]
    assert "\\t(" in line
    assert "\\fscx" in line


def test_parse_srt_real_file():
    cues = ss.parse_srt(SRT_PATH)
    assert len(cues) == 29
    assert cues[0]["start"] == pytest.approx(0.2)
    prev_start = -1.0
    for cue in cues:
        assert cue["text"].strip()
        assert "\n" not in cue["text"]
        assert cue["end"] > cue["start"]
        assert cue["start"] >= prev_start
        prev_start = cue["start"]
    # multi-line cue text is joined with a space
    assert "기계 내부의" in cues[1]["text"]


def test_parse_srt_missing_file():
    with pytest.raises(ss.SubtitleParseError):
        ss.parse_srt(os.path.join(os.path.dirname(__file__), "no_such_file.srt"))


def test_parse_srt_garbage_and_empty(tmp_path):
    garbage = tmp_path / "garbage.srt"
    garbage.write_text("hello world\nthis is not a subtitle\n\nstill not one", encoding="utf-8")
    with pytest.raises(ss.SubtitleParseError):
        ss.parse_srt(str(garbage))

    empty = tmp_path / "empty.srt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ss.SubtitleParseError):
        ss.parse_srt(str(empty))


def test_parse_srt_handles_bom_and_crlf(tmp_path):
    path = tmp_path / "bom.srt"
    content = "1\r\n00:00:01,000 --> 00:00:03,500\r\nXin chào\r\nthế giới\r\n"
    path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    cues = ss.parse_srt(str(path))
    assert len(cues) == 1
    assert cues[0]["start"] == pytest.approx(1.0)
    assert cues[0]["end"] == pytest.approx(3.5)
    assert cues[0]["text"] == "Xin chào thế giới"


def test_resegment_korean_long_cue():
    cues = ss.parse_srt(SRT_PATH)
    cue = cues[0]
    line_cap, lines_cap = 18, 2
    blocks = ss.resegment([cue], max_chars_per_line=line_cap, max_lines=lines_cap, min_cue_duration=0.8)
    assert len(blocks) > 3
    for block in blocks:
        lines = block["text"].split("\n")
        assert all(len(line) <= line_cap for line in lines)
        assert sum(len(line) for line in lines) <= line_cap * lines_cap
        assert block["end"] - block["start"] >= 0.8 - 0.01
    assert blocks[0]["start"] == pytest.approx(cue["start"], abs=0.002)
    assert blocks[-1]["end"] == pytest.approx(cue["end"], abs=0.002)
    for prev, nxt in zip(blocks, blocks[1:]):
        assert nxt["start"] == pytest.approx(prev["end"], abs=0.002)
    total = sum(block["end"] - block["start"] for block in blocks)
    assert total == pytest.approx(cue["end"] - cue["start"], abs=0.05)


def test_resegment_latin_breaks_only_at_spaces():
    text = "Đêm đó, sự thật đã thức tỉnh trong căn phòng nhỏ và mọi người đều biết rõ."
    cue = {"start": 0.0, "end": 8.0, "text": text}
    blocks = ss.resegment([cue], max_chars_per_line=16, max_lines=2, min_cue_duration=0.8)
    for block in blocks:
        for line in block["text"].split("\n"):
            assert len(line) <= 16
    rebuilt = " ".join(block["text"].replace("\n", " ") for block in blocks)
    assert rebuilt == text


def test_resegment_overlong_latin_word_not_split():
    long_word = "extraordinarilylongword"
    cue = {"start": 0.0, "end": 4.0, "text": f"Hello {long_word} end."}
    blocks = ss.resegment([cue], max_chars_per_line=10, max_lines=2, min_cue_duration=0.8)
    all_tokens = []
    for block in blocks:
        for line in block["text"].split("\n"):
            all_tokens.extend(line.split(" "))
    assert long_word in all_tokens


def test_resegment_mixed_korean_vietnamese():
    cue = {"start": 0.0, "end": 5.0, "text": "그날 밤, sự thật đã 깨어났다 trong chiếc radio cũ kỹ."}
    blocks = ss.resegment([cue], max_chars_per_line=14, max_lines=2, min_cue_duration=0.8)
    assert blocks
    for block in blocks:
        for line in block["text"].split("\n"):
            assert len(line) <= 14


def test_resegment_pure_korean_no_space_falls_back_per_character():
    cue = {"start": 0.0, "end": 6.0, "text": "가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허고노도로모보소오조초코토포호"}
    blocks = ss.resegment([cue], max_chars_per_line=10, max_lines=2, min_cue_duration=0.8)
    for block in blocks:
        for line in block["text"].split("\n"):
            assert len(line) <= 10


def test_resegment_min_duration_merges_short_sentences():
    cue = {"start": 0.0, "end": 1.2, "text": "Hi there. Bye now."}
    blocks = ss.resegment([cue], max_chars_per_line=42, max_lines=2, min_cue_duration=0.8)
    assert len(blocks) == 1
    assert blocks[0]["start"] == pytest.approx(0.0)
    assert blocks[0]["end"] == pytest.approx(1.2)


def test_resegment_preserves_overlapping_source_cues():
    cues = [
        {"start": 0.0, "end": 4.0, "text": "Speaker one talks."},
        {"start": 0.5, "end": 2.0, "text": "Speaker two."},
    ]
    blocks = ss.resegment(cues, max_chars_per_line=42, max_lines=2, min_cue_duration=0.8)
    assert len(blocks) == 2
    by_text = {block["text"]: block for block in blocks}
    assert by_text["Speaker one talks."]["start"] == pytest.approx(0.0)
    assert by_text["Speaker one talks."]["end"] == pytest.approx(4.0)
    assert by_text["Speaker two."]["start"] == pytest.approx(0.5)
    assert by_text["Speaker two."]["end"] == pytest.approx(2.0)
    assert all("_cue" not in block for block in blocks)


def test_resegment_merge_respects_max_lines_after_repack():
    cue = {"start": 0.0, "end": 2.0, "text": "aaaaaaa bbbbbb. cccc"}
    blocks = ss.resegment([cue], max_chars_per_line=10, max_lines=2, min_cue_duration=0.8)
    for block in blocks:
        lines = block["text"].split("\n")
        assert len(lines) <= 2
        assert all(len(line) <= 10 for line in lines)


def test_build_ass_dialogue_has_exactly_10_fields_and_escapes():
    cues = [{"start": 1.0, "end": 3.5, "text": "He said {hello} \\ done\nsecond line"}]
    ass_text = ss.build_ass(cues, "Arial", preset_id="clean")
    lines = _dialogue_lines(ass_text)
    assert len(lines) == 1
    payload = lines[0][len("Dialogue: "):]
    fields = payload.split(",", 9)
    assert len(fields) == 10
    assert fields[3] == "Default"
    assert fields[4] == ""  # Name
    assert fields[5] == "0" and fields[6] == "0" and fields[7] == "0"  # margins
    assert fields[8] == ""  # Effect
    text_field = fields[9]
    assert "\\{hello\\}" in text_field
    assert "{hello}" not in text_field
    assert "\\\\" in text_field
    assert "\\N" in text_field
    # Events format line matches the 10-field layout
    assert "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text" in ass_text


def test_build_ass_header_and_style_defaults():
    ass_text = ss.build_ass(
        [{"start": 0.0, "end": 2.0, "text": "xin chào"}], "Malgun Gothic", preset_id="clean"
    )
    assert "PlayResX: 1920" in ass_text
    assert "PlayResY: 1080" in ass_text
    assert "WrapStyle: 2" in ass_text
    style_fields = _style_line(ass_text)[len("Style: "):].split(",")
    assert len(style_fields) == 23
    assert style_fields[1] == "Malgun Gothic"
    assert style_fields[2] == "54"
    assert style_fields[3] == "&H00FFFFFF"
    assert style_fields[5] == "&H00000000"
    assert style_fields[15] == "1"  # BorderStyle
    assert style_fields[16] == "3"  # Outline
    assert style_fields[17] == "1"  # Shadow
    assert style_fields[18] == "2"  # Alignment
    assert style_fields[21] == "60"  # MarginV

    scaled = ss.build_ass(
        [{"start": 0.0, "end": 2.0, "text": "xin chào"}], "Arial",
        preset_id="clean", play_res=(1280, 720),
    )
    scaled_fields = _style_line(scaled)[len("Style: "):].split(",")
    assert scaled_fields[2] == "36"
    assert scaled_fields[21] == "40"

    overridden = ss.build_ass(
        [{"start": 0.0, "end": 2.0, "text": "xin chào"}], "Arial",
        preset_id="clean", style_overrides={"fontSize": 70, "marginV": 90, "alignment": 8},
    )
    over_fields = _style_line(overridden)[len("Style: "):].split(",")
    assert over_fields[2] == "70"
    assert over_fields[21] == "90"
    assert over_fields[18] == "8"


def test_build_ass_ignores_invalid_style_overrides():
    cues = [{"start": 0.0, "end": 2.0, "text": "abc"}]
    ass_text = ss.build_ass(
        cues, "Arial", preset_id="clean",
        style_overrides={"fontSize": "abc", "marginV": {}, "alignment": ["x"]},
    )
    fields = _style_line(ass_text)[len("Style: "):].split(",")
    assert fields[2] == "54"
    assert fields[18] == "2"
    assert fields[21] == "60"
    # negative/zero values also fall back to defaults
    negative = ss.build_ass(
        cues, "Arial", preset_id="clean",
        style_overrides={"fontSize": -5, "marginV": 0, "alignment": "0"},
    )
    neg_fields = _style_line(negative)[len("Style: "):].split(",")
    assert neg_fields[2] == "54"
    assert neg_fields[18] == "2"
    assert neg_fields[21] == "60"


def test_build_ass_sanitizes_font_family():
    cues = [{"start": 0.0, "end": 2.0, "text": "abc"}]
    ass_text = ss.build_ass(cues, "Bad,Font\nDialogue: injected", preset_id="clean")
    fields = _style_line(ass_text)[len("Style: "):].split(",")
    assert len(fields) == 23
    assert fields[1] == "Bad Font Dialogue: injected"
    assert len(_dialogue_lines(ass_text)) == 1  # no forged event line
    # empty family falls back to the configured default
    fallback = ss.build_ass(cues, ",\r\n", preset_id="clean")
    fallback_fields = _style_line(fallback)[len("Style: "):].split(",")
    assert fallback_fields[1] == Config.STORY_SUBTITLE_DEFAULT_FONT


def test_build_ass_preset_clean():
    ass_text = ss.build_ass([{"start": 0.0, "end": 2.0, "text": "abc"}], "Arial", "clean")
    assert "{\\fad(250,250)}" in _dialogue_lines(ass_text)[0]


def test_build_ass_preset_fade_soft_uses_cue_duration():
    ass_text = ss.build_ass([{"start": 0.0, "end": 2.0, "text": "abc"}], "Arial", "fade_soft")
    assert "{\\fade(255,0,255,0,400,1600,2000)}" in _dialogue_lines(ass_text)[0]
    short = ss.build_ass([{"start": 0.0, "end": 0.5, "text": "abc"}], "Arial", "fade_soft")
    assert "{\\fad(200,200)}" in _dialogue_lines(short)[0]


def test_build_ass_preset_karaoke_pop_timing_and_colors():
    ass_text = ss.build_ass(
        [{"start": 0.0, "end": 3.0, "text": "안녕하세요 세상 좋은 아침"}],
        "Malgun Gothic", "karaoke_pop",
    )
    style = _style_line(ass_text)
    fields = style[len("Style: "):].split(",")
    assert fields[3] == "&H0000FFFF"
    assert fields[4] == "&H00FFFFFF"
    line = _dialogue_lines(ass_text)[0]
    assert "{\\fad(150,150)}" in line
    kf_values = [int(value) for value in re.findall(r"\\kf(\d+)", line)]
    assert kf_values
    assert abs(sum(kf_values) - 300) <= 1

    # no-space Korean: per-character clusters of 2-3 chars
    no_space = ss.build_ass(
        [{"start": 0.0, "end": 2.0, "text": "안녕하세요진우야"}],
        "Malgun Gothic", "karaoke_pop",
    )
    line2 = _dialogue_lines(no_space)[0]
    kf_values2 = [int(value) for value in re.findall(r"\\kf(\d+)", line2)]
    assert len(kf_values2) >= 2
    assert abs(sum(kf_values2) - 200) <= 1


def test_build_ass_preset_word_bounce_reveals_each_word():
    ass_text = ss.build_ass(
        [{"start": 0.0, "end": 3.0, "text": "một hai ba bốn"}], "Arial", "word_bounce"
    )
    line = _dialogue_lines(ass_text)[0]
    # One reset + hidden-alpha block per word, revealed via \t and bounced via \fscx.
    assert line.count("\\r") == 4
    assert line.count("\\alpha&HFF&") == 4
    assert line.count("\\t(") == 12  # 3 transforms per word
    assert "\\fscx135" in line and "\\fscx100" in line
    # Word start times cover the cue: first word at 0, later words strictly increasing.
    starts = [int(m) for m in re.findall(r"\\alpha&HFF&\\t\((\d+),", line)]
    assert starts[0] == 0
    assert starts == sorted(starts)
    assert starts[-1] < 3000


def test_build_ass_preset_word_bounce_box_style():
    ass_text = ss.build_ass([{"start": 0.0, "end": 2.0, "text": "abc def"}], "Arial", "word_bounce_box")
    fields = _style_line(ass_text)[len("Style: "):].split(",")
    assert fields[15] == "3"  # BorderStyle = opaque box
    assert fields[5] == "&H80000000"


def test_build_ass_preset_rainbow_cycle_sweeps_colours():
    ass_text = ss.build_ass(
        [{"start": 0.0, "end": 4.0, "text": "chuyện kể đêm khuya"}], "Arial", "rainbow_cycle"
    )
    line = _dialogue_lines(ass_text)[0]
    # Starts at the first colour then sweeps through the remaining four.
    assert "\\1c&H5D5DFF&" in line
    assert line.count("\\t(") == 4
    assert "\\1c&HE37DD4&" in line
    assert "\\fad(150,150)" in line


def test_build_ass_preset_color_pulse_beats_scale_with_duration():
    long = ss.build_ass([{"start": 0.0, "end": 6.0, "text": "abc"}], "Arial", "color_pulse")
    long_line = _dialogue_lines(long)[0]
    short = ss.build_ass([{"start": 0.0, "end": 1.0, "text": "abc"}], "Arial", "color_pulse")
    short_line = _dialogue_lines(short)[0]
    # Each beat is a pair of transforms; longer cues pulse more times.
    assert short_line.count("\\t(") == 2
    assert long_line.count("\\t(") == 10  # 5 beats at ~1.2s per beat
    assert "\\1c&H00FFFF&" in long_line and "\\1c&HFFFFFF&" in long_line


def test_build_ass_preset_karaoke_zoom_keeps_kf_timing():
    ass_text = ss.build_ass(
        [{"start": 0.0, "end": 3.0, "text": "một hai ba"}], "Arial", "karaoke_zoom"
    )
    line = _dialogue_lines(ass_text)[0]
    kf_values = [int(value) for value in re.findall(r"\\kf(\d+)", line)]
    assert len(kf_values) == 3
    assert abs(sum(kf_values) - 300) <= 1
    # Every word gets its own reset + zoom-in/out pair.
    assert line.count("\\r") == 3
    assert line.count("\\fscx122") == 3
    assert line.count("\\fscx100") == 3


def test_build_ass_preset_karaoke_neon_redeclares_blur_per_word():
    ass_text = ss.build_ass(
        [{"start": 0.0, "end": 2.0, "text": "abc def"}], "Arial", "karaoke_neon"
    )
    line = _dialogue_lines(ass_text)[0]
    # \r resets overrides, so the glow must be re-declared inside every block.
    assert line.count("\\blur5") == 2
    fields = _style_line(ass_text)[len("Style: "):].split(",")
    assert fields[5] == "&H00FFFF00"  # cyan glow outline


def test_build_ass_preset_emphasis_bold():
    ass_text = ss.build_ass([{"start": 0.0, "end": 2.0, "text": "abc"}], "Arial", "emphasis_bold")
    line = _dialogue_lines(ass_text)[0]
    assert "\\fad(200,200)" in line
    assert "\\bord4" in line
    assert "\\shad2" in line
    assert "\\3c&H000000&" in line
    assert "\\4c&H202020&" in line
    fields = _style_line(ass_text)[len("Style: "):].split(",")
    assert fields[7] == "1"  # Bold


def test_ass_filter_path_same_drive(monkeypatch):
    monkeypatch.setattr(os, "getcwd", lambda: "E:\\proj")
    result = ss.ass_filter_path("E:\\proj\\storage\\story_subtitle_previews\\demo.ass")
    assert result == "storage/story_subtitle_previews/demo.ass"
    assert ":" not in result
    assert "\\" not in result


def test_ass_filter_path_different_drive_fallback(monkeypatch):
    monkeypatch.setattr(os, "getcwd", lambda: "D:\\work")
    result = ss.ass_filter_path("C:\\Windows\\Temp\\subs.ass")
    assert result.startswith("'") and result.endswith("'")
    assert "\\:" in result
    assert "C:" not in result
    assert result == "'C\\:/Windows/Temp/subs.ass'"


def test_write_ass_file(tmp_path):
    output = ss.write_ass_file("[Script Info]\n", str(tmp_path / "nested" / "out.ass"))
    assert os.path.isfile(output)
    with open(output, "r", encoding="utf-8") as file_obj:
        assert file_obj.read().startswith("[Script Info]")


@pytest.mark.skipif(not os.path.isdir("C:/Windows/Fonts"), reason="Windows fonts dir unavailable")
def test_scan_fonts_registry_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "STORY_FONTS_DIR", str(tmp_path / "user_fonts"))
    fonts = ss.scan_fonts(force_refresh=True)
    assert fonts
    by_family = {item["family"]: item for item in fonts}

    # Font Hàn bị loại hẳn khỏi danh sách, và cờ Korean không còn trong payload.
    assert "Malgun Gothic" not in by_family
    assert all("supportsKorean" not in item for item in fonts)
    assert all("_hasHangul" not in item for item in fonts)

    arial = by_family.get("Arial")
    if arial is not None:
        assert arial["supportsVietnamese"] is True
        assert arial["supportsIndonesian"] is True
        assert arial["source"] == "system"
        assert arial["files"]

    # Font symbol không có Latin/"é" nên không tính là hỗ trợ tiếng Indonesia.
    wingdings = by_family.get("Wingdings")
    if wingdings is not None:
        assert wingdings["supportsIndonesian"] is False

    index_path = os.path.join(Config.STORY_FONTS_DIR, "fonts_index.json")
    assert os.path.isfile(index_path)

    # cache hit: tampered cache is returned as-is when signature matches
    with open(index_path, "r", encoding="utf-8") as file_obj:
        cached = json.load(file_obj)
    cached["fonts"].append({
        "family": "ZZZ Fake Font", "files": [], "supportsIndonesian": False,
        "supportsVietnamese": False, "source": "system",
    })
    with open(index_path, "w", encoding="utf-8") as file_obj:
        json.dump(cached, file_obj)
    cached_fonts = ss.scan_fonts()
    assert any(item["family"] == "ZZZ Fake Font" for item in cached_fonts)

    # force_refresh bypasses the tampered cache
    refreshed = ss.scan_fonts(force_refresh=True)
    assert not any(item["family"] == "ZZZ Fake Font" for item in refreshed)
