"""Luu tru run cua pipeline kich ban -> video.

Moi run nam o ``PIPELINE_DIR/runs/<runId>/``:

- ``run.json``      trang thai run + moi item (ghi atomic, xem ``_save_json``)
- ``events.jsonl``  nhat ky su kien, chi ghi noi them
- ``uploads/``      file nguoi dung upload (giu nguyen)
- ``items/<id>/``   txt da chuan hoa + mp3/srt do DGT xuat canh txt

Reconciler va cac route cung sua ``run.json`` nen moi lan sua deu di qua
``update_run`` (khoa theo tung run). Moi item co ``rev`` tang moi lan doi: reconciler
chay handler tren ban sao ngoai khoa, chi ghi lai neu ``rev`` chua bi route doi.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable

from src.config import Config
from src.utils.story_video_pipeline import _load_json, _save_json

RUN_TERMINAL = {"completed", "completed_with_errors", "cancelled"}
ITEM_TERMINAL = {"done", "failed", "cancelled"}

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_run_id() -> str:
    return f"pl-{uuid.uuid4().hex[:8]}"


def runs_root() -> str:
    return os.path.abspath(os.path.join(Config.PIPELINE_DIR, "runs"))


def run_dir(run_id: str) -> str:
    if not _valid_run_id(run_id):
        raise ValueError(f"runId khong hop le: {run_id!r}")
    return os.path.join(runs_root(), run_id)


def item_dir(run_id: str, item_id: str) -> str:
    return os.path.join(run_dir(run_id), "items", item_id)


def _valid_run_id(run_id: str) -> bool:
    return bool(run_id) and run_id.startswith("pl-") and run_id[3:].isalnum() and len(run_id) <= 40


def _run_path(run_id: str) -> str:
    return os.path.join(run_dir(run_id), "run.json")


def _events_path(run_id: str) -> str:
    return os.path.join(run_dir(run_id), "events.jsonl")


def run_lock(run_id: str) -> threading.RLock:
    with _locks_guard:
        lock = _locks.get(run_id)
        if lock is None:
            lock = _locks[run_id] = threading.RLock()
        return lock


def load_run(run_id: str) -> dict | None:
    if not _valid_run_id(run_id):
        return None
    return _load_json(_run_path(run_id))


def save_run(run: dict) -> None:
    run["updatedAt"] = utc_now()
    _save_json(_run_path(run["runId"]), run)


def update_run(run_id: str, mutate: Callable[[dict], object]) -> dict | None:
    """Khoa run, doc, goi ``mutate(run)``, ghi lai. Tra ve run moi (None neu khong co)."""
    with run_lock(run_id):
        run = load_run(run_id)
        if run is None:
            return None
        mutate(run)
        save_run(run)
        return run


def list_run_ids() -> list[str]:
    root = runs_root()
    if not os.path.isdir(root):
        return []
    return [name for name in os.listdir(root) if _valid_run_id(name) and os.path.isfile(_run_path(name))]


def list_runs(limit: int = 50) -> list[dict]:
    runs = [run for run in (load_run(rid) for rid in list_run_ids()) if run]
    runs.sort(key=lambda run: run.get("createdAt") or "", reverse=True)
    return runs[: max(1, limit)]


def find_item(run: dict, item_id: str) -> dict | None:
    for item in run.get("items", []):
        if item.get("itemId") == item_id:
            return item
    return None


def touch_item(item: dict) -> None:
    """Danh dau item vua doi (tang rev) — bat buoc moi khi sua item."""
    item["rev"] = int(item.get("rev") or 0) + 1
    item["updatedAt"] = utc_now()


def snapshot(run: dict) -> dict:
    return copy.deepcopy(run)


def append_event(run: dict, item_id: str | None, stage: str, status: str, message: str) -> dict:
    """Ghi 1 dong vao events.jsonl. Goi khi dang giu khoa run (seq nam trong run.json)."""
    seq = int(run.get("eventSeq") or 0) + 1
    run["eventSeq"] = seq
    event = {
        "seq": seq,
        "at": utc_now(),
        "itemId": item_id,
        "stage": stage,
        "status": status,
        "message": message,
    }
    path = _events_path(run["runId"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_events(run_id: str, since: int = 0, limit: int = 500) -> list[dict]:
    path = _events_path(run_id)
    if not os.path.isfile(path):
        return []
    events: list[dict] = []
    with open(path, encoding="utf-8") as file_obj:
        for line in file_obj:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(event.get("seq") or 0) > since:
                events.append(event)
    return events[-limit:] if limit else events


def summarize_run(run: dict) -> dict:
    """Ban rut gon cho danh sach run (khong kem items)."""
    counts: dict[str, int] = {"total": 0}
    for item in run.get("items", []):
        counts["total"] += 1
        status = item.get("status") or "pending"
        counts[status] = counts.get(status, 0) + 1
    return {
        "runId": run.get("runId"),
        "name": run.get("name"),
        "status": run.get("status"),
        "counts": counts,
        "createdAt": run.get("createdAt"),
        "updatedAt": run.get("updatedAt"),
    }


def refresh_run_status(run: dict) -> None:
    """Tinh lai status cua run tu cac item (tru khi run da bi huy / tam dung)."""
    if run.get("status") in ("cancelled", "paused"):
        return
    items = run.get("items", [])
    if items and all(item.get("status") in ITEM_TERMINAL for item in items):
        bad = any(item.get("status") in ("failed", "cancelled") for item in items)
        run["status"] = "completed_with_errors" if bad else "completed"
        run.setdefault("finishedAt", utc_now())
    elif items:
        run["status"] = "running"
