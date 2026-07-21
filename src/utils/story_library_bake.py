"""Background runner that bakes a TV effect into a clip library (style-only).

Each 5s clip is re-encoded with the style-only filter (eq / noise / scanline /
vignette) and marked ``styled:true``. Waveform + CTA are NOT baked here — they
stay runtime overlays added at the render step, which also skips the style pass
for a styled library. Clips keep their 5s length (no pairing into longer units).

Concurrency mirrors ``StoryVideoBatchRunner``: a daemon thread drives a bounded
``ThreadPoolExecutor`` (``Config.STORY_BAKE_MAX_WORKERS``); progress is persisted
to JSON for UI polling and a ``cancel.requested`` marker stops it.
"""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger
from src.utils.story_library import (
    delete_library,
    load_story_library_index,
    save_story_library_index,
    story_library_root,
)
from src.utils.story_video_pipeline import _load_json, _save_json


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# --------------------------------------------------------------------------- #
# Shared ffmpeg bake primitives (used by StoryLibraryBakeRunner and by the
# import path that re-bakes freshly-added clips into an existing styled library).
# --------------------------------------------------------------------------- #
def _hwaccel_flags() -> list[str]:
    return ["-hwaccel", "cuda"] if Config.USE_GPU_NVENC else []


def _run_ffmpeg_with_fallback(cmd: list, hwaccel_len: int, cancel_cb) -> bool:
    """Run a bake command; on GPU-decode failure retry once with CPU decode."""
    ok = FFmpegHelper.run_command(cmd, cancel_callback=cancel_cb)
    if not ok and hwaccel_len and not cancel_cb():
        ok = FFmpegHelper.run_command(cmd[:2] + cmd[2 + hwaccel_len:], cancel_callback=cancel_cb)
    return ok


def _style_only_cmd(src_path: str, out_path: str, style_filter: str, hwaccel: list) -> list:
    cmd = ["ffmpeg", "-y", *hwaccel, "-i", src_path,
           "-vf", f"{style_filter},format=yuv420p", "-an"]
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(["-movflags", "+faststart", out_path])
    return cmd


def _bake_dir(job_id: str) -> str:
    path = os.path.join(Config.STORY_LIBRARY_DIR, "_bake_jobs", job_id)
    os.makedirs(path, exist_ok=True)
    return path


def _bake_progress_path(job_id: str) -> str:
    return os.path.join(_bake_dir(job_id), "progress.json")


def _bake_cancel_path(job_id: str) -> str:
    return os.path.join(_bake_dir(job_id), "cancel.requested")


def load_bake_progress(job_id: str) -> dict | None:
    return _load_json(_bake_progress_path(job_id))


def is_bake_cancel_requested(job_id: str) -> bool:
    return os.path.isfile(_bake_cancel_path(job_id))


def request_bake_cancel(job_id: str) -> dict | None:
    """Persist a cancellation request so the running bake job stops."""
    progress = load_bake_progress(job_id)
    if not progress:
        return None
    if progress.get("status") not in {"completed", "failed", "partial", "cancelled"}:
        with open(_bake_cancel_path(job_id), "w", encoding="utf-8") as file_obj:
            file_obj.write(_utc_now())
        progress["status"] = "cancelling"
        progress["message"] = "Đang hủy bake..."
        progress["updatedAt"] = _utc_now()
        _save_json(_bake_progress_path(job_id), progress)
    return progress


class StoryLibraryBakeRunner:
    """Bakes a TV style (style-only) into a new target library.

    The target library is created synchronously by the route (so duplicate-name
    errors surface immediately). On cancellation or a total failure the runner
    deletes the partial target library so no broken library lingers.
    """

    def __init__(
        self,
        job_id: str,
        *,
        source_library_id: str,
        target_library_id: str,
        target_name: str,
        style_filter: str,
        style_id: str,
        style_label: str,
    ):
        self.job_id = job_id
        self.source_library_id = source_library_id
        self.target_library_id = target_library_id
        self.target_name = target_name
        self.style_filter = style_filter
        self.style_id = style_id
        self.style_label = style_label

        self._lock = threading.RLock()
        self._max_workers = max(1, int(Config.STORY_BAKE_MAX_WORKERS))
        self.completed_count = 0
        self.failed_count = 0

        assets = load_story_library_index(source_library_id).get("assets", [])
        self._items = self._build_items(list(assets))

        self.progress = {
            "jobId": job_id,
            "status": "pending",
            "mode": "style",
            "sourceLibraryId": source_library_id,
            "targetLibraryId": target_library_id,
            "targetName": target_name,
            "styleId": style_id,
            "styleLabel": style_label,
            "total": len(self._items),
            "completed": 0,
            "failed": 0,
            "percent": 0,
            "message": "Chờ xử lý...",
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
        }
        self._save_progress()

    def _build_items(self, assets: list[dict]) -> list[dict]:
        """One work item per source clip — each 5s clip is baked style-only,
        keeping its own id (no pairing into longer units)."""
        return [{"out_id": str(a.get("id") or ""), "srcs": [a]} for a in assets if a.get("id")]

    def _save_progress(self):
        with self._lock:
            self.progress["updatedAt"] = _utc_now()
            _save_json(_bake_progress_path(self.job_id), self.progress)

    def _emit_progress(self, *, status: str | None = None, message: str | None = None):
        with self._lock:
            total = self.progress["total"] or 1
            finished = self.completed_count + self.failed_count
            self.progress["completed"] = self.completed_count
            self.progress["failed"] = self.failed_count
            self.progress["percent"] = round(min(100, max(0, (finished / total) * 100)), 1)
            if status:
                self.progress["status"] = status
            elif is_bake_cancel_requested(self.job_id):
                self.progress["status"] = "cancelling"
            else:
                self.progress["status"] = "running"
            self.progress["message"] = message or (
                f"Đã bake {finished}/{self.progress['total']} clip..."
            )
            self.progress["updatedAt"] = _utc_now()
            _save_json(_bake_progress_path(self.job_id), self.progress)

    def start_async(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        logger.info(
            f"[StoryBake:{self.job_id}] Started baking {len(self._items)} clips "
            f"(style-only, style={self.style_id}, "
            f"source={self.source_library_id} -> {self.target_library_id}, "
            f"max_workers={self._max_workers})."
        )

    def _src_abs(self, asset: dict) -> str | None:
        rel = str(asset.get("relative_path") or "").strip()
        if not rel:
            return None
        path = os.path.join(story_library_root(self.source_library_id), rel)
        return path if os.path.isfile(path) else None

    def _run_ffmpeg_with_fallback(self, cmd: list, hwaccel_len: int) -> bool:
        return _run_ffmpeg_with_fallback(
            cmd, hwaccel_len, lambda: is_bake_cancel_requested(self.job_id)
        )

    def _bake_one(self, item: dict) -> dict | None:
        """Bake one 5s clip style-only. Returns the new asset record or None."""
        if is_bake_cancel_requested(self.job_id):
            return None

        out_id = item["out_id"]
        srcs = [a for a in item["srcs"] if a]
        src_paths = [p for p in (self._src_abs(a) for a in srcs) if p]
        if not out_id or not src_paths:
            return None

        out_rel = f"clips/{out_id}.mp4"
        out_path = os.path.join(story_library_root(self.target_library_id), out_rel)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        hwaccel = _hwaccel_flags()

        # style-only: single clip, style filter, keep source metadata
        cmd = _style_only_cmd(src_paths[0], out_path, self.style_filter, hwaccel)
        if not self._run_ffmpeg_with_fallback(cmd, len(hwaccel)) or not os.path.isfile(out_path):
            return None
        a = srcs[0]
        return {
            "id": out_id,
            "source_type": a.get("source_type", "styled"),
            "source_name": a.get("source_name", ""),
            "relative_path": out_rel,
            "duration": a.get("duration", 0),
            "tags": list(a.get("tags", [])),
            "created_at": _utc_now(),
            "styled_from": self.source_library_id,
        }

    def _run(self):
        if not self._items:
            self._finish_failed("Thư viện nguồn không có clip nào để bake.")
            return

        self._emit_progress(status="running", message="Bắt đầu bake...")

        results: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(self._bake_one, item): idx for idx, item in enumerate(self._items)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    record = future.result()
                except Exception as exc:  # noqa: BLE001 - never let one unit kill the job
                    logger.error(f"[StoryBake:{self.job_id}] Unit {idx} raised: {exc}", exc_info=True)
                    record = None
                with self._lock:
                    if record is not None:
                        results[idx] = record
                        self.completed_count += 1
                    else:
                        self.failed_count += 1
                self._emit_progress()

        if is_bake_cancel_requested(self.job_id):
            self._cleanup_target()
            self._emit_progress(status="cancelled", message="Đã hủy bake.")
            logger.info(f"[StoryBake:{self.job_id}] Cancelled; partial library removed.")
            return

        if not results:
            self._cleanup_target()
            self._finish_failed("Bake thất bại cho toàn bộ clip.")
            return

        ordered = [results[idx] for idx in sorted(results)]
        save_story_library_index({"assets": ordered}, self.target_library_id)

        status = "completed" if self.failed_count == 0 else "partial"
        message = (
            f"Bake hoàn tất: {self.completed_count} clip"
            + (f", {self.failed_count} lỗi." if self.failed_count else ".")
        )
        self._emit_progress(status=status, message=message)
        logger.info(
            f"[StoryBake:{self.job_id}] Finished: completed={self.completed_count}, "
            f"failed={self.failed_count}, target={self.target_library_id}"
        )

    def _cleanup_target(self):
        try:
            delete_library(self.target_library_id, delete_clips=True)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            logger.warning(f"[StoryBake:{self.job_id}] Could not remove partial library: {exc}")

    def _finish_failed(self, message: str):
        with self._lock:
            self.progress["status"] = "failed"
            self.progress["message"] = message
            self.progress["error"] = message
            self.progress["updatedAt"] = _utc_now()
            _save_json(_bake_progress_path(self.job_id), self.progress)
        logger.error(f"[StoryBake:{self.job_id}] {message}")


# --------------------------------------------------------------------------- #
# Append-mode bake: re-bake freshly imported/uploaded clips into an EXISTING
# styled library so they match its baked look. Unlike the runner above (which
# creates a fresh target and overwrites its index), this reads loose raw clip
# files, bakes them style-only with the target library's own stored style, and
# appends the results to the target's index.
# --------------------------------------------------------------------------- #
def _style_filter_from_record(record: dict, width: int, height: int) -> str:
    """Rebuild the exact style filter a library was baked with, from its metadata.

    Mirrors the route's ``_resolve_bake_style``: prefer the stored ``styleParams``
    (captures both preset and custom bakes); fall back to the named ``styleId``.
    """
    from src.processors.crt_effect_processor import (
        build_custom_tv_effect_filter,
        build_tv_effect_filter,
        sanitize_tv_effect_params,
    )

    params = record.get("styleParams")
    if isinstance(params, dict):
        return build_custom_tv_effect_filter(sanitize_tv_effect_params(params), width, height)
    return build_tv_effect_filter(str(record.get("styleId") or "").strip(), width, height)


def bake_and_append_clips(
    library_id,
    raw_clip_paths: list[str],
    *,
    extra_tags: list[str] | None = None,
    source_type: str = "styled",
    session_id: str | None = None,
    progress_cb=None,
) -> list[dict]:
    """Bake loose raw 5s clips style-only with a styled library's own style filter
    and append them to that library. Returns the list of added asset records.

    Each clip keeps its 5s length (no pairing); waveform + CTA are added at render
    time, not baked here. Raises ``ValueError`` if the library is not a styled
    library or its style filter cannot be rebuilt (callers treat this as an
    import failure).
    """
    from src.processors.crt_effect_processor import _target_resolution
    from src.utils.story_library import _index_lock, get_library

    record = get_library(library_id) or {}
    if not record.get("styled"):
        raise ValueError(f"Library {library_id!r} is not a styled/baked library.")

    width, height = _target_resolution()
    style_filter = _style_filter_from_record(record, width, height)
    if not style_filter:
        raise ValueError(f"Could not rebuild style filter for library {library_id!r}.")

    valid_paths = [p for p in raw_clip_paths if p and os.path.isfile(p)]
    items = [{"out_id": uuid.uuid4().hex[:12], "src": p} for p in valid_paths]

    target_root = story_library_root(library_id)
    os.makedirs(os.path.join(target_root, "clips"), exist_ok=True)
    hwaccel = _hwaccel_flags()
    no_cancel = lambda: False

    added: list[dict] = []
    total = len(items)
    for idx, item in enumerate(items):
        out_id = item["out_id"]
        src_path = item["src"]
        if not os.path.isfile(src_path):
            continue

        out_rel = f"clips/{out_id}.mp4"
        out_path = os.path.join(target_root, out_rel)
        if progress_cb:
            progress_cb({"stage": "baking", "current": idx + 1, "total": total,
                         "message": f"Đang bake clip {idx + 1}/{total} vào thư viện..."})

        cmd = _style_only_cmd(src_path, out_path, style_filter, hwaccel)
        if not _run_ffmpeg_with_fallback(cmd, len(hwaccel), no_cancel) or not os.path.isfile(out_path):
            continue
        duration = FFmpegHelper.probe_duration(out_path) or 0

        added.append({
            "id": out_id,
            "source_type": source_type or "styled",
            "source_name": os.path.basename(src_path),
            "relative_path": out_rel,
            "duration": round(float(duration), 3),
            "tags": list(extra_tags or []),
            "created_at": _utc_now(),
            "styled_from": record.get("sourceLibraryId") or library_id,
        })

    if added:
        with _index_lock(library_id):
            index = load_story_library_index(library_id)
            index.setdefault("assets", []).extend(added)
            save_story_library_index(index, library_id)

    logger.info(
        f"[StoryBake] Appended {len(added)}/{total} style-only clips into library {library_id!r}."
    )
    return added
