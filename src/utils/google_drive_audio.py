"""Import public Google Drive folder audio files into Story Video staging."""

import json
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from werkzeug.utils import secure_filename

from src.config import Config
from src.processors.audio_utils import get_audio_duration

_SESSION_ID_PATTERN = re.compile(r"gda-[0-9a-f]{8}")
_TOKEN_PATTERN = re.compile(r"(gda-[0-9a-f]{8})\.([A-Za-z0-9_-]{16,})")
_DRIVE_FILE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_FOLDER_PATH_PATTERN = re.compile(r"^/drive/folders/([^/?#]+)")
_MANIFEST_FILENAME = "manifest.json"
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
}


class DriveAudioImportError(RuntimeError):
    """Expected Drive folder import failure with an API-safe error code."""

    def __init__(self, message: str, code: str = "drive_audio_import_failed"):
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _imports_root() -> str:
    return os.path.abspath(Config.STORY_DRIVE_AUDIO_IMPORT_DIR)


def _session_dir(session_id: str) -> str:
    if not _SESSION_ID_PATTERN.fullmatch(str(session_id or "")):
        raise DriveAudioImportError("Drive audio import session khong hop le.", "invalid_drive_audio_session")
    return os.path.join(_imports_root(), session_id)


def _manifest_path(session_id: str) -> str:
    return os.path.join(_session_dir(session_id), _MANIFEST_FILENAME)


def _save_manifest(session_id: str, manifest: dict) -> dict:
    session_dir = _session_dir(session_id)
    os.makedirs(session_dir, exist_ok=True)
    manifest["updatedAt"] = _utc_now()
    manifest_path = _manifest_path(session_id)
    temp_path = f"{manifest_path}.{secrets.token_hex(4)}.tmp"
    with open(temp_path, "w", encoding="utf-8") as file_obj:
        json.dump(manifest, file_obj, ensure_ascii=False, indent=2)
    os.replace(temp_path, manifest_path)
    return manifest


def load_drive_audio_import(session_id: str) -> dict | None:
    """Load a staging manifest, returning None when the session does not exist."""
    try:
        manifest_path = _manifest_path(session_id)
    except DriveAudioImportError:
        return None
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def public_drive_audio_import(manifest: dict) -> dict:
    """Remove internal staging filenames before returning a manifest to clients."""
    result = dict(manifest)
    result["items"] = [
        {key: value for key, value in item.items() if key != "storedFileName"}
        for item in manifest.get("items", [])
        if isinstance(item, dict)
    ]
    return result


def create_drive_audio_import_session(folder_url: str, session_id: str | None = None) -> dict:
    """Create a manifest before the asynchronous Drive import starts."""
    normalized_url = str(folder_url or "").strip()
    if not parse_google_drive_folder_id(normalized_url):
        raise DriveAudioImportError(
            "Link Google Drive folder khong hop le.",
            "invalid_drive_folder_url",
        )
    session_id = session_id or f"gda-{secrets.token_hex(4)}"
    session_dir = _session_dir(session_id)
    os.makedirs(session_dir, exist_ok=False)
    return _save_manifest(session_id, {
        "sessionId": session_id,
        "folderUrl": normalized_url,
        "status": "listing",
        "current": 0,
        "total": 0,
        "message": "Dang doc danh sach file trong Google Drive folder...",
        "items": [],
        "skipped": [],
        "error": None,
        "createdAt": _utc_now(),
    })


def fail_drive_audio_import(session_id: str, error: Exception | str) -> dict:
    """Persist a failed import state so polling clients receive the reason."""
    manifest = load_drive_audio_import(session_id) or {
        "sessionId": session_id,
        "items": [],
        "skipped": [],
        "createdAt": _utc_now(),
    }
    message = str(error)
    manifest.update({
        "status": "failed",
        "message": message,
        "error": message,
    })
    return _save_manifest(session_id, manifest)


def cleanup_expired_drive_audio_imports(
    *,
    now: float | None = None,
    ttl_seconds: int | None = None,
) -> list[str]:
    """Remove staging sessions older than the configured retention period."""
    imports_root = _imports_root()
    if not os.path.isdir(imports_root):
        return []
    expiry_seconds = Config.STORY_DRIVE_AUDIO_IMPORT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    current_time = time.time() if now is None else now
    removed: list[str] = []
    for entry in os.scandir(imports_root):
        if not entry.is_dir() or not _SESSION_ID_PATTERN.fullmatch(entry.name):
            continue
        manifest_path = os.path.join(entry.path, _MANIFEST_FILENAME)
        latest_mtime = entry.stat().st_mtime
        if os.path.isfile(manifest_path):
            latest_mtime = max(latest_mtime, os.path.getmtime(manifest_path))
        if current_time - latest_mtime <= expiry_seconds:
            continue
        shutil.rmtree(entry.path, ignore_errors=True)
        removed.append(entry.name)
    return removed


def parse_google_drive_folder_id(folder_url: str) -> str | None:
    """Return the folder id for supported public Google Drive folder URLs."""
    value = str(folder_url or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "drive.google.com":
        return None
    match = _FOLDER_PATH_PATTERN.match(parsed.path)
    if not match:
        return None
    folder_id = match.group(1)
    return folder_id if _DRIVE_FILE_ID_PATTERN.fullmatch(folder_id) else None


def _parse_folder_entries(html: str) -> list[dict]:
    """Extract direct child entries rendered in a public Drive folder page."""
    soup = BeautifulSoup(html, "html.parser")
    entries_by_id: dict[str, dict] = {}
    for node in soup.select("[data-id]"):
        file_id = str(node.get("data-id") or "").strip()
        if not file_id or not _DRIVE_FILE_ID_PATTERN.fullmatch(file_id):
            continue
        strong = node.select_one("strong")
        file_name = strong.get_text(strip=True) if strong else ""
        tooltip = str(node.get("data-tooltip") or "").strip()
        labelled_parent = node.find_parent(attrs={"aria-label": True})
        aria_label = str(labelled_parent.get("aria-label") or "").strip() if labelled_parent else ""
        type_hint = " ".join(part for part in (tooltip, aria_label) if part).strip()
        if not file_name:
            continue
        existing = entries_by_id.get(file_id)
        if not existing:
            entries_by_id[file_id] = {
                "fileId": file_id,
                "fileName": file_name,
                "typeHint": type_hint,
            }
        elif type_hint and not existing.get("typeHint"):
            existing["typeHint"] = type_hint
    return list(entries_by_id.values())


def _select_audio_entries(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split listed Drive entries into supported audio files and skipped records."""
    audio_entries: list[dict] = []
    skipped: list[dict] = []
    for entry in entries:
        file_name = str(entry.get("fileName") or "").strip()
        suffix = Path(file_name).suffix.lower().lstrip(".")
        type_hint = str(entry.get("typeHint") or "").lower()
        if "folder" in type_hint or "thu muc" in type_hint:
            skipped.append({"fileName": file_name or "(folder)", "reason": "Bo qua thu muc con."})
        elif suffix not in Config.ALLOWED_AUDIO_EXTENSIONS:
            skipped.append({"fileName": file_name or "(unknown)", "reason": "Dinh dang khong phai audio ho tro."})
        else:
            audio_entries.append(entry)
    return audio_entries, skipped


def _download_drive_file(
    http_session: requests.Session,
    file_id: str,
    output_path: str,
    remaining_bytes: int,
) -> int:
    url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
    temp_path = f"{output_path}.part"
    try:
        with http_session.get(url, headers=_REQUEST_HEADERS, stream=True, timeout=60) as response:
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").lower()
            if "text/html" in content_type:
                raise DriveAudioImportError("Google Drive khong tra ve file audio.", "drive_audio_download_failed")
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > remaining_bytes:
                raise DriveAudioImportError(
                    "Tong dung luong audio trong folder vuot qua gioi han.",
                    "drive_audio_total_size_exceeded",
                )
            downloaded_bytes = 0
            with open(temp_path, "wb") as file_obj:
                for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                    if not chunk:
                        continue
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > remaining_bytes:
                        raise DriveAudioImportError(
                            "Tong dung luong audio trong folder vuot qua gioi han.",
                            "drive_audio_total_size_exceeded",
                        )
                    file_obj.write(chunk)
        os.replace(temp_path, output_path)
        return downloaded_bytes
    finally:
        if os.path.isfile(temp_path):
            os.remove(temp_path)


def run_drive_audio_import(
    session_id: str,
    folder_url: str,
    *,
    http_session: requests.Session | None = None,
) -> dict:
    """Download supported direct-child audio files and complete the staging manifest."""
    manifest = load_drive_audio_import(session_id)
    if not manifest:
        raise DriveAudioImportError("Drive audio import session khong ton tai.", "drive_audio_session_not_found")

    session = http_session or requests.Session()
    try:
        manifest.update({
            "status": "listing",
            "current": 0,
            "total": 0,
            "message": "Dang doc danh sach file trong Google Drive folder...",
        })
        _save_manifest(session_id, manifest)

        folder_id = parse_google_drive_folder_id(folder_url)
        if not folder_id:
            raise DriveAudioImportError("Link Google Drive folder khong hop le.", "invalid_drive_folder_url")
        response = session.get(folder_url, headers=_REQUEST_HEADERS, timeout=60)
        response.raise_for_status()
        entries = _parse_folder_entries(response.text)
        audio_entries, skipped = _select_audio_entries(entries)
        if len(audio_entries) > Config.STORY_DRIVE_AUDIO_MAX_FILES:
            raise DriveAudioImportError(
                f"Folder co qua {Config.STORY_DRIVE_AUDIO_MAX_FILES} file audio.",
                "drive_audio_file_limit_exceeded",
            )

        manifest.update({
            "status": "downloading",
            "current": 0,
            "total": len(audio_entries),
            "message": f"Dang tai {len(audio_entries)} file audio...",
            "items": [],
            "skipped": skipped,
        })
        _save_manifest(session_id, manifest)

        max_total_bytes = Config.STORY_DRIVE_AUDIO_MAX_TOTAL_MB * 1024 * 1024
        downloaded_total = 0
        session_dir = _session_dir(session_id)
        for index, entry in enumerate(audio_entries):
            original_name = str(entry["fileName"])
            safe_name = secure_filename(original_name) or f"audio_{index + 1}.mp3"
            stored_filename = f"{index:04d}_{safe_name}"
            output_path = os.path.join(session_dir, stored_filename)
            try:
                file_size = _download_drive_file(
                    session,
                    str(entry["fileId"]),
                    output_path,
                    max_total_bytes - downloaded_total,
                )
                duration = get_audio_duration(output_path)
                if duration <= 0:
                    raise DriveAudioImportError("File audio khong hop le hoac khong doc duoc.", "invalid_audio")
                downloaded_total += file_size
                manifest["items"].append({
                    "token": f"{session_id}.{secrets.token_urlsafe(24)}",
                    "fileName": original_name,
                    "outputName": Path(original_name).stem or f"story-{index + 1}",
                    "durationSeconds": round(duration, 3),
                    "sizeBytes": file_size,
                    "storedFileName": stored_filename,
                })
            except DriveAudioImportError as exc:
                if exc.code == "drive_audio_total_size_exceeded":
                    raise
                if os.path.isfile(output_path):
                    os.remove(output_path)
                manifest["skipped"].append({"fileName": original_name, "reason": str(exc)})
            except Exception as exc:
                if os.path.isfile(output_path):
                    os.remove(output_path)
                manifest["skipped"].append({"fileName": original_name, "reason": str(exc)})
            manifest.update({
                "current": index + 1,
                "message": f"Da xu ly {index + 1}/{len(audio_entries)} file audio.",
            })
            _save_manifest(session_id, manifest)

        if not manifest["items"]:
            raise DriveAudioImportError(
                "Khong tim thay file audio hop le trong Google Drive folder.",
                "no_valid_drive_audio",
            )
        manifest.update({
            "status": "completed",
            "message": f"Da tai {len(manifest['items'])} file audio.",
            "error": None,
        })
        return _save_manifest(session_id, manifest)
    except Exception as exc:
        fail_drive_audio_import(session_id, exc)
        raise


def resolve_staged_audio_token(token: str) -> dict:
    """Resolve an opaque staging token without accepting client-provided paths."""
    match = _TOKEN_PATTERN.fullmatch(str(token or ""))
    if not match:
        raise DriveAudioImportError("Drive audio token khong hop le.", "invalid_drive_audio_token")
    session_id = match.group(1)
    manifest = load_drive_audio_import(session_id)
    if not manifest or manifest.get("status") != "completed":
        raise DriveAudioImportError("Drive audio import chua san sang.", "drive_audio_import_not_ready")
    record = next(
        (item for item in manifest.get("items", []) if isinstance(item, dict) and item.get("token") == token),
        None,
    )
    if not record:
        raise DriveAudioImportError("Drive audio token khong ton tai.", "drive_audio_token_not_found")
    stored_filename = str(record.get("storedFileName") or "")
    if not stored_filename or os.path.basename(stored_filename) != stored_filename:
        raise DriveAudioImportError("Drive audio staging path khong hop le.", "invalid_drive_audio_token")
    session_dir = os.path.abspath(_session_dir(session_id))
    source_path = os.path.abspath(os.path.join(session_dir, stored_filename))
    if os.path.commonpath([session_dir, source_path]) != session_dir or not os.path.isfile(source_path):
        raise DriveAudioImportError("Drive audio staging file khong ton tai.", "drive_audio_file_not_found")
    return {**record, "sourcePath": source_path}


def copy_staged_audio_to_batch(token: str, batch_dir: str, index: int) -> tuple[str, str]:
    """Copy a staged Drive audio file into a durable story batch directory."""
    record = resolve_staged_audio_token(token)
    original_name = str(record.get("fileName") or f"audio_{index + 1}.mp3")
    safe_name = secure_filename(original_name) or f"audio_{index + 1}.mp3"
    destination = os.path.join(batch_dir, f"drive_audio_{index}_{safe_name}")
    shutil.copy2(record["sourcePath"], destination)
    return destination, original_name
