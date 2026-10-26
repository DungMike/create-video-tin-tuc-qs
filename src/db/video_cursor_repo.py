"""Con tro phan trang tim VIDEO theo tu khoa (``video_search_cursors``).

Chi harvest bat ``use_page_cursor`` (luong tu dong tim nguon,
src/utils/story_auto_source.py) moi doc/ghi o day; harvest thuong van quet tu trang 1.

``_id = "<provider>:<tu khoa chuan hoa>"``. ``per_page`` CHOT tu lan tim dau: doi no
giua chung lam lech ranh gioi trang (trang 3 x 80 khong con la trang 3 x 200).
``next_page`` = trang lan sau bat dau. Dung giua trang (du so video cua lan chay,
huy, het dia) thi giu nguyen trang do: lan sau tai lai trang va bo video da co
(chong trung theo ``source_videos``).

Giong ``image_repo``: ghi DONG BO va de loi noi len -- job harvest tu dung khi Mongo
tat, khong am tham quay lai trang 1.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from src.db import mongo
from src.db.keyword_repo import normalize_keyword, normalize_provider

DEFAULT_PER_PAGE = {"pexels": 80, "pixabay": 200}
# So ket qua dau moi truy van API cho truy cap: Pixabay totalHits toi da 500; Pexels
# bao total_results hang nghin nhung tu vi tri 481 tro di trang rong (do that voi anh
# 2026-09-29; harvest video "bangkok street" cung dung dung 480 video).
RESULT_CAP = {"pixabay": 500, "pexels": 480}
PAGE_LOG_LIMIT = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _collection():
    return mongo.get_db()[mongo.VIDEO_SEARCH_CURSORS]


def cursor_id(provider, keyword) -> str:
    return f"{normalize_provider(provider)}:{normalize_keyword(keyword)}"


def default_per_page(provider) -> int:
    return DEFAULT_PER_PAGE.get(normalize_provider(provider), 80)


def compute_max_page(provider, total, per_page) -> int:
    """So trang toi da truy cap duoc (0 = khong co ket qua)."""
    try:
        total = max(0, int(total or 0))
        per_page = max(1, int(per_page or 1))
    except (TypeError, ValueError):
        return 0
    cap = RESULT_CAP.get(normalize_provider(provider))
    reachable = min(total, cap) if cap else total
    return math.ceil(reachable / per_page) if reachable > 0 else 0


def get_cursor(provider, keyword) -> dict | None:
    return _collection().find_one({"_id": cursor_id(provider, keyword)})


def get_cursors(provider, keywords) -> dict[str, dict]:
    """``{normalized: doc}`` cho cac tu khoa da co con tro tren ``provider``."""
    ids = list(dict.fromkeys(cursor_id(provider, kw) for kw in keywords if normalize_keyword(kw)))
    if not ids:
        return {}
    return {str(doc.get("normalized") or ""): doc for doc in _collection().find({"_id": {"$in": ids}})}


def list_open_cursors(provider) -> list[dict]:
    """Con tro con trang (chua quet het) cua ``provider``, cu nhat truoc."""
    flt = {"provider": normalize_provider(provider), "exhausted": {"$ne": True}}
    return list(_collection().find(flt).sort("first_searched_at", 1))


def start_position(doc: dict | None, provider) -> dict:
    """Trang + per_page cho lan tim tiep theo cua mot cap (provider, tu khoa)."""
    fallback = default_per_page(provider)
    if not doc:
        return {"page": 1, "per_page": fallback, "fresh": True, "exhausted": False}
    try:
        per_page = max(1, int(doc.get("per_page") or fallback))
        page = max(1, int(doc.get("next_page") or 1))
    except (TypeError, ValueError):
        return {"page": 1, "per_page": fallback, "fresh": True, "exhausted": False}
    return {"page": page, "per_page": per_page, "fresh": False, "exhausted": bool(doc.get("exhausted"))}


def record_page(
    provider,
    keyword,
    *,
    page: int,
    per_page: int,
    total: int,
    raw_count: int,
    new_count: int = 0,
    known_count: int = 0,
    page_done: bool,
    job_id: str | None = None,
) -> dict:
    """Ghi ket qua mot trang vua tim. Tra ve ``{next_page, max_page, exhausted, reason}``.

    ``page_done`` = da xu ly het trang. Dung giua trang thi ``next_page`` giu nguyen.
    ``new_count`` = video vua tai, ``known_count`` = video bo qua vi da co.
    """
    page = max(1, int(page))
    per_page = max(1, int(per_page))
    total = max(0, int(total or 0))
    raw_count = max(0, int(raw_count or 0))
    max_page = compute_max_page(provider, total, per_page)

    exhausted, reason = False, None
    if page_done:
        if raw_count == 0:
            exhausted, reason = True, ("no_results" if page == 1 else "empty_page")
        elif max_page and page >= max_page:
            exhausted, reason = True, "last_page"
    next_page = page + 1 if page_done else page

    now = _now()
    update = {
        "$setOnInsert": {
            "provider": normalize_provider(provider),
            "normalized": normalize_keyword(keyword),
            "keyword": str(keyword).strip(),
            "first_searched_at": now,
            "schema_version": mongo.SCHEMA_VERSION,
        },
        "$set": {
            "per_page": per_page,
            "next_page": next_page,
            "last_page_fetched": page,
            "total_results": total,
            "max_page": max_page,
            "exhausted": exhausted,
            "exhausted_reason": reason,
            "last_job_id": job_id,
            "last_searched_at": now,
        },
        "$inc": {
            "requests": 1,
            "videos_seen": raw_count,
            "videos_new": max(0, int(new_count or 0)),
            "videos_known": max(0, int(known_count or 0)),
        },
        "$push": {"page_log": {"$each": [{
            "page": page, "per_page": per_page, "raw": raw_count,
            "new": int(new_count or 0), "known": int(known_count or 0),
            "done": bool(page_done), "job_id": job_id, "at": now,
        }], "$slice": -PAGE_LOG_LIMIT}},
    }
    _collection().update_one({"_id": cursor_id(provider, keyword)}, update, upsert=True)
    return {"next_page": next_page, "max_page": max_page, "exhausted": exhausted, "reason": reason}


def reset_cursor(provider, keyword) -> bool:
    """Lan sau tim lai tu trang 1. Video da tai van khong bi tai trung (source_videos)."""
    result = _collection().update_one(
        {"_id": cursor_id(provider, keyword)},
        {"$set": {"next_page": 1, "exhausted": False, "exhausted_reason": None, "reset_at": _now()}},
    )
    return bool(result.matched_count)


def list_cursors(provider: str | None = None, limit: int = 500) -> list[dict]:
    flt = {"provider": normalize_provider(provider)} if provider else {}
    docs = _collection().find(flt, {"page_log": 0}).sort("last_searched_at", -1).limit(max(1, int(limit)))
    return [serialize_cursor(doc) for doc in docs]


def serialize_cursor(doc: dict | None) -> dict | None:
    if not doc:
        return None
    return {
        "provider": doc.get("provider"),
        "keyword": doc.get("keyword"),
        "normalized": doc.get("normalized"),
        "nextPage": int(doc.get("next_page") or 1),
        "perPage": doc.get("per_page"),
        "lastPageFetched": doc.get("last_page_fetched"),
        "maxPage": doc.get("max_page"),
        "totalResults": doc.get("total_results"),
        "exhausted": bool(doc.get("exhausted")),
        "exhaustedReason": doc.get("exhausted_reason"),
        "requests": int(doc.get("requests") or 0),
        "videosNew": int(doc.get("videos_new") or 0),
        "videosKnown": int(doc.get("videos_known") or 0),
        "firstSearchedAt": _iso(doc.get("first_searched_at")),
        "lastSearchedAt": _iso(doc.get("last_searched_at")),
    }
