import base64
import hashlib
import json
import os
import re
import shutil
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from src.config import Config
from src.processors.audio_utils import validate_audio
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import storage_absolute_path, storage_relative_path
from src.utils.logger import logger


class TTSAudioError(Exception):
    def __init__(self, message: str, code: str = "tts_error", details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


SENTENCE_PATTERN = re.compile(r".+?(?:\.\.\.|[.!?]+)(?=\s|$)|.+$", re.S)
CHUNK_MANIFEST_VERSION = 1
CHUNK_MAX_RETRY_CYCLES = 5
CHUNK_MAX_ATTEMPTS = CHUNK_MAX_RETRY_CYCLES + 1


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _storage_dir(*parts: str) -> str:
    path = os.path.join(Config.STORAGE_DIR, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def generated_audio_dir() -> str:
    return _storage_dir("audio", "generated")


def generated_chunks_dir(audio_id: str) -> str:
    return _storage_dir("audio", "generated", "chunks", audio_id)


def generated_chunk_manifest_path(audio_id: str) -> str:
    return os.path.join(generated_chunks_dir(audio_id), "manifest.json")


def text_sources_dir() -> str:
    return _storage_dir("text_sources")


def voices_dir() -> str:
    return _storage_dir("voices")


def voices_index_path() -> str:
    path = os.path.join(voices_dir(), "index.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as file_obj:
            json.dump({"voices": []}, file_obj, ensure_ascii=False, indent=2)
    return path


def _safe_name(raw_name: str) -> str:
    name = (raw_name or "").strip()
    name = os.path.splitext(name)[0]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", name)
    name = re.sub(r"\s+", "-", name)
    name = name.strip(".- ")
    return name or next_default_audio_name()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def next_default_audio_name() -> str:
    prefix = datetime.now().strftime("%d-%m")
    pattern = re.compile(rf"^{re.escape(prefix)}-audio-(\d+)\.mp3$", re.I)
    max_index = 0
    for path in Path(generated_audio_dir()).glob(f"{prefix}-audio-*.mp3"):
        match = pattern.match(path.name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"{prefix}-audio-{max_index + 1}"


def _effective_speed(speed: float | None) -> float:
    return speed if speed is not None else Config.TTS_SPEED


def _effective_volume(volume: float | None) -> float:
    return volume if volume is not None else Config.TTS_VOLUME


def _generated_audio_output_path(audio_id: str) -> str:
    return os.path.join(generated_audio_dir(), f"{audio_id}.mp3")


def _chunk_output_path(output_dir: str, index: int) -> str:
    return os.path.join(output_dir, f"{index:04d}.mp3")


def _build_audio_item_from_path(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    duration = FFmpegHelper.probe_duration(path)
    return {
        "id": Path(path).stem,
        "name": Path(path).stem,
        "relativePath": storage_relative_path(path),
        "duration": round(duration, 3),
        "createdAt": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds"),
    }


def _is_valid_audio_path(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and validate_audio(path)


def parse_google_doc_id(doc_url: str) -> str | None:
    value = (doc_url or "").strip()
    if not value:
        return None

    patterns = [
        r"docs\.google\.com/document/d/([^/?#]+)",
        r"drive\.google\.com/file/d/([^/?#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)

    parsed = urlparse(value)
    query_id = parse_qs(parsed.query).get("id")
    if query_id and query_id[0]:
        return query_id[0]
    return None


def download_google_doc_text(doc_url: str) -> str:
    doc_id = parse_google_doc_id(doc_url)
    if not doc_id:
        logger.error(f"[TTS] Invalid Google Docs URL: {doc_url}")
        raise TTSAudioError("Google Docs link is invalid.", "invalid_doc_url")

    logger.info(f"[TTS] Parsed Google document id: {doc_id}")
    export_urls = [
        f"https://docs.google.com/document/d/{doc_id}/export?format=txt",
        f"https://drive.google.com/uc?export=download&id={doc_id}",
    ]
    last_error = None
    for export_url in export_urls:
        try:
            logger.info(f"[TTS] Downloading Google Docs text from: {export_url}")
            response = requests.get(export_url, timeout=60)
            response.raise_for_status()
            text = response.text.strip()
            if text and "<html" not in text[:300].lower():
                logger.info(f"[TTS] Google Docs text downloaded successfully. Characters: {len(text)}")
                return text
            logger.warning(
                f"[TTS] Export URL returned non-text or empty content. Status={response.status_code}, chars={len(text)}"
            )
        except Exception as exc:
            last_error = exc
            logger.error(f"[TTS] Failed to download text from {export_url}: {exc}")

    raise TTSAudioError(
        "Cannot export Google Docs text. Make sure the document is public or exportable.",
        "doc_export_failed",
        {"documentId": doc_id, "error": str(last_error) if last_error else None},
    )


def split_text_into_chunks(text: str, max_chars: int | None = None) -> list[str]:
    limit = max_chars or Config.TTS_MAX_CHARS
    normalized = _normalize_text(text)
    if not normalized:
        logger.warning("[TTS] Text is empty after normalization; no chunks created.")
        return []

    chunks: list[str] = []
    current = ""

    for match in SENTENCE_PATTERN.finditer(normalized):
        sentence = match.group(0).strip()
        if not sentence:
            continue

        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_sentence(sentence, limit))
            continue

        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)

    logger.info(
        f"[TTS] Text chunking completed. chunks={len(chunks)}, max_chars={limit}, "
        f"min_len={min((len(chunk) for chunk in chunks), default=0)}, "
        f"max_len={max((len(chunk) for chunk in chunks), default=0)}"
    )
    return chunks


def _split_long_sentence(sentence: str, limit: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for word in sentence.split(" "):
        if not word:
            continue
        if len(word) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(word[index : index + limit] for index in range(0, len(word), limit))
            continue
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = word
    if current:
        parts.append(current)
    return parts


def _require_tts_config(voice_id: str):
    if not Config.TTS_API_KEY:
        logger.error("[TTS] Missing TTS_API_KEY in config.")
        raise TTSAudioError("TTS_API_KEY is missing in .env.", "tts_config_missing")
    if not voice_id:
        logger.error("[TTS] Missing voice id. Provide voiceId or set TTS_DEFAULT_VOICE_ID.")
        raise TTSAudioError("VoiceId is missing. Select a voice or set TTS_DEFAULT_VOICE_ID.", "tts_config_missing")


def _resolve_voice_id(voice_id: str | None) -> str:
    selected_voice_id = (voice_id or Config.TTS_DEFAULT_VOICE_ID or "").strip()
    if selected_voice_id:
        return selected_voice_id

    voices = load_voices()
    if voices:
        fallback_voice_id = str(voices[0].get("voiceId") or "").strip()
        if fallback_voice_id:
            logger.info(f"[TTS] No voice id provided. Falling back to latest cloned voice: {fallback_voice_id}")
            return fallback_voice_id

    return ""


def _load_chunk_manifest(audio_id: str) -> dict[str, Any] | None:
    manifest_path = generated_chunk_manifest_path(audio_id)
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"[TTS] Could not load chunk manifest for {audio_id}: {exc}")
        return None


def _save_chunk_manifest(manifest: dict[str, Any]):
    manifest["updatedAt"] = _utc_now()
    manifest_path = generated_chunk_manifest_path(str(manifest["audioId"]))
    with open(manifest_path, "w", encoding="utf-8") as file_obj:
        json.dump(manifest, file_obj, ensure_ascii=False, indent=2)


def _default_chunk_record(index: int, text: str, output_dir: str) -> dict[str, Any]:
    return {
        "index": index,
        "textHash": _hash_text(text),
        "chars": len(text),
        "status": "pending",
        "attemptsUsed": 0,
        "maxRetries": CHUNK_MAX_RETRY_CYCLES,
        "lastTaskId": None,
        "lastErrorCode": None,
        "lastErrorMessage": None,
        "outputRelativePath": storage_relative_path(_chunk_output_path(output_dir, index)),
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
        "completedAt": None,
        "failedAt": None,
    }


def _build_chunk_manifest(
    audio_id: str,
    doc_url: str,
    doc_id: str | None,
    text_path: str,
    normalized_text: str,
    voice_id: str,
    speed: float,
    volume: float,
    chunks: list[str],
) -> dict[str, Any]:
    chunk_dir = generated_chunks_dir(audio_id)
    output_path = _generated_audio_output_path(audio_id)
    return {
        "version": CHUNK_MANIFEST_VERSION,
        "audioId": audio_id,
        "docUrl": doc_url,
        "docId": doc_id,
        "textRelativePath": storage_relative_path(text_path),
        "textHash": _hash_text(normalized_text),
        "voiceId": voice_id,
        "speed": speed,
        "volume": volume,
        "chunkCount": len(chunks),
        "maxRetries": CHUNK_MAX_RETRY_CYCLES,
        "maxAttempts": CHUNK_MAX_ATTEMPTS,
        "audioStatus": "none",
        "finalAudioRelativePath": storage_relative_path(output_path),
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
        "lastErrorCode": None,
        "lastErrorMessage": None,
        "chunks": [_default_chunk_record(index, chunk, chunk_dir) for index, chunk in enumerate(chunks, start=1)],
    }


def _manifest_matches_inputs(
    manifest: dict[str, Any],
    doc_id: str | None,
    normalized_text: str,
    voice_id: str,
    speed: float,
    volume: float,
    chunk_count: int,
) -> bool:
    return (
        int(manifest.get("version") or 0) == CHUNK_MANIFEST_VERSION
        and str(manifest.get("docId") or "") == str(doc_id or "")
        and str(manifest.get("textHash") or "") == _hash_text(normalized_text)
        and str(manifest.get("voiceId") or "") == voice_id
        and float(manifest.get("speed") or 0) == float(speed)
        and float(manifest.get("volume") or 0) == float(volume)
        and int(manifest.get("chunkCount") or 0) == int(chunk_count)
    )


def _clear_audio_cache(audio_id: str):
    chunk_dir = generated_chunks_dir(audio_id)
    for old_chunk in Path(chunk_dir).glob("*.mp3"):
        try:
            logger.info(f"[TTS] Removing stale chunk file: {old_chunk}")
            old_chunk.unlink()
        except OSError as exc:
            logger.warning(f"[TTS] Could not remove stale chunk file {old_chunk}: {exc}")

    output_path = _generated_audio_output_path(audio_id)
    concat_path = os.path.join(generated_audio_dir(), f"{audio_id}_concat.txt")
    for stale_path in (output_path, concat_path):
        if os.path.isfile(stale_path):
            try:
                logger.info(f"[TTS] Removing stale audio artifact: {stale_path}")
                os.remove(stale_path)
            except OSError as exc:
                logger.warning(f"[TTS] Could not remove stale audio artifact {stale_path}: {exc}")


def _chunk_record_output_path(record: dict[str, Any]) -> str:
    return storage_absolute_path(str(record.get("outputRelativePath") or ""))


def _chunk_record_has_valid_audio(record: dict[str, Any]) -> bool:
    return _is_valid_audio_path(_chunk_record_output_path(record))


def _chunk_summary_from_manifest(manifest: dict[str, Any]) -> dict[str, int]:
    completed = sum(1 for record in manifest.get("chunks", []) if str(record.get("status")) == "completed")
    failed = sum(1 for record in manifest.get("chunks", []) if str(record.get("status")) == "failed")
    total = int(manifest.get("chunkCount") or len(manifest.get("chunks", [])))
    return {"completed": completed, "failed": failed, "total": total}


def _manifest_audio_state(
    manifest: dict[str, Any],
    *,
    failure_code: str | None = None,
    failure_stage: str | None = None,
) -> dict[str, Any]:
    summary = _chunk_summary_from_manifest(manifest)
    final_audio_relative_path = str(manifest.get("finalAudioRelativePath") or "")
    final_audio_path = storage_absolute_path(final_audio_relative_path) if final_audio_relative_path else ""
    final_audio_ready = _is_valid_audio_path(final_audio_path)

    if final_audio_ready:
        audio_status = "ready"
    elif summary["completed"] > 0 or summary["failed"] > 0:
        audio_status = "partial"
    else:
        audio_status = "none"

    manifest["audioStatus"] = audio_status
    return {
        "audioRelativePath": final_audio_relative_path if final_audio_ready else None,
        "audioStatus": audio_status,
        "chunkSummary": summary,
        "failureCode": failure_code,
        "failureStage": failure_stage,
    }


def _update_chunk_record(
    manifest: dict[str, Any],
    manifest_lock: threading.Lock,
    index: int,
    **updates: Any,
) -> dict[str, Any]:
    with manifest_lock:
        record = manifest["chunks"][index - 1]
        record.update(updates)
        record["updatedAt"] = _utc_now()
        if "lastErrorCode" in updates:
            manifest["lastErrorCode"] = updates["lastErrorCode"]
        if "lastErrorMessage" in updates:
            manifest["lastErrorMessage"] = updates["lastErrorMessage"]
        _manifest_audio_state(manifest)
        _save_chunk_manifest(manifest)
        return dict(record)


def _sync_chunk_manifest_records(manifest: dict[str, Any], chunks: list[str]) -> bool:
    audio_id = str(manifest["audioId"])
    chunk_dir = generated_chunks_dir(audio_id)
    existing_records = {
        int(record.get("index") or 0): dict(record)
        for record in manifest.get("chunks", [])
        if isinstance(record, dict) and int(record.get("index") or 0) > 0
    }
    new_records: list[dict[str, Any]] = []
    changed = False

    for index, chunk in enumerate(chunks, start=1):
        expected_hash = _hash_text(chunk)
        expected_chars = len(chunk)
        current = existing_records.get(index)
        if not current or str(current.get("textHash") or "") != expected_hash or int(current.get("chars") or 0) != expected_chars:
            current = _default_chunk_record(index, chunk, chunk_dir)
            changed = True
        else:
            current["index"] = index
            current["textHash"] = expected_hash
            current["chars"] = expected_chars
            current["maxRetries"] = CHUNK_MAX_RETRY_CYCLES
            current["outputRelativePath"] = storage_relative_path(_chunk_output_path(chunk_dir, index))
            current.setdefault("attemptsUsed", 0)
            current.setdefault("createdAt", _utc_now())
            current.setdefault("completedAt", None)
            current.setdefault("failedAt", None)
            current.setdefault("lastTaskId", None)
            current.setdefault("lastErrorCode", None)
            current.setdefault("lastErrorMessage", None)

            if _chunk_record_has_valid_audio(current):
                if str(current.get("status") or "") != "completed":
                    current["status"] = "completed"
                    current["completedAt"] = current.get("completedAt") or _utc_now()
                    current["lastErrorCode"] = None
                    current["lastErrorMessage"] = None
                    changed = True
            else:
                current_status = str(current.get("status") or "pending")
                if current_status in {"completed", "running"}:
                    current["status"] = "pending"
                    current["attemptsUsed"] = 0
                    current["completedAt"] = None
                    current["lastErrorCode"] = "invalid_cached_audio"
                    current["lastErrorMessage"] = "Cached chunk audio is missing or invalid."
                    changed = True
                elif current_status not in {"pending", "failed"}:
                    current["status"] = "pending"
                    changed = True
        new_records.append(current)

    if len(new_records) != len(manifest.get("chunks", [])):
        changed = True

    manifest["chunks"] = new_records
    manifest["chunkCount"] = len(new_records)
    _manifest_audio_state(manifest)
    return changed


def _reset_retryable_chunk_attempts(manifest: dict[str, Any]):
    changed = False
    for record in manifest.get("chunks", []):
        if str(record.get("status") or "") == "completed" and _chunk_record_has_valid_audio(record):
            continue
        if int(record.get("attemptsUsed") or 0) != 0:
            record["attemptsUsed"] = 0
            changed = True
        if str(record.get("status") or "") != "pending":
            record["status"] = "pending"
            changed = True
        if record.get("lastErrorCode") is not None:
            record["lastErrorCode"] = None
            changed = True
        if record.get("lastErrorMessage") is not None:
            record["lastErrorMessage"] = None
            changed = True
        if record.get("lastTaskId") is not None:
            record["lastTaskId"] = None
            changed = True
        record["failedAt"] = None
        record["updatedAt"] = _utc_now()
    if changed:
        manifest["lastErrorCode"] = None
        manifest["lastErrorMessage"] = None
        _manifest_audio_state(manifest)
    return changed


def _classify_manifest_chunks(manifest: dict[str, Any]) -> tuple[list[int], list[int]]:
    processable: list[int] = []
    exhausted: list[int] = []
    for record in manifest.get("chunks", []):
        index = int(record.get("index") or 0)
        if index <= 0:
            continue
        if str(record.get("status") or "") == "completed" and _chunk_record_has_valid_audio(record):
            continue
        attempts_used = int(record.get("attemptsUsed") or 0)
        if attempts_used >= CHUNK_MAX_ATTEMPTS:
            exhausted.append(index)
        else:
            processable.append(index)
    return processable, exhausted


def _ordered_completed_chunk_paths(manifest: dict[str, Any]) -> list[str]:
    chunk_paths: list[str] = []
    for record in sorted(manifest.get("chunks", []), key=lambda item: int(item.get("index") or 0)):
        if str(record.get("status") or "") != "completed" or not _chunk_record_has_valid_audio(record):
            raise TTSAudioError(
                f"Chunk {int(record.get('index') or 0):04d} is not ready for concat.",
                "audio_concat_failed",
                {"audioState": _manifest_audio_state(manifest, failure_code="audio_concat_failed", failure_stage="tts_audio")},
            )
        chunk_paths.append(_chunk_record_output_path(record))
    return chunk_paths


def _tts_headers() -> dict[str, str]:
    return {"T-API-KEY": Config.TTS_API_KEY, "Content-Type": "application/json"}


def _api_url(path: str) -> str:
    return f"{Config.TTS_API_BASE_URL.rstrip('/')}/{path.lstrip('/')}"


def _create_tts_task(text: str, voice_id: str, speed: float, volume: float) -> str:
    payload = {
        "VoiceId": voice_id,
        "Text": text,
        "Platform": Config.TTS_PLATFORM,
        "Model": "",
        "ModelId": "",
        "Style": 0,
        "Stability": 0,
        "Similarity": 0,
        "Speed": speed,
        "Volume": volume,
        "Direction": "",
        "DirectorStyle": "",
        "EngineType": "",
        "Locale": "",
        "Seed": int(time.time() * 1000) % 2147483647,
    }
    url = _api_url("/api/minimax/createtask2")
    logger.info(f"[TTS] Creating TTS task. voiceId={voice_id}, chars={len(text)}, url={url}")

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=_tts_headers(), json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            break
        except Exception as exc:
            if attempt < max_retries - 1:
                logger.warning(
                    f"[TTS][HTTP retry] Create TTS task request failed (Attempt {attempt + 1}/{max_retries}). "
                    f"Retrying in {3 * (attempt + 1)}s... Error: {exc}"
                )
                time.sleep(3 * (attempt + 1))
                continue
            response_text = getattr(locals().get("response", None), "text", "")
            logger.error(
                f"[TTS] Create TTS task failed after {max_retries} HTTP attempts. "
                f"error={exc}, response={response_text[:1000]}"
            )
            raise TTSAudioError(
                "Create TTS task failed.",
                "tts_create_task_failed",
                {"error": str(exc), "response": response_text[:2000]},
            ) from exc
    task_id = data.get("taskId")
    if not task_id:
        logger.error(f"[TTS] Create TTS task response missing taskId. response={data}")
        raise TTSAudioError("TTS task response missing taskId.", "tts_task_failed", {"response": data})
    logger.info(f"[TTS] TTS task created successfully. taskId={task_id}")
    return task_id


def _poll_tts_task(task_id: str) -> str:
    deadline = time.time() + Config.TTS_TASK_TIMEOUT_SECONDS
    poll_count = 0
    consecutive_failures = 0
    max_failures = 5
    logger.info(f"[TTS] Polling TTS task. taskId={task_id}")
    while time.time() < deadline:
        poll_count += 1
        url = _api_url(f"/api/minimax/{task_id}")
        try:
            response = requests.get(url, headers={"T-API-KEY": Config.TTS_API_KEY}, timeout=60)
            response.raise_for_status()
            data = response.json()
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            if consecutive_failures < max_failures:
                logger.warning(
                    f"[TTS][HTTP retry] Poll TTS task failed (Failure {consecutive_failures}/{max_failures}). "
                    f"Continuing poll in {3 * consecutive_failures}s... Error: {exc}"
                )
                time.sleep(3 * consecutive_failures)
                continue
            response_text = getattr(locals().get("response", None), "text", "")
            logger.error(
                f"[TTS] Poll TTS task failed after {max_failures} consecutive HTTP failures. "
                f"taskId={task_id}, error={exc}, response={response_text[:1000]}"
            )
            raise TTSAudioError(
                "Poll TTS task failed.",
                "tts_poll_failed",
                {"taskId": task_id, "error": str(exc), "response": response_text[:2000]},
            ) from exc
        status = str(data.get("status", "")).lower()
        logger.info(f"[TTS] Poll result. taskId={task_id}, poll={poll_count}, status={status}")
        if status == "completed":
            audio_url = data.get("audioUrl")
            if not audio_url:
                logger.error(f"[TTS] Completed task missing audioUrl. taskId={task_id}, response={data}")
                raise TTSAudioError("Completed TTS task missing audioUrl.", "tts_task_failed", {"taskId": task_id})
            logger.info(f"[TTS] Task completed. taskId={task_id}, audioUrl={audio_url}")
            return audio_url
        if status == "fail":
            logger.error(f"[TTS] Task failed. taskId={task_id}, response={data}")
            raise TTSAudioError("TTS task failed.", "tts_task_failed", {"taskId": task_id, "response": data})
        time.sleep(Config.TTS_POLL_INTERVAL_SECONDS)
    logger.error(f"[TTS] Task timed out. taskId={task_id}, timeout={Config.TTS_TASK_TIMEOUT_SECONDS}s")
    raise TTSAudioError("TTS task timed out.", "tts_task_timeout", {"taskId": task_id})


def _download_audio(audio_url: str, output_path: str):
    logger.info(f"[TTS] Downloading chunk audio. url={audio_url}, output={output_path}")
    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.get(audio_url, timeout=120)
            response.raise_for_status()
            break
        except Exception as exc:
            if attempt < max_retries - 1:
                logger.warning(
                    f"[TTS][HTTP retry] Download chunk audio failed (Attempt {attempt + 1}/{max_retries}). "
                    f"Retrying in {3 * (attempt + 1)}s... Error: {exc}"
                )
                time.sleep(3 * (attempt + 1))
                continue
            response_text = getattr(locals().get("response", None), "text", "")
            logger.error(
                f"[TTS] Download chunk audio failed after {max_retries} HTTP attempts. "
                f"error={exc}, response={response_text[:1000]}"
            )
            raise TTSAudioError(
                "Download chunk audio failed.",
                "tts_download_failed",
                {"error": str(exc), "response": response_text[:2000], "audioUrl": audio_url},
            ) from exc
    with open(output_path, "wb") as file_obj:
        file_obj.write(response.content)
    logger.info(f"[TTS] Chunk audio downloaded. output={output_path}, bytes={len(response.content)}")


def _synthesize_chunk_with_retry(
    index: int,
    text: str,
    voice_id: str,
    speed: float,
    volume: float,
    output_dir: str,
    manifest: dict[str, Any],
    manifest_lock: threading.Lock,
) -> str:
    output_path = _chunk_output_path(output_dir, index)
    if _is_valid_audio_path(output_path):
        _update_chunk_record(
            manifest,
            manifest_lock,
            index,
            status="completed",
            completedAt=_utc_now(),
            lastErrorCode=None,
            lastErrorMessage=None,
        )
        logger.info(f"[TTS] Chunk {index:04d} reused from cache. output={output_path}")
        return output_path

    while True:
        current_record = manifest["chunks"][index - 1]
        attempts_used = int(current_record.get("attemptsUsed") or 0)
        if attempts_used >= CHUNK_MAX_ATTEMPTS:
            raise TTSAudioError(
                f"Chunk {index:04d} exhausted retry budget.",
                "tts_chunk_exhausted",
                {"audioState": _manifest_audio_state(manifest, failure_code="tts_chunk_exhausted", failure_stage="tts_audio")},
            )

        current_attempt = attempts_used + 1
        task_id: str | None = None
        _update_chunk_record(
            manifest,
            manifest_lock,
            index,
            status="running",
            lastErrorCode=None,
            lastErrorMessage=None,
            failedAt=None,
        )
        logger.info(
            f"[TTS] Chunk {index:04d} started. attempt={current_attempt}/{CHUNK_MAX_ATTEMPTS}, chars={len(text)}"
        )
        try:
            task_id = _create_tts_task(text, voice_id, speed, volume)
            _update_chunk_record(manifest, manifest_lock, index, lastTaskId=task_id)
            logger.info(f"[TTS] Chunk {index:04d} task created. taskId={task_id}")
            audio_url = _poll_tts_task(task_id)

            if os.path.isfile(output_path) and not _is_valid_audio_path(output_path):
                os.remove(output_path)
            _download_audio(audio_url, output_path)
            if not _is_valid_audio_path(output_path):
                raise TTSAudioError(
                    "Downloaded chunk audio is invalid.",
                    "tts_invalid_chunk_audio",
                    {"chunkIndex": index, "taskId": task_id},
                )

            _update_chunk_record(
                manifest,
                manifest_lock,
                index,
                status="completed",
                attemptsUsed=current_attempt,
                lastTaskId=task_id,
                lastErrorCode=None,
                lastErrorMessage=None,
                completedAt=_utc_now(),
                failedAt=None,
            )
            logger.info(f"[TTS] Chunk {index:04d} completed. output={output_path}")
            return output_path
        except TTSAudioError as exc:
            error_code = exc.code
            error_message = str(exc)
        except Exception as exc:
            error_code = "tts_chunk_failed"
            error_message = str(exc)

        if os.path.isfile(output_path) and not _is_valid_audio_path(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass

        terminal_failure = current_attempt >= CHUNK_MAX_ATTEMPTS
        _update_chunk_record(
            manifest,
            manifest_lock,
            index,
            status="failed" if terminal_failure else "pending",
            attemptsUsed=current_attempt,
            lastTaskId=task_id,
            lastErrorCode=error_code,
            lastErrorMessage=error_message,
            failedAt=_utc_now() if terminal_failure else None,
        )

        if terminal_failure:
            logger.error(
                f"[TTS][Chunk retry] Chunk {index:04d} exhausted retry budget after "
                f"{current_attempt}/{CHUNK_MAX_ATTEMPTS} attempts. code={error_code}, error={error_message}"
            )
            raise TTSAudioError(
                error_message,
                error_code,
                {
                    "chunkIndex": index,
                    "taskId": task_id,
                    "audioState": _manifest_audio_state(
                        manifest,
                        failure_code=error_code,
                        failure_stage="tts_audio",
                    ),
                },
            )

        retry_delay = min(15, current_attempt * 2)
        logger.warning(
            f"[TTS][Chunk retry] Chunk {index:04d} attempt {current_attempt}/{CHUNK_MAX_ATTEMPTS} failed. "
            f"Retrying chunk in {retry_delay}s. code={error_code}, error={error_message}"
        )
        time.sleep(retry_delay)


def _concat_audio_chunks(chunk_paths: list[str], output_path: str) -> bool:
    concat_path = os.path.join(os.path.dirname(output_path), f"{Path(output_path).stem}_concat.txt")
    logger.info(f"[TTS] Writing FFmpeg concat list. chunks={len(chunk_paths)}, path={concat_path}")
    with open(concat_path, "w", encoding="utf-8") as file_obj:
        for chunk_path in chunk_paths:
            normalized = os.path.abspath(chunk_path).replace("\\", "/").replace("'", "'\\''")
            file_obj.write(f"file '{normalized}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_path,
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        output_path,
    ]
    logger.info(f"[TTS] Concatenating audio chunks with FFmpeg. output={output_path}")
    success = FFmpegHelper.run_command(cmd)
    if success:
        logger.info(f"[TTS] FFmpeg concat completed. output={output_path}")
    else:
        logger.error(f"[TTS] FFmpeg concat failed. output={output_path}")
    return success


def list_generated_audio() -> list[dict]:
    items = []
    for path in sorted(Path(generated_audio_dir()).glob("*.mp3"), key=lambda item: item.stat().st_mtime, reverse=True):
        duration = FFmpegHelper.probe_duration(str(path))
        items.append(
            {
                "id": path.stem,
                "name": path.stem,
                "relativePath": storage_relative_path(str(path)),
                "duration": round(duration, 3),
                "createdAt": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return items


def create_audio_from_google_doc(
    doc_url: str,
    output_name: str | None = None,
    voice_id: str | None = None,
    speed: float | None = None,
    volume: float | None = None,
    *,
    reset_failed_chunk_attempts: bool = True,
) -> dict:
    selected_voice_id = _resolve_voice_id(voice_id)
    effective_speed = _effective_speed(speed)
    effective_volume = _effective_volume(volume)
    logger.info(
        f"[TTS] Docs-to-audio started. outputName={output_name or '<auto>'}, "
        f"voiceId={selected_voice_id or '<missing>'}, speed={effective_speed}, volume={effective_volume}"
    )
    _require_tts_config(selected_voice_id)

    doc_id = parse_google_doc_id(doc_url)
    text = download_google_doc_text(doc_url)
    if not text.strip():
        logger.error("[TTS] Google Docs text is empty.")
        raise TTSAudioError("Google Docs text is empty.", "empty_doc_text")
    normalized_text = _normalize_text(text)

    audio_name = _safe_name(output_name or "")
    audio_id = audio_name
    text_path = os.path.join(text_sources_dir(), f"{audio_id}.txt")
    logger.info(f"[TTS] Saving downloaded text to file. path={text_path}, chars={len(text)}")
    with open(text_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(text)
    logger.info(f"[TTS] Text file saved. path={text_path}")

    chunks = split_text_into_chunks(text, Config.TTS_MAX_CHARS)
    if not chunks:
        logger.error("[TTS] No chunks created after text chunking.")
        raise TTSAudioError("Google Docs text is empty after normalization.", "empty_doc_text")

    chunk_dir = generated_chunks_dir(audio_id)
    logger.info(f"[TTS] Preparing chunk output directory. path={chunk_dir}")
    manifest = _load_chunk_manifest(audio_id)
    manifest_changed = False

    if manifest and _manifest_matches_inputs(
        manifest,
        doc_id,
        normalized_text,
        selected_voice_id,
        effective_speed,
        effective_volume,
        len(chunks),
    ):
        manifest_changed = _sync_chunk_manifest_records(manifest, chunks)
        manifest["docUrl"] = doc_url
        manifest["docId"] = doc_id
        manifest["textRelativePath"] = storage_relative_path(text_path)
        manifest["textHash"] = _hash_text(normalized_text)
        manifest["voiceId"] = selected_voice_id
        manifest["speed"] = effective_speed
        manifest["volume"] = effective_volume
        manifest["chunkCount"] = len(chunks)
        manifest["maxRetries"] = CHUNK_MAX_RETRY_CYCLES
        manifest["maxAttempts"] = CHUNK_MAX_ATTEMPTS
        manifest["finalAudioRelativePath"] = storage_relative_path(_generated_audio_output_path(audio_id))

        cached_audio_state = _manifest_audio_state(manifest)
        if cached_audio_state["audioStatus"] == "ready" and cached_audio_state["audioRelativePath"]:
            if manifest_changed:
                _save_chunk_manifest(manifest)
            cached_audio_path = storage_absolute_path(cached_audio_state["audioRelativePath"])
            audio_item = _build_audio_item_from_path(cached_audio_path)
            logger.info(
                f"[TTS] Reusing existing final audio without new TTS requests. "
                f"output={cached_audio_path}, chunks={len(chunks)}"
            )
            return {
                "audio": audio_item,
                "chunkCount": len(chunks),
                "textRelativePath": storage_relative_path(text_path),
                "audioState": cached_audio_state,
            }

        if reset_failed_chunk_attempts:
            manifest_changed = _reset_retryable_chunk_attempts(manifest) or manifest_changed
    else:
        if manifest:
            logger.info(f"[TTS] Manifest fingerprint changed for {audio_id}. Resetting cached chunks/audio.")
            _clear_audio_cache(audio_id)
        manifest = _build_chunk_manifest(
            audio_id,
            doc_url,
            doc_id,
            text_path,
            normalized_text,
            selected_voice_id,
            effective_speed,
            effective_volume,
            chunks,
        )
        manifest_changed = True

    if manifest_changed:
        _save_chunk_manifest(manifest)

    processable_indexes, exhausted_indexes = _classify_manifest_chunks(manifest)
    if exhausted_indexes and not reset_failed_chunk_attempts:
        failure_code = str(manifest.get("lastErrorCode") or "tts_chunk_exhausted")
        failure_message = str(manifest.get("lastErrorMessage") or "One or more TTS chunks exhausted retry budget.")
        raise TTSAudioError(
            failure_message,
            failure_code,
            {"audioState": _manifest_audio_state(manifest, failure_code=failure_code, failure_stage="tts_audio")},
        )

    chunk_results: dict[int, str] = {}
    concurrency = max(1, min(Config.TTS_MAX_CONCURRENCY, 15, len(processable_indexes) or len(chunks)))
    manifest_lock = threading.Lock()
    if processable_indexes:
        logger.info(
            f"[TTS] Starting TTS chunk processing. totalChunks={len(chunks)}, "
            f"pendingChunks={len(processable_indexes)}, concurrency={concurrency}"
        )

    first_error: TTSAudioError | None = None
    cancelled_pending = False
    if processable_indexes:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(
                    _synthesize_chunk_with_retry,
                    index,
                    chunks[index - 1],
                    selected_voice_id,
                    effective_speed,
                    effective_volume,
                    chunk_dir,
                    manifest,
                    manifest_lock,
                ): index
                for index in processable_indexes
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    chunk_results[index] = future.result()
                    logger.info(
                        f"[TTS] Chunk future success. chunk={index:04d}, "
                        f"completed={len(chunk_results)}/{len(processable_indexes)}"
                    )
                except CancelledError:
                    logger.info(f"[TTS] Chunk future cancelled. chunk={index:04d}")
                except TTSAudioError as exc:
                    logger.exception(f"[TTS] Chunk future failed. chunk={index:04d}, error={exc}")
                    if first_error is None:
                        first_error = exc
                    if not cancelled_pending:
                        cancelled_pending = True
                        for pending_future, pending_index in futures.items():
                            if pending_future is future or pending_future.done():
                                continue
                            if pending_future.cancel():
                                logger.info(
                                    f"[TTS] Cancelled pending chunk future after terminal failure. "
                                    f"chunk={pending_index:04d}"
                                )
                except Exception as exc:
                    logger.exception(f"[TTS] Chunk future failed. chunk={index:04d}, error={exc}")
                    if first_error is None:
                        first_error = TTSAudioError(
                            "TTS request failed.",
                            "tts_task_failed",
                            {"error": str(exc)},
                        )
                    if not cancelled_pending:
                        cancelled_pending = True
                        for pending_future, pending_index in futures.items():
                            if pending_future is future or pending_future.done():
                                continue
                            if pending_future.cancel():
                                logger.info(
                                    f"[TTS] Cancelled pending chunk future after terminal failure. "
                                    f"chunk={pending_index:04d}"
                                )

    if first_error is not None:
        manifest["lastErrorCode"] = first_error.code
        manifest["lastErrorMessage"] = str(first_error)
        _save_chunk_manifest(manifest)
        logger.error("[TTS] Docs-to-audio stopped because a TTS chunk failed with TTSAudioError.")
        raise TTSAudioError(
            str(first_error),
            first_error.code,
            {
                **(first_error.details or {}),
                "audioState": _manifest_audio_state(
                    manifest,
                    failure_code=first_error.code,
                    failure_stage="tts_audio",
                ),
            },
        )

    ordered_chunks = _ordered_completed_chunk_paths(manifest)
    output_path = _generated_audio_output_path(audio_id)
    logger.info(f"[TTS] All chunks completed. orderedChunks={len(ordered_chunks)}, finalOutput={output_path}")
    if not _concat_audio_chunks(ordered_chunks, output_path):
        manifest["lastErrorCode"] = "audio_concat_failed"
        manifest["lastErrorMessage"] = "Cannot concat TTS audio chunks."
        _save_chunk_manifest(manifest)
        raise TTSAudioError(
            "Cannot concat TTS audio chunks.",
            "audio_concat_failed",
            {"audioState": _manifest_audio_state(manifest, failure_code="audio_concat_failed", failure_stage="tts_audio")},
        )
    if not validate_audio(output_path):
        logger.error(f"[TTS] Final generated audio failed validation. output={output_path}")
        manifest["lastErrorCode"] = "audio_concat_failed"
        manifest["lastErrorMessage"] = "Generated audio is invalid."
        _save_chunk_manifest(manifest)
        raise TTSAudioError(
            "Generated audio is invalid.",
            "audio_concat_failed",
            {"audioState": _manifest_audio_state(manifest, failure_code="audio_concat_failed", failure_stage="tts_audio")},
        )

    manifest["lastErrorCode"] = None
    manifest["lastErrorMessage"] = None
    _manifest_audio_state(manifest)
    _save_chunk_manifest(manifest)
    audio_item = _build_audio_item_from_path(output_path)
    logger.info(
        f"[TTS] Docs-to-audio completed successfully. output={output_path}, "
        f"relativePath={storage_relative_path(output_path)}, chunks={len(chunks)}"
    )
    return {
        "audio": audio_item,
        "chunkCount": len(chunks),
        "textRelativePath": storage_relative_path(text_path),
        "audioState": _manifest_audio_state(manifest),
    }


def is_valid_generated_audio_relative_path(relative_path: str) -> bool:
    normalized = os.path.normpath(relative_path or "").replace("\\", "/")
    if not normalized.startswith("audio/generated/") or "/chunks/" in normalized:
        return False
    absolute_path = storage_absolute_path(normalized)
    generated_root = os.path.abspath(generated_audio_dir())
    return os.path.isfile(absolute_path) and os.path.abspath(absolute_path).startswith(generated_root)


def copy_generated_audio_to_job(relative_path: str, job_audio_dir: str) -> str:
    if not is_valid_generated_audio_relative_path(relative_path):
        raise TTSAudioError("Existing generated audio is invalid.", "invalid_existing_audio")
    source_path = storage_absolute_path(relative_path)
    target_name = f"audio_{Path(source_path).name}"
    target_path = os.path.join(job_audio_dir, target_name)
    shutil.copy2(source_path, target_path)
    return target_path


def load_voices() -> list[dict]:
    with open(voices_index_path(), "r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    voices = data.get("voices", []) if isinstance(data, dict) else []
    return sorted(voices, key=lambda voice: voice.get("createdAt", ""), reverse=True)


def save_voice_record(record: dict):
    voices = load_voices()
    lookup = {voice.get("voiceId"): voice for voice in voices if voice.get("voiceId")}
    lookup[record["voiceId"]] = record
    with open(voices_index_path(), "w", encoding="utf-8") as file_obj:
        json.dump({"voices": list(lookup.values())}, file_obj, ensure_ascii=False, indent=2)


def clone_voice(audio_bytes: bytes, source_file_name: str, voice_name: str) -> dict:
    if not Config.TTS_API_KEY:
        raise TTSAudioError("TTS_API_KEY is missing in .env.", "tts_config_missing")
    clean_voice_name = (voice_name or "").strip()
    if not clean_voice_name:
        raise TTSAudioError("Voice name is required.", "bad_request")

    payload = {
        "FileName": f"{int(time.time() * 1000)}-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', source_file_name or 'voice')}",
        "voiceName": clean_voice_name,
        "AudioBytes": base64.b64encode(audio_bytes).decode("ascii"),
    }
    response = requests.post(_api_url("/api/minimax/clone2job"), headers=_tts_headers(), json=payload, timeout=180)
    response.raise_for_status()
    data = response.json()
    voice_id = data.get("voiceId")
    if not voice_id:
        raise TTSAudioError("Clone voice response missing voiceId.", "tts_task_failed", {"response": data})

    record = {
        "voiceId": voice_id,
        "voiceName": clean_voice_name,
        "sourceFileName": source_file_name,
        "createdAt": _utc_now(),
    }
    save_voice_record(record)
    return record
