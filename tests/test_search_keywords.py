"""Search-keyword history: recorded passively, skipped only when the user opts in."""

import pytest

from src.config import Config
from src.db import background_writer, keyword_repo, mongo
from src.utils import story_bulk_harvest


@pytest.fixture
def raw_env(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "STORY_RAW_DIR", str(tmp_path / "story_raw_videos"))
    monkeypatch.setattr(Config, "STORY_LIBRARY_DIR", str(tmp_path / "story_library"))
    monkeypatch.setattr(Config, "PIXABAY_API_KEY", "test-pixabay-key")
    monkeypatch.setattr(Config, "PEXELS_API_KEYS", ["test-pexels-key"])
    return tmp_path


def _run_harvest(**kwargs) -> dict:
    """Start a harvest job and wait for its worker thread to finish."""
    import time

    progress = story_bulk_harvest.start_harvest_job(library_id="default", **kwargs)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = story_bulk_harvest.load_harvest_progress(progress["jobId"]) or {}
        if current.get("status") in story_bulk_harvest.TERMINAL_HARVEST_STATUSES:
            return current
        time.sleep(0.02)
    raise AssertionError("harvest worker did not finish")


@pytest.fixture
def harvest_calls(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_search(provider, keyword, page, per_page=None, **_kwargs):
        calls.append((provider, keyword))
        return {"items": [], "perPage": 200, "total": 0, "page": page, "provider": provider}

    monkeypatch.setattr(story_bulk_harvest, "search_provider_videos", fake_search)
    return calls


def _wait_writes():
    assert background_writer.get_background_writer().wait_idle(5)


def test_normalize_keyword():
    assert keyword_repo.normalize_keyword("  Thai   BEACH \n") == "thai beach"
    # NFC: a decomposed "é" matches the precomposed one.
    assert keyword_repo.normalize_keyword("Cafe\u0301") == keyword_repo.normalize_keyword("Caf\u00e9")


def test_status_only_moves_up(mongo_db):
    keyword_repo.record_keyword_sweep("pexels", "Thai beach", "completed", "harvest", results_total=80)
    keyword_repo.record_keyword_sweep("pexels", "thai  beach", "partial", "prefetch", results_total=10)

    doc = mongo_db[mongo.SEARCH_KEYWORDS].find_one({"_id": "pexels:thai beach"})
    assert doc["status"] == "completed" and doc["last_status"] == "partial"
    assert doc["sweep_count"] == 2 and doc["results_total"] == 80
    assert sorted(doc["flows"]) == ["harvest", "prefetch"]


def test_find_used_pairs_is_disabled_without_mongo():
    assert keyword_repo.find_used_keyword_pairs(["pexels"], ["x"]) == ([], "disabled")


def test_harvest_skips_used_pairs_only_when_opted_in(raw_env, harvest_calls, mongo_db):
    keyword_repo.record_keyword_sweep("pexels", "Thai beach", "completed", "harvest")
    keyword_repo.record_keyword_sweep("pixabay", "Thai beach", "partial", "harvest")

    progress = _run_harvest(
        keywords=["Thai Beach", "Bangkok"], providers=["pixabay", "pexels"], skip_used_keywords=True,
    )
    _wait_writes()

    # pexels already swept "thai beach" to the end; the partial pixabay sweep is retried.
    assert ("pexels", "Thai Beach") not in harvest_calls
    assert set(harvest_calls) == {("pixabay", "Thai Beach"), ("pixabay", "Bangkok"), ("pexels", "Bangkok")}
    assert [(p["provider"], p["keyword"]) for p in progress["skippedPairs"]] == [("pexels", "Thai Beach")]
    assert progress["keywordCheck"] == "ok"
    statuses = {doc["_id"]: doc["status"] for doc in mongo_db[mongo.SEARCH_KEYWORDS].find()}
    assert statuses == {
        "pexels:thai beach": "completed",
        "pixabay:thai beach": "completed",
        "pixabay:bangkok": "completed",
        "pexels:bangkok": "completed",
    }


def test_harvest_default_searches_everything(raw_env, harvest_calls, mongo_db):
    keyword_repo.record_keyword_sweep("pexels", "Thai beach", "completed", "harvest")

    progress = _run_harvest(keywords=["Thai Beach"], providers=["pexels"])

    assert harvest_calls == [("pexels", "Thai Beach")]
    assert "skippedPairs" not in progress and "skipUsedKeywords" not in progress


def test_harvest_refuses_when_every_pair_is_used(raw_env, harvest_calls, mongo_db):
    keyword_repo.record_keyword_sweep("pexels", "Thai beach", "limited", "harvest")

    with pytest.raises(ValueError):
        story_bulk_harvest.start_harvest_job(
            keywords=["thai beach"], providers=["pexels"], library_id="default",
            skip_used_keywords=True,
        )
    assert harvest_calls == []


def test_harvest_records_limited_when_max_per_keyword_stops_it(raw_env, monkeypatch, mongo_db):
    def fake_search(provider, keyword, page, per_page=None, **_kwargs):
        return {"items": [{"id": str(i), "previewUrl": f"https://x/{i}.mp4", "width": 1920, "height": 1080}
                          for i in range(1, 4)], "perPage": 3, "total": 300, "page": page}

    monkeypatch.setattr(story_bulk_harvest, "search_provider_videos", fake_search)
    monkeypatch.setattr(story_bulk_harvest, "_download_file", lambda _url, path: open(path, "wb").close() or True)

    _run_harvest(keywords=["Bangkok"], providers=["pixabay"], max_per_keyword=2, min_free_gb=0)
    _wait_writes()

    doc = mongo_db[mongo.SEARCH_KEYWORDS].find_one({"_id": "pixabay:bangkok"})
    assert doc["status"] == "limited" and doc["videos_downloaded"] == 2


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(raw_env, monkeypatch):
    from src.utils import story_video_prefetch
    from src.web_app import app

    # Never start a real sweep (it would call the provider API).
    monkeypatch.setattr(story_video_prefetch, "run_prefetch", lambda _session_id: None)
    return app.test_client()


def test_prefetch_rejects_used_keyword_only_when_opted_in(client, mongo_db):
    keyword_repo.record_keyword_sweep("pixabay", "Thai police", "completed", "harvest")
    body = {"provider": "pixabay", "query": "thai police"}

    refused = client.post("/api/story-video/library/prefetch", json={**body, "skipUsedKeywords": True})
    assert refused.status_code == 409
    assert refused.get_json()["error"]["code"] == "keyword_used"

    allowed = client.post("/api/story-video/library/prefetch", json=body)
    assert allowed.status_code == 202


def test_prefetch_opt_in_does_not_block_when_mongo_is_off(client):
    response = client.post(
        "/api/story-video/library/prefetch",
        json={"provider": "pixabay", "query": "thai police", "skipUsedKeywords": True},
    )
    assert response.status_code == 202


def test_keyword_lookup_list_and_delete(client, mongo_db):
    keyword_repo.record_keyword_sweep("pexels", "Thai beach", "completed", "harvest")

    lookup = client.get("/api/story-video/search-keywords/lookup?provider=pexels&q=THAI%20BEACH").get_json()
    assert lookup["record"]["status"] == "completed" and lookup["record"]["used"] is True

    listed = client.get("/api/story-video/search-keywords?provider=pexels").get_json()["items"]
    assert [item["keyword"] for item in listed] == ["Thai beach"]

    deleted = client.delete("/api/story-video/search-keywords/pexels?keyword=thai%20beach")
    assert deleted.status_code == 200
    assert client.get("/api/story-video/search-keywords/lookup?provider=pexels&q=thai beach").get_json()["record"] is None


def test_keyword_routes_without_mongo(client):
    assert client.get("/api/story-video/search-keywords").status_code == 503
    lookup = client.get("/api/story-video/search-keywords/lookup?provider=pexels&q=x").get_json()
    assert lookup == {"record": None, "mongoAvailable": False}


def test_once_mode_render_is_refused_without_mongo(client):
    response = client.post("/api/story-video/create", json={
        "inputType": "script_url", "inputValue": "https://docs.google.com/x", "outputName": "t",
        "clipUsageMode": "once",
    })
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "mongo_unavailable"


def test_import_selected_records_each_provider_with_its_own_query(client, mongo_db, monkeypatch):
    import time

    captured: list[dict] = []

    def fake_download(items, session_id, tags, progress_cb, library_id=None):
        captured.extend(items)
        return [{"id": "clip"}]

    monkeypatch.setattr("src.utils.video_source_downloader.download_from_provider_items", fake_download)
    response = client.post("/api/story-video/library/import-selected", json={
        "items": [
            {"provider": "pixabay", "id": "11", "previewUrl": "https://x/11.mp4", "title": "Police"},
            {"provider": "pexels", "id": "22", "previewUrl": "https://x/22.mp4"},
        ],
        "queries": {"pixabay": "Thai police", "pexels": "Bangkok night"},
    })
    assert response.status_code == 202

    deadline = time.monotonic() + 5
    while mongo_db[mongo.SEARCH_KEYWORDS].count_documents({}) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    _wait_writes()

    statuses = {doc["_id"]: doc["status"] for doc in mongo_db[mongo.SEARCH_KEYWORDS].find()}
    assert statuses == {"pixabay:thai police": "manual", "pexels:bangkok night": "manual"}
    assert {item["provider"]: item.get("keyword") for item in captured} == {
        "pixabay": "Thai police", "pexels": "Bangkok night",
    }
    assert captured[0]["title"] == "Police"
