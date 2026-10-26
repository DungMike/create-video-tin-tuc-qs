"""API /api/story-video/auto-source/* -- tu dong tim nguon video + kiem tra kho.

Xem src/utils/story_auto_source.py. Loi cung dang voi story_video_routes:
{"error": {"code", "message"}}.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from src.utils import story_auto_source as auto
from src.utils.logger import logger

auto_source_bp = Blueprint("auto_source", __name__)


def _error(message: str, code: str = "bad_request", status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _mongo_or_503():
    from src.db import mongo

    if not mongo.is_configured() or not mongo.is_available():
        return _error(mongo.unavailable_message(), code="mongo_unavailable", status=503)
    return None


def _auto_error(exc: auto.AutoSourceError):
    status = 409 if isinstance(exc, auto.AutoSourceBusy) else 503 if exc.code == "mongo_unavailable" else 400
    return _error(str(exc), code=exc.code, status=status)


@auto_source_bp.route("/api/story-video/auto-source", methods=["GET"])
def get_auto_source():
    settings = auto.load_settings()
    return jsonify({
        "settings": settings,
        "running": auto.is_running(),
        "currentRun": auto.current_run(),
        "runs": auto.list_runs(int(request.args.get("limit") or 15)),
        "state": auto.load_state(),
        "nextRunAt": auto.next_run_at(settings),
    })


@auto_source_bp.route("/api/story-video/auto-source/settings", methods=["PUT"])
def put_auto_source_settings():
    data = request.get_json(silent=True)
    target = str((data or {}).get("targetLibraryId") or "").strip() if isinstance(data, dict) else ""
    if target:
        from src.utils.story_library import get_library

        if not get_library(target):
            return _error("Khong tim thay thu vien dich.", code="library_not_found", status=404)
    try:
        settings = auto.save_settings(data)
    except ValueError as exc:
        return _error(str(exc), code="invalid_settings")
    return jsonify({"settings": settings, "nextRunAt": auto.next_run_at(settings)})


@auto_source_bp.route("/api/story-video/auto-source/report", methods=["GET"])
def get_auto_source_report():
    err = _mongo_or_503()
    if err is not None:
        return err
    refresh = str(request.args.get("refresh") or "").lower() in {"1", "true", "yes"}
    try:
        report = auto.stock_report(refresh=refresh)
        cap = report["maxClipUses"]
        report["cleanup"] = auto.plan_cleanup(cap) if cap > 0 else None
    except auto.AutoSourceError as exc:
        return _auto_error(exc)
    except Exception as exc:
        logger.warning(f"[AutoSource] report failed: {exc}", exc_info=True)
        return _error(f"Khong lap duoc bao cao kho: {exc}", code="report_failed", status=503)
    return jsonify(report)


@auto_source_bp.route("/api/story-video/auto-source/keywords", methods=["GET"])
def get_auto_source_keywords():
    err = _mongo_or_503()
    if err is not None:
        return err
    try:
        limit = max(1, min(200, int(request.args.get("limit") or 0))) if request.args.get("limit") else None
    except ValueError:
        return _error("limit khong hop le.", code="invalid_limit")
    try:
        return jsonify(auto.plan_keywords(auto.load_settings(), limit))
    except Exception as exc:
        logger.warning(f"[AutoSource] keyword plan failed: {exc}", exc_info=True)
        return _error(f"Khong lap duoc danh sach tu khoa: {exc}", code="keyword_plan_failed", status=503)


@auto_source_bp.route("/api/story-video/auto-source/cursors", methods=["GET"])
def get_auto_source_cursors():
    from src.db import video_cursor_repo

    err = _mongo_or_503()
    if err is not None:
        return err
    provider = str(request.args.get("provider") or "").strip().lower() or None
    return jsonify({"items": video_cursor_repo.list_cursors(provider)})


@auto_source_bp.route("/api/story-video/auto-source/run", methods=["POST"])
def run_auto_source():
    err = _mongo_or_503()
    if err is not None:
        return err
    try:
        run = auto.start_run("manual")
    except auto.AutoSourceError as exc:
        return _auto_error(exc)
    return jsonify(run), 202


@auto_source_bp.route("/api/story-video/auto-source/cancel", methods=["POST"])
def cancel_auto_source():
    run = auto.request_cancel()
    if run is None:
        return _error("Khong co luot nao dang chay.", code="not_running", status=404)
    return jsonify(run)


@auto_source_bp.route("/api/story-video/auto-source/cleanup", methods=["POST"])
def cleanup_auto_source():
    """Xoa ngay clip da dung >= maxClipUses video (can ``{"confirm": true}``)."""
    err = _mongo_or_503()
    if err is not None:
        return err
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return _error("Can xac nhan (confirm=true) truoc khi xoa clip.", code="confirm_required")
    try:
        result = auto.execute_cleanup(auto.load_settings()["maxClipUses"])
    except auto.AutoSourceError as exc:
        return _auto_error(exc)
    return jsonify(result)
