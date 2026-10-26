"""API /api/pipeline/* qua Flask test client (client ngoai la ban gia)."""

import io
import json
import os

import pytest

from pipeline_fakes import FakeDgt, FakeTst
from src.config import Config
from src.utils import pipeline_runner as runner
from src.web_app import app

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "pipeline")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DIR", str(tmp_path / "pipeline"))
    monkeypatch.setattr(Config, "STORAGE_DIR", str(tmp_path))
    dgt, tst = FakeDgt(), FakeTst()
    runner.set_client_overrides(_dgt=dgt, _tst=tst)
    with app.test_client() as test_client:
        yield test_client
    runner.set_client_overrides()


CONFIG = {"tts": {"voiceRef": "app-clone:1"}, "type2": {"sharedConfig": {"libraryIds": ["lib-1"]}}}


def test_create_run_multipart_upload(client, tmp_path):
    docx = open(os.path.join(FIXTURES, "basic.docx"), "rb").read()
    payload = {"name": "Đợt 1", "config": CONFIG,
               "items": [{"source": "upload:0", "type": 2}, {"source": "upload:1", "type": 2}]}
    response = client.post("/api/pipeline/runs", data={
        "payload": json.dumps(payload),
        "files": [(io.BytesIO(docx), "Tập 1.docx"), (io.BytesIO("Xin chào".encode()), "Tập 2.txt")],
    }, content_type="multipart/form-data")
    assert response.status_code == 201, response.get_json()
    run = response.get_json()
    assert run["name"] == "Đợt 1"
    assert [i["title"] for i in run["items"]] == ["Tập 1", "Tập 2"]
    assert all(i["stage"] == "TTS" for i in run["items"])
    assert os.path.isfile(run["items"][0]["source"]["path"])
    assert "uploads" in run["items"][0]["source"]["path"]

    listing = client.get("/api/pipeline/runs").get_json()["runs"]
    assert listing[0]["runId"] == run["runId"] and listing[0]["counts"]["total"] == 2
    detail = client.get(f"/api/pipeline/runs/{run['runId']}").get_json()
    assert detail["items"][0]["media"]["audioUrl"] is None
    events = client.get(f"/api/pipeline/runs/{run['runId']}/events?since=0").get_json()
    assert len(events["events"]) == 2 and events["next"] == 2


def test_create_run_from_folder_path(client, tmp_path):
    folder = tmp_path / "kich-ban"
    folder.mkdir()
    (folder / "Tap 2.txt").write_text("hai", encoding="utf-8")
    (folder / "Tap 1.txt").write_text("một", encoding="utf-8")
    scan = client.post("/api/pipeline/scan", json={"path": str(folder)}).get_json()
    assert [f["name"] for f in scan["files"]] == ["Tap 1.txt", "Tap 2.txt"]

    response = client.post("/api/pipeline/runs", json={
        "config": {**CONFIG, "defaultType": 2}, "folderPath": str(folder),
    })
    assert response.status_code == 201
    assert [i["slug"] for i in response.get_json()["items"]] == ["tap-1", "tap-2"]


def test_create_run_errors_leave_nothing_behind(client, tmp_path):
    response = client.post("/api/pipeline/runs", data={
        "payload": json.dumps({"config": {"tts": {}}, "items": [{"source": "upload:0", "type": 2}]}),
        "files": [(io.BytesIO(b"x"), "a.txt")],
    }, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "voice_required"
    runs_root = tmp_path / "pipeline" / "runs"
    assert not runs_root.exists() or not any(runs_root.iterdir())

    bad = client.post("/api/pipeline/runs", json={"config": CONFIG, "items": [{"source": "C:/khong/co.txt"}]})
    assert bad.status_code == 404
    wrong = client.post("/api/pipeline/runs", data={
        "payload": json.dumps({"config": CONFIG}), "files": [(io.BytesIO(b"x"), "a.pdf")],
    }, content_type="multipart/form-data")
    assert wrong.status_code == 400 and wrong.get_json()["error"]["code"] == "unsupported_file"


def test_scan_missing_folder(client, tmp_path):
    response = client.post("/api/pipeline/scan", json={"path": str(tmp_path / "x")})
    assert response.status_code == 404


def test_run_actions(client, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("xin chào", encoding="utf-8")
    run = client.post("/api/pipeline/runs", json={"config": CONFIG, "items": [{"source": str(src), "type": 2}]}).get_json()
    rid = run["runId"]
    assert client.post(f"/api/pipeline/runs/{rid}/pause").get_json()["status"] == "paused"
    assert client.post(f"/api/pipeline/runs/{rid}/resume").get_json()["status"] == "running"
    retry = client.post(f"/api/pipeline/runs/{rid}/items/it-01/retry", json={})
    assert retry.status_code == 409  # dang cho chay
    cancelled = client.post(f"/api/pipeline/runs/{rid}/cancel").get_json()
    assert cancelled["status"] == "cancelled" and cancelled["items"][0]["status"] == "cancelled"
    assert client.get("/api/pipeline/runs/pl-khongco").status_code == 404


def test_health_and_proxies(client, monkeypatch):
    class DownDgt:
        def __init__(self, *a, **k):
            pass

        def health(self):
            from src.utils.pipeline_clients import ServiceUnavailable
            raise ServiceUnavailable("dgt", "api_down")

        voices = health

    monkeypatch.setattr("src.routes.pipeline_routes.DgtClient", DownDgt)
    monkeypatch.setattr("src.routes.pipeline_routes.TstClient", lambda *a, **k: FakeTst())
    body = client.get("/api/pipeline/health").get_json()
    assert body["services"]["dgt"]["ok"] is False
    assert body["services"]["tst"] == {"online": True, "loggedIn": True}
    assert body["services"]["claude"]["found"] is True
    assert client.get("/api/pipeline/tts/voices").status_code == 503
    projects = client.get("/api/pipeline/tst/projects").get_json()["projects"]
    assert projects[0]["id"] == "demo"


def test_item_file_endpoint_serves_only_recorded_files(client, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("xin chào", encoding="utf-8")
    run = client.post("/api/pipeline/runs", json={"config": CONFIG, "items": [{"source": str(src), "type": 2}]}).get_json()
    rid = run["runId"]
    assert run["items"][0]["media"]["txtUrl"] == f"/api/pipeline/runs/{rid}/items/it-01/files/txt"
    body = client.get(run["items"][0]["media"]["txtUrl"])
    assert body.status_code == 200 and body.data.decode("utf-8-sig") == "xin chào"
    assert client.get(f"/api/pipeline/runs/{rid}/items/it-01/files/audio").status_code == 404
    assert client.get(f"/api/pipeline/runs/{rid}/items/it-01/files/..%2Frun.json").status_code == 404


def test_google_docs_link_becomes_txt_item(client, monkeypatch):
    monkeypatch.setattr("src.utils.tts_audio.download_google_doc_text", lambda url: "Nội dung từ Google Docs.")
    response = client.post("/api/pipeline/runs", json={
        "config": CONFIG,
        "items": [{"source": "https://docs.google.com/document/d/abc123/edit", "type": 2, "title": "Tập GDoc"}],
    })
    assert response.status_code == 201, response.get_json()
    item = response.get_json()["items"][0]
    assert item["title"] == "Tập GDoc" and item["stage"] == "TTS"
    assert open(item["files"]["txt"], "rb").read().decode("utf-8-sig") == "Nội dung từ Google Docs."


def test_presets_crud_and_run_uses_preset(client):
    bad = client.post("/api/pipeline/presets", json={"name": "x", "sharedConfig": {}})
    assert bad.status_code == 400
    created = client.post("/api/pipeline/presets", json={
        "name": "Kênh Thái", "sharedConfig": {"libraryIds": ["lib-9"], "waveformOverlayIds": ["w1"], "optimizeMode": True},
    })
    assert created.status_code == 201
    preset = created.get_json()
    assert "optimizeMode" not in preset["sharedConfig"]
    assert client.get("/api/pipeline/presets").get_json()["presets"][0]["id"] == preset["id"]

    import tempfile
    src = os.path.join(tempfile.mkdtemp(), "a.txt")
    open(src, "w", encoding="utf-8").write("xin chào")
    run = client.post("/api/pipeline/runs", json={
        "config": {"tts": {"voiceRef": "v"}, "type2": {"presetId": preset["id"]}},
        "items": [{"source": src, "type": 2}],
    }).get_json()
    assert run["config"]["type2"]["sharedConfig"]["waveformOverlayIds"] == ["w1"]
    assert run["config"]["type2"]["presetName"] == "Kênh Thái"
    assert client.delete(f"/api/pipeline/presets/{preset['id']}").status_code == 200
    missing = client.post("/api/pipeline/runs", json={
        "config": {"tts": {"voiceRef": "v"}, "type2": {"presetId": preset["id"]}},
        "items": [{"source": src, "type": 2}],
    })
    assert missing.status_code == 404


SRT = "1\n00:00:00,000 --> 00:00:09,000\nXin chào\n"
TYPE2_ONLY = {"tts": {"voiceRef": ""}, "type2": {"sharedConfig": {"libraryIds": ["lib-1"]}}}


def test_upload_audio_and_srt_skips_tts_and_needs_no_voice(client):
    payload = {"config": TYPE2_ONLY, "items": [{"source": "upload:0", "srt": "upload:1", "type": 2}]}
    response = client.post("/api/pipeline/runs", data={
        "payload": json.dumps(payload),
        "files": [(io.BytesIO(b"ID3fake"), "Tập 5.mp3"), (io.BytesIO(SRT.encode()), "Tập 5.srt")],
    }, content_type="multipart/form-data")
    assert response.status_code == 201, response.get_json()
    item = response.get_json()["items"][0]
    assert item["input"] == "media" and item["stage"] == "SRT_CHECK" and item["status"] == "pending"
    assert item["title"] == "Tập 5" and item["slug"] == "tap-5"
    assert item["files"]["txt"] is None and "tts" not in item
    assert item["files"]["audio"].endswith("01_Tập 5.mp3") and item["files"]["srt"].endswith("02_Tập 5.srt")
    assert item["source"]["srtName"] == "Tập 5.srt"
    assert item["media"]["txtUrl"] is None and item["media"]["audioUrl"].endswith("/files/audio")


def test_media_mode_scan_pairs_audio_with_srt(client, tmp_path):
    folder = tmp_path / "media"
    (folder / "con").mkdir(parents=True)
    (folder / "Tap 2.mp3").write_bytes(b"a")
    (folder / "tap 2.SRT").write_text(SRT, encoding="utf-8")
    (folder / "Tap 2.wav").write_bytes(b"a")
    (folder / "Tap 1.wav").write_bytes(b"a")
    (folder / "Tap 1.srt").write_text(SRT, encoding="utf-8")
    (folder / "Tap 3.m4a").write_bytes(b"a")
    (folder / "Le.srt").write_text(SRT, encoding="utf-8")
    (folder / "Tap 4.txt").write_text("kịch bản", encoding="utf-8")
    (folder / "con" / "Tap 9.mp3").write_bytes(b"a")
    (folder / "con" / "Tap 9.srt").write_text(SRT, encoding="utf-8")

    scan = client.post("/api/pipeline/scan", json={"path": str(folder), "mode": "media"}).get_json()
    files = {f["name"]: f for f in scan["files"]}
    assert list(files) == ["Tap 1.wav", "Tap 2.mp3", "Tap 2.wav", "Tap 3.m4a"]
    assert files["Tap 1.wav"]["valid"] and files["Tap 1.wav"]["srtName"] == "Tap 1.srt"
    assert files["Tap 2.mp3"]["valid"] and files["Tap 2.mp3"]["srtName"] == "tap 2.SRT"
    assert not files["Tap 2.wav"]["valid"] and "Trùng tên" in files["Tap 2.wav"]["warning"]
    assert not files["Tap 3.m4a"]["valid"] and files["Tap 3.m4a"]["srtPath"] is None
    assert scan["warnings"] == ["Le.srt: không có file audio cùng tên"]
    deep = client.post("/api/pipeline/scan", json={"path": str(folder), "mode": "media", "recursive": True}).get_json()
    assert "Tap 9.mp3" in [f["name"] for f in deep["files"]]
    # Che do mac dinh van chi thay kich ban.
    assert [f["name"] for f in client.post("/api/pipeline/scan", json={"path": str(folder)}).get_json()["files"]] == ["Tap 4.txt"]

    response = client.post("/api/pipeline/runs", json={"config": TYPE2_ONLY, "folderPath": str(folder), "mode": "media"})
    assert response.status_code == 201, response.get_json()
    items = response.get_json()["items"]
    assert [i["slug"] for i in items] == ["tap-1", "tap-2"]
    assert items[1]["files"]["audio"] == str(folder / "Tap 2.mp3") and items[1]["source"]["uploaded"] is False


def test_media_items_validation(client, tmp_path):
    audio, srt, txt = tmp_path / "a.mp3", tmp_path / "a.srt", tmp_path / "b.txt"
    audio.write_bytes(b"a")
    srt.write_text(SRT, encoding="utf-8")
    txt.write_text("xin chào", encoding="utf-8")

    def create(items, config=TYPE2_ONLY):
        return client.post("/api/pipeline/runs", json={"config": config, "items": items})

    no_srt = create([{"source": str(audio), "type": 2}])
    assert no_srt.status_code == 400 and no_srt.get_json()["error"]["code"] == "unsupported_file"
    swapped = create([{"source": str(srt), "srt": str(audio), "type": 2}])
    assert swapped.status_code == 400 and swapped.get_json()["error"]["code"] == "unsupported_file"
    missing = create([{"source": str(audio), "srt": str(tmp_path / "x.srt"), "type": 2}])
    assert missing.status_code == 404
    # Run tron kich ban + audio co san van can giong doc cho phan kich ban.
    mixed = create([{"source": str(audio), "srt": str(srt), "type": 2}, {"source": str(txt), "type": 2}])
    assert mixed.status_code == 400 and mixed.get_json()["error"]["code"] == "voice_required"
    ok = create([{"source": str(audio), "srt": str(srt), "type": 2}, {"source": str(txt), "type": 2}], CONFIG)
    assert ok.status_code == 201
    assert [(i["input"], i["stage"]) for i in ok.get_json()["items"]] == [("media", "SRT_CHECK"), ("script", "TTS")]
