"""Tu khoa da tim tren tung provider (``search_keywords``).

Trang thai (``status``), xep theo muc "da dung" tang dan:

- ``partial``   quet do dang (het quota, huy, cham tran trang, day dia) -- KHONG chan
- ``manual``    tim tay roi import mot so video
- ``limited``   dung chu dong vi gioi han so video (maxPerKeyword / STORY_PREFETCH_MAX_VIDEOS)
- ``completed`` quet het ket qua

Mot tu khoa chi len hang, khong xuong: quet do lan sau khong xoa dau "completed".
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

from src.db import mongo

STATUS_RANK = {"partial": 0, "manual": 1, "limited": 2, "completed": 3}
USED_STATUSES = ("manual", "limited", "completed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_keyword(keyword) -> str:
    text = unicodedata.normalize("NFC", str(keyword or "")).casefold()
    return " ".join(text.split())


def normalize_provider(provider) -> str:
    return str(provider or "").strip().lower()


def keyword_id(provider, keyword) -> str:
    return f"{normalize_provider(provider)}:{normalize_keyword(keyword)}"


def get_keyword_records(provider, keywords) -> dict[str, dict]:
    """``{normalized: doc}`` cho nhung tu khoa da co trong DB (moi trang thai)."""
    ids = list(dict.fromkeys(keyword_id(provider, kw) for kw in keywords if normalize_keyword(kw)))
    if not ids:
        return {}
    collection = mongo.get_db()[mongo.SEARCH_KEYWORDS]
    return {str(doc.get("normalized") or ""): doc for doc in collection.find({"_id": {"$in": ids}})}


def get_used_keywords(provider, keywords) -> dict[str, dict]:
    """Nhu ``get_keyword_records`` nhung chi giu tu khoa "da dung" (bo qua duoc)."""
    return {
        normalized: doc
        for normalized, doc in get_keyword_records(provider, keywords).items()
        if doc.get("status") in USED_STATUSES
    }


def is_used(doc: dict | None) -> bool:
    return bool(doc) and doc.get("status") in USED_STATUSES


def record_keyword_sweep(
    provider,
    keyword,
    status: str,
    flow: str,
    *,
    results_total: int | None = None,
    videos_downloaded: int = 0,
) -> None:
    provider = normalize_provider(provider)
    normalized = normalize_keyword(keyword)
    if not provider or not normalized or status not in STATUS_RANK:
        return
    collection = mongo.get_db()[mongo.SEARCH_KEYWORDS]
    doc_id = f"{provider}:{normalized}"
    current = collection.find_one({"_id": doc_id}, {"status": 1}) or {}
    best = status
    if STATUS_RANK.get(current.get("status"), -1) > STATUS_RANK[status]:
        best = current["status"]

    now = _now()
    update: dict = {
        "$setOnInsert": {
            "provider": provider,
            "normalized": normalized,
            "keyword": str(keyword).strip(),
            "first_searched_at": now,
            "schema_version": mongo.SCHEMA_VERSION,
        },
        "$set": {"status": best, "last_status": status, "last_searched_at": now},
        "$inc": {"sweep_count": 1, "videos_downloaded": max(0, int(videos_downloaded or 0))},
        "$addToSet": {"flows": flow},
    }
    if results_total is not None:
        update["$max"] = {"results_total": int(results_total)}
    collection.update_one({"_id": doc_id}, update, upsert=True)


def list_keywords(provider: str | None = None, limit: int = 5000) -> list[dict]:
    query = {"provider": normalize_provider(provider)} if provider else {}
    collection = mongo.get_db()[mongo.SEARCH_KEYWORDS]
    docs = collection.find(query).sort("last_searched_at", -1).limit(max(1, int(limit)))
    return [serialize_keyword(doc) for doc in docs]


def delete_keyword(provider, keyword) -> bool:
    result = mongo.get_db()[mongo.SEARCH_KEYWORDS].delete_one({"_id": keyword_id(provider, keyword)})
    return bool(result.deleted_count)


def report_keyword_sweep(provider, keyword, status: str, flow: str, **stats) -> None:
    """Ghi nen ``record_keyword_sweep`` (qua background_writer). Khong raise, khong chan."""
    try:
        if not mongo.is_configured():
            return
        from src.db import background_writer

        background_writer.submit(record_keyword_sweep, provider, keyword, status, flow, **stats)
    except Exception as exc:  # pragma: no cover - chi la bao cao
        from src.utils.logger import logger

        logger.warning(f"[Keywords] Khong xep duoc ban ghi tu khoa {keyword!r}: {exc}")


def find_used_keyword_pairs(providers, keywords) -> tuple[list[dict], str]:
    """Cac cap (tu khoa, provider) da dung -> ``(pairs, check)``.

    ``check``: ``"ok"``, ``"disabled"`` (chua cau hinh Mongo) hoac ``"unavailable"``
    (Mongo tat) -- hai truong hop sau tra ve rong de luong goi chay nhu binh thuong.
    """
    if not mongo.is_configured():
        return [], "disabled"
    if not mongo.is_available():
        return [], "unavailable"
    try:
        pairs: list[dict] = []
        for provider in providers:
            used = get_used_keywords(provider, keywords)
            for keyword in keywords:
                doc = used.get(normalize_keyword(keyword))
                if doc:
                    pairs.append({
                        "keyword": keyword,
                        "provider": normalize_provider(provider),
                        "status": doc.get("status"),
                        "lastSearchedAt": _iso(doc.get("last_searched_at")),
                    })
        return pairs, "ok"
    except Exception as exc:
        from src.utils.logger import logger

        logger.warning(f"[Keywords] Khong kiem tra duoc tu khoa da dung: {exc}")
        return [], "unavailable"


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def serialize_keyword(doc: dict | None) -> dict | None:
    if not doc:
        return None
    return {
        "provider": doc.get("provider"),
        "keyword": doc.get("keyword"),
        "normalized": doc.get("normalized"),
        "status": doc.get("status"),
        "lastStatus": doc.get("last_status"),
        "used": is_used(doc),
        "flows": list(doc.get("flows") or []),
        "sweepCount": int(doc.get("sweep_count") or 0),
        "resultsTotal": doc.get("results_total"),
        "videosDownloaded": int(doc.get("videos_downloaded") or 0),
        "firstSearchedAt": _iso(doc.get("first_searched_at")),
        "lastSearchedAt": _iso(doc.get("last_searched_at")),
        "backfilled": bool(doc.get("backfilled")),
    }
