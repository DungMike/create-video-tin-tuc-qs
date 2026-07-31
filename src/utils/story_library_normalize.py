"""Repair story-library clips that don't match the canonical clip spec.

Every render logs a line like::

    Excluded 1848 clip(s) with mismatched resolution/pix_fmt/color tags
    (expected 1920x1080 yuv420p/tv/bt709)

— clips that entered the library before the write paths forced the canonical
spec (see src/utils/clip_canonical.py). They are dead weight: silently dropped
from the selection pool of every render, forever. This module finds them and
re-encodes them in place so the whole library is one uniform format.

``scan_libraries`` reports what is off-spec (cheap: reuses the per-library probe
cache the render already fills). ``StoryLibraryNormalizeRunner`` does the repair
in the background with the same progress-JSON + cancel-marker contract as
``StoryLibraryBakeRunner``, so the UI can poll and cancel it the same way.

Only clip *format* is touched. Clip length is left alone — the render trims each
clip to the library's segment duration at concat time, so a 5s clip in a 3s
library is already handled and re-cutting it would throw away footage.
"""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from src.config import Config
from src.utils.clip_canonical import describe_spec, normalize_clip_file
from src.utils.clip_spec_validation import (
    expected_spec,
    matches_expected_spec,
    probe_clip_spec,
    probe_specs_cached,
    store_spec,
)
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger
from src.utils.story_library import (
    _index_lock,
    get_library,
    library_clip_duration,
    load_libraries,
    load_story_library_index,
    resolve_library_id,
    save_story_library_index,
    story_library_root,
)
from src.utils.story_video_pipeline import _load_json, _save_json


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_dir(job_id: str) -> str:
    path = os.path.join(Config.STORY_LIBRARY_DIR, "_normalize_jobs", job_id)
    os.makedirs(path, exist_ok=True)
    return path


def _progress_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "progress.json")


def _cancel_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "cancel.requested")


def load_normalize_progress(job_id: str) -> dict | None:
    return _load_json(_progress_path(job_id))


def is_normalize_cancel_requested(job_id: str) -> bool:
    return os.path.isfile(_cancel_path(job_id))


def request_normalize_cancel(job_id: str) -> dict | None:
    progress = load_normalize_progress(job_id)
    if not progress:
        return None
    if progress.get("status") not in {"completed", "failed", "partial", "cancelled"}:
        with open(_cancel_path(job_id), "w", encoding="utf-8") as file_obj:
            file_obj.write(_utc_now())
        progress["status"] = "cancelling"
        progress["message"] = "Đang hủy chuẩn hóa..."
        progress["updatedAt"] = _utc_now()
        _save_json(_progress_path(job_id), progress)
    return progress


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #
def _library_clip_paths(library_id) -> list[tuple[str, dict]]:
    """(absolute_path, asset) for every asset of a library whose file exists."""
    root = story_library_root(library_id)
    items: list[tuple[str, dict]] = []
    for asset in load_story_library_index(library_id).get("assets", []):
        rel_path = str(asset.get("relative_path") or "").strip()
        if not rel_path:
            continue
        path = os.path.join(root, rel_path)
        if os.path.isfile(path):
            items.append((path, asset))
    return items


def scan_library(library_id) -> dict:
    """Off-spec report for one library.

    Reuses the render's probe cache, so a library the pipeline already scanned
    costs a handful of stat() calls rather than thousands of ffprobe runs.
    """
    resolved = resolve_library_id(library_id)
    record = get_library(resolved) or {}
    items = _library_clip_paths(resolved)
    specs = probe_specs_cached(story_library_root(resolved), [path for path, _asset in items])

    combos: dict[str, int] = {}
    mismatched: list[str] = []
    for path, _asset in items:
        spec = specs.get(path)
        if matches_expected_spec(spec):
            continue
        mismatched.append(path)
        combos[describe_spec(spec)] = combos.get(describe_spec(spec), 0) + 1

    return {
        "libraryId": resolved,
        "name": record.get("name") or resolved,
        "totalClips": len(items),
        "mismatchedClips": len(mismatched),
        "combos": [
            {"spec": spec, "count": count}
            for spec, count in sorted(combos.items(), key=lambda item: -item[1])
        ],
    }


def scan_libraries(library_ids: list | None = None) -> dict:
    """Off-spec report for the given libraries (all of them when omitted)."""
    ids = library_ids or [lib.get("id") for lib in load_libraries() if lib.get("id")]
    libraries = [scan_library(library_id) for library_id in ids]
    return {
        "expected": describe_spec(expected_spec()),
        "libraries": libraries,
        "totalClips": sum(item["totalClips"] for item in libraries),
        "mismatchedClips": sum(item["mismatchedClips"] for item in libraries),
    }


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
class StoryLibraryNormalizeRunner:
    """Re-encodes off-spec clips in place, one library at a time.

    Each clip is written to a sibling temp file and only swapped in once the
    result verifies as canonical, so a failed or cancelled run can never leave a
    half-written clip in the library.
    """

    def __init__(self, job_id: str, library_ids: list, *, include_all: bool = False):
        self.job_id = job_id
        self.library_ids = [resolve_library_id(library_id) for library_id in library_ids]
        # include_all re-encodes every clip, not just the off-spec ones. Off by
        # default: repairing 11% of a library beats re-encoding 100% of it.
        self.include_all = include_all

        self._lock = threading.Lock()
        self._max_workers = max(1, int(Config.STORY_BAKE_MAX_WORKERS))
        self.completed_count = 0
        self.failed_count = 0

        self._targets = self._collect_targets()
        self.progress = {
            "jobId": job_id,
            "status": "pending",
            "libraryIds": self.library_ids,
            "expected": describe_spec(expected_spec()),
            "total": len(self._targets),
            "completed": 0,
            "failed": 0,
            "percent": 0,
            "message": "Chờ xử lý...",
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
        }
        self._save_progress()

    def _collect_targets(self) -> list[dict]:
        targets: list[dict] = []
        for library_id in self.library_ids:
            root = story_library_root(library_id)
            items = _library_clip_paths(library_id)
            specs = probe_specs_cached(root, [path for path, _asset in items])
            segment = max(1, library_clip_duration(library_id, max(1, int(Config.STORY_CLIP_DURATION))))
            for path, asset in items:
                spec = specs.get(path)
                if not self.include_all and matches_expected_spec(spec):
                    continue
                targets.append({
                    "libraryId": library_id,
                    "root": root,
                    "path": path,
                    "assetId": str(asset.get("id") or ""),
                    "spec": spec,
                    "segment": segment,
                })
        return targets

    def _save_progress(self):
        with self._lock:
            self.progress["updatedAt"] = _utc_now()
            _save_json(_progress_path(self.job_id), self.progress)

    def _emit_progress(self, *, status: str | None = None, message: str | None = None):
        with self._lock:
            total = self.progress["total"] or 1
            finished = self.completed_count + self.failed_count
            self.progress["completed"] = self.completed_count
            self.progress["failed"] = self.failed_count
            self.progress["percent"] = round(min(100, max(0, (finished / total) * 100)), 1)
            if status:
                self.progress["status"] = status
            elif is_normalize_cancel_requested(self.job_id):
                self.progress["status"] = "cancelling"
            else:
                self.progress["status"] = "running"
            self.progress["message"] = message or (
                f"Đã chuẩn hóa {finished}/{self.progress['total']} clip..."
            )
            self.progress["updatedAt"] = _utc_now()
            _save_json(_progress_path(self.job_id), self.progress)

    def start_async(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        logger.info(
            f"[StoryNormalize:{self.job_id}] Started: {len(self._targets)} clip(s) "
            f"across {len(self.library_ids)} library(ies), "
            f"expected={describe_spec(expected_spec())}, max_workers={self._max_workers}"
        )

    def _normalize_one(self, target: dict) -> tuple[bool, float]:
        """Re-encode one clip in place. Returns (ok, new_duration)."""
        if is_normalize_cancel_requested(self.job_id):
            return False, 0.0

        path = target["path"]
        tmp_path = f"{os.path.splitext(path)[0]}.normalizing.mp4"
        try:
            ok = normalize_clip_file(
                path,
                tmp_path,
                spec=target.get("spec"),
                segment_seconds=target.get("segment"),
            )
            if not ok:
                return False, 0.0

            new_spec = probe_clip_spec(tmp_path)
            if not matches_expected_spec(new_spec):
                logger.warning(
                    f"[StoryNormalize:{self.job_id}] {os.path.basename(path)} still off-spec "
                    f"after re-encode ({describe_spec(new_spec)}); keeping the original."
                )
                return False, 0.0

            duration = FFmpegHelper.probe_duration(tmp_path)
            # os.replace is atomic, but Windows refuses it while a render holds the
            # clip open — surfaced as a failed clip rather than a crashed job.
            os.replace(tmp_path, path)
            store_spec(target["root"], path, new_spec)
            return True, duration
        except OSError as exc:
            logger.error(f"[StoryNormalize:{self.job_id}] Could not replace {path}: {exc}")
            return False, 0.0
        finally:
            if os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _run(self):
        if not self._targets:
            self._finish(status="completed", message="Tất cả clip đã đúng chuẩn, không cần xử lý.")
            return

        self._emit_progress(status="running", message="Bắt đầu chuẩn hóa...")

        durations: dict[str, dict[str, float]] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(self._normalize_one, target): target for target in self._targets}
            for future in as_completed(futures):
                target = futures[future]
                try:
                    ok, duration = future.result()
                except Exception as exc:  # noqa: BLE001 - one bad clip must not kill the job
                    logger.error(
                        f"[StoryNormalize:{self.job_id}] {target['path']} raised: {exc}", exc_info=True
                    )
                    ok, duration = False, 0.0
                with self._lock:
                    if ok:
                        self.completed_count += 1
                        if duration > 0 and target["assetId"]:
                            durations.setdefault(target["libraryId"], {})[target["assetId"]] = duration
                    else:
                        self.failed_count += 1
                self._emit_progress()

        for library_id, by_asset in durations.items():
            self._sync_index_durations(library_id, by_asset)

        if is_normalize_cancel_requested(self.job_id):
            self._finish(
                status="cancelled",
                message=f"Đã hủy — {self.completed_count} clip đã được chuẩn hóa.",
            )
            return

        status = "completed" if self.failed_count == 0 else "partial"
        self._finish(
            status=status,
            message=(
                f"Chuẩn hóa xong {self.completed_count} clip"
                + (f", {self.failed_count} clip lỗi." if self.failed_count else ".")
            ),
        )

    def _sync_index_durations(self, library_id, by_asset: dict[str, float]):
        """Write back durations that shifted by more than a frame after re-encode."""
        try:
            with _index_lock(library_id):
                index = load_story_library_index(library_id)
                changed = False
                for asset in index.get("assets", []):
                    new_duration = by_asset.get(str(asset.get("id") or ""))
                    if new_duration is None:
                        continue
                    if abs(float(asset.get("duration") or 0) - new_duration) > 0.05:
                        asset["duration"] = round(new_duration, 3)
                        changed = True
                if changed:
                    save_story_library_index(index, library_id)
        except Exception as exc:  # noqa: BLE001 - index sync is best-effort
            logger.warning(
                f"[StoryNormalize:{self.job_id}] Could not sync durations for {library_id}: {exc}"
            )

    def _finish(self, *, status: str, message: str):
        with self._lock:
            self.progress["status"] = status
            self.progress["message"] = message
            self.progress["completed"] = self.completed_count
            self.progress["failed"] = self.failed_count
            self.progress["percent"] = 100 if status != "cancelled" else self.progress["percent"]
            self.progress["updatedAt"] = _utc_now()
            _save_json(_progress_path(self.job_id), self.progress)
        logger.info(f"[StoryNormalize:{self.job_id}] {status}: {message}")


def start_normalize_job(library_ids: list, *, include_all: bool = False) -> StoryLibraryNormalizeRunner:
    runner = StoryLibraryNormalizeRunner(
        f"norm-{str(uuid.uuid4())[:8]}", library_ids, include_all=include_all
    )
    runner.start_async()
    return runner
