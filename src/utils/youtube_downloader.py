"""Tai video YouTube ve MP4 va/hoac tach audio MP3 (trang /youtube-download).

Moi lan nguoi dung bam "Tai" tao mot job gom nhieu URL; job chay trong mot thread
nen va tai LAN LUOT tung URL (khong song song). Cac job cung chi chay mot cai mot
luc (``_run_lock``) de khong tranh bang thong / dia voi nhau.

Che do "both" chi tai MP4 mot lan roi dung ffmpeg tach MP3 tu file do, thay vi tai
hai lan tu YouTube.

Trang thai job nam trong RAM (mat khi restart server); file da tai van nam o
``Config.YOUTUBE_DOWNLOAD_DIR/<job_id>/``.
"""

import glob
import os
import subprocess
import threading
import time
import uuid

from src.config import Config
from src.utils.logger import logger

FORMATS = ("mp3", "mp4", "both")
MAX_URLS_PER_JOB = 50

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
# Cac job xep hang: chi mot job tai tai mot thoi diem.
_run_lock = threading.Lock()

_MP4_FORMAT = (
    "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo+bestaudio/"
    "best[ext=mp4]/best"
)

class JobCancelled(Exception):
    pass


def job_dir(job_id: str) -> str:
    return os.path.join(Config.YOUTUBE_DOWNLOAD_DIR, job_id)


def _public_job(job: dict) -> dict:
    """Ban sao an toan de tra ve API (bo duong dan tuyet doi cua file)."""
    items = []
    for item in job["items"]:
        public_item = {k: v for k, v in item.items() if k != "files"}
        public_item["files"] = [
            {
                "kind": f["kind"],
                "name": f["name"],
                "size": f["size"],
                "url": f"/api/youtube-download/jobs/{job['jobId']}/items/{item['index']}/{f['kind']}",
            }
            for f in item["files"]
        ]
        items.append(public_item)
    return {
        **{k: v for k, v in job.items() if k not in ("items", "cancelRequested")},
        "items": items,
        "outputDir": os.path.abspath(job_dir(job["jobId"])),
    }


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return _public_job(job) if job else None


def get_item_file(job_id: str, index: int, kind: str) -> str | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or not (0 <= index < len(job["items"])):
            return None
        for f in job["items"][index]["files"]:
            if f["kind"] == kind and os.path.isfile(f["path"]):
                return f["path"]
    return None


def cancel_job(job_id: str) -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job["status"] in ("completed", "failed", "cancelled"):
            return False
        job["cancelRequested"] = True
        return True


def _update_item(job_id: str, index: int, **updates):
    with _jobs_lock:
        _jobs[job_id]["items"][index].update(updates)


def _update_job(job_id: str, **updates):
    with _jobs_lock:
        _jobs[job_id].update(updates)


def _is_cancelled(job_id: str) -> bool:
    with _jobs_lock:
        return bool(_jobs[job_id].get("cancelRequested"))


def create_job(urls: list[str], fmt: str) -> dict:
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job = {
        "jobId": job_id,
        "format": fmt,
        "status": "queued",
        "createdAt": time.time(),
        "finishedAt": None,
        "cancelRequested": False,
        "items": [
            {
                "index": i,
                "url": url,
                "status": "pending",
                "phase": "",
                "title": None,
                "percent": 0.0,
                "speed": None,
                "eta": None,
                "error": None,
                "files": [],
            }
            for i, url in enumerate(urls)
        ],
    }
    with _jobs_lock:
        _jobs[job_id] = job
    threading.Thread(target=_run_job, args=(job_id,), daemon=True, name=f"yt-download-{job_id}").start()
    return get_job(job_id)


def _run_job(job_id: str):
    with _run_lock:
        if _is_cancelled(job_id):
            _finish_cancelled(job_id)
            return
        _update_job(job_id, status="running")
        os.makedirs(job_dir(job_id), exist_ok=True)
        with _jobs_lock:
            fmt = _jobs[job_id]["format"]
            count = len(_jobs[job_id]["items"])

        for index in range(count):
            if _is_cancelled(job_id):
                break
            try:
                _download_item(job_id, index, fmt)
            except JobCancelled:
                _update_item(job_id, index, status="cancelled", phase="")
                break
            except Exception as exc:  # noqa: BLE001 - mot URL loi khong duoc lam hong ca job
                message = _clean_error(exc)
                logger.error(f"[YouTubeDownload] {job_id} item {index} failed: {message}")
                _update_item(job_id, index, status="failed", phase="", error=message)

        if _is_cancelled(job_id):
            _finish_cancelled(job_id)
            return
        with _jobs_lock:
            statuses = [item["status"] for item in _jobs[job_id]["items"]]
        status = "completed" if any(s == "done" for s in statuses) else "failed"
        _update_job(job_id, status=status, finishedAt=time.time())


def _finish_cancelled(job_id: str):
    with _jobs_lock:
        job = _jobs[job_id]
        for item in job["items"]:
            if item["status"] in ("pending", "downloading", "converting"):
                item.update(status="cancelled", phase="")
        job.update(status="cancelled", finishedAt=time.time())


def _clean_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    # yt-dlp them ma mau ANSI va tien to "ERROR: " vao message.
    for prefix in ("ERROR: ", "\x1b[0;31mERROR:\x1b[0m "):
        if message.startswith(prefix):
            message = message[len(prefix):]
    return message[:500]


def _download_item(job_id: str, index: int, fmt: str):
    import yt_dlp

    with _jobs_lock:
        url = _jobs[job_id]["items"][index]["url"]
    out_dir = job_dir(job_id)
    _update_item(job_id, index, status="downloading", phase="Dang lay thong tin video...", percent=0.0)

    def progress_hook(data: dict):
        if _is_cancelled(job_id):
            raise JobCancelled()
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            info = data.get("info_dict") or {}
            # Video+audio tach roi tai hai luot; ghi ro luot nao de % khong "nhay lui" kho hieu.
            stream = "audio" if info.get("vcodec") == "none" else "video"
            _update_item(
                job_id,
                index,
                status="downloading",
                phase=f"Dang tai {stream}...",
                percent=round(done * 100.0 / total, 1) if total else 0.0,
                speed=data.get("speed"),
                eta=data.get("eta"),
            )
        elif data.get("status") == "finished":
            _update_item(job_id, index, percent=100.0, speed=None, eta=None)

    def postprocessor_hook(data: dict):
        if data.get("status") == "started":
            name = data.get("postprocessor") or ""
            phase = "Dang tach MP3..." if name == "ExtractAudio" else "Dang ghep video + audio..."
            _update_item(job_id, index, status="converting", phase=phase)

    options = {
        "outtmpl": os.path.join(out_dir, "%(title).120B [%(id)s].%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        # YouTube bat giai JS challenge; yt-dlp mac dinh chi bat deno, may nay co node.
        "js_runtimes": {"deno": {}, "node": {}},
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
    }
    if fmt == "mp3":
        options["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": Config.YOUTUBE_MP3_BITRATE.rstrip("kK"),
            }
        ]
    else:
        options["format"] = _MP4_FORMAT
        options["merge_output_format"] = "mp4"

    info = None
    clients = list(Config.YOUTUBE_PLAYER_CLIENTS or ["default"])
    retried_403 = False
    attempt = 0
    while attempt < len(clients):
        client = clients[attempt]
        attempt_options = dict(options)
        if client != "default":
            attempt_options["extractor_args"] = {"youtube": {"player_client": [client]}}
        try:
            with yt_dlp.YoutubeDL(attempt_options) as downloader:
                info = downloader.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as exc:
            if isinstance(exc.exc_info[1] if exc.exc_info else None, JobCancelled) or _is_cancelled(job_id):
                raise JobCancelled() from exc
            attempt += 1
            # YouTube thinh thoang tra 403 cho link tai vua cap; lay link moi (them mot vong) thuong qua.
            if attempt == len(clients) and not retried_403 and "HTTP Error 403" in str(exc):
                retried_403 = True
                clients.extend(clients)
            if attempt == len(clients):
                raise
            logger.warning(f"[YouTubeDownload] {url} client={client} failed, thu client tiep: {_clean_error(exc)}")
            _update_item(job_id, index, status="downloading", phase="Thu lai bang client khac...", percent=0.0)
    if info is None:
        raise RuntimeError("Khong lay duoc thong tin video.")
    video_id = info.get("id") or ""
    _update_item(job_id, index, title=info.get("title"))

    files: list[dict] = []
    main_kind = "mp3" if fmt == "mp3" else "mp4"
    main_path = _find_output(out_dir, video_id, main_kind)
    if not main_path:
        raise RuntimeError(f"Khong tim thay file {main_kind.upper()} sau khi tai.")
    files.append(_file_entry(main_kind, main_path))

    if fmt == "both":
        if _is_cancelled(job_id):
            raise JobCancelled()
        _update_item(job_id, index, status="converting", phase="Dang tach MP3 tu MP4...")
        mp3_path = os.path.splitext(main_path)[0] + ".mp3"
        _extract_mp3(main_path, mp3_path)
        files.append(_file_entry("mp3", mp3_path))

    _update_item(job_id, index, status="done", phase="", percent=100.0, files=files)


def _find_output(out_dir: str, video_id: str, ext: str) -> str | None:
    pattern = os.path.join(glob.escape(out_dir), f"*[[]{glob.escape(video_id)}[]].{ext}")
    matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    return matches[0] if matches else None


def _file_entry(kind: str, path: str) -> dict:
    return {"kind": kind, "path": path, "name": os.path.basename(path), "size": os.path.getsize(path)}


def _extract_mp3(src_path: str, dst_path: str):
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src_path,
        "-vn", "-c:a", "libmp3lame", "-b:a", Config.YOUTUBE_MP3_BITRATE,
        dst_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not os.path.isfile(dst_path):
        raise RuntimeError(f"ffmpeg tach MP3 that bai: {(result.stderr or '').strip()[-400:]}")
