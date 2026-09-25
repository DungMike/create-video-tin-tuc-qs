"""ClipUsageLedger: the "use each clip once" selection, leases, commit and outbox."""

import os

import pytest

from src.db import media_repo, mongo
from src.utils import clip_usage
from src.utils.clip_usage import ClipUsageLedger


@pytest.fixture
def ordered(monkeypatch):
    """Make the within-tier shuffle a no-op so picks follow pool order."""
    monkeypatch.setattr(clip_usage.random, "shuffle", lambda _items: None)


def _pool(*names, dur=3.0):
    return [(f"/lib/{name}.mp4", dur) for name in names]


def _key(path: str) -> str:
    return os.path.basename(path)[:-4]


def _set_counts(db, **counts):
    for key, value in counts.items():
        db[mongo.CLIPS].update_one({"_id": key}, {"$set": {"use_count": value}}, upsert=True)


def _count(db, key) -> int:
    doc = db[mongo.CLIPS].find_one({"_id": key}) or {}
    return int(doc.get("use_count") or 0)


def test_unused_clips_first_then_least_used(mongo_db):
    ledger = ClipUsageLedger()
    pool = _pool("a", "b", "c", "d", "e", "f")
    _set_counts(mongo_db, a=2, b=1)

    first = ledger.select("s1", pool, _key, target_duration=12, unit=3)
    assert sorted(_key(p) for p in first.paths) == ["c", "d", "e", "f"]
    assert (first.fresh, first.reused) == (4, 0)
    ledger.commit("s1")

    # Now c-f and b are at 1, a is at 2: a 5-clip video must leave `a` out.
    second = ledger.select("s2", pool, _key, target_duration=15, unit=3)
    assert sorted(_key(p) for p in second.paths) == ["b", "c", "d", "e", "f"]
    assert second.max_count == 1


def test_parallel_videos_never_share_leased_clips(mongo_db):
    ledger = ClipUsageLedger()
    pool = _pool("a", "b", "c", "d", "e", "f")

    one = ledger.select("s1", pool, _key, target_duration=9, unit=3)
    two = ledger.select("s2", pool, _key, target_duration=9, unit=3)

    assert not set(one.paths) & set(two.paths)
    assert len(one.paths) == len(two.paths) == 3


def test_release_hands_clips_back_without_counting(mongo_db, ordered):
    ledger = ClipUsageLedger()
    pool = _pool("a", "b", "c")

    picked = ledger.select("s1", pool, _key, target_duration=6, unit=3)
    ledger.release("s1")

    assert all(_count(mongo_db, _key(p)) == 0 for p in picked.paths)
    assert ledger.leased_keys() == set()
    again = ledger.select("s2", pool, _key, target_duration=6, unit=3)
    assert again.paths == picked.paths


def test_commit_counts_each_clip_once_even_when_the_video_repeats_it(mongo_db):
    ledger = ClipUsageLedger()
    pool = _pool("a", "b")

    picked = ledger.select("s1", pool, _key, target_duration=12, unit=3)  # needs 4 clips from 2
    assert len(picked.paths) == 4
    result = ledger.commit("s1", {"output_path": "out.mp4"})

    assert result["committed"] == 2
    assert _count(mongo_db, "a") == 1 and _count(mongo_db, "b") == 1
    event = mongo_db[mongo.CLIP_USAGE_EVENTS].find_one({"story_id": "s1"})
    assert event["clip_keys"] == ["a", "b"] and event["output_path"] == "out.mp4"
    assert ledger.leased_keys() == set()


def test_failed_commit_goes_to_outbox_and_replays_once(mongo_db, monkeypatch):
    ledger = ClipUsageLedger()
    pool = _pool("a", "b")
    ledger.select("s1", pool, _key, target_duration=6, unit=3)

    real_commit = media_repo.commit_clip_usage

    def boom(*_args, **_kwargs):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(media_repo, "commit_clip_usage", boom)
    result = ledger.commit("s1")
    assert result["committed"] == 0 and result["queued"] == 2
    assert os.path.isfile(clip_usage._outbox_path())
    assert _count(mongo_db, "a") == 0

    monkeypatch.setattr(media_repo, "commit_clip_usage", real_commit)
    assert ledger.flush_outbox() == 1
    assert not os.path.exists(clip_usage._outbox_path())
    assert _count(mongo_db, "a") == 1 and _count(mongo_db, "b") == 1

    # Replaying the same usage id (e.g. a crash between commit and outbox rewrite) is a no-op.
    usage_id = result["usageId"]
    media_repo.commit_clip_usage("s1", ["a", "b"], usage_id=usage_id)
    assert _count(mongo_db, "a") == 1


def test_select_fails_loudly_when_counts_cannot_be_read(mongo_db, monkeypatch):
    def boom(_keys):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(media_repo, "get_clip_use_counts", boom)
    with pytest.raises(RuntimeError):
        ClipUsageLedger().select("s1", _pool("a"), _key, target_duration=3, unit=3)


def test_long_takes_chain_only_equally_fresh_successors(mongo_db, ordered):
    ledger = ClipUsageLedger()
    pool = _pool("s0", "s1", "s2", "x0")
    chain = {"/lib/s0.mp4": "/lib/s1.mp4", "/lib/s1.mp4": "/lib/s2.mp4"}
    _set_counts(mongo_db, s1=1)

    picked = ledger.select(
        "st", pool, _key, target_duration=3, unit=3,
        run_length=lambda: 3, successor=chain.get,
    )
    # s0 is fresh but its successor s1 was already used: the shot stops at s0.
    assert picked.paths == ["/lib/s0.mp4"]
    assert picked.runs == [1] and picked.run_starts == [0]


def test_long_takes_chain_consecutive_fresh_clips(mongo_db, ordered):
    ledger = ClipUsageLedger()
    pool = _pool("s0", "s1", "s2", "x0")
    chain = {"/lib/s0.mp4": "/lib/s1.mp4", "/lib/s1.mp4": "/lib/s2.mp4"}

    picked = ledger.select(
        "st", pool, _key, target_duration=12, unit=3,
        run_length=lambda: 3, successor=chain.get,
    )
    assert picked.paths == ["/lib/s0.mp4", "/lib/s1.mp4", "/lib/s2.mp4", "/lib/x0.mp4"]
    assert picked.runs == [3, 1] and picked.run_starts == [0, 3]


def test_normalize_clip_usage_mode():
    assert clip_usage.normalize_clip_usage_mode(None) == "reuse"
    assert clip_usage.normalize_clip_usage_mode("") == "reuse"
    assert clip_usage.normalize_clip_usage_mode("reuse") == "reuse"
    assert clip_usage.normalize_clip_usage_mode(" ONCE ") == "once"
    assert clip_usage.normalize_clip_usage_mode("garbage") == "reuse"


def test_usage_summary(mongo_db):
    _set_counts(mongo_db, a=1, b=2, c=2)
    summary = media_repo.usage_summary(["a", "b", "c", "d", "e"])
    assert summary == {"total": 5, "unused": 2, "byCount": {"1": 1, "2": 2}, "maxCount": 2}
