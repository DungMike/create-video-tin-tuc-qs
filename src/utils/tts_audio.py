import base64
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
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


def next_default_audio_name() -> str:
    prefix = datetime.now().strftime("%d-%m")
    pattern = re.compile(rf"^{re.escape(prefix)}-audio-(\d+)\.mp3$", re.I)
    max_index = 0
    for path in Path(generated_audio_dir()).glob(f"{prefix}-audio-*.mp3"):
        match = pattern.match(path.name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"{prefix}-audio-{max_index + 1}"


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
    normalized = re.sub(r"\s+", " ", (text or "").strip())
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
    try:
        response = requests.post(url, headers=_tts_headers(), json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        response_text = getattr(locals().get("response", None), "text", "")
        logger.error(f"[TTS] Create TTS task failed. error={exc}, response={response_text[:1000]}")
        raise TTSAudioError(
            "Create TTS task failed.",
            "tts_task_failed",
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
    logger.info(f"[TTS] Polling TTS task. taskId={task_id}")
    while time.time() < deadline:
        poll_count += 1
        url = _api_url(f"/api/minimax/{task_id}")
        try:
            response = requests.get(url, headers={"T-API-KEY": Config.TTS_API_KEY}, timeout=60)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            response_text = getattr(locals().get("response", None), "text", "")
            logger.error(f"[TTS] Poll TTS task failed. taskId={task_id}, error={exc}, response={response_text[:1000]}")
            raise
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
    try:
        response = requests.get(audio_url, timeout=120)
        response.raise_for_status()
    except Exception as exc:
        response_text = getattr(locals().get("response", None), "text", "")
        logger.error(f"[TTS] Download chunk audio failed. error={exc}, response={response_text[:1000]}")
        raise
    with open(output_path, "wb") as file_obj:
        file_obj.write(response.content)
    logger.info(f"[TTS] Chunk audio downloaded. output={output_path}, bytes={len(response.content)}")


def _synthesize_chunk(index: int, text: str, voice_id: str, speed: float, volume: float, output_dir: str) -> str:
    logger.info(f"[TTS] Chunk {index:04d} started. chars={len(text)}")
    try:
        task_id = _create_tts_task(text, voice_id, speed, volume)
        logger.info(f"[TTS] Chunk {index:04d} task created. taskId={task_id}")
        audio_url = _poll_tts_task(task_id)
        output_path = os.path.join(output_dir, f"{index:04d}.mp3")
        _download_audio(audio_url, output_path)
        logger.info(f"[TTS] Chunk {index:04d} completed. output={output_path}")
        return output_path
    except Exception as exc:
        logger.exception(f"[TTS] Chunk {index:04d} failed. chars={len(text)}, error={exc}")
        raise


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
) -> dict:
    selected_voice_id = _resolve_voice_id(voice_id)
    logger.info(
        f"[TTS] Docs-to-audio started. outputName={output_name or '<auto>'}, "
        f"voiceId={selected_voice_id or '<missing>'}, speed={speed if speed is not None else Config.TTS_SPEED}, "
        f"volume={volume if volume is not None else Config.TTS_VOLUME}"
    )
    _require_tts_config(selected_voice_id)

    text = download_google_doc_text(doc_url)
    if not text.strip():
        logger.error("[TTS] Google Docs text is empty.")
        raise TTSAudioError("Google Docs text is empty.", "empty_doc_text")

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
    for old_chunk in Path(chunk_dir).glob("*.mp3"):
        logger.info(f"[TTS] Removing stale chunk file: {old_chunk}")
        old_chunk.unlink()

    chunk_results: dict[int, str] = {}
    concurrency = max(1, min(Config.TTS_MAX_CONCURRENCY, 15, len(chunks)))
    logger.info(f"[TTS] Starting TTS chunk processing. totalChunks={len(chunks)}, concurrency={concurrency}")
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(
                    _synthesize_chunk,
                    index,
                    chunk,
                    selected_voice_id,
                    speed if speed is not None else Config.TTS_SPEED,
                    volume if volume is not None else Config.TTS_VOLUME,
                    chunk_dir,
                ): index
                for index, chunk in enumerate(chunks, start=1)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    chunk_results[index] = future.result()
                    logger.info(
                        f"[TTS] Chunk future success. chunk={index:04d}, "
                        f"completed={len(chunk_results)}/{len(chunks)}"
                    )
                except Exception as exc:
                    logger.exception(f"[TTS] Chunk future failed. chunk={index:04d}, error={exc}")
                    raise
    except TTSAudioError:
        logger.error("[TTS] Docs-to-audio stopped because a TTS chunk failed with TTSAudioError.")
        raise
    except Exception as exc:
        logger.exception(f"[TTS] Docs-to-audio stopped because a TTS request failed: {exc}")
        raise TTSAudioError("TTS request failed.", "tts_task_failed", {"error": str(exc)}) from exc

    ordered_chunks = [chunk_results[index] for index in sorted(chunk_results)]
    output_path = os.path.join(generated_audio_dir(), f"{audio_id}.mp3")
    logger.info(f"[TTS] All chunks completed. orderedChunks={len(ordered_chunks)}, finalOutput={output_path}")
    if not _concat_audio_chunks(ordered_chunks, output_path):
        raise TTSAudioError("Cannot concat TTS audio chunks.", "audio_concat_failed")
    if not validate_audio(output_path):
        logger.error(f"[TTS] Final generated audio failed validation. output={output_path}")
        raise TTSAudioError("Generated audio is invalid.", "audio_concat_failed")

    audio_item = next((item for item in list_generated_audio() if item["relativePath"] == storage_relative_path(output_path)), None)
    logger.info(
        f"[TTS] Docs-to-audio completed successfully. output={output_path}, "
        f"relativePath={storage_relative_path(output_path)}, chunks={len(chunks)}"
    )
    return {
        "audio": audio_item,
        "chunkCount": len(chunks),
        "textRelativePath": storage_relative_path(text_path),
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
