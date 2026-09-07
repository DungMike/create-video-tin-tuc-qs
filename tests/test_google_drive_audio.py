import json
import os
from pathlib import Path

import pytest

from src.config import Config
from src.utils import google_drive_audio

FOLDER_URL = "https://drive.google.com/drive/folders/1Pkb-Lov4fGWElreJ2Ap1M1yB0VXe_j57"


class FakeResponse:
    def __init__(self, *, text: str = "", content: bytes = b"", content_type: str = "text/html"):
        self.text = text
        self._content = content
        self.headers = {
            "content-type": content_type,
            "content-length": str(len(content)),
        }

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self._content), chunk_size):
            yield self._content[offset:offset + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeSession:
    def __init__(self, folder_html: str, files: dict[str, bytes]):
        self.folder_html = folder_html
        self.files = files

    def get(self, url: str, **_kwargs):
        if url == FOLDER_URL:
            return FakeResponse(text=self.folder_html)
        file_id = url.split("id=", 1)[1].split("&", 1)[0]
        return FakeResponse(content=self.files[file_id], content_type="audio/mpeg")


@pytest.fixture
def drive_import_storage(tmp_path, monkeypatch):
    story_dir = tmp_path / "story_video"
    import_dir = story_dir / "drive_audio_imports"
    monkeypatch.setattr(Config, "STORY_VIDEO_DIR", str(story_dir))
    monkeypatch.setattr(Config, "STORY_DRIVE_AUDIO_IMPORT_DIR", str(import_dir))
    monkeypatch.setattr(Config, "STORY_DRIVE_AUDIO_IMPORT_TTL_SECONDS", 86400)
    monkeypatch.setattr(Config, "STORY_DRIVE_AUDIO_MAX_FILES", 100)
    monkeypatch.setattr(Config, "STORY_DRIVE_AUDIO_MAX_TOTAL_MB", 4096)
    return story_dir, import_dir


def _complete_staging_session(session_id: str = "gda-a1b2c3d4") -> tuple[str, Path]:
    token = f"{session_id}.abcdefghijklmnop"
    google_drive_audio.create_drive_audio_import_session(FOLDER_URL, session_id=session_id)
    source_path = Path(Config.STORY_DRIVE_AUDIO_IMPORT_DIR) / session_id / "0000_kenh-1-vid-1.mp3"
    source_path.write_bytes(b"test-audio")
    manifest = google_drive_audio.load_drive_audio_import(session_id)
    assert manifest
    manifest.update({
        "status": "completed",
        "current": 1,
        "total": 1,
        "items": [{
            "token": token,
            "fileName": "kenh-1-vid-1.mp3",
            "outputName": "kenh-1-vid-1",
            "durationSeconds": 10.0,
            "sizeBytes": source_path.stat().st_size,
            "storedFileName": source_path.name,
        }],
    })
    google_drive_audio._save_manifest(session_id, manifest)
    return token, source_path


def test_parse_google_drive_folder_id_accepts_only_supported_folder_links():
    assert google_drive_audio.parse_google_drive_folder_id(FOLDER_URL) == "1Pkb-Lov4fGWElreJ2Ap1M1yB0VXe_j57"
    assert google_drive_audio.parse_google_drive_folder_id(f"{FOLDER_URL}?usp=sharing") == "1Pkb-Lov4fGWElreJ2Ap1M1yB0VXe_j57"
    assert google_drive_audio.parse_google_drive_folder_id("https://example.com/drive/folders/abc") is None
    assert google_drive_audio.parse_google_drive_folder_id("https://drive.google.com/file/d/abc") is None


def test_parse_folder_entries_deduplicates_and_skips_non_audio_and_nested_folder():
    html = """
    <div aria-label="episode.mp3 Audio Shared"><div data-id="audio-id"><strong>episode.mp3</strong></div></div>
    <div data-id="audio-id" data-tooltip="episode.mp3 Audio"><strong>episode.mp3</strong></div>
    <div data-id="notes-id"><strong>notes.txt</strong></div>
    <div aria-label="nested Folder"><div data-id="folder-id"><strong>nested</strong></div></div>
    """

    entries = google_drive_audio._parse_folder_entries(html)
    audio_entries, skipped = google_drive_audio._select_audio_entries(entries)

    assert audio_entries == [{
        "fileId": "audio-id",
        "fileName": "episode.mp3",
        "typeHint": "episode.mp3 Audio Shared",
    }]
    assert skipped == [
        {"fileName": "notes.txt", "reason": "Dinh dang khong phai audio ho tro."},
        {"fileName": "nested", "reason": "Bo qua thu muc con."},
    ]


def test_run_drive_audio_import_downloads_supported_direct_children(drive_import_storage, monkeypatch):
    html = """
    <div data-id="audio-id"><strong>episode.mp3</strong></div>
    <div data-id="text-id"><strong>notes.txt</strong></div>
    <div aria-label="nested Folder"><div data-id="folder-id"><strong>nested</strong></div></div>
    """
    session = FakeSession(html, {"audio-id": b"audio-content"})
    monkeypatch.setattr(google_drive_audio, "get_audio_duration", lambda _path: 12.3456)
    manifest = google_drive_audio.create_drive_audio_import_session(FOLDER_URL, session_id="gda-11111111")

    result = google_drive_audio.run_drive_audio_import(manifest["sessionId"], FOLDER_URL, http_session=session)
    public_result = google_drive_audio.public_drive_audio_import(result)

    assert result["status"] == "completed"
    assert public_result["items"] == [{
        "token": result["items"][0]["token"],
        "fileName": "episode.mp3",
        "outputName": "episode",
        "durationSeconds": 12.346,
        "sizeBytes": 13,
    }]
    assert "storedFileName" not in public_result["items"][0]
    assert public_result["skipped"] == [
        {"fileName": "notes.txt", "reason": "Dinh dang khong phai audio ho tro."},
        {"fileName": "nested", "reason": "Bo qua thu muc con."},
    ]


def test_resolve_staged_audio_token_rejects_traversal(drive_import_storage):
    token, source_path = _complete_staging_session()

    assert google_drive_audio.resolve_staged_audio_token(token)["sourcePath"] == str(source_path)

    manifest = google_drive_audio.load_drive_audio_import("gda-a1b2c3d4")
    assert manifest
    manifest["items"][0]["storedFileName"] = "../escape.mp3"
    google_drive_audio._save_manifest("gda-a1b2c3d4", manifest)

    with pytest.raises(google_drive_audio.DriveAudioImportError, match="staging path"):
        google_drive_audio.resolve_staged_audio_token(token)


def test_cleanup_expired_drive_audio_imports(drive_import_storage):
    google_drive_audio.create_drive_audio_import_session(FOLDER_URL, session_id="gda-aaaaaaaa")
    google_drive_audio.create_drive_audio_import_session(FOLDER_URL, session_id="gda-bbbbbbbb")
    expired_dir = Path(Config.STORY_DRIVE_AUDIO_IMPORT_DIR) / "gda-aaaaaaaa"
    expired_manifest = expired_dir / "manifest.json"
    os.utime(expired_manifest, (10, 10))
    os.utime(expired_dir, (10, 10))

    removed = google_drive_audio.cleanup_expired_drive_audio_imports(now=100, ttl_seconds=20)

    assert removed == ["gda-aaaaaaaa"]
    assert not expired_dir.exists()
    assert (Path(Config.STORY_DRIVE_AUDIO_IMPORT_DIR) / "gda-bbbbbbbb").exists()


def test_story_batch_create_copies_drive_audio_before_starting_runner(drive_import_storage, monkeypatch):
    from src.utils import story_video_batch
    from src.web_app import app

    token, _source_path = _complete_staging_session()
    captured: dict = {}

    class FakeRunner:
        def __init__(self, batch_id: str, story_configs: list[dict], **kwargs):
            captured["batchId"] = batch_id
            captured["storyConfigs"] = story_configs
            captured["kwargs"] = kwargs

        def start_async(self):
            captured["started"] = True

    monkeypatch.setattr(story_video_batch, "StoryVideoBatchRunner", FakeRunner)
    client = app.test_client()
    response = client.post(
        "/api/story-video/batch/create",
        data={"payload": json.dumps({
            "items": [{
                "id": "batch_item_1",
                "inputType": "drive_audio",
                "inputValue": token,
                "outputName": "kenh-1-vid-1",
            }],
            "sharedConfig": {"clipTags": []},
        })},
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    assert captured["started"] is True
    config = captured["storyConfigs"][0]
    assert config["input_type"] == "audio_file"
    assert config["output_name"] == "kenh-1-vid-1"
    assert Path(config["input_value"]).is_file()
    assert Path(config["input_value"]).name == "drive_audio_0_kenh-1-vid-1.mp3"
