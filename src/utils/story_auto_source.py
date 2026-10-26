"""Tu dong tim nguon video cho thu vien clip + kiem tra kho hang ngay.

Moi luot chay (bam "Chay ngay" hoac lich hang ngay):

1. Bao cao kho: tung thu vien (cu -> moi) con bao nhieu clip chua dung, uoc tinh con
   bao nhieu video o che do "moi clip 1 lan" (so lan dung o Mongo ``clips``).
2. Don clip: clip da dung >= ``maxClipUses`` video bi xoa khoi thu vien (chi khi bat
   ``autoCleanup``; mac dinh chi liet ke). Ban ghi Mongo cua clip/video goc giu nguyen
   nen video goc do khong bao gio bi tai lai.
3. Tim + tai nguon moi: chon tu khoa chua dung (file ``keywords/video-keywords.txt``
   roi bo sinh toan cau src/utils/auto_source_keywords.py), harvest voi con tro trang
   (tu khoa dang do thi di tiep trang sau) + bo video goc da co o MOI thu vien, roi cat
   clip vao thu vien dich (tu chia v2/v3 khi day).

Tat ca TAT mac dinh: chua bat ``enabled`` thi lich khong chay, ``maxClipUses`` = 0 thi
render "moi clip 1 lan" khong gioi han nhu cu. Cai dat + lich su luu duoi
``Config.STORY_AUTO_SOURCE_DIR`` (settings.json, state.json, runs/*.json).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from src.config import Config
from src.utils.logger import logger

PROVIDERS = ("pexels", "pixabay")

DEFAULT_SETTINGS: dict = {
    "enabled": False,  # chay tu dong hang ngay
    "runHour": 2,  # gio may (0-23) bat dau luot hang ngay
    "targetLibraryId": "",  # thu vien nhan clip moi (ho v2/v3 tu chia)
    "providers": ["pexels", "pixabay"],
    "keywordsPerRun": 15,
    "maxVideosPerRun": 800,  # 0 = khong gioi han
    "minStockHours": 0,  # chi tai khi kho chua dung cua thu vien dich < X gio (0 = luon tai)
    "landscapeOnly": True,
    "minFreeGb": 50,  # dung tai / bo qua cat clip khi o dia con duoi muc nay
    "useKeywordFile": True,
    "maxClipUses": 0,  # 0 = khong gioi han; 3 = moi clip toi da 3 video (che do "1 lan")
    "autoCleanup": False,  # luot hang ngay tu xoa clip da cham maxClipUses
    "keepJobDays": 3,  # xoa job harvest tu dong da cat xong sau N ngay
}
_INT_LIMITS = {
    "runHour": (0, 23),
    "keywordsPerRun": (1, 200),
    "maxVideosPerRun": (0, 20000),
    "minStockHours": (0, 100000),
    "minFreeGb": (0, 10000),
    "maxClipUses": (0, 1000),
    "keepJobDays": (0, 365),
}

_REPORT_TTL_SECONDS = 300
_RECENT_EVENTS = 30
_RUN_HISTORY_KEEP = 200
_SCHEDULER_TICK_SECONDS = 300
_HARVEST_POLL_SECONDS = 2.0
_TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


class AutoSourceError(RuntimeError):
    def __init__(self, message: str, code: str = "auto_source_failed"):
        super().__init__(message)
        self.code = code


class AutoSourceBusy(AutoSourceError):
    def __init__(self, message: str):
        super().__init__(message, "auto_source_busy")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# File state
# --------------------------------------------------------------------------- #
def _root() -> str:
    return Config.STORY_AUTO_SOURCE_DIR


def _settings_path() -> str:
    return os.path.join(_root(), "settings.json")


def _state_path() -> str:
    return os.path.join(_root(), "state.json")


def _runs_dir() -> str:
    return os.path.join(_root(), "runs")


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning(f"[AutoSource] Khong doc duoc {path}: {exc}")
        return None


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.4)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _clean_providers(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    wanted = {str(item).strip().lower() for item in value}
    return [name for name in PROVIDERS if name in wanted]


def _clean_int(key: str, value) -> int:
    low, high = _INT_LIMITS[key]
    return max(low, min(high, int(value)))


def normalize_settings(raw) -> dict:
    """Ghep ``raw`` len mac dinh, bo qua gia tri hong (file cu / sua tay)."""
    settings = {**DEFAULT_SETTINGS, "providers": list(DEFAULT_SETTINGS["providers"])}
    if not isinstance(raw, dict):
        return settings
    for key, default in DEFAULT_SETTINGS.items():
        if key not in raw:
            continue
        value = raw[key]
        try:
            if isinstance(default, bool):
                settings[key] = bool(value)
            elif key in _INT_LIMITS:
                settings[key] = _clean_int(key, value)
            elif key == "providers":
                settings[key] = _clean_providers(value) or settings[key]
            elif key == "targetLibraryId":
                settings[key] = str(value or "").strip()
        except (TypeError, ValueError):
            continue
    return settings


def load_settings() -> dict:
    return normalize_settings(_read_json(_settings_path()))


def save_settings(patch: dict) -> dict:
    """Kiem tra chat (sai thi ValueError) roi ghi de len cai dat hien tai."""
    if not isinstance(patch, dict):
        raise ValueError("Cai dat phai la object.")
    current = load_settings()
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[key]
        if isinstance(default, bool):
            current[key] = bool(value)
        elif key in _INT_LIMITS:
            try:
                current[key] = _clean_int(key, value)
            except (TypeError, ValueError):
                raise ValueError(f"{key} phai la so nguyen.") from None
        elif key == "providers":
            providers = _clean_providers(value)
            if not providers:
                raise ValueError("Can chon it nhat 1 nguon (pexels/pixabay).")
            current[key] = providers
        elif key == "targetLibraryId":
            current[key] = str(value or "").strip()
    _write_json(_settings_path(), current)
    invalidate_report_cache()
    return current


def clip_max_uses() -> int:
    """Gioi han luot dung moi clip cho render "moi clip 1 lan" (0 = khong gioi han).

    Goi o moi render che do once: khong bao gio raise.
    """
    try:
        return int(load_settings().get("maxClipUses") or 0)
    except Exception:  # pragma: no cover - doc file hong da xu ly trong load_settings
        return 0


def load_state() -> dict:
    data = _read_json(_state_path())
    return data if isinstance(data, dict) else {}


def _update_state(**fields) -> dict:
    state = load_state()
    state.update(fields)
    _write_json(_state_path(), state)
    return state


# --------------------------------------------------------------------------- #
# Stock report
# --------------------------------------------------------------------------- #
_report_lock = threading.Lock()
_report_cache: dict = {"at": 0.0, "key": None, "data": None}


def invalidate_report_cache() -> None:
    with _report_lock:
        _report_cache.update({"at": 0.0, "key": None, "data": None})


def _disk_free_gb(path: str) -> float | None:
    probe = path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        return round(shutil.disk_usage(probe).free / 1024 ** 3, 1)
    except OSError:
        return None


def _scan_libraries() -> tuple[list[dict], dict[str, float]]:
    """Thu vien xep theo ngay tao (cu -> moi) kem clip key, do dai, video goc."""
    from src.utils.clip_identity import clip_identity
    from src.utils.story_library import load_libraries, load_story_library_index

    libraries = sorted(load_libraries(), key=lambda lib: str(lib.get("createdAt") or ""))
    key_duration: dict[str, float] = {}
    scanned: list[dict] = []
    for lib in libraries:
        library_id = str(lib.get("id") or "")
        clips: list[tuple[str, str, float]] = []  # (asset id, clip key, duration)
        sources: set[str] = set()
        first_at = last_at = None
        for asset in load_story_library_index(library_id).get("assets", []):
            if not asset.get("relative_path"):
                continue
            source_key, clip_key = clip_identity(library_id, asset)
            try:
                duration = float(asset.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            clips.append((str(asset.get("id") or ""), clip_key, duration))
            key_duration[clip_key] = duration
            sources.add(source_key)
            created = str(asset.get("created_at") or "")
            if created:
                first_at = created if first_at is None or created < first_at else first_at
                last_at = created if last_at is None or created > last_at else last_at
        scanned.append({"record": lib, "clips": clips, "sources": len(sources),
                         "firstClipAt": first_at, "lastClipAt": last_at})
    return scanned, key_duration


def _usage_rate(key_duration: dict[str, float]) -> dict:
    """Do dai video trung binh + so video/ngay tu cac luot dung gan day (che do once)."""
    from src.db import mongo

    events = mongo.get_db()[mongo.CLIP_USAGE_EVENTS]
    lengths: list[float] = []
    for event in events.find({}, {"clip_keys": 1}).sort("used_at", -1).limit(_RECENT_EVENTS):
        keys = event.get("clip_keys") or []
        known = [key_duration[k] for k in keys if k in key_duration]
        # Video dung clip cua thu vien da xoa: khong do duoc, bo qua.
        if keys and len(known) >= len(keys) * 0.5:
            lengths.append(sum(known) * len(keys) / len(known))
    since = datetime.now(timezone.utc) - timedelta(days=7)
    week = events.count_documents({"used_at": {"$gte": since}})
    avg_seconds = sum(lengths) / len(lengths) if lengths else 0.0
    return {
        "avgVideoMinutes": round(avg_seconds / 60, 1),
        "videosLast7Days": int(week),
        "videosPerDay": round(week / 7, 1),
        "sampleVideos": len(lengths),
    }


def _summarize(clips: list[tuple[str, str, float]], counts: dict[str, int], max_uses: int,
               avg_seconds: float) -> dict:
    unique: dict[str, float] = {}
    for _asset_id, key, duration in clips:
        unique.setdefault(key, duration)
    by_count: dict[str, int] = {}
    unused_seconds = 0.0
    remaining_seconds = 0.0
    retire = 0
    retire_seconds = 0.0
    for key, duration in unique.items():
        count = int(counts.get(key, 0))
        by_count[str(count)] = by_count.get(str(count), 0) + 1
        if count == 0:
            unused_seconds += duration
        if max_uses > 0:
            if count < max_uses:
                remaining_seconds += duration * (max_uses - count)
            else:
                retire += 1
                retire_seconds += duration
    unused_hours = unused_seconds / 3600
    return {
        "clips": len(clips),
        "uniqueClips": len(unique),
        "hours": round(sum(unique.values()) / 3600, 2),
        "unused": by_count.get("0", 0),
        "unusedHours": round(unused_hours, 2),
        "useCounts": dict(sorted(by_count.items(), key=lambda item: int(item[0]))),
        "maxCount": max((int(c) for c in by_count), default=0),
        "videosLeftOnce": round(unused_seconds / avg_seconds, 1) if avg_seconds else None,
        "remainingHoursUnderCap": round(remaining_seconds / 3600, 2) if max_uses > 0 else None,
        "atCap": retire if max_uses > 0 else None,
        "atCapHours": round(retire_seconds / 3600, 2) if max_uses > 0 else None,
    }


def stock_report(*, refresh: bool = False, max_uses: int | None = None) -> dict:
    """Bao cao kho clip (can Mongo). Cache ``_REPORT_TTL_SECONDS`` tru khi ``refresh``."""
    from src.db import media_repo, mongo
    from src.utils.story_library import library_version_family

    settings = load_settings()
    cap = settings["maxClipUses"] if max_uses is None else max(0, int(max_uses))
    cache_key = (cap, settings["targetLibraryId"])
    with _report_lock:
        cached = _report_cache.get("data")
        if (not refresh and cached is not None and _report_cache.get("key") == cache_key
                and time.monotonic() - _report_cache["at"] < _REPORT_TTL_SECONDS):
            return cached

    if not mongo.is_configured() or not mongo.is_available(force=True):
        raise AutoSourceError(mongo.unavailable_message(), "mongo_unavailable")

    started = time.monotonic()
    scanned, key_duration = _scan_libraries()
    counts = media_repo.get_clip_use_counts(key_duration.keys())
    rate = _usage_rate(key_duration)
    avg_seconds = rate["avgVideoMinutes"] * 60

    rows = []
    for entry in scanned:
        lib = entry["record"]
        rows.append({
            "id": lib.get("id"),
            "name": lib.get("name"),
            "createdAt": lib.get("createdAt"),
            "styled": bool(lib.get("styled")),
            "versionOf": lib.get("versionOf"),
            "sources": entry["sources"],
            "firstClipAt": entry["firstClipAt"],
            "lastClipAt": entry["lastClipAt"],
            **_summarize(entry["clips"], counts, cap, avg_seconds),
        })

    all_clips = [(k, k, d) for k, d in key_duration.items()]
    overall = _summarize(all_clips, counts, cap, avg_seconds)
    per_day_hours = rate["videosPerDay"] * rate["avgVideoMinutes"] / 60
    overall["daysLeftOnce"] = round(overall["unusedHours"] / per_day_hours, 1) if per_day_hours else None

    target = None
    if settings["targetLibraryId"]:
        family = [lib.get("id") for lib in library_version_family(settings["targetLibraryId"])]
        if family:
            family_rows = [row for row in rows if row["id"] in family]
            target = {
                "libraryIds": family,
                "clips": sum(row["clips"] for row in family_rows),
                "unusedHours": round(sum(row["unusedHours"] for row in family_rows), 2),
            }

    report = {
        "generatedAt": _utc_now(),
        "elapsedSeconds": round(time.monotonic() - started, 1),
        "maxClipUses": cap,
        "libraries": rows,
        "overall": overall,
        "usage": rate,
        "target": target,
        "disk": {
            "rawFreeGb": _disk_free_gb(Config.STORY_RAW_DIR),
            "libraryFreeGb": _disk_free_gb(Config.STORY_LIBRARY_DIR),
        },
    }
    with _report_lock:
        _report_cache.update({"at": time.monotonic(), "key": cache_key, "data": report})
    return report


# --------------------------------------------------------------------------- #
# Cleanup: clip da dung >= max_uses video
# --------------------------------------------------------------------------- #
def _render_busy() -> str | None:
    """Ly do khong duoc xoa clip luc nay (None = ranh)."""
    from src.utils.clip_usage import get_clip_usage_ledger
    from src.utils.story_video_batch import has_active_or_queued_batch

    if has_active_or_queued_batch():
        return "Dang co batch render chay/cho trong hang doi."
    if get_clip_usage_ledger().leased_keys():
        return "Dang co video render giu clip (che do moi clip 1 lan)."
    return None


def plan_cleanup(max_uses: int, *, with_ids: bool = False) -> dict:
    """Clip da dung >= ``max_uses`` video, theo tung thu vien (cu -> moi)."""
    from src.db import media_repo
    from src.utils.clip_usage import get_clip_usage_ledger

    max_uses = max(0, int(max_uses or 0))
    result = {"maxUses": max_uses, "libraries": [], "totalClips": 0, "totalHours": 0.0}
    if max_uses <= 0:
        return result
    scanned, key_duration = _scan_libraries()
    counts = media_repo.get_clip_use_counts(key_duration.keys())
    leased = get_clip_usage_ledger().leased_keys()
    total_seconds = 0.0
    for entry in scanned:
        lib = entry["record"]
        hits = [
            (asset_id, duration) for asset_id, key, duration in entry["clips"]
            if asset_id and key not in leased and int(counts.get(key, 0)) >= max_uses
        ]
        if not hits:
            continue
        seconds = sum(duration for _id, duration in hits)
        total_seconds += seconds
        row = {
            "id": lib.get("id"),
            "name": lib.get("name"),
            "createdAt": lib.get("createdAt"),
            "clips": len(hits),
            "hours": round(seconds / 3600, 2),
            "remaining": len(entry["clips"]) - len(hits),
        }
        row["emptyAfter"] = row["remaining"] == 0
        if with_ids:
            row["clipIds"] = [asset_id for asset_id, _dur in hits]
        result["libraries"].append(row)
        result["totalClips"] += len(hits)
    result["totalHours"] = round(total_seconds / 3600, 2)
    return result


def execute_cleanup(max_uses: int) -> dict:
    """Xoa clip da cham gioi han khoi thu vien (file + index). Dang render thi tu choi."""
    from src.utils.story_library import delete_story_library_assets

    if int(max_uses or 0) <= 0:
        raise AutoSourceError("Chua dat gioi han luot dung (maxClipUses) nen khong co gi de don.",
                              "cleanup_disabled")
    busy = _render_busy()
    if busy:
        raise AutoSourceBusy(f"Khong don luc nay: {busy}")
    plan = plan_cleanup(max_uses, with_ids=True)
    deleted = 0
    failed = 0
    libraries = []
    for row in plan["libraries"]:
        summary = delete_story_library_assets(row.pop("clipIds"), library_id=row["id"])
        deleted += int(summary.get("deletedCount") or 0)
        failed += len(summary.get("failedClipIds") or [])
        libraries.append({**row, "deleted": summary.get("deletedCount", 0),
                          "remaining": summary.get("remainingCount", row["remaining"])})
    invalidate_report_cache()
    logger.info(f"[AutoSource] Don clip >= {max_uses} lan dung: xoa {deleted}, loi {failed}")
    return {"maxUses": int(max_uses), "deleted": deleted, "failed": failed,
            "hours": plan["totalHours"], "libraries": libraries}


# --------------------------------------------------------------------------- #
# Keyword plan
# --------------------------------------------------------------------------- #
def plan_keywords(settings: dict | None = None, limit: int | None = None) -> dict:
    """Tu khoa cho luot sau: tu khoa dang do trang truoc, roi xen ke file / bo sinh.

    Moi cap (tu khoa, nguon): ``used`` (DB tu khoa ghi da dung) / ``exhausted`` (con
    tro da het trang) -> bo; ``resume`` -> tai tiep tu ``page``; ``new`` -> trang 1.
    """
    from src.db import keyword_repo, video_cursor_repo
    from src.db.keyword_repo import normalize_keyword
    from src.utils.auto_source_keywords import file_keywords, generated_keywords, interleave

    settings = settings or load_settings()
    providers = list(settings["providers"])
    limit = settings["keywordsPerRun"] if limit is None else max(1, int(limit))

    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(keyword: str, source: str):
        normalized = normalize_keyword(keyword)
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append((str(keyword).strip(), source))

    for provider in providers:
        for doc in video_cursor_repo.list_open_cursors(provider):
            add(doc.get("keyword") or doc.get("normalized") or "", "cursor")
    file_list = file_keywords() if settings.get("useKeywordFile") else []
    bank = generated_keywords()
    file_set = set(file_list)
    for keyword in interleave(file_list, bank):
        add(keyword, "file" if keyword in file_set else "bank")

    words = [keyword for keyword, _source in candidates]
    records = {p: keyword_repo.get_keyword_records(p, words) for p in providers}
    cursors = {p: video_cursor_repo.get_cursors(p, words) for p in providers}

    picked: list[dict] = []
    remaining = {"file": 0, "bank": 0}
    for keyword, source in candidates:
        normalized = normalize_keyword(keyword)
        states = {}
        for provider in providers:
            record = records[provider].get(normalized)
            cursor = cursors[provider].get(normalized)
            if keyword_repo.is_used(record):
                states[provider] = {"state": "used"}
            elif cursor and cursor.get("exhausted"):
                states[provider] = {"state": "exhausted"}
            elif cursor:
                states[provider] = {"state": "resume", "page": int(cursor.get("next_page") or 1)}
            else:
                states[provider] = {"state": "new", "page": 1}
        if not any(item["state"] in ("resume", "new") for item in states.values()):
            continue
        if source in remaining:
            remaining[source] += 1
        if len(picked) < limit:
            picked.append({"keyword": keyword, "source": source, "providers": states})
    return {
        "keywords": picked,
        "available": remaining,
        "bankSize": len(bank),
        "fileSize": len(file_list),
    }


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
_run_lock = threading.Lock()
_active: dict = {"run": None}
_cancel_event = threading.Event()


def _run_path(run_id: str) -> str:
    return os.path.join(_runs_dir(), f"{run_id}.json")


def _save_run(run: dict) -> None:
    run["updatedAt"] = _utc_now()
    _write_json(_run_path(run["runId"]), run)


def list_runs(limit: int = 20) -> list[dict]:
    root = _runs_dir()
    if not os.path.isdir(root):
        return []
    runs = [data for name in os.listdir(root) if name.endswith(".json")
            if isinstance(data := _read_json(os.path.join(root, name)), dict)]
    active = _active.get("run")
    for run in runs:
        # Luot "running" ma khong phai luot dang chay trong process nay: app da tat giua chung.
        if run.get("status") == "running" and (not active or active.get("runId") != run.get("runId")):
            run["status"] = "interrupted"
    runs.sort(key=lambda run: str(run.get("startedAt") or ""), reverse=True)
    return runs[:max(1, int(limit))]


def _prune_runs() -> None:
    root = _runs_dir()
    if not os.path.isdir(root):
        return
    names = sorted(name for name in os.listdir(root) if name.endswith(".json"))
    for name in names[:-_RUN_HISTORY_KEEP]:
        try:
            os.remove(os.path.join(root, name))
        except OSError:
            pass


def is_running() -> bool:
    return _run_lock.locked()


def current_run() -> dict | None:
    """Ban luu tren dia cua luot dang chay (worker van sua dict trong RAM)."""
    run = _active.get("run")
    return _read_json(_run_path(run["runId"])) if run else None


def next_run_at(settings: dict | None = None, now: datetime | None = None) -> str | None:
    settings = settings or load_settings()
    if not settings["enabled"]:
        return None
    now = now or datetime.now()
    slot = now.replace(hour=settings["runHour"], minute=0, second=0, microsecond=0)
    if load_state().get("lastScheduledDate") == now.date().isoformat():
        slot += timedelta(days=1)
    elif now >= slot:
        # Qua gio ma hom nay chua chay: lan kiem tra ke tiep (<= 5 phut) se chay.
        slot = now
    return slot.isoformat(timespec="minutes")


def start_run(trigger: str = "manual", *, background: bool = True) -> dict:
    """Bat dau mot luot. Dang co luot khac thi ``AutoSourceBusy``."""
    if not _run_lock.acquire(blocking=False):
        raise AutoSourceBusy("Dang co mot luot tu dong tim nguon chay.")
    try:
        _cancel_event.clear()
        run = {
            "runId": f"as-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}",
            "trigger": trigger,
            "status": "running",
            "startedAt": _utc_now(),
            "steps": [],
            "message": "Bat dau...",
        }
        _active["run"] = run
        _save_run(run)
    except Exception:
        _active["run"] = None
        _run_lock.release()
        raise

    if background:
        threading.Thread(target=_execute_run, args=(run,), daemon=True).start()
    else:
        _execute_run(run)
    return run


def request_cancel() -> dict | None:
    """Huy luot dang chay: dung tai (giu video da tai), tam dung cat clip."""
    from src.utils import story_bulk_harvest as harvest

    # Chi worker sua + ghi dict cua luot; o day chi doc ban tren dia.
    run = current_run()
    if not run:
        return None
    _cancel_event.set()
    job_id = (run.get("harvest") or {}).get("jobId")
    if job_id:
        harvest.request_harvest_cancel(job_id)
        if harvest.is_commit_active(job_id):
            harvest.request_commit_pause(job_id)
    return {**run, "cancelRequested": True}


def _step(run: dict, step: str, status: str, message: str, **extra) -> None:
    run["steps"].append({"step": step, "status": status, "message": message, "at": _utc_now(), **extra})
    run["message"] = message
    _save_run(run)
    logger.info(f"[AutoSource] {run['runId']} {step}: {status} - {message}")


def _cleanup_old_jobs(keep_days: int) -> list[str]:
    """Xoa job harvest TU DONG da cat xong het (khong con video cho) qua ``keep_days`` ngay."""
    from src.utils import story_bulk_harvest as harvest

    if keep_days <= 0:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    removed = []
    for job in harvest.list_harvest_jobs():
        if job.get("origin") != "auto" or job.get("commitStatus") != "completed" or job.get("keptItems"):
            continue
        try:
            started = datetime.strptime(str(job.get("startedAt") or "").rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        if started.replace(tzinfo=timezone.utc) < cutoff and harvest.delete_harvest_job(job["jobId"]):
            removed.append(job["jobId"])
    return removed


def _wait_harvest(job_id: str) -> dict:
    from src.utils import story_bulk_harvest as harvest

    while True:
        progress = harvest.load_harvest_progress(job_id) or {}
        if progress.get("status") in harvest.TERMINAL_HARVEST_STATUSES:
            return progress
        if harvest.is_harvest_stale(progress):
            return {**progress, "status": "failed", "message": "Worker harvest da dung bat thuong."}
        time.sleep(_HARVEST_POLL_SECONDS)


def _execute_run(run: dict) -> None:
    from src.db import mongo
    from src.utils import story_bulk_harvest as harvest
    from src.utils.story_library import get_library

    try:
        settings = load_settings()
        run["settings"] = settings
        if not mongo.is_configured() or not mongo.is_available(force=True):
            raise AutoSourceError(mongo.unavailable_message(), "mongo_unavailable")

        # 1. Kho
        report = stock_report(refresh=True)
        run["stock"] = {"overall": report["overall"], "usage": report["usage"], "target": report["target"],
                        "disk": report["disk"]}
        overall = report["overall"]
        _step(run, "report", "ok",
              f"Kho: {overall['uniqueClips']} clip, chua dung {overall['unused']} "
              f"({overall['unusedHours']} gio)")

        # 2. Don clip da cham gioi han
        cap = settings["maxClipUses"]
        if cap > 0:
            plan = plan_cleanup(cap)
            if not plan["totalClips"]:
                _step(run, "cleanup", "ok", f"Khong co clip nao dung >= {cap} lan.")
            elif not settings["autoCleanup"]:
                run["cleanup"] = plan
                _step(run, "cleanup", "skipped",
                      f"{plan['totalClips']} clip dung >= {cap} lan ({plan['totalHours']} gio) - "
                      f"chua bat tu dong don, xoa tay o Settings.")
            else:
                try:
                    run["cleanup"] = execute_cleanup(cap)
                    _step(run, "cleanup", "ok", f"Da xoa {run['cleanup']['deleted']} clip dung >= {cap} lan.")
                except AutoSourceBusy as exc:
                    _step(run, "cleanup", "skipped", str(exc))
        removed_jobs = _cleanup_old_jobs(settings["keepJobDays"])
        if removed_jobs:
            _step(run, "jobs", "ok", f"Xoa {len(removed_jobs)} job harvest tu dong cu.", jobIds=removed_jobs)

        # 3. Tim + tai nguon moi
        target_id = settings["targetLibraryId"]
        if not target_id or not get_library(target_id):
            _step(run, "harvest", "skipped", "Chua chon thu vien dich (hoac thu vien da bi xoa).")
            return _finish(run, "completed")
        target = report.get("target") or {}
        if settings["minStockHours"] and float(target.get("unusedHours") or 0) >= settings["minStockHours"]:
            _step(run, "harvest", "skipped",
                  f"Kho thu vien dich con {target.get('unusedHours')} gio >= {settings['minStockHours']} gio.")
            return _finish(run, "completed")
        free_gb = _disk_free_gb(Config.STORY_LIBRARY_DIR)
        if free_gb is not None and free_gb < settings["minFreeGb"]:
            _step(run, "harvest", "skipped",
                  f"O thu vien con {free_gb} GB < {settings['minFreeGb']} GB.")
            return _finish(run, "completed")
        plan = plan_keywords(settings)
        keywords = [item["keyword"] for item in plan["keywords"]]
        run["keywords"] = plan["keywords"]
        if not keywords:
            _step(run, "harvest", "skipped", "Het tu khoa chua dung: them tu vao keywords/video-keywords.txt.")
            return _finish(run, "completed")
        if _cancel_event.is_set():
            return _finish(run, "cancelled")

        progress = harvest.start_harvest_job(
            keywords=keywords,
            providers=settings["providers"],
            library_id=target_id,
            tags=["auto-source"],
            landscape_only=settings["landscapeOnly"],
            max_per_keyword=0,
            min_free_gb=settings["minFreeGb"],
            skip_used_keywords=True,
            use_page_cursor=True,
            skip_known_sources=True,
            max_total_videos=settings["maxVideosPerRun"],
            origin="auto",
        )
        job_id = progress["jobId"]
        run["harvest"] = {"jobId": job_id}
        _step(run, "harvest", "running", f"Dang tai theo {len(keywords)} tu khoa (job {job_id})...")
        final = _wait_harvest(job_id)
        run["harvest"] = {
            "jobId": job_id,
            "status": final.get("status"),
            "downloaded": int(final.get("downloaded") or 0),
            "skippedKnown": int(final.get("skippedKnown") or 0),
            "cursorSkipped": int(final.get("cursorSkipped") or 0),
            "searchRequests": int(final.get("searchRequests") or 0),
            "failed": int(final.get("failed") or 0),
            "gb": round(int(final.get("bytesDownloaded") or 0) / 1024 ** 3, 2),
            "message": final.get("message"),
        }
        _step(run, "harvest", "ok" if final.get("status") == "completed" else final.get("status") or "failed",
              final.get("message") or "")
        if _cancel_event.is_set() or final.get("status") in {"cancelled", "failed"}:
            return _finish(run, "cancelled" if _cancel_event.is_set() else "failed")

        # 4. Cat clip vao thu vien dich
        pending = [item for item in harvest.load_harvest_manifest(job_id)["items"] if item.get("status") == "kept"]
        if not pending:
            _step(run, "commit", "ok", "Khong co video moi de cat.")
            return _finish(run, "completed")
        session_id = f"hvc-{uuid.uuid4().hex[:8]}"
        if not harvest.claim_commit(job_id, session_id):
            raise AutoSourceError(f"Job {job_id} dang duoc cat o noi khac.")
        _step(run, "commit", "running", f"Dang cat {len(pending)} video thanh clip...")
        try:
            result = harvest.commit_harvest_job(
                job_id, library_id=target_id, session_id=session_id, delete_staging=True,
            )
        except Exception:
            harvest.mark_commit_failed(job_id)
            raise
        finally:
            harvest.release_commit(job_id, session_id)
        run["commit"] = {"added": len(result["added"]), "libraryIds": result["libraryIds"],
                         "paused": result["paused"]}
        invalidate_report_cache()
        _step(run, "commit", "paused" if result["paused"] else "ok",
              f"Da them {len(result['added'])} clip vao {', '.join(result['libraryIds'])}.")
        return _finish(run, "cancelled" if result["paused"] else "completed")
    except Exception as exc:
        logger.error(f"[AutoSource] {run['runId']} loi: {exc}", exc_info=True)
        run["error"] = str(exc)
        _step(run, "error", "failed", f"Loi: {exc}")
        return _finish(run, "failed")


def _finish(run: dict, status: str) -> None:
    run["status"] = status
    run["finishedAt"] = _utc_now()
    try:
        _save_run(run)
        _prune_runs()
    finally:
        _active["run"] = None
        if _run_lock.locked():
            _run_lock.release()


# --------------------------------------------------------------------------- #
# Lich hang ngay
# --------------------------------------------------------------------------- #
_scheduler_started = threading.Event()


def scheduler_tick(now: datetime | None = None) -> bool:
    """Chay luot hang ngay neu da toi gio va hom nay chua chay. True = da chay."""
    settings = load_settings()
    if not settings["enabled"]:
        return False
    now = now or datetime.now()
    today = now.date().isoformat()
    if now.hour < settings["runHour"] or load_state().get("lastScheduledDate") == today:
        return False
    if is_running():
        return False
    # Ghi truoc khi chay: luot loi khong bi chay lai lien tuc trong ngay.
    _update_state(lastScheduledDate=today, lastScheduledAt=_utc_now())
    try:
        start_run("schedule", background=False)
    except AutoSourceBusy:
        return False
    return True


def start_scheduler() -> bool:
    """Thread nen kiem tra moi ``_SCHEDULER_TICK_SECONDS``. Goi 1 lan khi server khoi dong."""
    if _scheduler_started.is_set():
        return False
    _scheduler_started.set()

    def loop():
        while True:
            try:
                scheduler_tick()
            except Exception as exc:  # pragma: no cover - khong de thread chet
                logger.error(f"[AutoSource] Scheduler loi: {exc}", exc_info=True)
            time.sleep(_SCHEDULER_TICK_SECONDS)

    threading.Thread(target=loop, name="auto-source-scheduler", daemon=True).start()
    logger.info("[AutoSource] Scheduler da bat (kiem tra moi 5 phut).")
    return True
