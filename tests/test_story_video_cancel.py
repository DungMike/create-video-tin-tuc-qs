import sys
import time
from pathlib import Path

import pytest

from src.config import Config
from src.utils import story_video_batch, story_video_pipeline
from src.utils.ffmpeg_helper import FFmpegHelper


@pytest.fixture
def story_storage(tmp_path, monkeypatch):
    story_dir = tmp_path / "story_video"
    output_dir = tmp_path / "output"
    library_dir = tmp_path / "story_library"
    monkeypatch.setattr(Config, "STORY_VIDEO_DIR", str(story_dir))
    monkeypatch.setattr(Config, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(Config, "STORY_LIBRARY_DIR", str(library_dir))
    return story_dir


def _write_batch_progress(batch_id: str, stories: list[dict], status: str = "running"):
    story_video_pipeline._save_json(story_video_batch._batch_progress_path(batch_id), {
        "batchId": batch_id,
        "status": status,
        "total": len(stories),
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "current": 0,
        "percent": 0,
        "message": "Dang xu ly...",
        "stories": stories,
        "results": [],
    })


def test_story_pipeline_stops_before_work_when_cancel_marker_exists(story_storage):
    story_id = "sv-cancelled"
    story_video_pipeline.request_story_cancel(story_id)
    runner = story_video_pipeline.StoryVideoPipelineRunner(story_id, {
        "input_type": "audio_file",
        "input_value": "missing.mp3",
        "output_name": "cancelled",
    })

    assert runner.run() is None
    assert story_video_pipeline.load_story_progress(story_id)["status"] == "cancelled"


def test_ffmpeg_helper_kills_active_process_when_cancel_requested():
    calls = 0

    def cancel_callback():
        nonlocal calls
        calls += 1
        return calls >= 2

    started_at = time.monotonic()
    result = FFmpegHelper.run_command(
        [
            sys.executable,
            "-c",
            "import time; print('out_time_ms=1000', flush=True); time.sleep(10)",
        ],
        progress_callback=lambda _payload: None,
        progress_total_seconds=10,
        cancel_callback=cancel_callback,
    )

    assert result is False
    assert time.monotonic() - started_at < 3


def test_batch_runner_skips_cancelled_item_and_continues(story_storage, monkeypatch):
    story_video_pipeline.request_story_cancel("sv-one")
    started_story_ids: list[str] = []

    class FakePipelineRunner:
        def __init__(self, story_id: str, _config: dict, **_kwargs):
            started_story_ids.append(story_id)

        def run(self):
            return "completed.mp4"

    monkeypatch.setattr(story_video_batch, "StoryVideoPipelineRunner", FakePipelineRunner)
    runner = story_video_batch.StoryVideoBatchRunner("sb-runner", [
        {"story_id": "sv-one", "output_name": "one"},
        {"story_id": "sv-two", "output_name": "two"},
    ])

    runner._run_batch()

    progress = story_video_batch.load_batch_progress("sb-runner")
    assert started_story_ids == ["sv-two"]
    assert progress["stories"][0]["status"] == "cancelled"
    assert progress["stories"][1]["status"] == "completed"
    assert progress["completed"] == 1
    assert progress["cancelled"] == 1


def test_cancel_batch_item_api_marks_only_requested_story(story_storage):
    from src.web_app import app

    _write_batch_progress("sb-item", [
        {"storyId": "sv-one", "status": "running"},
        {"storyId": "sv-two", "status": "pending"},
    ])

    response = app.test_client().post("/api/story-video/batch/sb-item/items/sv-two/cancel")

    assert response.status_code == 200
    progress = story_video_batch.load_batch_progress("sb-item")
    assert progress["stories"][0]["status"] == "running"
    assert progress["stories"][1]["status"] == "cancelling"
    assert story_video_pipeline.is_story_cancel_requested("sv-two") is True


def test_cancel_batch_api_marks_all_incomplete_stories(story_storage):
    from src.web_app import app

    _write_batch_progress("sb-all", [
        {"storyId": "sv-one", "status": "running"},
        {"storyId": "sv-two", "status": "pending"},
        {"storyId": "sv-three", "status": "completed"},
    ])

    response = app.test_client().post("/api/story-video/batch/sb-all/cancel")

    assert response.status_code == 200
    progress = story_video_batch.load_batch_progress("sb-all")
    assert progress["status"] == "cancelling"
    assert [story["status"] for story in progress["stories"]] == ["cancelling", "cancelling", "completed"]
    assert story_video_pipeline.is_story_cancel_requested("sv-one") is True
    assert story_video_pipeline.is_story_cancel_requested("sv-two") is True
    assert story_video_pipeline.is_story_cancel_requested("sv-three") is False
