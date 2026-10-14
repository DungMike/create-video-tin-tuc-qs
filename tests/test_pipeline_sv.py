"""Loai 2: micro-batch qua route batch/create that (runner render gia), theo doi tien do."""

import json
import os

import pytest

from pipeline_fakes import FakeDgt, FakeTst
from src.config import Config
from src.utils import pipeline_runner as runner
from src.utils import pipeline_store as store
from src.utils import story_video_batch, story_video_pipeline


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DIR", str(tmp_path / "pipeline"))
    monkeypatch.setattr(Config, "STORY_VIDEO_DIR", str(tmp_path / "story_video"))
    monkeypatch.setattr(Config, "STORY_LIBRARY_DIR", str(tmp_path / "story_library"))
    monkeypatch.setattr(Config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(Config, "OUTPUT_DRIVES", [])
    monkeypatch.setattr("src.processors.audio_utils.get_audio_duration", lambda _p: 9.0)
    monkeypatch.setattr("src.utils.pipeline_sv._queued_batch_ids", lambda: set(captured["queued"]))

    captured: dict = {"batches": [], "queued": []}

    class FakeRunner:
        """Thay StoryVideoBatchRunner: ghi progress.json nhu runner that, khong render."""

        def __init__(self, batch_id, story_configs, *, optimize_mode=False, output_subdir=None, output_drive=""):
            captured["batches"].append({"batchId": batch_id, "configs": story_configs, "optimize": optimize_mode,
                                         "subdir": output_subdir, "drive": output_drive})
            self.progress = {
                "batchId": batch_id, "status": "queued", "outputSubdir": output_subdir or batch_id,
                "stories": [{"storyId": c["story_id"], "status": "pending", "outputName": c["output_name"],
                             "input_value": c["input_value"]} for c in story_configs],
                "results": [],
            }

        def enqueue(self):
            story_video_pipeline._save_json(story_video_batch._batch_progress_path(self.progress["batchId"]), self.progress)
            captured["queued"].append(self.progress["batchId"])
            return 1

    monkeypatch.setattr(story_video_batch, "StoryVideoBatchRunner", FakeRunner)
    from src.utils.story_library import create_library

    library = create_library("01-05 thu vien test")
    dgt = FakeDgt()
    runner.set_client_overrides(_dgt=dgt, _tst=FakeTst())
    yield {"tmp": tmp_path, "dgt": dgt, "captured": captured, "library": library["id"]}
    runner.set_client_overrides()


def _set_story(batch_id, output_name, status, video=None, error=None):
    path = story_video_batch._batch_progress_path(batch_id)
    progress = json.load(open(path, encoding="utf-8"))
    story = next(s for s in progress["stories"] if s["outputName"] == output_name)
    story["status"] = status
    if status in ("completed", "failed"):
        result = {"storyId": story["storyId"], "status": status}
        if video:
            result["videoPath"] = video
        if error:
            result["error"] = error
        progress["results"].append(result)
    story_video_pipeline._save_json(path, progress)


def _make_run(env, count=2):
    sources = []
    for i in range(1, count + 1):
        path = env["tmp"] / "in" / f"Tap {i}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"Kịch bản {i}", encoding="utf-8")
        sources.append({"path": str(path), "type": 2})
    config = {"tts": {"voiceRef": "v"}, "type2": {"sharedConfig": {"libraryIds": [env["library"]], "subtitlePreset": "clean"}}}
    return runner.create_run("Loai 2", config, sources, run_id=store.new_run_id())


def _finish_tts(env, run_id):
    runner.tick()
    for job_id in list(env["dgt"].jobs):
        env["dgt"].finish(job_id, "1\n00:00:00,000 --> 00:00:09,000\nxin chao\n")
    runner.tick()  # TTS xong -> SRT_CHECK -> SV_RENDER -> run hook gui batch


def test_type2_items_go_out_in_one_batch_and_finish(env):
    run = _make_run(env)
    rid = run["runId"]
    _finish_tts(env, rid)

    batches = env["captured"]["batches"]
    assert len(batches) == 1
    batch = batches[0]
    assert batch["subdir"] == f"pipeline-{rid}" and batch["optimize"] is False
    assert [c["output_name"] for c in batch["configs"]] == ["tap-1", "tap-2"]
    first = batch["configs"][0]
    items = store.load_run(rid)["items"]
    assert first["input_value"] == items[0]["files"]["audio"]
    assert first["subtitle_path"] == items[0]["files"]["srt"]
    assert first["library_ids"] == [env["library"]]
    assert all(i["status"] == "running" and i["sv"]["batchId"] == batch["batchId"] for i in items)

    video_dir = env["tmp"] / "output" / "story-video" / f"pipeline-{rid}"
    video_dir.mkdir(parents=True)
    for name in ("tap-1", "tap-2"):
        (video_dir / f"{name}.mp4").write_bytes(b"mp4")
    _set_story(batch["batchId"], "tap-1", "completed", str(video_dir / "tap-1.mp4"))
    _set_story(batch["batchId"], "tap-2", "running")
    runner.tick()
    items = store.load_run(rid)["items"]
    assert items[0]["status"] == "done" and items[0]["files"]["video"].endswith("tap-1.mp4")
    assert items[1]["status"] == "running"

    _set_story(batch["batchId"], "tap-2", "completed", str(video_dir / "tap-2.mp4"))
    runner.tick()
    assert store.load_run(rid)["status"] == "completed"


def test_late_items_wait_for_queued_batch_then_form_next_batch(env):
    run = _make_run(env, count=1)
    _finish_tts(env, run["runId"])
    assert len(env["captured"]["batches"]) == 1
    # Them item moi vao cung run trong luc batch 1 con xep hang.
    second = env["tmp"] / "in" / "Tap 9.txt"
    second.write_text("chín", encoding="utf-8")

    def add_item(r):
        item = json.loads(json.dumps(r["items"][0]))
        item.update(itemId="it-02", index=2, slug="tap-9", sv={}, status="pending", stage="SV_RENDER", rev=0)
        r["items"].append(item)

    store.update_run(run["runId"], add_item)
    runner.tick()
    assert len(env["captured"]["batches"]) == 1  # batch 1 van dang cho
    env["captured"]["queued"].clear()
    runner.tick()
    assert len(env["captured"]["batches"]) == 2


def test_failed_story_resubmitted_once_then_fails(env):
    run = _make_run(env, count=1)
    rid = run["runId"]
    _finish_tts(env, rid)
    batch_id = env["captured"]["batches"][0]["batchId"]
    env["captured"]["queued"].clear()
    _set_story(batch_id, "tap-1", "failed", error="ffmpeg lỗi")
    runner.tick()
    assert len(env["captured"]["batches"]) == 2
    item = store.load_run(rid)["items"][0]
    assert item["sv"]["resubmits"] == 1
    env["captured"]["queued"].clear()
    _set_story(item["sv"]["batchId"], "tap-1", "failed", error="ffmpeg lỗi")
    runner.tick()
    item = store.load_run(rid)["items"][0]
    assert item["status"] == "failed" and item["error"]["code"] == "sv_render_failed"


def test_bad_library_needs_attention(env):
    run = _make_run(env, count=1)
    store.update_run(run["runId"], lambda r: r["config"]["type2"]["sharedConfig"].update(libraryIds=["khong-co"]))
    _finish_tts(env, run["runId"])
    item = store.load_run(run["runId"])["items"][0]
    assert item["status"] == "needs_attention" and item["error"]["code"] == "library_not_found"


def test_orphan_batch_is_reattached_after_crash(env, monkeypatch):
    run = _make_run(env, count=1)
    rid = run["runId"]
    _finish_tts(env, rid)
    batch_id = env["captured"]["batches"][0]["batchId"]
    # Gia lap: server chet truoc khi ghi batchId vao run.json.
    store.update_run(rid, lambda r: (r["items"][0].update(status="pending"), r["items"][0].update(sv={})))
    env["captured"]["queued"].clear()
    runner.tick()
    assert len(env["captured"]["batches"]) == 1  # khong gui trung
    assert store.load_run(rid)["items"][0]["sv"]["batchId"] == batch_id


def test_cancel_item_cancels_story_in_batch(env):
    run = _make_run(env, count=1)
    rid = run["runId"]
    _finish_tts(env, rid)
    runner.tick()  # lay storyId
    batch_id = env["captured"]["batches"][0]["batchId"]
    runner.cancel_item(rid, "it-01")
    progress = story_video_batch.load_batch_progress(batch_id)
    assert progress["stories"][0]["status"] == "cancelling"


def test_batch_create_accepts_json_body_and_output_subdir(env):
    from src.web_app import app

    audio = env["tmp"] / "a.mp3"
    audio.write_bytes(b"x")
    body = {"items": [{"inputType": "audio_file", "inputValue": str(audio), "outputName": "a"}],
            "sharedConfig": {"libraryIds": [env["library"]], "outputSubdir": "pipeline-pl-1"}}
    response = app.test_client().post("/api/story-video/batch/create", json=body)
    assert response.status_code == 202, response.get_json()
    assert env["captured"]["batches"][-1]["subdir"] == "pipeline-pl-1"

    body["sharedConfig"]["outputSubdir"] = "../thoat"
    bad = app.test_client().post("/api/story-video/batch/create", json=body)
    assert bad.status_code == 400 and bad.get_json()["error"]["code"] == "invalid_output_subdir"
    assert not os.path.exists(os.path.join(Config.STORY_VIDEO_DIR, "batches", "thoat"))


def test_manual_retry_renders_again_instead_of_reattaching_old_batch(env):
    run = _make_run(env, count=1)
    rid = run["runId"]
    _finish_tts(env, rid)
    batch_id = env["captured"]["batches"][0]["batchId"]
    video = env["tmp"] / "v.mp4"
    video.write_bytes(b"x")
    _set_story(batch_id, "tap-1", "completed", str(video))
    env["captured"]["queued"].clear()
    runner.tick()
    assert store.load_run(rid)["items"][0]["status"] == "done"
    runner.retry_item(rid, "it-01", "SV_RENDER")
    runner.tick()
    assert len(env["captured"]["batches"]) == 2
    assert store.load_run(rid)["items"][0]["sv"]["batchId"] != batch_id
