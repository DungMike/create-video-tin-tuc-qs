"""Thu vien clip tu anh: con tro phan trang theo tu khoa + anh da biet.

``image_search_cursors`` -- ``_id = "<provider>:<tu khoa chuan hoa>"``. Luu trang
lan tim sau bat dau (``next_page``) va ``per_page`` da dung: ``per_page`` CHOT tu
lan tim dau, vi doi no giua chung lam lech ranh gioi trang (trang 3 x 80 khong con
la trang 3 x 200). Chi ``reset_cursor`` hoac doi ``query_signature`` (bo loc gui
len API) moi bat dau lai tu trang 1 voi ``per_page`` mac dinh.

``source_images`` -- ``_id = "<provider>-photo:<id>"``, cung la ``source_key`` cua
clip tao tu anh (``src.utils.clip_identity``). Chong trung TOAN HE THONG: anh da
``staged`` / ``rejected`` / ``committed`` khong bao gio duoc tai lai.

Khac ``keyword_repo`` / ``media_repo`` (ghi nen, bo qua loi): luong anh can ket qua
dung (trang ke tiep, chan trung) nen o day ghi DONG BO va de loi noi len; job va
route tu xu ly (Mongo tat -> 503 / job dung).

Moi update tach truong giua cac toan tu: Mongo that bao loi (code 40) khi mot
truong nam o ca ``$setOnInsert`` lan ``$set``, con mongomock thi khong bat.
"""

from __future__ import annotations

import math
import re
import threading
from datetime import datetime, timezone

from src.db import mongo
from src.db.keyword_repo import normalize_keyword, normalize_provider

PROVIDERS = ("pixabay", "pexels")
DEFAULT_PER_PAGE = {"pexels": 80, "pixabay": 200}
# Pixabay chi cho truy cap toi da 500 ket qua moi truy van (totalHits).
RESULT_CAP = {"pixabay": 500}
PAGE_LOG_LIMIT = 200

PHOTO_STATUSES = ("staged", "rejected", "committed", "discarded", "failed")
# Anh o cac trang thai nay khong duoc tai lai (discarded/failed thi duoc).
KNOWN_PHOTO_STATUSES = ("staged", "rejected", "committed")

_IN_CHUNK = 10000
_indexes_lock = threading.Lock()
_indexes_ready_for: tuple | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _db():
    """``mongo.get_db()`` + tao index luoi cho 2 collection cua module nay."""
    global _indexes_ready_for
    db = mongo.get_db()
    key = (id(db.client), db.name)
    if _indexes_ready_for != key:
        with _indexes_lock:
            if _indexes_ready_for != key:
                try:
                    db[mongo.IMAGE_SEARCH_CURSORS].create_index("provider")
                    db[mongo.SOURCE_IMAGES].create_index("status")
                    db[mongo.SOURCE_IMAGES].create_index("keywords")
                except Exception as exc:  # index chi de nhanh hon, khong bat buoc
                    from src.utils.logger import logger

                    logger.warning(f"[ImageRepo] Khong tao duoc index: {exc}")
                _indexes_ready_for = key
    return db


# --------------------------------------------------------------------------- #
# Con tro phan trang
# --------------------------------------------------------------------------- #
def cursor_id(provider, keyword) -> str:
    return f"{normalize_provider(provider)}:{normalize_keyword(keyword)}"


def photo_key(provider, photo_id) -> str:
    return f"{normalize_provider(provider)}-photo:{str(photo_id).strip()}"


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
    return _db()[mongo.IMAGE_SEARCH_CURSORS].find_one({"_id": cursor_id(provider, keyword)})


def get_cursors(provider, keywords) -> dict[str, dict]:
    """``{normalized: doc}`` cho cac tu khoa da co con tro."""
    ids = list(dict.fromkeys(cursor_id(provider, kw) for kw in keywords if normalize_keyword(kw)))
    if not ids:
        return {}
    docs = _db()[mongo.IMAGE_SEARCH_CURSORS].find({"_id": {"$in": ids}})
    return {str(doc.get("normalized") or ""): doc for doc in docs}


def start_position(doc: dict | None, provider, signature: str) -> dict:
    """Trang + per_page cho lan tim tiep theo cua mot cap (provider, tu khoa)."""
    fallback = default_per_page(provider)
    if not doc or doc.get("query_signature") != signature:
        return {"page": 1, "per_page": fallback, "fresh": True, "exhausted": False}
    try:
        per_page = max(1, int(doc.get("per_page") or fallback))
        page = max(1, int(doc.get("next_page") or 1))
    except (TypeError, ValueError):
        return {"page": 1, "per_page": fallback, "fresh": True, "exhausted": False}
    return {"page": page, "per_page": per_page, "fresh": False, "exhausted": bool(doc.get("exhausted"))}


def _cursor_update(provider, keyword, set_fields: dict, inc: dict | None = None, log_entry: dict | None = None):
    now = _now()
    update: dict = {
        "$setOnInsert": {
            "provider": normalize_provider(provider),
            "normalized": normalize_keyword(keyword),
            "keyword": str(keyword).strip(),
            "first_searched_at": now,
            "schema_version": mongo.SCHEMA_VERSION,
        },
        "$set": {**set_fields, "last_searched_at": now},
    }
    if inc:
        update["$inc"] = inc
    if log_entry:
        update["$push"] = {"page_log": {"$each": [{**log_entry, "at": now}], "$slice": -PAGE_LOG_LIMIT}}
    return update


def record_page(
    provider,
    keyword,
    *,
    signature: str,
    page: int,
    per_page: int,
    total: int,
    raw_count: int,
    accepted: int,
    rejected: int,
    page_done: bool,
    has_next: bool | None = None,
    new_run: bool = False,
    job_id: str | None = None,
) -> dict:
    """Ghi ket qua mot trang vua tim. Tra ve ``{next_page, max_page, exhausted, reason}``.

    ``page_done`` = da xu ly het trang. Dung giua trang (du so anh, huy, het dia)
    thi ``next_page`` giu nguyen trang do: lan sau tai lai trang va bo id da biet.
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
        elif has_next is False:
            exhausted, reason = True, "no_next_page"
    next_page = page + 1 if page_done else page

    set_fields = {
        "query_signature": signature,
        "per_page": per_page,
        "next_page": next_page,
        "last_page_fetched": page,
        "total_results": total,
        "max_page": max_page,
        "exhausted": exhausted,
        "exhausted_reason": reason,
        "last_job_id": job_id,
    }
    inc = {
        "pages_fetched": 1,
        "requests": 1,
        "photos_seen": raw_count,
        "photos_accepted": max(0, int(accepted or 0)),
        "photos_rejected": max(0, int(rejected or 0)),
        "runs": 1 if new_run else 0,
    }
    log_entry = {"page": page, "per_page": per_page, "raw": raw_count, "accepted": int(accepted or 0)}
    _db()[mongo.IMAGE_SEARCH_CURSORS].update_one(
        {"_id": cursor_id(provider, keyword)},
        _cursor_update(provider, keyword, set_fields, inc, log_entry),
        upsert=True,
    )
    return {"next_page": next_page, "max_page": max_page, "exhausted": exhausted, "reason": reason}


def record_out_of_range(provider, keyword, *, signature: str, page: int, per_page: int, job_id=None) -> None:
    """Provider bao trang vuot pham vi (Pixabay 400 "out of valid range"): het ket qua."""
    set_fields = {
        "query_signature": signature,
        "per_page": max(1, int(per_page)),
        "next_page": max(1, int(page)),
        "exhausted": True,
        "exhausted_reason": "out_of_range",
        "last_job_id": job_id,
    }
    _db()[mongo.IMAGE_SEARCH_CURSORS].update_one(
        {"_id": cursor_id(provider, keyword)},
        _cursor_update(provider, keyword, set_fields, {"requests": 1}),
        upsert=True,
    )


def reset_cursor(provider, keyword) -> bool:
    """Lan sau tim lai tu trang 1 (per_page mac dinh). Giu lich su va id anh."""
    result = _db()[mongo.IMAGE_SEARCH_CURSORS].update_one(
        {"_id": cursor_id(provider, keyword)},
        {"$set": {
            "query_signature": None,
            "next_page": 1,
            "exhausted": False,
            "exhausted_reason": None,
            "reset_at": _now(),
        }},
    )
    return bool(result.matched_count)


def list_cursors(provider: str | None = None, query: str | None = None, limit: int = 500) -> list[dict]:
    flt: dict = {}
    if provider:
        flt["provider"] = normalize_provider(provider)
    text = normalize_keyword(query or "")
    if text:
        flt["normalized"] = {"$regex": re.escape(text)}
    docs = _db()[mongo.IMAGE_SEARCH_CURSORS].find(flt).sort("last_searched_at", -1).limit(max(1, int(limit)))
    return list(docs)


def serialize_cursor(doc: dict | None, signature: str | None = None) -> dict | None:
    if not doc:
        return None
    current = signature is None or doc.get("query_signature") == signature
    return {
        "provider": doc.get("provider"),
        "keyword": doc.get("keyword"),
        "normalized": doc.get("normalized"),
        "perPage": doc.get("per_page"),
        "nextPage": int(doc.get("next_page") or 1) if current else 1,
        "lastPageFetched": doc.get("last_page_fetched"),
        "totalResults": doc.get("total_results"),
        "maxPage": doc.get("max_page"),
        "exhausted": bool(doc.get("exhausted")) and current,
        "exhaustedReason": doc.get("exhausted_reason") if current else None,
        "signatureCurrent": bool(current),
        "pagesFetched": int(doc.get("pages_fetched") or 0),
        "requests": int(doc.get("requests") or 0),
        "photosSeen": int(doc.get("photos_seen") or 0),
        "photosAccepted": int(doc.get("photos_accepted") or 0),
        "photosRejected": int(doc.get("photos_rejected") or 0),
        "runs": int(doc.get("runs") or 0),
        "firstSearchedAt": _iso(doc.get("first_searched_at")),
        "lastSearchedAt": _iso(doc.get("last_searched_at")),
        "lastJobId": doc.get("last_job_id"),
    }


# --------------------------------------------------------------------------- #
# Anh da biet
# --------------------------------------------------------------------------- #
def find_known_photo_keys(keys) -> set[str]:
    """Nhung key anh da staged/rejected/committed (khong tai lai)."""
    return {key for key, status in get_photo_statuses(keys).items() if status in KNOWN_PHOTO_STATUSES}


def get_photo_statuses(keys) -> dict[str, str]:
    unique = list(dict.fromkeys(str(key) for key in keys if key))
    if not unique:
        return {}
    collection = _db()[mongo.SOURCE_IMAGES]
    statuses: dict[str, str] = {}
    for start in range(0, len(unique), _IN_CHUNK):
        chunk = unique[start:start + _IN_CHUNK]
        for doc in collection.find({"_id": {"$in": chunk}}, {"status": 1}):
            statuses[str(doc["_id"])] = str(doc.get("status") or "")
    return statuses


_PHOTO_META_FIELDS = {
    "title": "title",
    "tags": "tags",
    "pageUrl": "page_url",
    "author": "author",
    "width": "width",
    "height": "height",
    "downloadUrl": "download_url",
    "thumbnailUrl": "thumbnail_url",
}


def record_photo_staged(job_id: str, provider, photo_id, keyword, item: dict | None = None, effect: str | None = None) -> str:
    """Anh vua tai xong va da nam trong manifest cua ``job_id``."""
    key = photo_key(provider, photo_id)
    now = _now()
    to_set: dict = {"status": "staged", "updated_at": now}
    for src_field, db_field in _PHOTO_META_FIELDS.items():
        value = (item or {}).get(src_field)
        if value not in (None, "", 0, []):
            to_set[db_field] = value
    if effect:
        to_set["effect"] = effect
    add_to_set: dict = {"job_ids": job_id}
    if normalize_keyword(keyword):
        add_to_set["keywords"] = str(keyword).strip()
    _db()[mongo.SOURCE_IMAGES].update_one(
        {"_id": key},
        {
            "$setOnInsert": {
                "provider": normalize_provider(provider),
                "provider_id": str(photo_id).strip(),
                "first_seen_at": now,
                "schema_version": mongo.SCHEMA_VERSION,
            },
            "$set": to_set,
            "$addToSet": add_to_set,
        },
        upsert=True,
    )
    return key


def mark_photos(keys, status: str, *, only_from: tuple[str, ...] | None = ("staged",)) -> int:
    """Doi trang thai hang loat. Mac dinh chi doi anh dang ``staged``."""
    if status not in PHOTO_STATUSES:
        raise ValueError(f"Trang thai anh khong hop le: {status}")
    unique = list(dict.fromkeys(str(key) for key in keys if key))
    if not unique:
        return 0
    collection = _db()[mongo.SOURCE_IMAGES]
    changed = 0
    for start in range(0, len(unique), _IN_CHUNK):
        flt: dict = {"_id": {"$in": unique[start:start + _IN_CHUNK]}}
        if only_from:
            flt["status"] = {"$in": list(only_from)}
        result = collection.update_many(flt, {"$set": {"status": status, "updated_at": _now()}})
        changed += int(result.modified_count or 0)
    return changed


def mark_photo_committed(key: str, library_id: str, asset_id: str | None, effect: str | None = None) -> None:
    to_set: dict = {"status": "committed", "updated_at": _now()}
    if effect:
        to_set["effect"] = effect
    add_to_set: dict = {"libraries": library_id}
    if asset_id:
        add_to_set["asset_ids"] = asset_id
    _db()[mongo.SOURCE_IMAGES].update_one({"_id": key}, {"$set": to_set, "$addToSet": add_to_set})


def photo_status_counts() -> dict[str, int]:
    counts = {status: 0 for status in PHOTO_STATUSES}
    collection = _db()[mongo.SOURCE_IMAGES]
    for status in PHOTO_STATUSES:
        counts[status] = int(collection.count_documents({"status": status}))
    return counts
