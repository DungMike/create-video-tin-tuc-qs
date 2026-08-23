"""Flask Blueprint for the standalone YouTube downloader page.

Unlike the other yt-dlp call sites in this repo (batch pipeline source videos,
TV noise overlays, story library), this one does not feed a render pipeline: it
just drops MP4 / MP3 files into a folder the user picks. Links are processed
one at a time inside a single worker thread, and the page polls the job for
per-link progress.
"""

import os
import threading
import time
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

youtube_download_bp = Blueprint("youtube_download", __name__)

# In-memory job registry, same shape as the story-video download sessions.
_yt_jobs: dict[str, dict] = {}
_yt_jobs_lock = threading.Lock()

_MEDIA_EXTENSIONS = {".mp4", ".mp3"}

# Shared with the batch pipeline downloader: prefer H.264/mp4 so the files play
# everywhere without a re-encode.
_VIDEO_FORMAT = (
    "bestvideo[vcodec^=avc1][ext=mp4][height>=720]+bestaudio[ext=m4a]/"
    "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[ext=mp4]+bestaudio/"
    "best[ext=mp4]/best"
)

_BASE_YDL_OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "no_color": True,
    # Progress reaches the UI through progress_hooks; the console bar would only
    # spam the server log (quiet alone does not disable it).
    "noprogress": True,
    "noplaylist": True,
    "retries": 5,
    "fragment_retries": 5,
    "socket_timeout": 30,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    },
}

_MAX_RETRIES = 3


class _JobCancelled(Exception):
    """Raised from the yt-dlp progress hook to abort the current download."""


def _error(message: str, code: str = "bad_request", status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _resolve_output_dir(raw) -> str:
    """Validate a user-supplied output folder and make sure it exists.

    Blank falls back to the configured default. Anything else must be an
    absolute path so a stray relative value cannot land next to the app code.
    """
    value = str(raw or "").strip().strip('"')
    if not value:
        value = Config.YOUTUBE_DOWNLOAD_DIR
    target = os.path.normpath(os.path.abspath(value))
    if os.path.isfile(target):
        raise ValueError("Đường dẫn đích là một file, không phải thư mục.")
    if value != Config.YOUTUBE_DOWNLOAD_DIR and not os.path.isabs(os.path.normpath(value)):
        raise ValueError("Thư mục lưu phải là đường dẫn tuyệt đối (vd D:\\Downloads\\YouTube).")
    os.makedirs(target, exist_ok=True)
    return target


def _parse_links(raw) -> list[str]:
    """Split the textarea into unique, non-empty links (order preserved)."""
    if isinstance(raw, list):
        candidates = [str(item) for item in raw]
    else:
        candidates = str(raw or "").splitlines()

    seen: set[str] = set()
    links: list[str] = []
    for candidate in candidates:
        value = candidate.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        links.append(value)
    return links


def _get_job(session_id: str) -> dict | None:
    with _yt_jobs_lock:
        job = _yt_jobs.get(session_id)
        return dict(job) if job else None


def _update_job(session_id: str, **updates):
    with _yt_jobs_lock:
        if session_id in _yt_jobs:
            _yt_jobs[session_id].update(updates)


def _update_item(session_id: str, index: int, **updates):
    with _yt_jobs_lock:
        job = _yt_jobs.get(session_id)
        if not job:
            return
        items = job.get("items") or []
        if 0 <= index < len(items):
            items[index].update(updates)


def _is_cancelled(session_id: str) -> bool:
    with _yt_jobs_lock:
        job = _yt_jobs.get(session_id)
        return bool(job and job.get("cancelRequested"))


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _downloaded_path(info: dict, downloader) -> str:
    """Actual file yt-dlp wrote, straight from the info dict.

    Reading it back beats diffing os.listdir() before/after (what the older call
    sites do) because two jobs can share one output folder here.
    """
    requested = info.get("requested_downloads") or []
    for entry in requested:
        path = entry.get("filepath") or entry.get("_filename")
        if path and os.path.isfile(path):
            return path
    fallback = downloader.prepare_filename(info)
    return fallback if fallback and os.path.isfile(fallback) else ""


def _progress_hook_factory(session_id: str, index: int):
    def _hook(status: dict):
        if _is_cancelled(session_id):
            raise _JobCancelled()
        if status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        downloaded = status.get("downloaded_bytes") or 0
        percent = round(downloaded * 100.0 / total, 1) if total else 0.0
        _update_item(
            session_id,
            index,
            percent=min(percent, 100.0),
            speed=(status.get("_speed_str") or "").strip(),
            eta=(status.get("_eta_str") or "").strip(),
        )

    return _hook


def _run_with_retries(session_id: str, index: int, action):
    """Run one yt-dlp attempt up to _MAX_RETRIES times; cancellation is final."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        if _is_cancelled(session_id):
            raise _JobCancelled()
        try:
            return action()
        except _JobCancelled:
            raise
        except Exception as exc:
            # yt-dlp wraps hook exceptions in DownloadError, so _JobCancelled can
            # arrive here disguised as an ordinary failure — re-check the flag.
            if _is_cancelled(session_id):
                raise _JobCancelled()
            last_exc = exc
            logger.warning(f"[YoutubeDownload] Attempt {attempt}/{_MAX_RETRIES} failed: {exc}")
            if attempt < _MAX_RETRIES:
                time.sleep(2)
    raise last_exc if last_exc else RuntimeError("Tải thất bại.")


def _download_video(session_id: str, index: int, link: str, output_dir: str) -> tuple[str, str]:
    """Download the MP4 for one link. Returns (file_path, title)."""
    import yt_dlp

    options = dict(_BASE_YDL_OPTIONS)
    options.update({
        "format": _VIDEO_FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(output_dir, "%(title).150B [%(id)s].%(ext)s"),
        "progress_hooks": [_progress_hook_factory(session_id, index)],
    })

    def _attempt():
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(link, download=True)
            return _downloaded_path(info, downloader), str(info.get("title") or "")

    return _run_with_retries(session_id, index, _attempt)


def _download_audio(session_id: str, index: int, link: str, output_dir: str) -> tuple[str, str]:
    """Download audio only and convert it to MP3. Returns (file_path, title)."""
    import yt_dlp

    options = dict(_BASE_YDL_OPTIONS)
    options.update({
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "%(title).150B [%(id)s].%(ext)s"),
        "progress_hooks": [_progress_hook_factory(session_id, index)],
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    })

    def _attempt():
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(link, download=True)
            path = _downloaded_path(info, downloader)
            title = str(info.get("title") or "")
            # The post-processor rewrites the extension; the info dict may still
            # point at the pre-conversion container.
            if path and not path.lower().endswith(".mp3"):
                converted = f"{os.path.splitext(path)[0]}.mp3"
                if os.path.isfile(converted):
                    path = converted
            return path, title

    return _run_with_retries(session_id, index, _attempt)


def _extract_mp3_from_video(video_path: str, output_dir: str) -> str:
    """Strip the audio track of an already-downloaded MP4 into a sibling MP3."""
    mp3_path = os.path.join(output_dir, f"{os.path.splitext(os.path.basename(video_path))[0]}.mp3")
    ok = FFmpegHelper.run_command([
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-b:a", "192k",
        "-ar", "44100",
        mp3_path,
    ])
    if not ok or not os.path.isfile(mp3_path):
        raise RuntimeError("Không tách được MP3 từ file video.")
    return mp3_path


def _run_job(session_id: str):
    job = _get_job(session_id)
    if not job:
        return

    output_dir = job["outputDir"]
    want_video = bool(job["downloadVideo"])
    want_audio = bool(job["extractAudio"])
    items = job.get("items") or []
    total = len(items)
    failed = 0

    for index, item in enumerate(items):
        if _is_cancelled(session_id):
            break

        link = item["url"]
        _update_job(
            session_id,
            current=index + 1,
            message=f"Đang tải {index + 1}/{total}: {link[:80]}",
        )
        _update_item(session_id, index, status="downloading", percent=0.0)

        try:
            video_path = ""
            audio_path = ""
            title = ""

            if want_video:
                video_path, title = _download_video(session_id, index, link, output_dir)
                if not video_path:
                    raise RuntimeError("Không tìm thấy file MP4 sau khi tải.")
                _update_item(
                    session_id,
                    index,
                    title=title,
                    videoFile=os.path.basename(video_path),
                    percent=100.0,
                )

            if want_audio:
                if video_path:
                    _update_item(session_id, index, status="extracting")
                    _update_job(session_id, message=f"Đang tách MP3 {index + 1}/{total}...")
                    audio_path = _extract_mp3_from_video(video_path, output_dir)
                else:
                    audio_path, title = _download_audio(session_id, index, link, output_dir)
                    if not audio_path:
                        raise RuntimeError("Không tìm thấy file MP3 sau khi tải.")
                _update_item(session_id, index, title=title, audioFile=os.path.basename(audio_path))

            _update_item(
                session_id,
                index,
                status="completed",
                percent=100.0,
                sizeBytes=_file_size(video_path) + _file_size(audio_path),
                error=None,
            )
        except _JobCancelled:
            _update_item(session_id, index, status="cancelled", error=None)
            break
        except Exception as exc:
            if _is_cancelled(session_id):
                _update_item(session_id, index, status="cancelled", error=None)
                break
            failed += 1
            logger.error(f"[YoutubeDownload] {link} failed: {exc}")
            _update_item(session_id, index, status="failed", error=str(exc))

    if _is_cancelled(session_id):
        with _yt_jobs_lock:
            current = _yt_jobs.get(session_id)
            if current:
                for entry in current.get("items", []):
                    if entry["status"] in {"pending", "downloading", "extracting"}:
                        entry["status"] = "cancelled"
        _update_job(session_id, status="cancelled", message="Đã huỷ tải.")
        return

    done = sum(1 for entry in (_get_job(session_id) or {}).get("items", []) if entry["status"] == "completed")
    if failed and not done:
        _update_job(session_id, status="failed", message=f"Tất cả {failed} link đều lỗi.")
    else:
        suffix = f" ({failed} link lỗi)" if failed else ""
        _update_job(session_id, status="completed", current=total, message=f"Hoàn tất {done}/{total} link{suffix}.")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@youtube_download_bp.route("/api/youtube-download/config", methods=["GET"])
def youtube_download_config():
    return jsonify({"defaultOutputDir": os.path.normpath(os.path.abspath(Config.YOUTUBE_DOWNLOAD_DIR))})


@youtube_download_bp.route("/api/youtube-download/jobs", methods=["POST"])
def create_youtube_download_job():
    data = request.get_json(silent=True) or {}
    links = _parse_links(data.get("links"))
    download_video = bool(data.get("downloadVideo", True))
    extract_audio = bool(data.get("extractAudio", False))

    if not links:
        return _error("Nhập ít nhất 1 link YouTube.", code="no_links")
    if not download_video and not extract_audio:
        return _error("Chọn ít nhất một định dạng: MP4 hoặc MP3.", code="no_format_selected")

    try:
        output_dir = _resolve_output_dir(data.get("outputDir"))
    except (ValueError, OSError) as exc:
        return _error(str(exc), code="invalid_output_dir")

    session_id = f"ytdl-{str(uuid.uuid4())[:8]}"
    job = {
        "sessionId": session_id,
        "status": "downloading",
        "outputDir": output_dir,
        "downloadVideo": download_video,
        "extractAudio": extract_audio,
        "current": 0,
        "total": len(links),
        "message": "Bắt đầu tải...",
        "cancelRequested": False,
        "items": [
            {
                "id": f"{session_id}-{index}",
                "url": link,
                "title": "",
                "status": "pending",
                "percent": 0.0,
                "speed": "",
                "eta": "",
                "videoFile": "",
                "audioFile": "",
                "sizeBytes": 0,
                "error": None,
            }
            for index, link in enumerate(links)
        ],
    }
    with _yt_jobs_lock:
        _yt_jobs[session_id] = job

    threading.Thread(target=_run_job, args=(session_id,), daemon=True).start()
    return jsonify({"sessionId": session_id}), 202


@youtube_download_bp.route("/api/youtube-download/jobs/<session_id>", methods=["GET"])
def get_youtube_download_job(session_id: str):
    job = _get_job(session_id)
    if not job:
        return _error("Không tìm thấy phiên tải.", code="session_not_found", status=404)
    return jsonify(job)


@youtube_download_bp.route("/api/youtube-download/jobs/<session_id>/cancel", methods=["POST"])
def cancel_youtube_download_job(session_id: str):
    with _yt_jobs_lock:
        job = _yt_jobs.get(session_id)
        if not job:
            return _error("Không tìm thấy phiên tải.", code="session_not_found", status=404)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return jsonify(dict(job))
        job["cancelRequested"] = True
        job["message"] = "Đang huỷ..."
        snapshot = dict(job)
    return jsonify(snapshot)


@youtube_download_bp.route("/api/youtube-download/files", methods=["GET"])
def list_youtube_download_files():
    try:
        output_dir = _resolve_output_dir(request.args.get("dir"))
    except (ValueError, OSError) as exc:
        return _error(str(exc), code="invalid_output_dir")

    files = []
    try:
        entries = os.listdir(output_dir)
    except OSError as exc:
        return _error(f"Không đọc được thư mục: {exc}", code="output_dir_unreadable")

    for name in entries:
        path = os.path.join(output_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if ext not in _MEDIA_EXTENSIONS or not os.path.isfile(path):
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        files.append({
            "name": name,
            "kind": ext.lstrip("."),
            "sizeBytes": stat.st_size,
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })

    files.sort(key=lambda entry: entry["modifiedAt"], reverse=True)
    return jsonify({"outputDir": output_dir, "files": files})


@youtube_download_bp.route("/api/youtube-download/files", methods=["DELETE"])
def delete_youtube_download_file():
    data = request.get_json(silent=True) or {}
    try:
        output_dir = _resolve_output_dir(data.get("outputDir"))
    except (ValueError, OSError) as exc:
        return _error(str(exc), code="invalid_output_dir")

    raw_name = str(data.get("name") or "").strip()
    name = os.path.basename(raw_name)
    if not name or name != raw_name:
        return _error("Tên file không hợp lệ.", code="invalid_file_name")
    if os.path.splitext(name)[1].lower() not in _MEDIA_EXTENSIONS:
        return _error("Chỉ xoá được file .mp4 hoặc .mp3.", code="invalid_file_type")

    path = os.path.join(output_dir, name)
    if not os.path.isfile(path):
        return _error("Không tìm thấy file.", code="file_not_found", status=404)

    try:
        os.remove(path)
    except OSError as exc:
        return _error(f"Không xoá được file: {exc}", code="delete_failed", status=409)

    return jsonify({"deleted": name, "outputDir": output_dir})
