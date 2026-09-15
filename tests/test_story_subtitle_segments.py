"""Segment boundaries for the parallel GPU overlay pass must not cut a cue in half.

`_rebase_ass_file` shifts an event's Start/End into the segment's own timeline but
leaves the `\\t` / `\\kf` offsets inside its text relative to the *original* Start,
and clamps a straddling cue's Start to 0. A cue cut by a boundary therefore replays
its whole animation in the next segment. Choosing boundaries that fall in the gaps
between cues avoids that for every preset.
"""

from src.utils.story_video_pipeline import (
    _ass_event_spans,
    _rebase_ass_file,
    _snap_segment_boundary,
)

ASS_HEADER = """[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _write_ass(tmp_path, events):
    path = tmp_path / "subs.ass"
    body = "".join(
        f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n" for start, end, text in events
    )
    path.write_text(ASS_HEADER + body, encoding="utf-8")
    return str(path)


def test_ass_event_spans_merges_overlapping_events(tmp_path):
    # The word animations emit one event per word, all inside the same cue, so the
    # spans have to be merged before they can be treated as "subtitle on screen".
    path = _write_ass(tmp_path, [
        ("0:00:01.00", "0:00:04.00", "{\\pos(1,1)}mot"),
        ("0:00:02.00", "0:00:04.00", "{\\pos(2,1)}hai"),
        ("0:00:09.00", "0:00:11.00", "{\\pos(3,1)}ba"),
    ])
    assert _ass_event_spans(path) == [(1.0, 4.0), (9.0, 11.0)]


def test_ass_event_spans_missing_file_is_empty():
    assert _ass_event_spans("does/not/exist.ass") == []


def test_snap_boundary_moves_off_a_cue_to_the_nearer_edge():
    spans = [(10.0, 20.0)]
    assert _snap_segment_boundary(spans, 12.0, window=5.0) == 10.0
    assert _snap_segment_boundary(spans, 18.0, window=5.0) == 20.0


def test_snap_boundary_keeps_a_time_that_is_already_in_a_gap():
    spans = [(10.0, 20.0), (30.0, 40.0)]
    for nominal in (9.0, 25.0, 45.0):
        assert _snap_segment_boundary(spans, nominal, window=5.0) == nominal


def test_snap_boundary_gives_up_when_no_edge_is_within_the_window():
    # A cue longer than the window on both sides: better to cut it than to move the
    # boundary far enough to unbalance the segments.
    spans = [(0.0, 100.0)]
    assert _snap_segment_boundary(spans, 50.0, window=5.0) == 50.0


def test_snapped_boundary_stops_a_cue_from_replaying(tmp_path):
    """End to end: the cue survives with its animation offsets intact."""
    path = _write_ass(tmp_path, [
        ("0:00:04.00", "0:00:08.00", "{\\t(0,150,\\fscx112)}xin chao"),
    ])
    spans = _ass_event_spans(path)
    nominal = 6.0  # straight through the middle of the cue

    # Unsnapped, the second segment starts the cue over: Start clamps to 0 while
    # \t(0,...) still says "animate at the start", so t=0 now means 6s, not 4s.
    naive = tmp_path / "naive.ass"
    _rebase_ass_file(path, nominal, 6.0, str(naive))
    replayed = [l for l in naive.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    assert replayed and replayed[0].split(",")[1] == "0:00:00.00"
    assert "\\t(0,150," in replayed[0]

    # Snapped, the boundary lands on the cue's start, so the whole cue lives in the
    # second segment with its offsets still measured from its own beginning.
    snapped = _snap_segment_boundary(spans, nominal, window=3.0)
    assert snapped == 4.0
    before = tmp_path / "before.ass"
    after = tmp_path / "after.ass"
    _rebase_ass_file(path, 0.0, snapped, str(before))
    _rebase_ass_file(path, snapped, 8.0 - snapped, str(after))
    assert not [l for l in before.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    kept = [l for l in after.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]
    assert len(kept) == 1
    assert kept[0].split(",")[1] == "0:00:00.00"
    assert kept[0].split(",")[2] == "0:00:04.00"
