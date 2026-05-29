"""Batch Pipeline orchestrates TTS and batch render with a shared image clip pool."""

import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import uuid
from datetime import datetime
from typing import Any

from src.composer.renderer import Renderer
from src.composer.timeline import TimelineComposer
from src.config import Config
from src.processors.audio_utils import get_audio_duration, validate_audio
from src.processors.image_processor import ImageProcessor
from src.utils.decor_videos import get_decor_video, get_decor_video_absolute_path, list_decor_videos
from src.utils.effects_library import load_active_animation_presets, load_active_transition_presets
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import (
    cleanup_job_files,
    save_job_manifest,
    setup_directories,
    storage_absolute_path,
    storage_relative_path,
)
from src.utils.logger import logger
from src.utils.tts_audio import (
    TTSAudioError,
    copy_generated_audio_to_job,
    create_audio_from_google_doc,
)

_UNSET = object()


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _batch_dir(batch_id: str) -> str:
    path = os.path.join(Config.STORAGE_DIR, "batch", batch_id)
    os.makedirs(path, exist_ok=True)
    return path


def _progress_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "progress.json")


def _state_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "state.json")


def _shared_images_dir(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "shared_images")


def _shared_clips_dir(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "shared_clips")


def _batch_temp_dir(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "temp")


def _shared_image_manifest_path(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "shared_image_clips.json")


def _uploaded_audio_dir(batch_id: str) -> str:
    return os.path.join(_batch_dir(batch_id), "uploaded_audio")


def _raw_video_output_dir() -> str:
    return os.path.join(Config.OUTPUT_DIR, "raw-video")


def _stable_seed(*parts) -> int:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _save_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _expires_at_iso() -> str:
    return datetime.utcfromtimestamp(
        datetime.utcnow().timestamp() + max(0, Config.BATCH_RETRY_RETENTION_SECONDS)
    ).isoformat(timespec="seconds") + "Z"


def _parse_utc_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_batch_expired(expires_at: str | None) -> bool:
    parsed = _parse_utc_iso(expires_at)
    if not parsed:
        return False
    return datetime.utcnow().timestamp() >= parsed.timestamp()


def _refresh_retry_flags(progress: dict):
    progress.setdefault("inputMode", "docs")
    failed_urls = sum(1 for item in progress.get("items", []) if item.get("status") == "failed")
    expired = _is_batch_expired(progress.get("expiresAt"))
    retry_artifacts_available = bool(progress.get("retryArtifactsAvailable", True))
    can_retry_failed = (
        progress.get("status") != "running" and failed_urls > 0 and not expired and retry_artifacts_available
    )

    progress["failedUrls"] = failed_urls
    progress["canRetryFailed"] = can_retry_failed
    progress["retryFailedLabel"] = f"Retry all failed ({failed_urls} items)" if failed_urls else "Retry all failed"
    for item in progress.get("items", []):
        item.setdefault("sourceType", "doc_url")
        item.setdefault("docUrl", None)
        item.setdefault("sourceAudioName", None)
        item.setdefault("sourceAudioRelativePath", None)
        item.setdefault("audioRelativePath", None)
        item.setdefault("audioStatus", "none")
        item.setdefault("chunkSummary", {"completed": 0, "failed": 0, "total": 0})
        item.setdefault("rawOutputVideo", None)
        item.setdefault("retryable", False)
        item.setdefault("failureCode", None)
        item.setdefault("failureStage", None)
        item.setdefault("lastRetryAt", None)
        item["retryable"] = bool(can_retry_failed and item.get("status") == "failed")
    return progress


def load_batch_progress(batch_id: str) -> dict | None:
    progress = _load_json(_progress_path(batch_id))
    if not progress:
        return None
    return _refresh_retry_flags(progress)


def load_batch_state(batch_id: str) -> dict | None:
    return _load_json(_state_path(batch_id))


def _save_progress(batch_id: str, data: dict):
    _refresh_retry_flags(data)
    _save_json(_progress_path(batch_id), data)


def _save_state(batch_id: str, data: dict):
    data["updatedAt"] = _utc_now()
    _save_json(_state_path(batch_id), data)


def cleanup_expired_batch_retry_artifacts():
    batch_root = os.path.join(Config.STORAGE_DIR, "batch")
    if not os.path.isdir(batch_root):
        return

    for entry in os.scandir(batch_root):
        if not entry.is_dir():
            continue
        batch_id = entry.name
        progress = _load_json(_progress_path(batch_id))
        if not progress or progress.get("status") == "running" or not _is_batch_expired(progress.get("expiresAt")):
            continue
        if not progress.get("retryArtifactsAvailable", True):
            continue

        for path in (
            _shared_images_dir(batch_id),
            _shared_clips_dir(batch_id),
            _batch_temp_dir(batch_id),
            _uploaded_audio_dir(batch_id),
            _shared_image_manifest_path(batch_id),
        ):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        progress["retryArtifactsAvailable"] = False
        _save_progress(batch_id, progress)


class BatchPipelineRunner:
    """Runs the full image+audio batch pipeline with shared motion clips."""

    def __init__(
        self,
        batch_id: str,
        items: list[dict],
        shared_image_paths: list[str],
        voice_id: str,
        speed: float,
        volume: float,
        input_mode: str = "docs",
        *,
        source_video_clips: list[dict] | None = None,
        source_video_batch_source_id: str = "",
        source_video_download_errors: list[str] | None = None,
        source_video_clip_tags: dict | None = None,
        timeline_config: dict | None = None,
        progress: dict | None = None,
        state: dict | None = None,
    ):
        self.batch_id = batch_id
        self.items = list(items)
        self.shared_image_paths = list(shared_image_paths)
        self.voice_id = voice_id
        self.speed = speed
        self.volume = volume
        self.input_mode = input_mode if input_mode in {"docs", "audio_upload"} else "docs"
        self.source_video_clips = self._normalize_source_video_clips(source_video_clips or [])
        self.source_video_batch_source_id = str(source_video_batch_source_id or "")
        self.source_video_download_errors = list(source_video_download_errors or [])
        self.source_video_clip_tags = source_video_clip_tags if isinstance(source_video_clip_tags, dict) else {}
        self.timeline_config = self._normalize_timeline_config(timeline_config)
        self.animation_presets = load_active_animation_presets()
        self.shared_image_render_plan: dict | None = None
        self.shared_image_pool: dict | None = None
        self._verified_shared_clip_ids: set[str] = set()
        self._shared_clip_fingerprints: dict[str, tuple[int, int]] = {}
        self.state = state or {
            "batchId": batch_id,
            "createdAt": _utc_now(),
            "updatedAt": _utc_now(),
            "expiresAt": _expires_at_iso(),
            "inputMode": self.input_mode,
            "voiceId": voice_id,
            "speed": speed,
            "volume": volume,
            "items": self.items,
            "sharedImagePaths": [storage_relative_path(path) for path in self.shared_image_paths],
            "sourceVideoBatchSourceId": self.source_video_batch_source_id,
            "sourceVideoClips": self.source_video_clips,
            "sourceVideoDownloadErrors": self.source_video_download_errors,
            "sourceVideoClipTags": self.source_video_clip_tags,
            "timelineConfig": self.timeline_config,
        }
        self.state["inputMode"] = self.input_mode
        self.state.setdefault("sourceVideoBatchSourceId", self.source_video_batch_source_id)
        self.state.setdefault("sourceVideoClips", self.source_video_clips)
        self.state.setdefault("sourceVideoDownloadErrors", self.source_video_download_errors)
        self.state.setdefault("sourceVideoClipTags", self.source_video_clip_tags)
        self.state.setdefault("timelineConfig", self.timeline_config)

        if progress is None:
            self.progress = {
                "batchId": batch_id,
                "inputMode": self.input_mode,
                "status": "pending",
                "totalUrls": len(items),
                "completedUrls": 0,
                "failedUrls": 0,
                "startedAt": _utc_now(),
                "updatedAt": _utc_now(),
                "expiresAt": self.state["expiresAt"],
                "retryArtifactsAvailable": True,
                "canRetryFailed": False,
                "retryFailedLabel": "Retry all failed",
                "sourceVideoPool": {
                    "batchSourceId": self.source_video_batch_source_id,
                    "selectedClips": len(self.source_video_clips),
                    "downloadErrors": self.source_video_download_errors,
                },
                "sharedImagePool": {
                    "status": "pending",
                    "totalClips": len(shared_image_paths),
                    "completedClips": 0,
                    "cacheHits": 0,
                    "cacheMisses": 0,
                    "manifestPath": storage_relative_path(_shared_image_manifest_path(batch_id)),
                    "message": "Cho tao shared image clip pool...",
                    "error": None,
                },
                "items": [],
            }
            for index, item in enumerate(items):
                decor_name = ""
                if item.get("decorVideoId"):
                    decor_record = get_decor_video(item["decorVideoId"])
                    decor_name = decor_record["name"] if decor_record else ""
                self.progress["items"].append(
                    {
                        "index": index,
                        "sourceType": item.get("sourceType") or "doc_url",
                        "outputName": item["outputName"],
                        "docUrl": item.get("docUrl"),
                        "sourceAudioName": item.get("sourceAudioName"),
                        "sourceAudioRelativePath": item.get("sourceAudioRelativePath"),
                        "decorVideoId": item.get("decorVideoId", ""),
                        "decorVideoName": decor_name,
                        "sourceText": item.get("sourceText", ""),
                        "status": "pending",
                        "stage": "pending",
                        "percent": 0,
                        "message": "Cho xu ly...",
                        "outputVideo": None,
                        "rawOutputVideo": None,
                        "jobId": None,
                        "error": None,
                        "audioRelativePath": None,
                        "audioStatus": "none",
                        "chunkSummary": {"completed": 0, "failed": 0, "total": 0},
                        "retryable": False,
                        "failureCode": None,
                        "failureStage": None,
                        "lastRetryAt": None,
                    }
                )
            self._assign_decor_videos()
            self._save_state()
            self._save()
        else:
            self.progress = progress
            self.input_mode = str(self.state.get("inputMode") or self.progress.get("inputMode") or self.input_mode)
            self.state["inputMode"] = self.input_mode
            self.progress.setdefault("inputMode", self.input_mode)
            self.progress.setdefault("expiresAt", self.state.get("expiresAt") or _expires_at_iso())
            self.progress.setdefault("retryArtifactsAvailable", True)
            self.progress.setdefault("failedUrls", 0)
            self.progress.setdefault("canRetryFailed", False)
            self.progress.setdefault("retryFailedLabel", "Retry all failed")
            self.source_video_clips = self._normalize_source_video_clips(self.state.get("sourceVideoClips") or [])
            self.source_video_batch_source_id = str(self.state.get("sourceVideoBatchSourceId") or "")
            self.source_video_download_errors = list(self.state.get("sourceVideoDownloadErrors") or [])
            self.source_video_clip_tags = (
                self.state.get("sourceVideoClipTags") if isinstance(self.state.get("sourceVideoClipTags"), dict) else {}
            )
            self.timeline_config = self._normalize_timeline_config(self.state.get("timelineConfig"))
            self.progress.setdefault(
                "sourceVideoPool",
                {
                    "batchSourceId": self.source_video_batch_source_id,
                    "selectedClips": len(self.source_video_clips),
                    "downloadErrors": self.source_video_download_errors,
                },
            )
            self.progress.setdefault(
                "sharedImagePool",
                {
                    "status": "pending",
                    "totalClips": len(shared_image_paths),
                    "completedClips": 0,
                    "cacheHits": 0,
                    "cacheMisses": 0,
                    "manifestPath": storage_relative_path(_shared_image_manifest_path(batch_id)),
                    "message": "Cho tao shared image clip pool...",
                    "error": None,
                },
            )
            _refresh_retry_flags(self.progress)

    def _normalize_source_video_clips(self, clips: list[dict]) -> list[dict]:
        normalized = []
        for index, clip in enumerate(clips):
            if not isinstance(clip, dict):
                continue
            relative_path = clip.get("relative_path") or clip.get("relativePath")
            if not relative_path:
                continue
            normalized.append(
                {
                    "id": str(clip.get("id") or f"source_clip_{index}"),
                    "relative_path": str(relative_path).replace("\\", "/"),
                    "source_name": clip.get("source_name") or clip.get("sourceName"),
                    "start": clip.get("start"),
                    "end": clip.get("end"),
                    "duration": clip.get("duration"),
                    "origin": clip.get("origin"),
                    "batch_source_id": clip.get("batch_source_id") or clip.get("batchSourceId"),
                    "asset_id": clip.get("asset_id") or clip.get("assetId"),
                    "source_clip_id": clip.get("source_clip_id") or clip.get("sourceClipId"),
                }
            )
        return normalized

    def _normalize_timeline_config(self, config: dict | None) -> dict:
        source = config if isinstance(config, dict) else {}

        def _int_value(key: str, default: int) -> int:
            try:
                return int(source.get(key, default))
            except (TypeError, ValueError):
                return default

        after_min = max(1, _int_value("afterPhaseImageEveryMin", 2))
        after_max = max(after_min, _int_value("afterPhaseImageEveryMax", 5))
        return {
            "mode": "batch_mixed_media",
            "firstPhaseSeconds": max(0, _int_value("firstPhaseSeconds", 300)),
            "firstPhaseVideoCount": max(1, _int_value("firstPhaseVideoCount", 3)),
            "firstPhaseImageCount": max(0, _int_value("firstPhaseImageCount", 1)),
            "afterPhaseImageEveryMin": after_min,
            "afterPhaseImageEveryMax": after_max,
        }

    @classmethod
    def from_saved_batch(cls, batch_id: str) -> "BatchPipelineRunner":
        progress = load_batch_progress(batch_id)
        state = load_batch_state(batch_id)
        if not progress:
            raise RuntimeError("Batch pipeline khong tim thay.")
        if not state:
            raise RuntimeError("Batch nay duoc tao truoc khi tinh nang retry duoc them vao, khong the retry tu dong.")

        shared_image_relative_paths = state.get("sharedImagePaths") or []
        shared_image_paths = [storage_absolute_path(path) for path in shared_image_relative_paths]
        return cls(
            batch_id=batch_id,
            items=list(state.get("items") or []),
            shared_image_paths=shared_image_paths,
            voice_id=str(state.get("voiceId") or ""),
            speed=float(state.get("speed") or 1),
            volume=float(state.get("volume") or 1),
            input_mode=str(state.get("inputMode") or progress.get("inputMode") or "docs"),
            source_video_clips=list(state.get("sourceVideoClips") or []),
            source_video_batch_source_id=str(state.get("sourceVideoBatchSourceId") or ""),
            source_video_download_errors=list(state.get("sourceVideoDownloadErrors") or []),
            source_video_clip_tags=state.get("sourceVideoClipTags") if isinstance(state.get("sourceVideoClipTags"), dict) else {},
            timeline_config=state.get("timelineConfig") if isinstance(state.get("timelineConfig"), dict) else None,
            progress=progress,
            state=state,
        )

    def _save(self):
        self.progress["updatedAt"] = _utc_now()
        self.progress["inputMode"] = self.input_mode
        self.progress["expiresAt"] = self.state.get("expiresAt") or self.progress.get("expiresAt") or _expires_at_iso()
        _save_progress(self.batch_id, self.progress)

    def _save_state(self):
        self.state["updatedAt"] = _utc_now()
        self.state["inputMode"] = self.input_mode
        self.state["expiresAt"] = self.state.get("expiresAt") or _expires_at_iso()
        _save_state(self.batch_id, self.state)

    def _extend_retry_retention(self):
        new_expiry = _expires_at_iso()
        self.state["expiresAt"] = new_expiry
        self.progress["expiresAt"] = new_expiry
        self._save_state()

    def _shared_paths(self) -> dict[str, str]:
        root_dir = _batch_dir(self.batch_id)
        return {
            "root": root_dir,
            "shared_images": _shared_images_dir(self.batch_id),
            "shared_clips": _shared_clips_dir(self.batch_id),
            "uploaded_audio": _uploaded_audio_dir(self.batch_id),
            "temp": _batch_temp_dir(self.batch_id),
            "manifest": _shared_image_manifest_path(self.batch_id),
        }

    def _is_valid_uploaded_audio_relative_path(self, relative_path: str) -> bool:
        normalized = os.path.normpath(relative_path or "").replace("\\", "/")
        if not normalized.startswith(f"batch/{self.batch_id}/uploaded_audio/"):
            return False
        absolute_path = storage_absolute_path(normalized)
        uploaded_root = os.path.abspath(_uploaded_audio_dir(self.batch_id))
        return os.path.isfile(absolute_path) and os.path.abspath(absolute_path).startswith(uploaded_root)

    def _copy_uploaded_audio_to_job(self, relative_path: str, job_audio_dir: str) -> str:
        if not self._is_valid_uploaded_audio_relative_path(relative_path):
            raise RuntimeError("Uploaded audio source is invalid or missing.")
        source_path = storage_absolute_path(relative_path)
        target_name = f"audio_{Path(source_path).name}"
        target_path = os.path.join(job_audio_dir, target_name)
        shutil.copy2(source_path, target_path)
        return target_path

    def _assign_decor_videos(self):
        """Auto-assign PiP overlays for items that do not already have one."""
        all_decor = list_decor_videos()
        if not all_decor:
            logger.info("[BatchPipeline] No decor videos in library. Skipping PiP auto-assign.")
            return

        used_ids: set[str] = set()
        for item in self.items:
            if item.get("decorVideoId"):
                used_ids.add(item["decorVideoId"])

        all_ids = [decor_video["id"] for decor_video in all_decor]
        decor_lookup = {decor_video["id"]: decor_video for decor_video in all_decor}
        available_pool = [decor_id for decor_id in all_ids if decor_id not in used_ids]
        random.shuffle(available_pool)

        for index, item in enumerate(self.items):
            if item.get("decorVideoId"):
                continue

            if not available_pool:
                available_pool = list(all_ids)
                random.shuffle(available_pool)

            chosen_id = available_pool.pop(0)
            item["decorVideoId"] = chosen_id
            used_ids.add(chosen_id)

            decor_record = decor_lookup.get(chosen_id)
            self.progress["items"][index]["decorVideoId"] = chosen_id
            self.progress["items"][index]["decorVideoName"] = (
                decor_record["name"] if decor_record else chosen_id
            )
            logger.info(
                f"[BatchPipeline] Auto-assigned decor '{decor_record['name'] if decor_record else chosen_id}' "
                f"to item {index} ({item['outputName']})"
            )

    def _update_item_progress(
        self,
        index: int,
        stage: str,
        percent: float,
        message: str,
        *,
        status: str = "running",
        output_video: str | None | object = _UNSET,
        job_id: str | None | object = _UNSET,
        error: str | None | object = _UNSET,
        audio_relative_path: str | None | object = _UNSET,
        audio_status: str | None | object = _UNSET,
        chunk_summary: dict | None | object = _UNSET,
        retryable: bool | object = _UNSET,
        failure_code: str | None | object = _UNSET,
        failure_stage: str | None | object = _UNSET,
        last_retry_at: str | None | object = _UNSET,
    ):
        item = self.progress["items"][index]
        item["status"] = status
        item["stage"] = stage
        item["percent"] = round(min(100, max(0, percent)), 1)
        item["message"] = message
        if output_video is not _UNSET:
            item["outputVideo"] = output_video
        if job_id is not _UNSET:
            item["jobId"] = job_id
        if error is not _UNSET:
            item["error"] = error
        if audio_relative_path is not _UNSET:
            item["audioRelativePath"] = audio_relative_path
        if audio_status is not _UNSET:
            item["audioStatus"] = audio_status
        if chunk_summary is not _UNSET:
            item["chunkSummary"] = chunk_summary
        if retryable is not _UNSET:
            item["retryable"] = bool(retryable)
        if failure_code is not _UNSET:
            item["failureCode"] = failure_code
        if failure_stage is not _UNSET:
            item["failureStage"] = failure_stage
        if last_retry_at is not _UNSET:
            item["lastRetryAt"] = last_retry_at

        self.progress["completedUrls"] = sum(
            1 for existing_item in self.progress["items"] if existing_item["status"] in ("completed", "failed")
        )
        self._save()

    def _copy_raw_video_output(self, source_path: str, output_name: str) -> str | None:
        """Copy the rendered base video before PiP/source-text overlays.

        This artifact is optional for the main pipeline; copy failures are
        logged and ignored so the normal overlay/final render flow can finish.
        """
        try:
            if not source_path or not os.path.isfile(source_path):
                logger.warning(f"[BatchPipeline] Raw video source not found: {source_path}")
                return None

            safe_name = Path(str(output_name or "")).name.strip() or f"video_{uuid.uuid4().hex[:8]}"
            raw_dir = _raw_video_output_dir()
            os.makedirs(raw_dir, exist_ok=True)
            raw_output_path = os.path.join(raw_dir, f"{safe_name}.mp4")
            shutil.copy2(source_path, raw_output_path)
            raw_relative = storage_relative_path(raw_output_path)
            logger.info(f"[BatchPipeline] Raw no-overlay video copied: {raw_relative}")
            return raw_relative
        except Exception as exc:
            logger.warning(f"[BatchPipeline] Cannot copy raw no-overlay video: {exc}", exc_info=True)
            return None

    def _update_shared_image_pool_progress(
        self,
        status: str,
        message: str,
        *,
        completed_clips: int | None = None,
        total_clips: int | None = None,
        cache_hits: int | None = None,
        cache_misses: int | None = None,
        error: str | None = None,
    ):
        shared_pool = self.progress["sharedImagePool"]
        shared_pool["status"] = status
        shared_pool["message"] = message
        if completed_clips is not None:
            shared_pool["completedClips"] = max(0, int(completed_clips))
        if total_clips is not None:
            shared_pool["totalClips"] = max(0, int(total_clips))
        if cache_hits is not None:
            shared_pool["cacheHits"] = max(0, int(cache_hits))
        if cache_misses is not None:
            shared_pool["cacheMisses"] = max(0, int(cache_misses))
        if error is not None:
            shared_pool["error"] = error
        self._save()

    def _apply_audio_state(self, index: int, audio_state: dict | None, *, clear_failure: bool = False):
        if not isinstance(audio_state, dict):
            return
        self._update_item_progress(
            index,
            self.progress["items"][index]["stage"],
            self.progress["items"][index]["percent"],
            self.progress["items"][index]["message"],
            status=self.progress["items"][index]["status"],
            audio_relative_path=audio_state.get("audioRelativePath"),
            audio_status=audio_state.get("audioStatus") or "none",
            chunk_summary=audio_state.get("chunkSummary") or {"completed": 0, "failed": 0, "total": 0},
            failure_code=None if clear_failure else audio_state.get("failureCode", _UNSET),
            failure_stage=None if clear_failure else audio_state.get("failureStage", _UNSET),
        )

    def retry_failed_items(self) -> list[int]:
        failed_indexes = [index for index, item in enumerate(self.progress["items"]) if item["status"] == "failed"]
        if not failed_indexes:
            raise RuntimeError("Khong co item failed de retry.")
        if self.progress.get("status") == "running":
            raise RuntimeError("Batch dang chay, khong the retry luc nay.")
        if _is_batch_expired(self.progress.get("expiresAt")):
            raise RuntimeError("Batch retry da het han.")
        if not self.progress.get("retryArtifactsAvailable", True):
            raise RuntimeError("Batch retry artifacts khong con san sang.")

        missing_shared_images = [path for path in self.shared_image_paths if not os.path.isfile(path)]
        if missing_shared_images:
            raise RuntimeError("Shared image artifacts da bi thieu, khong the retry batch nay.")

        missing_source_clips = [
            clip.get("id") or clip.get("relative_path")
            for clip in self.source_video_clips
            if not os.path.isfile(storage_absolute_path(str(clip.get("relative_path") or "")))
        ]
        if missing_source_clips:
            raise RuntimeError(
                "Source video clip artifacts da bi thieu, khong the retry batch nay: "
                + ", ".join(str(name) for name in missing_source_clips[:5])
            )

        missing_uploaded_audio = []
        for index in failed_indexes:
            item = self.items[index]
            if str(item.get("sourceType") or "doc_url") != "uploaded_audio":
                continue
            relative_path = str(item.get("sourceAudioRelativePath") or "")
            if not self._is_valid_uploaded_audio_relative_path(relative_path):
                missing_uploaded_audio.append(item.get("sourceAudioName") or item.get("outputName") or f"item {index}")
        if missing_uploaded_audio:
            raise RuntimeError(
                "Uploaded audio artifacts da bi thieu, khong the retry batch nay: "
                + ", ".join(str(name) for name in missing_uploaded_audio[:5])
            )

        retry_started_at = _utc_now()
        self._extend_retry_retention()
        self.progress["status"] = "pending"
        for index in failed_indexes:
            self._update_item_progress(
                index,
                "pending",
                0,
                "Cho retry...",
                status="pending",
                output_video=None,
                job_id=None,
                error=None,
                retryable=False,
                failure_code=None,
                failure_stage=None,
                last_retry_at=retry_started_at,
            )
        self._save()
        return failed_indexes

    def _build_shared_image_render_plan(self) -> dict:
        rng = random.Random(_stable_seed(self.batch_id, "shared_image_render_plan"))
        plan_items = []
        for image_path in self.shared_image_paths:
            preset = self.animation_presets[rng.randrange(len(self.animation_presets))]
            plan_items.append(
                {
                    "source_image_relative_path": storage_relative_path(image_path),
                    "animation_preset_id": preset["id"],
                }
            )
        return {"version": 1, "batch_id": self.batch_id, "items": plan_items}

    def _build_shared_image_pool_manifest(self, image_clips: list[dict], render_plan: dict) -> dict:
        cache_hits = int(render_plan.get("cache_hits") or 0)
        cache_misses = int(render_plan.get("cache_misses") or 0)
        records = []
        for index, clip in enumerate(image_clips):
            records.append(
                {
                    "sharedClipId": f"shared_img_clip_{index:04d}",
                    "sourceImageRelativePath": clip["source_image_relative_path"],
                    "animationPresetId": clip.get("animation_preset_id"),
                    "relativePath": clip["relative_path"],
                    "duration": clip.get("duration"),
                    "cacheKey": clip.get("cache_key"),
                }
            )
        return {
            "version": 1,
            "batchId": self.batch_id,
            "totalClips": len(records),
            "cacheHits": cache_hits,
            "cacheMisses": cache_misses,
            "clips": records,
        }

    def _file_fingerprint(self, path: str) -> tuple[int, int]:
        stat_result = os.stat(path)
        return (int(stat_result.st_size), int(stat_result.st_mtime_ns))

    def _mark_shared_clip_verified(self, record: dict):
        clip_id = record["sharedClipId"]
        clip_path = storage_absolute_path(record["relativePath"])
        self._verified_shared_clip_ids.add(clip_id)
        self._shared_clip_fingerprints[clip_id] = self._file_fingerprint(clip_path)

    def _shared_clip_is_valid(self, record: dict) -> bool:
        clip_path = storage_absolute_path(record["relativePath"])
        if not os.path.isfile(clip_path):
            return False
        expected_duration = float(record.get("duration") or Config.IMG_CLIP_DURATION)
        actual_duration = FFmpegHelper.probe_duration(clip_path)
        minimum_duration = max(0.1, expected_duration * 0.8)
        return actual_duration >= minimum_duration

    def _shared_clip_needs_repair(self, record: dict) -> bool:
        clip_id = record["sharedClipId"]
        clip_path = storage_absolute_path(record["relativePath"])
        if not os.path.isfile(clip_path):
            return True

        current_fingerprint = self._file_fingerprint(clip_path)
        known_fingerprint = self._shared_clip_fingerprints.get(clip_id)
        if clip_id not in self._verified_shared_clip_ids:
            return not self._shared_clip_is_valid(record)
        if known_fingerprint and current_fingerprint != known_fingerprint:
            return not self._shared_clip_is_valid(record)
        return False

    def _repair_shared_clip(self, record: dict) -> dict:
        source_image_path = storage_absolute_path(record["sourceImageRelativePath"])
        if not os.path.isfile(source_image_path):
            raise RuntimeError(f"Shared source image is missing: {source_image_path}")

        paths = self._shared_paths()
        repair_root = os.path.join(paths["temp"], "repair", record["sharedClipId"])
        repair_dirs = {
            "img_clips": os.path.join(repair_root, "clips"),
            "temp": os.path.join(repair_root, "temp"),
        }
        processor = ImageProcessor(
            self.batch_id,
            repair_dirs,
            animation_presets=self.animation_presets,
            image_render_plan=self.shared_image_render_plan,
        )

        logger.warning(
            f"[BatchPipeline] Shared clip missing or invalid. Regenerating {record['sharedClipId']} from "
            f"{record['sourceImageRelativePath']}"
        )
        try:
            repaired_clips = processor.process_images([source_image_path])
            if len(repaired_clips) != 1:
                raise RuntimeError(f"Could not regenerate shared clip {record['sharedClipId']}")

            repaired_clip = repaired_clips[0]
            target_path = storage_absolute_path(record["relativePath"])
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(repaired_clip["path"], target_path)

            record["duration"] = repaired_clip.get("duration")
            record["animationPresetId"] = repaired_clip.get("animation_preset_id") or record.get("animationPresetId")
            record["cacheKey"] = repaired_clip.get("cache_key")
            self._mark_shared_clip_verified(record)
            if self.shared_image_pool is not None:
                _save_json(paths["manifest"], self.shared_image_pool)
            return record
        finally:
            if os.path.isdir(repair_root):
                shutil.rmtree(repair_root, ignore_errors=True)

    def _ensure_shared_clip_record(self, record: dict) -> dict:
        if not self._shared_clip_needs_repair(record):
            clip_path = storage_absolute_path(record["relativePath"])
            if os.path.isfile(clip_path):
                self._verified_shared_clip_ids.add(record["sharedClipId"])
                self._shared_clip_fingerprints[record["sharedClipId"]] = self._file_fingerprint(clip_path)
            return record
        return self._repair_shared_clip(record)

    def _prepare_shared_image_pool(self) -> dict:
        if self.shared_image_pool is not None:
            return self.shared_image_pool

        paths = self._shared_paths()
        os.makedirs(paths["shared_clips"], exist_ok=True)
        os.makedirs(paths["temp"], exist_ok=True)
        self.shared_image_render_plan = self._build_shared_image_render_plan()

        cached_manifest = _load_json(paths["manifest"])
        if cached_manifest and isinstance(cached_manifest.get("clips"), list):
            self.shared_image_pool = cached_manifest
            for record in self.shared_image_pool["clips"]:
                clip_path = storage_absolute_path(record["relativePath"])
                if os.path.isfile(clip_path):
                    self._verified_shared_clip_ids.add(record["sharedClipId"])
                    self._shared_clip_fingerprints[record["sharedClipId"]] = self._file_fingerprint(clip_path)
            self._update_shared_image_pool_progress(
                "completed",
                f"Da nap shared image clip pool ({len(self.shared_image_pool['clips'])} clip).",
                completed_clips=len(self.shared_image_pool["clips"]),
                total_clips=len(self.shared_image_pool["clips"]),
                cache_hits=int(self.shared_image_pool.get("cacheHits") or 0),
                cache_misses=int(self.shared_image_pool.get("cacheMisses") or 0),
                error=None,
            )
            logger.info(
                f"[BatchPipeline] Loaded existing shared image clip pool for {self.batch_id}: "
                f"{len(self.shared_image_pool['clips'])} clips"
            )
            return self.shared_image_pool

        # Pre-validate images: remove corrupt/broken files before processing
        valid_image_paths = []
        for img_path in self.shared_image_paths:
            if not os.path.isfile(img_path):
                logger.warning(f"[BatchPipeline] Shared image missing, skipping: {img_path}")
                continue
            if os.path.getsize(img_path) < 1024:  # < 1KB is almost certainly corrupt
                logger.warning(
                    f"[BatchPipeline] Shared image too small ({os.path.getsize(img_path)} bytes), "
                    f"removing corrupt file: {img_path}"
                )
                try:
                    os.remove(img_path)
                except OSError:
                    pass
                continue
            try:
                from PIL import Image as _PILImage
                with _PILImage.open(img_path) as _img:
                    _img.verify()
                valid_image_paths.append(img_path)
            except Exception as exc:
                logger.warning(
                    f"[BatchPipeline] Shared image corrupt, removing: {img_path} | {exc}"
                )
                try:
                    os.remove(img_path)
                except OSError:
                    pass

        if len(valid_image_paths) != len(self.shared_image_paths):
            logger.info(
                f"[BatchPipeline] Filtered {len(self.shared_image_paths) - len(valid_image_paths)} "
                f"invalid images. Proceeding with {len(valid_image_paths)} valid images."
            )
            self.shared_image_paths = valid_image_paths

        self._update_shared_image_pool_progress(
            "running",
            f"Dang tao shared image clip pool tu {len(self.shared_image_paths)} anh...",
            completed_clips=0,
            total_clips=len(self.shared_image_paths),
            cache_hits=0,
            cache_misses=0,
            error=None,
        )

        processor = ImageProcessor(
            self.batch_id,
            {"img_clips": paths["shared_clips"], "temp": paths["temp"]},
            animation_presets=self.animation_presets,
            image_render_plan=self.shared_image_render_plan,
        )

        def _progress(event: dict):
            self._update_shared_image_pool_progress(
                "running",
                event.get("message", "Dang tao shared image clip pool..."),
                completed_clips=int(event.get("currentImage") or 0),
                total_clips=max(int(event.get("totalImages") or len(self.shared_image_paths)), 0),
                cache_hits=int(event.get("cacheHits") or 0),
                cache_misses=int(event.get("cacheMisses") or 0),
            )

        image_clips = processor.process_images(self.shared_image_paths, progress_callback=_progress)
        if not image_clips and self.shared_image_paths:
            raise RuntimeError("All shared image clips failed to process.")
        elif len(image_clips) != len(self.shared_image_paths):
            logger.warning(
                f"[BatchPipeline] Shared image clip pool is incomplete: expected {len(self.shared_image_paths)}, got {len(image_clips)}. "
                "Continuing with successfully processed clips."
            )

        self.shared_image_render_plan = processor.updated_image_render_plan
        self.shared_image_pool = self._build_shared_image_pool_manifest(image_clips, self.shared_image_render_plan)
        _save_json(paths["manifest"], self.shared_image_pool)

        for record in self.shared_image_pool["clips"]:
            self._mark_shared_clip_verified(record)

        self._update_shared_image_pool_progress(
            "completed",
            f"Da tao xong shared image clip pool ({len(image_clips)} clip).",
            completed_clips=len(image_clips),
            total_clips=len(image_clips),
            cache_hits=int(self.shared_image_pool.get("cacheHits") or 0),
            cache_misses=int(self.shared_image_pool.get("cacheMisses") or 0),
            error=None,
        )
        logger.info(
            f"[BatchPipeline] Shared image clip pool ready: {len(image_clips)} clips "
            f"(cache hit {self.shared_image_pool['cacheHits']}, miss {self.shared_image_pool['cacheMisses']})"
        )
        return self.shared_image_pool

    def _ordered_shared_image_clips(self, index: int) -> list[dict]:
        shared_pool = self._prepare_shared_image_pool()
        record_indexes = list(range(len(shared_pool["clips"])))
        rng = random.Random(_stable_seed(self.batch_id, "shared_image_clip_order", index))
        rng.shuffle(record_indexes)

        ordered_clips = []
        for record_index in record_indexes:
            record = self._ensure_shared_clip_record(shared_pool["clips"][record_index])
            ordered_clips.append(
                {
                    "id": record["sharedClipId"],
                    "shared_clip_id": record["sharedClipId"],
                    "kind": "image",
                    "path": storage_absolute_path(record["relativePath"]),
                    "relative_path": record["relativePath"],
                    "source_image_relative_path": record["sourceImageRelativePath"],
                    "duration": round(float(record.get("duration") or 0), 3),
                    "animation_preset_id": record.get("animationPresetId"),
                    "cache_key": record.get("cacheKey"),
                }
            )

        logger.info(
            f"[BatchPipeline] Item {index}: Reusing {len(ordered_clips)} shared image clips with deterministic order."
        )
        return ordered_clips

    def _ordered_source_video_paths(self, index: int) -> list[str]:
        record_indexes = list(range(len(self.source_video_clips)))
        rng = random.Random(_stable_seed(self.batch_id, "source_video_clip_order", index))
        rng.shuffle(record_indexes)

        ordered_paths = []
        for record_index in record_indexes:
            clip = self.source_video_clips[record_index]
            clip_path = storage_absolute_path(str(clip.get("relative_path") or ""))
            if os.path.isfile(clip_path):
                ordered_paths.append(clip_path)
            else:
                logger.warning(
                    f"[BatchPipeline] Source video clip missing for item {index}: "
                    f"{clip.get('id') or clip.get('relative_path')}"
                )
        return ordered_paths

    def _manifest_image_render_plan_for_item(self, ordered_clips: list[dict]) -> dict:
        shared_manifest_relative_path = storage_relative_path(_shared_image_manifest_path(self.batch_id))
        return {
            "version": 1,
            "source": "batch_shared_pool",
            "batch_id": self.batch_id,
            "shared_image_pool_relative_path": shared_manifest_relative_path,
            "cache_hits": int((self.shared_image_pool or {}).get("cacheHits") or 0),
            "cache_misses": int((self.shared_image_pool or {}).get("cacheMisses") or 0),
            "items": [
                {
                    "clip_index": clip_index,
                    "shared_clip_id": clip["shared_clip_id"],
                    "source_image_relative_path": clip["source_image_relative_path"],
                    "animation_preset_id": clip.get("animation_preset_id"),
                    "cache_key": clip.get("cache_key"),
                }
                for clip_index, clip in enumerate(ordered_clips)
            ],
        }

    def _cleanup_batch_assets(self):
        paths = self._shared_paths()
        for label, path in (
            ("shared images", paths["shared_images"]),
            ("shared clips", paths["shared_clips"]),
            ("batch temp", paths["temp"]),
        ):
            if not os.path.exists(path):
                continue
            try:
                shutil.rmtree(path)
                logger.info(f"[BatchPipeline] Cleaned up {label} for batch {self.batch_id}")
            except Exception as exc:
                logger.warning(f"[BatchPipeline] Failed to clean {label} for batch {self.batch_id}: {exc}")

    def _fail_pending_items(self, error_message: str):
        for index, progress_item in enumerate(self.progress["items"]):
            if progress_item["status"] != "pending":
                continue
            self._update_item_progress(
                index,
                "failed",
                0,
                f"That bai: {error_message[:200]}",
                status="failed",
                error=error_message[:500],
                failure_code="batch_shared_pool_failed",
                failure_stage="shared_image_pool",
            )

    def _run_single_url(self, index: int, item: dict):
        """Execute TTS -> job setup -> shared clip reuse -> timeline -> render."""
        import time as _time

        output_name = item["outputName"]
        source_type = str(item.get("sourceType") or "doc_url")
        doc_url = item.get("docUrl") or ""
        source_audio_name = item.get("sourceAudioName") or ""
        source_audio_relative_path = item.get("sourceAudioRelativePath") or ""
        decor_video_id = item.get("decorVideoId", "")
        item_source_text = item.get("sourceText", "") or Config.SOURCE_TEXT
        item_start_time = _time.time()

        def _elapsed():
            return f"{(_time.time() - item_start_time) * 1000:.0f}ms"

        logger.info(
            f"[BatchPipeline] ========== Item {index} START ==========\n"
            f"  outputName={output_name}, sourceType={source_type}, inputMode={self.input_mode}\n"
            f"  docUrl={doc_url!r}\n"
            f"  voiceId={self.voice_id!r}, speed={self.speed}, volume={self.volume}\n"
            f"  decorVideoId={decor_video_id!r}, sourceText={item_source_text!r}, batchId={self.batch_id}"
        )

        if source_type == "uploaded_audio":
            self._update_item_progress(
                index,
                "audio_source",
                12,
                f"Dang nap audio nguon: {source_audio_name or output_name}...",
            )
            if not self._is_valid_uploaded_audio_relative_path(source_audio_relative_path):
                raise RuntimeError(f"Uploaded audio source for '{output_name}' is missing or invalid.")
            source_audio_path = storage_absolute_path(source_audio_relative_path)
            if not validate_audio(source_audio_path):
                raise RuntimeError(f"Uploaded audio for '{output_name}' is invalid.")
            self._apply_audio_state(
                index,
                {
                    "audioRelativePath": source_audio_relative_path,
                    "audioStatus": "ready",
                    "chunkSummary": {"completed": 0, "failed": 0, "total": 0},
                },
                clear_failure=True,
            )
            audio_relative_path = source_audio_relative_path
            logger.info(f"[BatchPipeline] Item {index}: Uploaded audio ready -> {audio_relative_path} [{_elapsed()}]")
        else:
            self._update_item_progress(index, "tts_audio", 5, "Dang tao audio tu Google Docs...")
            logger.info(
                f"[BatchPipeline] Item {index}: Calling create_audio_from_google_doc. "
                f"docUrl={doc_url!r}, outputName={output_name!r}, "
                f"voiceId={self.voice_id!r}, speed={self.speed}, volume={self.volume}"
            )
            tts_start = _time.time()
            try:
                audio_result = create_audio_from_google_doc(
                    doc_url=doc_url,
                    output_name=output_name,
                    voice_id=self.voice_id,
                    speed=self.speed,
                    volume=self.volume,
                )
            except Exception as tts_exc:
                tts_elapsed = f"{(_time.time() - tts_start) * 1000:.0f}ms"
                logger.error(
                    f"[BatchPipeline] Item {index}: create_audio_from_google_doc FAILED. "
                    f"error_type={type(tts_exc).__name__}, error={tts_exc}, "
                    f"tts_elapsed={tts_elapsed}, item_elapsed={_elapsed()}"
                )
                raise
            tts_elapsed = f"{(_time.time() - tts_start) * 1000:.0f}ms"
            self._apply_audio_state(index, audio_result.get("audioState"), clear_failure=True)
            audio_relative_path = audio_result["audio"]["relativePath"]
            chunk_count = audio_result.get("chunkCount", "?")
            audio_state = audio_result.get("audioState", {})
            logger.info(
                f"[BatchPipeline] Item {index}: Audio created -> {audio_relative_path}. "
                f"chunks={chunk_count}, audioStatus={audio_state.get('audioStatus', 'N/A')}, "
                f"tts_elapsed={tts_elapsed}, item_elapsed={_elapsed()}"
            )

        self._update_item_progress(index, "job_setup", 25, "Dang tao job...")
        job_id = str(uuid.uuid4())[:8]
        self._update_item_progress(index, "job_setup", 25, "Dang tao job...", job_id=job_id)
        dirs = setup_directories(job_id)

        if source_type == "uploaded_audio":
            audio_path = self._copy_uploaded_audio_to_job(audio_relative_path, dirs["audio"])
        else:
            audio_path = copy_generated_audio_to_job(audio_relative_path, dirs["audio"])
        if not validate_audio(audio_path):
            raise RuntimeError(f"Audio generated for '{output_name}' is invalid.")
        audio_duration = get_audio_duration(audio_path)
        logger.info(
            f"[BatchPipeline] Item {index}: Job {job_id} setup done. "
            f"audio_duration={audio_duration:.1f}s, item_elapsed={_elapsed()}"
        )

        self._update_item_progress(
            index,
            "shared_image_pool",
            35,
            f"Dang dung lai shared image clip pool ({len(self.shared_image_paths)} clip)...",
        )
        image_clips = self._ordered_shared_image_clips(index)
        if not image_clips:
            raise RuntimeError(f"No valid shared image clips available for '{output_name}'.")
        selected_video_paths = self._ordered_source_video_paths(index)
        render_mode = "mixed_media" if selected_video_paths else "image_audio_only"

        manifest = {
            "job_id": job_id,
            "created_at": _utc_now(),
            "audio_relative_path": storage_relative_path(audio_path),
            "audio_duration": round(audio_duration, 3),
            "render_mode": render_mode,
            "image_paths": [storage_relative_path(path) for path in self.shared_image_paths],
            "source_videos": [storage_relative_path(path) for path in selected_video_paths],
            "download_errors": self.source_video_download_errors,
            "review_clips": self.source_video_clips,
            "selected_clip_ids": [clip["id"] for clip in self.source_video_clips],
            "selected_library_asset_ids": [],
            "clip_tags": self.source_video_clip_tags,
            "output_video": None,
            "raw_output_video": None,
            "batch_id": self.batch_id,
            "batch_item_index": index,
            "batch_output_name": output_name,
            "batch_source_video_id": self.source_video_batch_source_id,
            "timeline_config": self.timeline_config,
            "image_render_plan": self._manifest_image_render_plan_for_item(image_clips),
        }
        save_job_manifest(job_id, manifest)
        logger.info(
            f"[BatchPipeline] Item {index}: Job {job_id} created with "
            f"{len(image_clips)} shared image clips, item_elapsed={_elapsed()}"
        )

        self._update_item_progress(index, "timeline", 58, "Dang tao timeline render...")
        timeline_composer = TimelineComposer(job_id, dirs)
        if selected_video_paths:
            timeline_data = timeline_composer.create_batch_mixed_timeline(
                selected_video_paths,
                image_clips,
                audio_duration,
                seed=_stable_seed(self.batch_id, "batch_mixed_timeline", index),
                config=self.timeline_config,
            )
        else:
            timeline_data = timeline_composer.create_timeline([], image_clips, audio_duration, shuffle_inputs=False)
        segments = timeline_data.get("segments", [])
        if not segments:
            raise RuntimeError(f"Timeline generated 0 segments for '{output_name}'.")
        logger.info(
            f"[BatchPipeline] Item {index}: Timeline has {len(segments)} segments, item_elapsed={_elapsed()}"
        )

        self._update_item_progress(index, "render_video", 62, f"Dang render video ({len(segments)} segments)...")
        transition_presets = load_active_transition_presets()
        renderer = Renderer(job_id, dirs, transition_presets=transition_presets)
        decor_path = get_decor_video_absolute_path(decor_video_id) if decor_video_id else None

        render_start = _time.time()

        def _render_progress(event: dict):
            stage = event.get("stage", "render_video")
            ffmpeg_percent = event.get("ffmpegPercent")
            if stage in ("render_chunks", "render_video") and isinstance(ffmpeg_percent, (int, float)):
                percent = 62 + (30 * float(ffmpeg_percent) / 100)
            elif stage == "join_chunks":
                fraction = float(ffmpeg_percent) / 100 if isinstance(ffmpeg_percent, (int, float)) else 0
                percent = 92 + (5 * fraction)
            elif stage == "finalize":
                percent = 97
            else:
                percent = 65
            self._update_item_progress(
                index,
                stage,
                percent,
                event.get("message", f"Dang render {output_name}..."),
            )

        raw_output_relative = None

        def _capture_pre_overlay_video(pre_overlay_path: str):
            nonlocal raw_output_relative
            raw_output_relative = self._copy_raw_video_output(pre_overlay_path, output_name)
            if raw_output_relative:
                manifest["raw_output_video"] = raw_output_relative
                self.progress["items"][index]["rawOutputVideo"] = raw_output_relative
                save_job_manifest(job_id, manifest)
                self._save()

        output_path = renderer.render(
            timeline_data,
            audio_path,
            audio_duration,
            progress_callback=_render_progress,
            decor_video_path=decor_path,
            source_text_override=item_source_text,
            pre_overlay_callback=_capture_pre_overlay_video,
        )
        render_elapsed = f"{(_time.time() - render_start) * 1000:.0f}ms"
        if not output_path:
            logger.error(
                f"[BatchPipeline] Item {index}: Render returned None. "
                f"render_elapsed={render_elapsed}, item_elapsed={_elapsed()}"
            )
            raise RuntimeError(f"Render failed for '{output_name}'. Check logs/app.log.")

        desired_output = os.path.join(Config.OUTPUT_DIR, f"{output_name}.mp4")
        if os.path.abspath(output_path) != os.path.abspath(desired_output):
            os.makedirs(os.path.dirname(desired_output), exist_ok=True)
            if os.path.isfile(desired_output):
                os.remove(desired_output)
            shutil.move(output_path, desired_output)
            output_path = desired_output

        output_relative = storage_relative_path(output_path)
        manifest["output_video"] = output_relative
        if raw_output_relative:
            manifest["raw_output_video"] = raw_output_relative
        save_job_manifest(job_id, manifest)
        cleanup_job_files(job_id)

        self._update_item_progress(
            index,
            "completed",
            100,
            f"Render hoan tat: {output_name}.mp4",
            status="completed",
            output_video=output_relative,
            failure_code=None,
            failure_stage=None,
            error=None,
        )
        logger.info(
            f"[BatchPipeline] ========== Item {index} ({output_name}) COMPLETED ==========\n"
            f"  output={output_relative}, render_elapsed={render_elapsed}, total_item_elapsed={_elapsed()}"
        )

    def run_batch(self, target_indexes: list[int] | None = None):
        """Process selected items sequentially. Failed items are skipped."""
        indexes_to_run = list(range(len(self.items))) if target_indexes is None else list(target_indexes)
        self.progress["status"] = "running"
        self._save()
        logger.info(
            f"[BatchPipeline] Batch {self.batch_id} started with {len(indexes_to_run)} "
            f"target items out of {len(self.items)}"
        )

        try:
            self._prepare_shared_image_pool()
        except Exception as exc:
            error_message = str(exc)
            logger.exception(f"[BatchPipeline] Shared image clip pool failed: {error_message}")
            self._update_shared_image_pool_progress(
                "failed",
                f"That bai: {error_message[:200]}",
                error=error_message[:500],
            )
            for index in indexes_to_run:
                if self.progress["items"][index]["status"] in {"completed", "failed"}:
                    continue
                self._update_item_progress(
                    index,
                    "failed",
                    0,
                    f"That bai: {error_message[:200]}",
                    status="failed",
                    error=error_message[:500],
                    failure_code="batch_shared_pool_failed",
                    failure_stage="shared_image_pool",
                )
            self.progress["status"] = "failed" if all(
                item["status"] == "failed" for item in self.progress["items"]
            ) else "completed"
            self._save()
            return

        for index in indexes_to_run:
            item = self.items[index]
            try:
                self._run_single_url(index, item)
            except TTSAudioError as exc:
                error_message = str(exc)
                logger.exception(f"[BatchPipeline] Item {index} ({item['outputName']}) failed: {error_message}")
                audio_state = exc.details.get("audioState") if isinstance(exc.details, dict) else None
                self._apply_audio_state(index, audio_state)
                self._update_item_progress(
                    index,
                    "failed",
                    0,
                    f"That bai: {error_message[:200]}",
                    status="failed",
                    error=error_message[:500],
                    failure_code=exc.code,
                    failure_stage=(
                        audio_state.get("failureStage")
                        if isinstance(audio_state, dict)
                        else self.progress["items"][index].get("stage")
                    )
                    or "tts_audio",
                )
            except Exception as exc:
                error_message = str(exc)
                logger.exception(f"[BatchPipeline] Item {index} ({item['outputName']}) failed: {error_message}")
                self._update_item_progress(
                    index,
                    "failed",
                    0,
                    f"That bai: {error_message[:200]}",
                    status="failed",
                    error=error_message[:500],
                    failure_code="batch_item_failed",
                    failure_stage=self.progress["items"][index].get("stage") or "failed",
                )

        statuses = {progress_item["status"] for progress_item in self.progress["items"]}
        if statuses == {"completed"}:
            self.progress["status"] = "completed"
        elif "completed" in statuses:
            self.progress["status"] = "completed"
        else:
            self.progress["status"] = "failed"

        self._save()
        logger.info(
            f"[BatchPipeline] Batch {self.batch_id} finished. "
            f"Status: {self.progress['status']}, "
            f"Completed: {self.progress['completedUrls']}/{self.progress['totalUrls']}"
        )
