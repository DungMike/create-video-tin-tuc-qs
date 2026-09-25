"""Mot MongoClient dung chung cho ca process, tao luoi (lazy).

Khong ket noi luc import: test va ``src.web_app`` import module nay ma khong co
Mongo nao chay. Client chi duoc tao o lan ``get_db()`` dau tien, va duoc tao lai
khi ``Config.MONGODB_URI`` / ``MONGODB_TIMEOUT_MS`` doi (test monkeypatch Config).

``serverSelectionTimeoutMS`` ngan (``Config.MONGODB_TIMEOUT_MS``) de Mongo tat chi
lam cham mot thao tac toi da chung do, khong treo request. ``is_available()`` cache
ket qua ping vai giay de cac route kiem tra truoc (preflight) khong ping lien tuc.
"""

from __future__ import annotations

import threading
import time

from src.config import Config
from src.utils.logger import logger

SOURCE_VIDEOS = "source_videos"
CLIPS = "clips"
CLIP_USAGE_EVENTS = "clip_usage_events"
SEARCH_KEYWORDS = "search_keywords"

SCHEMA_VERSION = 1

_AVAILABILITY_TTL_SECONDS = 10.0


class MongoUnavailable(RuntimeError):
    """Mongo chua cau hinh hoac khong ket noi duoc."""


_lock = threading.Lock()
_client = None
_client_signature: tuple | None = None
_indexes_ready: tuple | None = None
_availability: tuple[float, bool, tuple | None] = (0.0, False, None)
# Test hook: callable(uri, timeout_ms) -> client (vd ``mongomock.MongoClient``).
_client_factory = None


def is_configured() -> bool:
    return bool(str(getattr(Config, "MONGODB_URI", "") or "").strip())


def _signature() -> tuple:
    return (
        str(Config.MONGODB_URI or "").strip(),
        int(getattr(Config, "MONGODB_TIMEOUT_MS", 2000) or 2000),
    )


def _make_client(uri: str, timeout_ms: int):
    if _client_factory is not None:
        return _client_factory(uri, timeout_ms)
    from pymongo import MongoClient

    return MongoClient(
        uri,
        serverSelectionTimeoutMS=timeout_ms,
        connectTimeoutMS=timeout_ms,
        socketTimeoutMS=max(timeout_ms * 10, 20000),
        appname="story-video-studio",
        tz_aware=True,
    )


def _client_for_config():
    global _client, _client_signature
    signature = _signature()
    with _lock:
        if _client is None or _client_signature != signature:
            old = _client
            _client = _make_client(*signature)
            _client_signature = signature
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
        return _client, signature


def get_db():
    """Database handle. Raise ``MongoUnavailable`` khi chua cau hinh URI.

    Chi tao client; ket noi that xay ra o thao tac dau tien, nen loi mang noi len
    tu thao tac do (pymongo ``ServerSelectionTimeoutError``), khong phai tu day.
    """
    global _indexes_ready
    if not is_configured():
        raise MongoUnavailable("MONGODB_URI chua duoc cau hinh trong .env")
    client, signature = _client_for_config()
    db_name = str(Config.MONGODB_DB or "story_video_studio")
    db = client[db_name]
    ready_key = (signature, db_name)
    if _indexes_ready != ready_key:
        # Ngoai lock: Mongo tat thi lan nay ton toi da timeout, khong chan ca
        # nhung thread khac dang can client.
        ensure_indexes(db)
        _indexes_ready = ready_key
    return db


def ensure_indexes(db) -> None:
    db[CLIPS].create_index("source_key")
    db[CLIPS].create_index("use_count")
    db[CLIPS].create_index("locations.library_id")
    db[SOURCE_VIDEOS].create_index("provider")
    db[SEARCH_KEYWORDS].create_index("provider")
    db[SEARCH_KEYWORDS].create_index("status")
    db[CLIP_USAGE_EVENTS].create_index("story_id")
    db[CLIP_USAGE_EVENTS].create_index("used_at")


def bulk_upsert(collection, updates: list[tuple[dict, dict]], batch_size: int = 1000) -> int:
    """Ap ``(filter, update)`` voi upsert=True theo lo. Tra ve so thao tac da gui.

    mongomock (dung trong test) khong chay duoc ``bulk_write`` cua pymongo >= 4.11
    (``UpdateOne`` gui them tham so ``sort``), nen voi collection gia thi ap tung
    cai mot -- ket qua giong het, chi cham hon.
    """
    if not updates:
        return 0
    if type(collection).__module__.startswith("mongomock"):
        for flt, update in updates:
            collection.update_one(flt, update, upsert=True)
        return len(updates)
    from pymongo import UpdateOne

    for start in range(0, len(updates), batch_size):
        chunk = updates[start:start + batch_size]
        collection.bulk_write([UpdateOne(flt, update, upsert=True) for flt, update in chunk], ordered=False)
    return len(updates)


def is_available(force: bool = False) -> bool:
    """Ping Mongo, cache ket qua ``_AVAILABILITY_TTL_SECONDS`` giay."""
    global _availability
    if not is_configured():
        return False
    signature = _signature()
    now = time.monotonic()
    checked_at, ok, cached_signature = _availability
    if not force and cached_signature == signature and now - checked_at < _AVAILABILITY_TTL_SECONDS:
        return ok
    try:
        get_db().command("ping")
        ok = True
    except Exception as exc:
        logger.warning(f"[Mongo] Khong ket noi duoc {signature[0]}: {exc}")
        ok = False
    _availability = (time.monotonic(), ok, signature)
    return ok


def unavailable_message() -> str:
    if not is_configured():
        return "MongoDB chua duoc cau hinh (MONGODB_URI trong .env)."
    return (
        "MongoDB khong ket noi duoc. Bat Docker Desktop roi chay "
        "`docker compose up -d` o thu muc du an, sau do thu lai."
    )


def set_client_factory(factory) -> None:
    """Test hook: thay cach tao client (vd ``lambda uri, t: mongomock.MongoClient()``)."""
    global _client_factory
    _client_factory = factory
    reset()


def reset() -> None:
    """Dong client va xoa moi cache. Dung cho test."""
    global _client, _client_signature, _indexes_ready, _availability
    with _lock:
        old = _client
        _client = None
        _client_signature = None
        _indexes_ready = None
        _availability = (0.0, False, None)
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
