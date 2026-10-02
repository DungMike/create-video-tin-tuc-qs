"""API cho trang /youtube-download: tai video YouTube ve MP4 va/hoac MP3."""

from urllib.parse import urlparse

from flask import Blueprint, jsonify, request, send_file

from src.utils import youtube_downloader as yt

youtube_download_bp = Blueprint("youtube_download", __name__)


def _error(message: str, code: str = "bad_request", status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


@youtube_download_bp.route("/api/youtube-download/jobs", methods=["POST"])
def create_youtube_download_job():
    payload = request.get_json(silent=True) or {}
    fmt = str(payload.get("format") or "").lower()
    if fmt not in yt.FORMATS:
        return _error("Dinh dang phai la mp3, mp4 hoac both.")

    raw_urls = payload.get("urls")
    if not isinstance(raw_urls, list):
        return _error("urls phai la danh sach.")
    urls: list[str] = []
    for raw in raw_urls:
        url = str(raw or "").strip()
        if url and url not in urls:
            urls.append(url)
    if not urls:
        return _error("Can it nhat mot URL.")
    if len(urls) > yt.MAX_URLS_PER_JOB:
        return _error(f"Toi da {yt.MAX_URLS_PER_JOB} URL moi lan.")
    invalid = [url for url in urls if not _is_http_url(url)]
    if invalid:
        return _error(f"URL khong hop le: {invalid[0]}", code="invalid_url")

    return jsonify(yt.create_job(urls, fmt)), 201


@youtube_download_bp.route("/api/youtube-download/jobs/<job_id>", methods=["GET"])
def get_youtube_download_job(job_id: str):
    job = yt.get_job(job_id)
    if not job:
        return _error("Khong tim thay job (server co the da khoi dong lai).", code="not_found", status=404)
    return jsonify(job)


@youtube_download_bp.route("/api/youtube-download/jobs/<job_id>/cancel", methods=["POST"])
def cancel_youtube_download_job(job_id: str):
    if not yt.get_job(job_id):
        return _error("Khong tim thay job.", code="not_found", status=404)
    yt.cancel_job(job_id)
    return jsonify(yt.get_job(job_id))


@youtube_download_bp.route("/api/youtube-download/jobs/<job_id>/items/<int:index>/<kind>", methods=["GET"])
def download_youtube_file(job_id: str, index: int, kind: str):
    path = yt.get_item_file(job_id, index, kind)
    if not path:
        return _error("Khong tim thay file.", code="not_found", status=404)
    return send_file(path, as_attachment=True)
