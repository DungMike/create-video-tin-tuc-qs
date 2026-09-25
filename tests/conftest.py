"""Shared fixtures.

`.env` (loaded by src/config.py at import) points MONGODB_URI at the real local
MongoDB. No test may write there -- a harvest/prefetch/ingest test would otherwise
leave fake source videos and keywords in the production DB whenever Docker is up.
So Mongo is OFF for every test by default; a test that needs it asks for
``mongo_db`` and gets an in-memory mongomock database instead.
"""

import pytest

from src.config import Config
from src.db import mongo


@pytest.fixture(autouse=True)
def _mongo_off_by_default(monkeypatch):
    monkeypatch.setattr(Config, "MONGODB_URI", "")
    mongo.reset()
    yield
    mongo.set_client_factory(None)


@pytest.fixture
def mongo_db(monkeypatch, tmp_path):
    """In-memory MongoDB (mongomock) wired into src.db.mongo for this test."""
    import mongomock

    client = mongomock.MongoClient()
    monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://mongomock.test:27017")
    monkeypatch.setattr(Config, "MONGODB_DB", "test_story_video_studio")
    # The clip-usage outbox lives under STORY_VIDEO_DIR.
    monkeypatch.setattr(Config, "STORY_VIDEO_DIR", str(tmp_path / "story_video"))
    mongo.set_client_factory(lambda _uri, _timeout: client)
    return mongo.get_db()
