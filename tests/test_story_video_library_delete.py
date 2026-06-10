import json
from pathlib import Path

import pytest

from src.config import Config


def _asset(clip_id: str) -> dict:
    return {
        "id": clip_id,
        "source_type": "pixabay",
        "source_name": f"{clip_id}.mp4",
        "relative_path": f"clips/{clip_id}.mp4",
        "duration": 5.0,
        "tags": ["test"],
        "created_at": "2026-06-05T00:00:00+00:00",
    }


def _write_library(library_dir: Path, clip_ids: list[str]) -> dict[str, Path]:
    clips_dir = library_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for clip_id in clip_ids:
        clip_path = clips_dir / f"{clip_id}.mp4"
        clip_path.write_bytes(clip_id.encode("utf-8"))
        paths[clip_id] = clip_path
    (library_dir / "index.json").write_text(
        json.dumps({"assets": [_asset(clip_id) for clip_id in clip_ids]}),
        encoding="utf-8",
    )
    return paths


def _read_assets(library_dir: Path) -> list[dict]:
    data = json.loads((library_dir / "index.json").read_text(encoding="utf-8"))
    return data["assets"]


@pytest.fixture
def story_library(tmp_path, monkeypatch):
    library_dir = tmp_path / "story_library"
    monkeypatch.setattr(Config, "STORY_LIBRARY_DIR", str(library_dir))

    from src.routes import story_video_routes

    with story_video_routes._download_sessions_lock:
        story_video_routes._download_sessions.clear()
    yield library_dir
    with story_video_routes._download_sessions_lock:
        story_video_routes._download_sessions.clear()


@pytest.fixture
def client():
    from src.web_app import app

    return app.test_client()


def test_bulk_delete_ids_only_removes_requested_clips(story_library, client):
    paths = _write_library(story_library, ["one", "two", "three"])

    response = client.post(
        "/api/story-video/library/bulk-delete",
        json={"scope": "ids", "clipIds": ["one", "missing", "one"]},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "scope": "ids",
        "requestedCount": 2,
        "deletedCount": 1,
        "remainingCount": 2,
        "missingClipIds": ["missing"],
        "failedClipIds": [],
        "failedFiles": [],
    }
    assert paths["one"].exists() is False
    assert paths["two"].exists() is True
    assert paths["three"].exists() is True
    assert [asset["id"] for asset in _read_assets(story_library)] == ["two", "three"]


def test_bulk_delete_all_clears_index_and_orphan_files(story_library, client):
    paths = _write_library(story_library, ["one", "two"])
    orphan_path = story_library / "clips" / "orphan.mp4"
    orphan_path.write_bytes(b"orphan")

    response = client.post("/api/story-video/library/bulk-delete", json={"scope": "all"})

    assert response.status_code == 200
    assert response.get_json() == {
        "scope": "all",
        "requestedCount": 2,
        "deletedCount": 2,
        "remainingCount": 0,
        "missingClipIds": [],
        "failedClipIds": [],
        "failedFiles": [],
    }
    assert all(path.exists() is False for path in paths.values())
    assert orphan_path.exists() is False
    assert _read_assets(story_library) == []


def test_bulk_delete_all_rejects_active_import_session(story_library, client):
    paths = _write_library(story_library, ["one"])
    from src.routes import story_video_routes

    with story_video_routes._download_sessions_lock:
        story_video_routes._download_sessions["imp-test"] = {"status": "downloading"}

    response = client.post("/api/story-video/library/bulk-delete", json={"scope": "all"})

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "library_busy"
    assert paths["one"].exists() is True
    assert [asset["id"] for asset in _read_assets(story_library)] == ["one"]


def test_bulk_delete_keeps_asset_when_file_cannot_be_removed(story_library, client, monkeypatch):
    paths = _write_library(story_library, ["one", "two"])
    from src.utils import story_library as story_library_utils

    original_remove = story_library_utils.remove_file_with_retries
    monkeypatch.setattr(
        story_library_utils,
        "remove_file_with_retries",
        lambda path: False if path.endswith("one.mp4") else original_remove(path),
    )

    response = client.post(
        "/api/story-video/library/bulk-delete",
        json={"scope": "ids", "clipIds": ["one", "two"]},
    )

    assert response.status_code == 200
    assert response.get_json()["failedClipIds"] == ["one"]
    assert response.get_json()["deletedCount"] == 1
    assert paths["one"].exists() is True
    assert paths["two"].exists() is False
    assert [asset["id"] for asset in _read_assets(story_library)] == ["one"]


@pytest.mark.parametrize(
    "payload,error_code",
    [
        (None, "invalid_payload"),
        ({"scope": "ids"}, "invalid_clip_ids"),
        ({"scope": "ids", "clipIds": []}, "invalid_clip_ids"),
        ({"scope": "unknown"}, "invalid_scope"),
    ],
)
def test_bulk_delete_validates_payload(story_library, client, payload, error_code):
    _write_library(story_library, ["one"])

    response = client.post("/api/story-video/library/bulk-delete", json=payload)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == error_code


def test_existing_single_delete_route_uses_shared_delete_logic(story_library, client):
    paths = _write_library(story_library, ["one", "two"])

    response = client.delete("/api/story-video/library/one")

    assert response.status_code == 200
    assert response.get_json() == {"deleted": True, "clipId": "one"}
    assert paths["one"].exists() is False
    assert [asset["id"] for asset in _read_assets(story_library)] == ["two"]
