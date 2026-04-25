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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as Urllib3Retry

from src.config import Config
from src.processors.audio_utils import validate_audio
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import storage_absolute_path, storage_relative_path
from src.utils.logger import logger


def _build_http_session() -> requests.Session:
    """Create a requests.Session with connection pooling and auto-retry on transport errors."""
    session = requests.Session()
    retry_strategy = Urllib3Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=10,
        pool_maxsize=20,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_http_session: requests.Session = _build_http_session()


def _elapsed_ms(start: float) -> str:
    return f"{(time.time() - start) * 1000:.0f}ms"


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
        logger.error(f"[TTS][GoogleDoc] Invalid Google Docs URL: {doc_url}")
        raise TTSAudioError("Google Docs link is invalid.", "invalid_doc_url")

    logger.info(f"[TTS][GoogleDoc] Parsed Google document id: {doc_id} from URL: {doc_url}")
    export_urls = [
        f"https://docs.google.com/document/d/{doc_id}/export?format=txt",
        f"https://drive.google.com/uc?export=download&id={doc_id}",
    ]
    last_error = None
    for url_index, export_url in enumerate(export_urls):
        t0 = time.time()
        try:
            logger.info(f"[TTS][GoogleDoc] Attempt {url_index + 1}/{len(export_urls)}: GET {export_url}")
            response = _http_session.get(export_url, timeout=60)
            elapsed = _elapsed_ms(t0)
            logger.info(
                f"[TTS][GoogleDoc] Response received. status={response.status_code}, "
                f"content_type={response.headers.get('content-type', 'N/A')}, "
                f"content_length={len(response.content)}, elapsed={elapsed}"
            )
            response.raise_for_status()
            text = response.text.strip()
            # Strip Unicode BOM that Google Docs export often prepends
            if text.startswith("\ufeff"):
                text = text[1:]
                logger.info("[TTS][GoogleDoc] Stripped Unicode BOM from exported text.")
            if text and "<html" not in text[:300].lower():
                logger.info(
                    f"[TTS][GoogleDoc] Text downloaded successfully. chars={len(text)}, "
                    f"first_100_chars={text[:100]!r}, elapsed={elapsed}"
                )
                return text
            logger.warning(
                f"[TTS][GoogleDoc] Export returned non-text or empty content. "
                f"status={response.status_code}, url={response.url}, "
                f"headers={dict(response.headers)}, chars={len(text)}, "
                f"starts_with={text[:500]!r}, elapsed={elapsed}"
            )
        except Exception as exc:
            elapsed = _elapsed_ms(t0)
            last_error = exc
            resp_text = getattr(exc.response, 'text', '') if hasattr(exc, 'response') and exc.response else ''
            logger.error(
                f"[TTS][GoogleDoc] Failed to download text. url={export_url}, "
                f"error_type={type(exc).__name__}, error={exc}, response_snippet={resp_text[:500]}, elapsed={elapsed}"
            )

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
    thread_name = threading.current_thread().name
    logger.info(
        f"[TTS][CreateTask] START. voiceId={voice_id}, chars={len(text)}, text_preview={text[:100]!r}, "
        f"speed={speed}, volume={volume}, thread={thread_name}, url={url}"
    )

    max_retries = 5
    overall_t0 = time.time()
    for attempt in range(max_retries):
        t0 = time.time()
        try:
            logger.debug(
                f"[TTS][CreateTask] HTTP POST attempt {attempt + 1}/{max_retries}. "
                f"payload_size={len(json.dumps(payload))} bytes, thread={thread_name}"
            )
            response = _http_session.post(url, headers=_tts_headers(), json=payload, timeout=60)
            elapsed = _elapsed_ms(t0)
            logger.info(
                f"[TTS][CreateTask] HTTP response. attempt={attempt + 1}, status={response.status_code}, "
                f"content_length={len(response.content)}, elapsed={elapsed}, thread={thread_name}"
            )
            response.raise_for_status()
            data = response.json()
            break
        except Exception as exc:
            elapsed = _elapsed_ms(t0)
            if attempt < max_retries - 1:
                wait_time = 3 * (attempt + 1)
                logger.warning(
                    f"[TTS][CreateTask] HTTP attempt {attempt + 1}/{max_retries} FAILED. "
                    f"error_type={type(exc).__name__}, error={exc}, elapsed={elapsed}, "
                    f"retry_in={wait_time}s, thread={thread_name}"
                )
                time.sleep(wait_time)
                continue
            response_text = ""
            try:
                response_text = response.text[:2000] if response else ""
            except Exception:
                pass
            total_elapsed = _elapsed_ms(overall_t0)
            logger.error(
                f"[TTS][CreateTask] FAILED after {max_retries} attempts. "
                f"error_type={type(exc).__name__}, error={exc}, "
                f"total_elapsed={total_elapsed}, response={response_text[:1000]}, thread={thread_name}"
            )
            raise TTSAudioError(
                "Create TTS task failed.",
                "tts_create_task_failed",
                {"error": str(exc), "response": response_text[:2000]},
            ) from exc
    task_id = data.get("taskId")
    total_elapsed = _elapsed_ms(overall_t0)
    if not task_id:
        logger.error(
            f"[TTS][CreateTask] Response missing taskId. response={data}, "
            f"total_elapsed={total_elapsed}, thread={thread_name}"
        )
        raise TTSAudioError("TTS task response missing taskId.", "tts_task_failed", {"response": data})
    logger.info(
        f"[TTS][CreateTask] SUCCESS. taskId={task_id}, total_elapsed={total_elapsed}, thread={thread_name}"
    )
    return task_id


def _poll_tts_task(task_id: str, timeout_seconds: int | None = None) -> str:
    """Poll a TTS task until it completes or times out.

    Args:
        task_id: The task ID returned by _create_tts_task.
        timeout_seconds: Hard deadline for this single task (default:
            TTS_SINGLE_TASK_TIMEOUT_SECONDS from config, typically 60 s).
            If the task is still "running" after this deadline, a
            tts_task_timeout TTSAudioError is raised so the caller can
            create a brand-new task and retry.
    """
    if timeout_seconds is None:
        timeout_seconds = Config.TTS_SINGLE_TASK_TIMEOUT_SECONDS
    deadline = time.time() + timeout_seconds
    poll_count = 0
    consecutive_failures = 0
    max_failures = 5
    thread_name = threading.current_thread().name
    poll_start = time.time()
    logger.info(
        f"[TTS][Poll] START. taskId={task_id}, timeout={timeout_seconds}s, "
        f"poll_interval={Config.TTS_POLL_INTERVAL_SECONDS}s, thread={thread_name}"
    )
    while time.time() < deadline:
        poll_count += 1
        url = _api_url(f"/api/minimax/{task_id}")
        t0 = time.time()
        try:
            response = _http_session.get(url, headers={"T-API-KEY": Config.TTS_API_KEY}, timeout=30)
            elapsed = _elapsed_ms(t0)
            response.raise_for_status()
            data = response.json()
            consecutive_failures = 0
        except Exception as exc:
            elapsed = _elapsed_ms(t0)
            consecutive_failures += 1
            if consecutive_failures < max_failures:
                wait_time = 3 * consecutive_failures
                logger.warning(
                    f"[TTS][Poll] HTTP failure {consecutive_failures}/{max_failures}. "
                    f"taskId={task_id}, poll={poll_count}, error_type={type(exc).__name__}, "
                    f"error={exc}, elapsed={elapsed}, retry_in={wait_time}s, thread={thread_name}"
                )
                time.sleep(wait_time)
                continue
            response_text = ""
            try:
                response_text = response.text[:2000] if response else ""
            except Exception:
                pass
            total_poll_elapsed = _elapsed_ms(poll_start)
            logger.error(
                f"[TTS][Poll] FAILED after {max_failures} consecutive HTTP failures. "
                f"taskId={task_id}, polls={poll_count}, total_poll_elapsed={total_poll_elapsed}, "
                f"error_type={type(exc).__name__}, error={exc}, response={response_text[:1000]}, thread={thread_name}"
            )
            raise TTSAudioError(
                "Poll TTS task failed.",
                "tts_poll_failed",
                {"taskId": task_id, "error": str(exc), "response": response_text[:2000]},
            ) from exc
        status = str(data.get("status", "")).lower()
        total_poll_elapsed = _elapsed_ms(poll_start)
        if poll_count <= 3 or poll_count % 5 == 0 or status in ("completed", "fail"):
            logger.info(
                f"[TTS][Poll] taskId={task_id}, poll={poll_count}, status={status}, "
                f"elapsed={elapsed}, total_poll_elapsed={total_poll_elapsed}, thread={thread_name}"
            )
        if status == "completed":
            audio_url = data.get("audioUrl")
            if not audio_url:
                logger.error(
                    f"[TTS][Poll] Completed task missing audioUrl. taskId={task_id}, "
                    f"response={data}, thread={thread_name}"
                )
                raise TTSAudioError("Completed TTS task missing audioUrl.", "tts_task_failed", {"taskId": task_id})
            logger.info(
                f"[TTS][Poll] Task completed. taskId={task_id}, polls={poll_count}, "
                f"total_poll_elapsed={total_poll_elapsed}, audioUrl={audio_url[:120]}, thread={thread_name}"
            )
            return audio_url
        if status == "fail":
            logger.error(
                f"[TTS][Poll] Task FAILED on server side. taskId={task_id}, polls={poll_count}, "
                f"total_poll_elapsed={total_poll_elapsed}, response={data}, thread={thread_name}"
            )
            raise TTSAudioError("TTS task failed.", "tts_task_failed", {"taskId": task_id, "response": data})
        # Sleep only up to the remaining deadline to avoid overshooting
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(Config.TTS_POLL_INTERVAL_SECONDS, remaining))
    total_poll_elapsed = _elapsed_ms(poll_start)
    logger.warning(
        f"[TTS][Poll] TIMEOUT after {timeout_seconds}s. taskId={task_id}, polls={poll_count}, "
        f"total_poll_elapsed={total_poll_elapsed}, thread={thread_name}. "
        f"Will retry with a new task if attempts remain."
    )
    raise TTSAudioError(
        f"TTS task timed out after {timeout_seconds}s (still running).",
        "tts_task_timeout",
        {"taskId": task_id, "polls": poll_count, "timeoutSeconds": timeout_seconds},
    )




def _download_audio(audio_url: str, output_path: str):
    thread_name = threading.current_thread().name
    logger.info(
        f"[TTS][Download] START. url={audio_url[:120]}, output={output_path}, thread={thread_name}"
    )
    max_retries = 5
    overall_t0 = time.time()
    for attempt in range(max_retries):
        t0 = time.time()
        try:
            response = _http_session.get(audio_url, timeout=120)
            elapsed = _elapsed_ms(t0)
            logger.info(
                f"[TTS][Download] HTTP response. attempt={attempt + 1}, status={response.status_code}, "
                f"content_length={len(response.content)}, content_type={response.headers.get('content-type', 'N/A')}, "
                f"elapsed={elapsed}, thread={thread_name}"
            )
            response.raise_for_status()
            break
        except Exception as exc:
            elapsed = _elapsed_ms(t0)
            if attempt < max_retries - 1:
                wait_time = 3 * (attempt + 1)
                logger.warning(
                    f"[TTS][Download] Attempt {attempt + 1}/{max_retries} FAILED. "
                    f"error_type={type(exc).__name__}, error={exc}, elapsed={elapsed}, "
                    f"retry_in={wait_time}s, thread={thread_name}"
                )
                time.sleep(wait_time)
                continue
            response_text = ""
            try:
                response_text = response.text[:2000] if response else ""
            except Exception:
                pass
            total_elapsed = _elapsed_ms(overall_t0)
            logger.error(
                f"[TTS][Download] FAILED after {max_retries} attempts. "
                f"error_type={type(exc).__name__}, error={exc}, "
                f"total_elapsed={total_elapsed}, response={response_text[:1000]}, thread={thread_name}"
            )
            raise TTSAudioError(
                "Download chunk audio failed.",
                "tts_download_failed",
                {"error": str(exc), "response": response_text[:2000], "audioUrl": audio_url},
            ) from exc
    with open(output_path, "wb") as file_obj:
        file_obj.write(response.content)
    total_elapsed = _elapsed_ms(overall_t0)
    logger.info(
        f"[TTS][Download] SUCCESS. output={output_path}, bytes={len(response.content)}, "
        f"total_elapsed={total_elapsed}, thread={thread_name}"
    )


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
    thread_name = threading.current_thread().name
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
        logger.info(f"[TTS][Chunk] {index:04d} reused from cache. output={output_path}, thread={thread_name}")
        return output_path

    chunk_start_time = time.time()

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
            f"[TTS][Chunk] {index:04d} attempt START. attempt={current_attempt}/{CHUNK_MAX_ATTEMPTS}, "
            f"chars={len(text)}, chunk_elapsed={_elapsed_ms(chunk_start_time)}, thread={thread_name}"
        )
        attempt_t0 = time.time()
        try:
            task_id = _create_tts_task(text, voice_id, speed, volume)
            _update_chunk_record(manifest, manifest_lock, index, lastTaskId=task_id)
            logger.info(
                f"[TTS][Chunk] {index:04d} task created. taskId={task_id}, "
                f"attempt_elapsed={_elapsed_ms(attempt_t0)}, thread={thread_name}"
            )
            # Use per-task timeout (60s). On timeout a new task will be created on next attempt.
            audio_url = _poll_tts_task(task_id, timeout_seconds=Config.TTS_SINGLE_TASK_TIMEOUT_SECONDS)

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
            logger.info(
                f"[TTS][Chunk] {index:04d} COMPLETED. output={output_path}, "
                f"attempt_elapsed={_elapsed_ms(attempt_t0)}, total_chunk_elapsed={_elapsed_ms(chunk_start_time)}, "
                f"thread={thread_name}"
            )
            return output_path
        except TTSAudioError as exc:
            error_code = exc.code
            error_message = str(exc)
            logger.warning(
                f"[TTS][Chunk] {index:04d} TTSAudioError in attempt {current_attempt}. "
                f"code={exc.code}, error={exc}, attempt_elapsed={_elapsed_ms(attempt_t0)}, thread={thread_name}"
            )
        except Exception as exc:
            error_code = "tts_chunk_failed"
            error_message = str(exc)
            logger.warning(
                f"[TTS][Chunk] {index:04d} unexpected error in attempt {current_attempt}. "
                f"error_type={type(exc).__name__}, error={exc}, "
                f"attempt_elapsed={_elapsed_ms(attempt_t0)}, thread={thread_name}"
            )

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
    func_start = time.time()
    selected_voice_id = _resolve_voice_id(voice_id)
    effective_speed = _effective_speed(speed)
    effective_volume = _effective_volume(volume)
    logger.info(
        f"[TTS] ===== Docs-to-audio START =====\n"
        f"  docUrl={doc_url!r}\n"
        f"  outputName={output_name or '<auto>'}, voiceId={selected_voice_id or '<missing>'}, "
        f"speed={effective_speed}, volume={effective_volume}"
    )
    _require_tts_config(selected_voice_id)

    doc_id = parse_google_doc_id(doc_url)
    doc_download_start = time.time()
    text = download_google_doc_text(doc_url)
    doc_download_elapsed = _elapsed_ms(doc_download_start)
    if not text.strip():
        logger.error("[TTS] Google Docs text is empty.")
        raise TTSAudioError("Google Docs text is empty.", "empty_doc_text")
    normalized_text = _normalize_text(text)
    logger.info(
        f"[TTS] Doc text downloaded. chars={len(text)}, normalized_chars={len(normalized_text)}, "
        f"doc_download_elapsed={doc_download_elapsed}"
    )

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
    # Batch size = TTS_MAX_CONCURRENCY (default 5).
    # Each batch runs in parallel; the NEXT batch starts only after the current
    # batch fully completes (all chunks done or failed). This prevents tasks
    # from piling up and being stuck indefinitely.
    batch_size = max(1, min(Config.TTS_MAX_CONCURRENCY, 15))
    manifest_lock = threading.Lock()
    first_error: TTSAudioError | None = None

    if processable_indexes:
        total_batches = (len(processable_indexes) + batch_size - 1) // batch_size
        logger.info(
            f"[TTS] Starting TTS chunk processing. totalChunks={len(chunks)}, "
            f"pendingChunks={len(processable_indexes)}, batchSize={batch_size}, "
            f"totalBatches={total_batches}, perTaskTimeout={Config.TTS_SINGLE_TASK_TIMEOUT_SECONDS}s"
        )

        for batch_num, batch_start in enumerate(range(0, len(processable_indexes), batch_size), start=1):
            if first_error is not None:
                remaining_batches = total_batches - batch_num + 1
                logger.warning(
                    f"[TTS] Skipping remaining {remaining_batches} batch(es) due to terminal chunk failure. "
                    f"error_code={first_error.code}"
                )
                break

            batch_indexes = processable_indexes[batch_start: batch_start + batch_size]
            batch_t0 = time.time()
            logger.info(
                f"[TTS] Batch {batch_num}/{total_batches} START. "
                f"chunks={[f'{i:04d}' for i in batch_indexes]}, workers={len(batch_indexes)}"
            )

            with ThreadPoolExecutor(max_workers=len(batch_indexes)) as executor:
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
                    for index in batch_indexes
                }
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        chunk_results[index] = future.result()
                        batch_done = sum(1 for i in batch_indexes if i in chunk_results)
                        logger.info(
                            f"[TTS] Chunk {index:04d} SUCCESS. "
                            f"batch={batch_num}/{total_batches}, "
                            f"batchProgress={batch_done}/{len(batch_indexes)}"
                        )
                    except CancelledError:
                        logger.info(f"[TTS] Chunk {index:04d} cancelled. batch={batch_num}")
                    except TTSAudioError as exc:
                        logger.error(
                            f"[TTS] Chunk {index:04d} FAILED (terminal). "
                            f"batch={batch_num}/{total_batches}, code={exc.code}, error={exc}"
                        )
                        if first_error is None:
                            first_error = exc
                    except Exception as exc:
                        logger.error(
                            f"[TTS] Chunk {index:04d} FAILED (unexpected). "
                            f"batch={batch_num}/{total_batches}, "
                            f"error_type={type(exc).__name__}, error={exc}"
                        )
                        if first_error is None:
                            first_error = TTSAudioError(
                                "TTS request failed.",
                                "tts_task_failed",
                                {"error": str(exc)},
                            )
                # NOTE: Do NOT break out of as_completed early — always wait for
                # all chunks in this batch to finish before moving to next batch.

            batch_elapsed = f"{(time.time() - batch_t0) * 1000:.0f}ms"
            batch_succeeded = sum(1 for i in batch_indexes if i in chunk_results)
            logger.info(
                f"[TTS] Batch {batch_num}/{total_batches} DONE. "
                f"succeeded={batch_succeeded}/{len(batch_indexes)}, "
                f"elapsed={batch_elapsed}, "
                f"hasError={'YES – stopping' if first_error else 'no'}"
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
    total_elapsed = _elapsed_ms(func_start)
    logger.info(
        f"[TTS] ===== Docs-to-audio COMPLETED =====\n"
        f"  output={output_path}, relativePath={storage_relative_path(output_path)}, "
        f"chunks={len(chunks)}, total_elapsed={total_elapsed}"
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
