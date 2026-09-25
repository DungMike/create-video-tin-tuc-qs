"""The render/ingest flows only reach the new MongoDB code when explicitly asked to.

Default ("reuse") renders, configs written before the option existed (resumed
batches, retries) and ingests with Mongo unconfigured must behave exactly as before.
"""

import pytest

from src.config import Config
from src.db import background_writer, mongo
from src.utils import clip_usage, story_video_pipeline, video_source_downloader


@pytest.fixture
def story_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "STORY_VIDEO_DIR", str(tmp_path / "story_video"))
    monkeypatch.setattr(Config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(Config, "STORY_LIBRARY_DIR", str(tmp_path / "story_library"))
    return tmp_path


def _runner(story_id: str, **extra):
    return story_video_pipeline.StoryVideoPipelineRunner(story_id, {
        "input_type": "audio_file",
        "input_value": "missing.mp3",
        "output_name": story_id,
        "library_ids": ["lib"],
        **extra,
    })


def _fake_pool(runner, monkeypatch, names=("a", "b", "c", "d")):
    pool = [(f"/lib/{name}.mp4", 3.0) for name in names]
    monkeypatch.setattr(runner, "_library_pool", lambda _lib: list(pool))
    monkeypatch.setattr(runner, "_segment_duration", lambda: 3)
    monkeypatch.setattr(runner, "_library_clip_keys", lambda _lib: {p: f"k:{p[5:6]}" for p, _d in pool})
    return pool


def _forbid_mongo(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("default flow touched MongoDB / the clip-usage ledger")

    monkeypatch.setattr(mongo, "get_db", boom)
    monkeypatch.setattr(clip_usage.ClipUsageLedger, "select", boom)
    monkeypatch.setattr(clip_usage.ClipUsageLedger, "commit", boom)
    monkeypatch.setattr(background_writer, "submit", boom)


@pytest.mark.parametrize("extra", [{}, {"clip_usage_mode": "reuse"}, {"clip_usage_mode": None}])
def test_default_render_never_touches_mongo(story_storage, monkeypatch, extra):
    runner = _runner("sv-reuse", **extra)
    assert runner.clip_usage_mode == "reuse"
    _fake_pool(runner, monkeypatch)
    _forbid_mongo(monkeypatch)

    clips = runner._select_clips(10.0)

    assert len(clips) == 4 and "clipUsage" not in runner.progress


def test_default_successful_run_commits_nothing(story_storage, monkeypatch):
    runner = _runner("sv-reuse-run")
    _forbid_mongo(monkeypatch)
    monkeypatch.setattr(runner, "_prepare_audio", lambda: "audio.mp3")
    monkeypatch.setattr(story_video_pipeline, "get_audio_duration", lambda _p: 10.0)
    monkeypatch.setattr(runner, "_select_clips", lambda _d: ["clip.mp4"])
    monkeypatch.setattr(runner, "_render_simple_video", lambda *_a: "rendered.mp4")
    monkeypatch.setattr(runner, "_apply_story_overlays", lambda *_a: "overlaid.mp4")
    monkeypatch.setattr(runner, "_finalize", lambda _v: str(story_storage / "out.mp4"))

    assert runner.run() is not None


def test_once_mode_selects_through_the_ledger_and_commits_on_success(story_storage, monkeypatch, mongo_db):
    ledger = clip_usage.ClipUsageLedger()
    monkeypatch.setattr(story_video_pipeline, "get_clip_usage_ledger", lambda: ledger)
    mongo_db[mongo.CLIPS].update_one({"_id": "k:a"}, {"$set": {"use_count": 3}}, upsert=True)

    runner = _runner("sv-once", clip_usage_mode="once")
    assert runner.clip_usage_mode == "once"
    _fake_pool(runner, monkeypatch)
    monkeypatch.setattr(runner, "_prepare_audio", lambda: "audio.mp3")
    monkeypatch.setattr(story_video_pipeline, "get_audio_duration", lambda _p: 8.0)
    monkeypatch.setattr(runner, "_render_simple_video", lambda *_a: "rendered.mp4")
    monkeypatch.setattr(runner, "_apply_story_overlays", lambda *_a: "overlaid.mp4")
    monkeypatch.setattr(runner, "_finalize", lambda _v: str(story_storage / "out.mp4"))

    assert runner.run() is not None

    usage = runner.progress["clipUsage"]
    assert usage["mode"] == "once" and usage["fresh"] == 3 and usage["committed"] == 3
    counts = {doc["_id"]: doc["use_count"] for doc in mongo_db[mongo.CLIPS].find()}
    # 8s of audio = 3 clips: the three unused ones, never the clip already used 3 times.
    assert counts == {"k:a": 3, "k:b": 1, "k:c": 1, "k:d": 1}
    assert ledger.leased_keys() == set()


def test_once_mode_failed_run_releases_without_counting(story_storage, monkeypatch, mongo_db):
    ledger = clip_usage.ClipUsageLedger()
    monkeypatch.setattr(story_video_pipeline, "get_clip_usage_ledger", lambda: ledger)

    runner = _runner("sv-once-fail", clip_usage_mode="once")
    _fake_pool(runner, monkeypatch)
    monkeypatch.setattr(runner, "_prepare_audio", lambda: "audio.mp3")
    monkeypatch.setattr(story_video_pipeline, "get_audio_duration", lambda _p: 8.0)
    monkeypatch.setattr(runner, "_render_simple_video", lambda *_a: None)  # render fails

    assert runner.run() is None
    assert mongo_db[mongo.CLIPS].count_documents({"use_count": {"$gt": 0}}) == 0
    assert ledger.leased_keys() == set()


def test_once_mode_without_mongo_fails_the_video_clearly(story_storage, monkeypatch):
    # Mongo off (conftest): the video fails with an explanation, never a silent random pick.
    runner = _runner("sv-once-nomongo", clip_usage_mode="once")
    _fake_pool(runner, monkeypatch)

    with pytest.raises(RuntimeError, match="MongoDB"):
        runner._select_clips(8.0)


def test_ingest_report_is_a_noop_without_mongo(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("ingest queued a Mongo write with Mongo unconfigured")

    monkeypatch.setattr(background_writer, "submit", boom)
    video_source_downloader._report_ingest("lib", {"provider": "pixabay", "provider_id": "1"}, [{"id": "x"}])


def test_ingest_report_records_source_and_clips_in_background(monkeypatch, mongo_db):
    monkeypatch.setattr("src.utils.story_library.resolve_library_id", lambda library_id=None: library_id)
    added = [{
        "id": "c1",
        "source_name": "pixabay_1297_75f26cc5_clip_000.mp4",
        "relative_path": "clips/c1.mp4",
        "duration": 3.0,
        "tags": ["pixabay", "session:hvc-1", "keyword:Thai police"],
    }]
    ref = video_source_downloader.provider_item_source_ref(
        "pixabay", "1297", {"title": "Police", "pageUrl": "https://pixabay.com/x-1297/"}, "harvest",
        keyword="Thai police",
    )

    video_source_downloader._report_ingest("lib-1", ref, added)
    assert background_writer.get_background_writer().wait_idle(5)

    source = mongo_db[mongo.SOURCE_VIDEOS].find_one({"_id": "pixabay:1297"})
    assert source["provider_id"] == "1297" and source["keyword"] == "Thai police"
    assert source["libraries"] == ["lib-1"] and source["flows"] == ["harvest"]
    clip = mongo_db[mongo.CLIPS].find_one({"_id": "pixabay:1297#000@3s"})
    assert clip["use_count"] == 0
    assert clip["locations"] == [{"library_id": "lib-1", "asset_id": "c1", "relative_path": "clips/c1.mp4"}]
