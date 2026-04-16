import json
import os
import time
from datetime import datetime
from typing import Any

from src.config import Config


MAX_RECENT_LOGS = 80


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _progress_path(job_id: str) -> str:
    return os.path.join(Config.STORAGE_DIR, "jobs", job_id, "progress.json")


def _default_progress(job_id: str, manifest: dict | None = None) -> dict:
    output_video = (manifest or {}).get("output_video")
    image_paths = (manifest or {}).get("image_paths", [])
    review_clips = (manifest or {}).get("review_clips", [])
    return {
        "jobId": job_id,
        "status": "completed" if output_video else "idle",
        "stage": "completed" if output_video else "not_started",
        "percent": 100 if output_video else 0,
        "message": "Render da hoan tat." if output_video else "Chua co tien trinh render.",
        "startedAt": None,
        "updatedAt": _utc_now(),
        "totals": {
            "images": len(image_paths),
            "segments": 0,
            "chunks": 0,
            "reviewClips": len(review_clips),
        },
        "current": {
            "image": 0,
            "segment": 0,
            "chunk": 0,
        },
        "recentLogs": [],
        "stageDurations": {},
        "stageStartedAt": None,
        "stageStartedEpoch": None,
    }


def _read_progress(job_id: str, manifest: dict | None = None) -> dict:
    path = _progress_path(job_id)
    if not os.path.isfile(path):
        return _default_progress(job_id, manifest)

    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else _default_progress(job_id, manifest)
    except Exception:
        return _default_progress(job_id, manifest)


def _write_progress(job_id: str, data: dict):
    path = _progress_path(job_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)

    last_error = None
    for attempt in range(12):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt < 11:
                time.sleep(0.15)

    try:
        os.remove(tmp_path)
    except OSError:
        pass
    raise last_error


def _merge_mapping(base: dict, updates: dict | None) -> dict:
    if not isinstance(updates, dict):
        return base
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, (int, float, str, bool)) or value is None:
            merged[key] = value
    return merged


def _is_progress_value(value: Any) -> bool:
    if isinstance(value, (int, float, str, bool)) or value is None:
        return True
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_progress_value(item) for key, item in value.items())
    return False


def init_job_progress(job_id: str, totals: dict | None = None, message: str = "Bat dau render job.") -> dict:
    now = _utc_now()
    now_epoch = time.time()
    progress = _default_progress(job_id)
    progress.update(
        {
            "status": "running",
            "stage": "starting",
            "percent": 0,
            "message": message,
            "startedAt": now,
            "updatedAt": now,
            "stageStartedAt": now,
            "stageStartedEpoch": now_epoch,
            "stageDurations": {},
            "recentLogs": [{"time": now, "level": "info", "message": message}],
        }
    )
    progress["totals"] = _merge_mapping(progress["totals"], totals)
    _write_progress(job_id, progress)
    return progress


def update_job_progress(
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    percent: float | int | None = None,
    message: str | None = None,
    level: str = "info",
    totals: dict | None = None,
    current: dict | None = None,
    extra: dict[str, Any] | None = None,
) -> dict:
    progress = _read_progress(job_id)
    now = _utc_now()
    now_epoch = time.time()

    if status:
        progress["status"] = status
    if stage:
        old_stage = progress.get("stage")
        if old_stage and old_stage != stage:
            stage_started_epoch = progress.get("stageStartedEpoch")
            if isinstance(stage_started_epoch, (int, float)):
                durations = progress.get("stageDurations", {})
                if not isinstance(durations, dict):
                    durations = {}
                durations[old_stage] = round(float(durations.get(old_stage, 0)) + max(0, now_epoch - stage_started_epoch), 2)
                progress["stageDurations"] = durations
            progress["stageStartedAt"] = now
            progress["stageStartedEpoch"] = now_epoch
        progress["stage"] = stage
    if percent is not None:
        progress["percent"] = max(0, min(100, round(float(percent), 2)))
    if message:
        progress["message"] = message
        recent_logs = list(progress.get("recentLogs", []))
        recent_logs.append({"time": now, "level": level, "message": message})
        progress["recentLogs"] = recent_logs[-MAX_RECENT_LOGS:]

    progress["totals"] = _merge_mapping(progress.get("totals", {}), totals)
    progress["current"] = _merge_mapping(progress.get("current", {}), current)

    if isinstance(extra, dict):
        for key, value in extra.items():
            if _is_progress_value(value):
                progress[key] = value

    progress["updatedAt"] = now
    _write_progress(job_id, progress)
    return progress


def load_job_progress(job_id: str, manifest: dict | None = None) -> dict:
    return _read_progress(job_id, manifest)
