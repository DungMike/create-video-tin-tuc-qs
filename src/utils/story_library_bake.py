"""Background runner that bakes a TV effect (and optionally waveform + CTA) into a clip library.

Two modes:
- ``style``: re-encode each 5s clip with the style-only filter (eq / noise /
  scanline / vignette). Waveform + CTA stay runtime overlays. Skips the style
  pass at render time.
- ``full``: pair clips into longer units (default 10s, matching the CTA loop) and
  bake style **+ waveform + CTA** in. At render time nothing but subtitle burn-in
  remains — the fastest path. Waveform/CTA become fixed (re-bake to change them).

Concurrency mirrors ``StoryVideoBatchRunner``: a daemon thread drives a bounded
``ThreadPoolExecutor`` (``Config.STORY_BAKE_MAX_WORKERS``); progress is persisted
to JSON for UI polling and a ``cancel.requested`` marker stops it.
"""

import os
import random
import re
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
    """Bakes a style (and optionally waveform + CTA) into a new target library.

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
        mode: str = "style",
        waveform_path: str | None = None,
        waveform_xy: tuple[str, str] = ("0", "0"),
        cta_path: str | None = None,
        cta_xy: tuple[str, str] = ("0", "0"),
        unit_seconds: int = 10,
    ):
        self.job_id = job_id
        self.source_library_id = source_library_id
        self.target_library_id = target_library_id
        self.target_name = target_name
        self.style_filter = style_filter
        self.style_id = style_id
        self.style_label = style_label
        self.mode = mode if mode in {"style", "full"} else "style"
        self.waveform_path = waveform_path
        self.waveform_xy = waveform_xy
        self.cta_path = cta_path
        self.cta_xy = cta_xy
        self.unit_seconds = max(1, int(unit_seconds))
        self._source_cap = max(1, int(Config.STORY_CLIP_DURATION))

        self._lock = threading.RLock()
        self._max_workers = max(1, int(Config.STORY_BAKE_MAX_WORKERS))
        self.completed_count = 0
        self.failed_count = 0

        assets = load_story_library_index(source_library_id).get("assets", [])
        self._items = self._build_items(list(assets))

        self.progress = {
            "jobId": job_id,
            "status": "pending",
            "mode": self.mode,
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

    @staticmethod
    def _source_key(asset: dict) -> str:
        """Group key for the original source video a clip came from.

        Clips are stored as ``<base>_clip_NNN.mp4``; stripping the ``_clip_NNN``
        suffix (and extension) groups all clips cut from the same source video so
        a 10s unit can be built from two *different* sources.
        """
        name = str(asset.get("source_name") or asset.get("id") or "")
        base = re.sub(r"\.[A-Za-z0-9]+$", "", name)
        base = re.sub(r"_clip_\d+$", "", base)
        return base or name

    def _build_items(self, assets: list[dict]) -> list[dict]:
        """One work item per output clip. ``full`` mode pairs two RANDOM clips,
        preferring two different source videos, into each 10s unit."""
        if self.mode != "full":
            return [{"out_id": str(a.get("id") or ""), "srcs": [a]} for a in assets if a.get("id")]

        pool = [a for a in assets if a.get("id")]
        random.shuffle(pool)  # random pairing, not consecutive (= same source) clips
        n = len(pool)
        used = [False] * n
        items: list[dict] = []
        for i in range(n):
            if used[i]:
                continue
            used[i] = True
            key = self._source_key(pool[i])
            partner = next(
                (j for j in range(i + 1, n) if not used[j] and self._source_key(pool[j]) != key),
                None,
            )
            if partner is None:  # fallback: any remaining clip (same source as last resort)
                partner = next((j for j in range(i + 1, n) if not used[j]), None)
            srcs = [pool[i]]
            if partner is not None:
                used[partner] = True
                srcs.append(pool[partner])
            items.append({"out_id": uuid.uuid4().hex[:12], "srcs": srcs})
        return items

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
            f"[StoryBake:{self.job_id}] Started baking {len(self._items)} units "
            f"(mode={self.mode}, style={self.style_id}, "
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
        cancel_cb = lambda: is_bake_cancel_requested(self.job_id)
        ok = FFmpegHelper.run_command(cmd, cancel_callback=cancel_cb)
        if not ok and hwaccel_len and not is_bake_cancel_requested(self.job_id):
            ok = FFmpegHelper.run_command(
                cmd[:2] + cmd[2 + hwaccel_len:], cancel_callback=cancel_cb
            )
        return ok

    def _bake_one(self, item: dict) -> dict | None:
        """Produce one baked clip/unit. Returns the new asset record or None."""
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
        hwaccel = ["-hwaccel", "cuda"] if Config.USE_GPU_NVENC else []

        if self.mode != "full":
            # style-only: single clip, style filter, keep source metadata
            cmd = ["ffmpeg", "-y", *hwaccel, "-i", src_paths[0],
                   "-vf", f"{self.style_filter},format=yuv420p", "-an"]
            cmd.extend(FFmpegHelper.get_nvenc_flags())
            cmd.extend(["-movflags", "+faststart", out_path])
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

        # full: concat the source clips into one unit, then style + waveform + CTA
        pairs_dir = os.path.join(_bake_dir(self.job_id), "pairs")
        os.makedirs(pairs_dir, exist_ok=True)
        seg_path = os.path.join(pairs_dir, f"{out_id}.txt")
        with open(seg_path, "w", encoding="utf-8") as fh:
            for p in src_paths:
                clean = os.path.abspath(p).replace("\\", "/").replace("'", "'\\''")
                fh.write(f"file '{clean}'\noutpoint {self._source_cap:.3f}\n")

        # Build the 10s base by concatenating the 2 source clips, then overlay the
        # waveform + CTA ONCE across the whole unit. The overlays are NOT looped
        # (no -stream_loop): each plays from its start over the unit and holds its
        # last frame if shorter (eof_action=repeat) — so a sub-10s asset is never
        # chopped into repeating 5s loops. Assets should be >= the unit length
        # (e.g. a 10s waveform/CTA) for full motion across the unit.
        wx, wy = self.waveform_xy
        cx, cy = self.cta_xy
        fc = (
            f"[0:v]setpts=N/{max(1, int(Config.TARGET_FPS))}/TB,{self.style_filter}[styled];"
            f"[1:v]setpts=PTS-STARTPTS[wave];"
            f"[styled][wave]overlay={wx}:{wy}:format=auto:eof_action=repeat:eval=init[wo];"
            f"[2:v]setpts=PTS-STARTPTS[cta];"
            f"[wo][cta]overlay={cx}:{cy}:format=auto:eof_action=repeat:eval=init[cout];"
            f"[cout]format=yuv420p[v]"
        )
        cmd = [
            "ffmpeg", "-y", *hwaccel,
            "-f", "concat", "-safe", "0", "-i", seg_path,
            "-i", self.waveform_path,
            "-i", self.cta_path,
            "-filter_complex", fc, "-map", "[v]", "-an", "-t", str(self.unit_seconds),
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(["-movflags", "+faststart", out_path])
        ok = self._run_ffmpeg_with_fallback(cmd, len(hwaccel))
        try:
            os.remove(seg_path)
        except OSError:
            pass
        if not ok or not os.path.isfile(out_path):
            return None

        duration = FFmpegHelper.probe_duration(out_path) or float(self.unit_seconds)
        tags: list[str] = []
        for a in srcs:
            for t in a.get("tags", []):
                if t not in tags:
                    tags.append(t)
        return {
            "id": out_id,
            "source_type": "styled_full",
            "source_name": " + ".join(str(a.get("source_name", "")) for a in srcs),
            "relative_path": out_rel,
            "duration": round(duration, 3),
            "tags": tags,
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
