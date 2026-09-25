"""Video goc (``source_videos``), clip (``clips``) va luot dung clip.

Key clip/nguon luon lay tu ``src.utils.clip_identity`` -- cung ham ma pipeline
dung luc render doc ``index.json`` -- nen ban ghi luc tai va luc render khop nhau
ma khong can them truong nao vao ``index.json``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.db import mongo
from src.utils.clip_identity import clip_identity, source_identity

_IN_CHUNK = 10000

# Metadata tu ket qua search (video_source_downloader._normalize_*) -> truong DB.
_SOURCE_META_FIELDS = {
    "title": "title",
    "tags": "tags",
    "pageUrl": "page_url",
    "page_url": "page_url",
    "author": "author",
    "duration": "duration",
    "width": "width",
    "height": "height",
    "thumbnailUrl": "thumbnail_url",
    "thumbnail_url": "thumbnail_url",
    "keyword": "keyword",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source_meta(source_ref: dict) -> dict:
    meta: dict = {}
    for src_field, db_field in _SOURCE_META_FIELDS.items():
        value = source_ref.get(src_field)
        if value not in (None, "", 0, [], {}):
            meta[db_field] = value
    return meta


def build_ingest_updates(library_id: str, source_ref: dict | None, assets: list[dict], now=None):
    """``(source_updates, clip_updates)`` cho ``mongo.bulk_upsert``. Tach rieng de backfill dung lai."""
    now = now or _now()
    source_ref = dict(source_ref or {})
    ref_provider = str(source_ref.get("provider") or "").strip().lower() or None
    ref_id = str(source_ref.get("provider_id") or source_ref.get("id") or "").strip() or None
    flow = str(source_ref.get("flow") or "").strip() or None
    meta = _source_meta(source_ref)

    sources: dict[str, dict] = {}
    clip_updates: list[tuple[dict, dict]] = []
    for asset in assets or []:
        source_key, clip_key = clip_identity(library_id, asset)
        info = source_identity(asset)
        entry = sources.setdefault(source_key, {
            "provider": info.get("provider") or ref_provider,
            "provider_id": info.get("provider_id") or ref_id,
        })
        # Luong dan link khong luu id vao ten file: id chi co trong source_ref.
        if not entry["provider_id"] and ref_id:
            entry["provider_id"] = ref_id
            entry["provider"] = ref_provider or entry["provider"]

        on_insert = {"use_count": 0, "created_at": now, "source_key": source_key}
        if info.get("piece") is not None:
            on_insert["piece"] = info["piece"]
        try:
            on_insert["clip_duration"] = int(round(float(asset.get("duration") or 0)))
        except (TypeError, ValueError):
            pass
        clip_updates.append((
            {"_id": clip_key},
            {
                "$setOnInsert": on_insert,
                "$addToSet": {"locations": {
                    "library_id": library_id,
                    "asset_id": asset.get("id"),
                    "relative_path": asset.get("relative_path"),
                }},
            },
        ))

    source_updates: list[tuple[dict, dict]] = []
    for source_key, entry in sources.items():
        to_set = {"last_seen_at": now, **meta}
        if entry.get("provider"):
            to_set["provider"] = entry["provider"]
        if entry.get("provider_id"):
            to_set["provider_id"] = entry["provider_id"]
        add_to_set: dict = {"libraries": library_id}
        if flow:
            add_to_set["flows"] = flow
        source_updates.append((
            {"_id": source_key},
            {
                "$setOnInsert": {"first_seen_at": now, "schema_version": mongo.SCHEMA_VERSION},
                "$set": to_set,
                "$addToSet": add_to_set,
            },
        ))
    return source_updates, clip_updates


def record_ingest(library_id: str, source_ref: dict | None, assets: list[dict]) -> None:
    """Ghi video goc + cac clip vua cat tu no. Goi qua ``background_writer``."""
    if not assets:
        return
    db = mongo.get_db()
    source_updates, clip_updates = build_ingest_updates(library_id, source_ref, assets)
    mongo.bulk_upsert(db[mongo.SOURCE_VIDEOS], source_updates)
    mongo.bulk_upsert(db[mongo.CLIPS], clip_updates)


def get_clip_use_counts(clip_keys) -> dict[str, int]:
    """``{clip_key: use_count}`` cho nhung key da dung it nhat 1 lan (con lai = 0)."""
    keys = list(dict.fromkeys(str(k) for k in clip_keys if k))
    if not keys:
        return {}
    collection = mongo.get_db()[mongo.CLIPS]
    counts: dict[str, int] = {}
    for start in range(0, len(keys), _IN_CHUNK):
        chunk = keys[start:start + _IN_CHUNK]
        for doc in collection.find({"_id": {"$in": chunk}, "use_count": {"$gt": 0}}, {"use_count": 1}):
            counts[str(doc["_id"])] = int(doc.get("use_count") or 0)
    return counts


def commit_clip_usage(story_id: str, clip_keys, meta: dict | None = None, usage_id: str | None = None) -> str:
    """+1 ``use_count`` cho moi clip DUY NHAT cua mot video, roi ghi event.

    ``usage_id`` la ``_id`` cua event: phat lai tu outbox voi cung id se bi bo qua,
    nen mot video khong bao gio bi dem hai lan vi phat lai.
    """
    db = mongo.get_db()
    usage_id = usage_id or f"{story_id}:{uuid.uuid4().hex[:12]}"
    events = db[mongo.CLIP_USAGE_EVENTS]
    if events.find_one({"_id": usage_id}, {"_id": 1}):
        return usage_id

    now = _now()
    unique = list(dict.fromkeys(str(k) for k in clip_keys if k))
    mongo.bulk_upsert(db[mongo.CLIPS], [
        (
            {"_id": key},
            {
                "$inc": {"use_count": 1},
                "$set": {"last_used_at": now},
                "$setOnInsert": {"created_at": now, "source_key": key.split("#", 1)[0]},
            },
        )
        for key in unique
    ])
    event = {
        "_id": usage_id,
        "story_id": story_id,
        "clip_keys": unique,
        "clip_count": len(unique),
        "used_at": now,
        "schema_version": mongo.SCHEMA_VERSION,
    }
    for field, value in (meta or {}).items():
        if field not in event:
            event[field] = value
    events.insert_one(event)
    return usage_id


def usage_summary(clip_keys) -> dict:
    """``{total, unused, byCount: {"1": n, ...}, maxCount}`` cho mot tap clip."""
    keys = list(dict.fromkeys(str(k) for k in clip_keys if k))
    counts = get_clip_use_counts(keys)
    by_count: dict[str, int] = {}
    for value in counts.values():
        by_count[str(value)] = by_count.get(str(value), 0) + 1
    return {
        "total": len(keys),
        "unused": len(keys) - len(counts),
        "byCount": dict(sorted(by_count.items(), key=lambda item: int(item[0]))),
        "maxCount": max(counts.values()) if counts else 0,
    }
