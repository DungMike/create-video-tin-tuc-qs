"""API /api/pipeline/* — pipeline kich ban (.docx/.txt) -> audio + srt -> video.

Thiet ke: docs/integration/04-thiet-ke-pipeline.md §12. Loi cung dang voi
story_video_routes: {"error": {"code", "message"}}.
"""

from __future__ import annotations

import json
import os
import threading
import time
from flask import Blueprint, jsonify, request, send_file

from src.config import Config
from src.utils.logger import logger
from src.utils.pipeline_clients import DgtClient, ServiceError, ServiceUnavailable, TstClient
from src.utils.pipeline_ingest import IngestError, TEXT_EXTENSIONS, safe_upload_name, scan_folder
from src.utils import pipeline_runner as runner
from src.utils.pipeline_store import list_runs, load_run, new_run_id, read_events, run_dir, summarize_run

pipeline_bp = Blueprint("pipeline", __name__)


def _error(message: str, code: str = "bad_request", status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status


def _pipeline_error(exc: runner.PipelineError):
    return _error(str(exc), exc.code, exc.status)


FILE_KINDS = ("txt", "audio", "srt", "video")


def _file_url(run_id: str, item: dict, kind: str) -> str | None:
    path = (item.get("files") or {}).get(kind)
    if not path or not os.path.isfile(path):
        return None
    return f"/api/pipeline/runs/{run_id}/items/{item['itemId']}/files/{kind}"


def _with_media(run: dict) -> dict:
    for item in run.get("items", []):
        item["media"] = {
            **(item.get("media") or {}),
            "txtUrl": _file_url(run["runId"], item, "txt"),
            "audioUrl": _file_url(run["runId"], item, "audio"),
            "srtUrl": _file_url(run["runId"], item, "srt"),
            "videoUrl": _file_url(run["runId"], item, "video"),
        }
    return run


# --- tinh trang dich vu -----------------------------------------------------------

# Tien trinh ma pipeline can chay (optimizeMode tam dung theo ten tien trinh).
_PIPELINE_PROCESSES = {
    "dgtautottsmm", "dgtautomation.api", "dgtautomation.desktop", "dotnet",
    "tstautogenerateimage", "chrome", "claude", "node", "python", "ffmpeg",
}


def _suspend_warning() -> str | None:
    names = [n.strip() for n in (Config.RENDER_SUSPEND_PROCESS_NAMES or "").split(",") if n.strip()]
    hit = [n for n in names if n.lower().removesuffix(".exe") in _PIPELINE_PROCESSES]
    if hit:
        return f"RENDER_SUSPEND_PROCESS_NAMES có tiến trình pipeline cần dùng: {', '.join(hit)} (pipeline luôn tắt optimizeMode)"
    return None


_dir_sizes: dict[str, tuple[float, int | None]] = {}
_dir_size_lock = threading.Lock()


def _dir_size_cached(path: str) -> int | None:
    """Kich thuoc thu muc (tinh o luong nen, cache 10 phut) — thu muc lon tinh mat vai giay."""
    now = time.monotonic()
    with _dir_size_lock:
        at, size = _dir_sizes.get(path, (0.0, None))
        if now - at < 600 and (size is not None or now - at < 60):
            return size
        _dir_sizes[path] = (now, size)

    def work():
        total = 0
        for current, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(current, name))
                except OSError:
                    pass
        with _dir_size_lock:
            _dir_sizes[path] = (time.monotonic(), total)

    threading.Thread(target=work, daemon=True, name="pipeline-dir-size").start()
    return size


def _disk_warnings() -> list[str]:
    warnings = []
    if Config.DGT_WORK_DIR and os.path.isdir(Config.DGT_WORK_DIR):
        size = _dir_size_cached(Config.DGT_WORK_DIR)
        if size is not None and size > Config.PIPELINE_DISK_WARN_GB * 1024 ** 3:
            warnings.append(f"Thư mục làm việc của app TTS chiếm {size / 1024 ** 3:.1f} GB ({Config.DGT_WORK_DIR}) — app không tự dọn")
    try:
        from src.utils.file_manager import output_drive_options

        for drive in output_drive_options().get("drives") or []:
            if drive.get("totalBytes") and drive["freeBytes"] < Config.PIPELINE_MIN_FREE_GB * 1024 ** 3:
                warnings.append(f"Ổ {drive['drive']}: chỉ còn {drive['freeBytes'] / 1024 ** 3:.1f} GB trống")
    except OSError:
        pass
    return warnings


@pipeline_bp.route("/api/pipeline/health", methods=["GET"])
def pipeline_health():
    services: dict = {}
    warnings: list[str] = []

    dgt = DgtClient(timeout=4)
    try:
        health = dgt.health()
        runtime = dgt.runtime()
        adapter = next((a for a in runtime.get("adapters") or [] if a.get("adapter") == "legacy-desktop"), {})
        services["dgt"] = {
            "ok": True,
            "version": health.get("version"),
            "ready": adapter.get("ready"),
            "reason": adapter.get("reason"),
            "activeJobId": runtime.get("active_job_id"),
        }
    except (ServiceUnavailable, ServiceError) as exc:
        services["dgt"] = {"ok": False, "error": str(exc)}

    tst = TstClient(timeout=6)
    try:
        state = tst.tst_state()
        services["tstStudio"] = {"ok": True}
        services["tst"] = {"online": bool(state.get("online")), "loggedIn": bool(state.get("loggedIn"))}
    except (ServiceUnavailable, ServiceError) as exc:
        services["tstStudio"] = {"ok": False, "error": str(exc)}
        services["tst"] = {"online": False, "loggedIn": False}

    if services["tstStudio"].get("ok"):
        try:
            ai = tst.ai_state()
            services["claude"] = {"found": bool(ai.get("found")), "path": ai.get("path"), "version": ai.get("version")}
        except ServiceError as exc:
            services["claude"] = {"found": None, "error": "TST Studio chưa có /api/ai" if exc.status == 404 else str(exc)}
        except ServiceUnavailable as exc:
            services["claude"] = {"found": None, "error": str(exc)}
    else:
        services["claude"] = {"found": None}

    from src.utils.story_video_batch import queue_snapshot

    snapshot = queue_snapshot()
    services["svQueue"] = {"activeBatchId": snapshot.get("activeBatchId"), "queued": len(snapshot.get("queuedIds") or [])}
    services["mongo"] = {"enabled": bool(Config.MONGODB_URI)}

    warning = _suspend_warning()
    if warning:
        warnings.append(warning)
    warnings.extend(_disk_warnings())
    return jsonify({"services": services, "warnings": warnings})


@pipeline_bp.route("/api/pipeline/tts/voices", methods=["GET"])
def pipeline_tts_voices():
    try:
        voices = DgtClient().voices()
    except (ServiceUnavailable, ServiceError) as exc:
        return _error(f"Không lấy được danh sách giọng từ app TTS: {exc}", "dgt_unavailable", 503)
    return jsonify({"voices": [
        {
            "voiceRef": v.get("voice_ref"),
            "name": v.get("name"),
            "language": v.get("language") or (v.get("app_voice") or {}).get("language"),
            "state": v.get("state"),
        }
        for v in voices if v.get("voice_ref")
    ]})


@pipeline_bp.route("/api/pipeline/tst/projects", methods=["GET"])
def pipeline_tst_projects():
    try:
        projects = TstClient().projects()
    except (ServiceUnavailable, ServiceError) as exc:
        return _error(f"Không lấy được chủ đề từ TST Studio: {exc}", "tst_unavailable", 503)
    return jsonify({"projects": [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "styleSkill": p.get("style_skill"),
            "aspectRatio": p.get("aspect_ratio"),
            "storyCount": p.get("storyCount"),
        }
        for p in projects
    ]})


# --- quet folder / tao run ---------------------------------------------------------

@pipeline_bp.route("/api/pipeline/scan", methods=["POST"])
def pipeline_scan():
    data = request.get_json(silent=True) or {}
    path = str(data.get("path") or "").strip().strip('"')
    if not path:
        return _error("Cần đường dẫn thư mục.", "path_required")
    try:
        return jsonify(scan_folder(path, bool(data.get("recursive"))))
    except IngestError as exc:
        return _error(str(exc), exc.code, 404 if exc.code == "folder_not_found" else 400)


def _discard_unfinished_run(run_id: str | None) -> None:
    """Tao run loi giua chung: xoa thu muc (upload) neu run.json chua kip ghi."""
    if not run_id:
        return
    folder = run_dir(run_id)
    if os.path.isdir(folder) and not os.path.isfile(os.path.join(folder, "run.json")):
        import shutil

        shutil.rmtree(folder, ignore_errors=True)


def _download_gdoc(url: str, entry: dict, upload_dir: str, index: int) -> dict:
    """Link Google Docs (cong khai) -> uploads/gdoc_NN.txt, coi nhu mot file upload."""
    from src.utils.tts_audio import TTSAudioError, download_google_doc_text

    try:
        text = download_google_doc_text(url)
    except TTSAudioError as exc:
        raise runner.PipelineError(exc.code, f"{url}: {exc}") from exc
    title = str(entry.get("title") or "").strip() or f"Google Doc {index}"
    os.makedirs(upload_dir, exist_ok=True)
    dest = os.path.join(upload_dir, f"gdoc_{index:02d}.txt")
    with open(dest, "w", encoding="utf-8") as file_obj:
        file_obj.write(text)
    return {"path": dest, "originalName": f"{safe_upload_name(title)}.txt", "type": entry.get("type"),
            "uploaded": True, "title": title}


def _read_create_payload() -> dict:
    if request.form.get("payload"):
        try:
            return json.loads(request.form["payload"])
        except json.JSONDecodeError as exc:
            raise runner.PipelineError("invalid_json", f"payload không phải JSON: {exc}") from exc
    return request.get_json(silent=True) or {}


@pipeline_bp.route("/api/pipeline/runs", methods=["POST"])
def pipeline_create_run():
    """multipart: payload(JSON) + files[]; hoac JSON.

    payload = {name, config, items:[{source:"upload:<i>" | "<abs path>", type}], folderPath?, recursive?}
    Khong co items ma co folderPath: lay moi file hop le trong folder voi config.defaultType.
    """
    try:
        data = _read_create_payload()
        run_id = new_run_id()
        uploads = request.files.getlist("files")
        upload_dir = os.path.join(run_dir(run_id), "uploads")
        uploaded: dict[int, dict] = {}
        for index, storage in enumerate(uploads):
            if not storage or not storage.filename:
                continue
            name = safe_upload_name(storage.filename)
            if os.path.splitext(name)[1].lower() not in TEXT_EXTENSIONS:
                raise runner.PipelineError("unsupported_file", f"{storage.filename}: chỉ nhận .docx hoặc .txt.")
            os.makedirs(upload_dir, exist_ok=True)
            dest = os.path.join(upload_dir, f"{index + 1:02d}_{name}")
            storage.save(dest)
            uploaded[index] = {"path": dest, "originalName": name}

        items = data.get("items")
        sources: list[dict] = []
        if not items and data.get("folderPath"):
            scan = scan_folder(str(data["folderPath"]), bool(data.get("recursive")))
            items = [{"source": f["path"], "type": None} for f in scan["files"] if f.get("valid")]
        if not items and uploaded:
            items = [{"source": f"upload:{i}", "type": None} for i in sorted(uploaded)]
        for entry in items or []:
            ref = str(entry.get("source") or "").strip()
            if ref.startswith("upload:"):
                try:
                    up = uploaded[int(ref.split(":", 1)[1])]
                except (ValueError, KeyError) as exc:
                    raise runner.PipelineError("upload_not_found", f"Không thấy file upload {ref}.") from exc
                sources.append({**up, "type": entry.get("type"), "uploaded": True, "title": entry.get("title")})
            elif ref.startswith(("https://docs.google.com/", "http://docs.google.com/")):
                sources.append(_download_gdoc(ref, entry, upload_dir, len(sources) + 1))
            else:
                if not os.path.isabs(ref) or not os.path.isfile(ref):
                    raise runner.PipelineError("source_not_found", f"Không tìm thấy file: {ref}", 404)
                if os.path.splitext(ref)[1].lower() not in TEXT_EXTENSIONS:
                    raise runner.PipelineError("unsupported_file", f"{ref}: chỉ nhận .docx hoặc .txt.")
                sources.append({"path": ref, "type": entry.get("type"), "title": entry.get("title")})

        config = data.get("config") or {}
        run = runner.create_run(str(data.get("name") or ""), config, sources, run_id=run_id)
    except (runner.PipelineError, IngestError) as exc:
        _discard_unfinished_run(locals().get("run_id"))
        if isinstance(exc, runner.PipelineError):
            return _pipeline_error(exc)
        return _error(str(exc), exc.code)
    logger.info(f"[Pipeline] tao run {run['runId']}: {len(run['items'])} item")
    return jsonify(_with_media(run)), 201


@pipeline_bp.route("/api/pipeline/runs", methods=["GET"])
def pipeline_list_runs():
    limit = request.args.get("limit", default=30, type=int)
    return jsonify({"runs": [summarize_run(run) for run in list_runs(limit)]})


@pipeline_bp.route("/api/pipeline/runs/<run_id>", methods=["GET"])
def pipeline_get_run(run_id: str):
    run = load_run(run_id)
    if run is None:
        return _error("Không tìm thấy run.", "run_not_found", 404)
    return jsonify(_with_media(run))


@pipeline_bp.route("/api/pipeline/runs/<run_id>/items/<item_id>/files/<kind>", methods=["GET"])
def pipeline_item_file(run_id: str, item_id: str, kind: str):
    """Phat file cua item (txt/audio/srt/video) — chi cac duong dan da ghi trong run.json."""
    run = load_run(run_id)
    item = next((i for i in (run or {}).get("items", []) if i.get("itemId") == item_id), None)
    if item is None or kind not in FILE_KINDS:
        return _error("Không tìm thấy file.", "file_not_found", 404)
    path = (item.get("files") or {}).get(kind)
    if not path or not os.path.isfile(path):
        return _error("File chưa có.", "file_not_found", 404)
    download = request.args.get("download") == "1"
    return send_file(path, as_attachment=download, download_name=os.path.basename(path), conditional=True)


@pipeline_bp.route("/api/pipeline/runs/<run_id>/events", methods=["GET"])
def pipeline_run_events(run_id: str):
    if load_run(run_id) is None:
        return _error("Không tìm thấy run.", "run_not_found", 404)
    since = request.args.get("since", default=0, type=int)
    events = read_events(run_id, since)
    return jsonify({"events": events, "next": events[-1]["seq"] if events else since})


def _action(fn, *args):
    try:
        run = fn(*args)
    except runner.PipelineError as exc:
        return _pipeline_error(exc)
    return jsonify(_with_media(run))


@pipeline_bp.route("/api/pipeline/runs/<run_id>/pause", methods=["POST"])
def pipeline_pause_run(run_id: str):
    return _action(runner.pause_run, run_id)


@pipeline_bp.route("/api/pipeline/runs/<run_id>/resume", methods=["POST"])
def pipeline_resume_run(run_id: str):
    return _action(runner.resume_run, run_id)


@pipeline_bp.route("/api/pipeline/runs/<run_id>/cancel", methods=["POST"])
def pipeline_cancel_run(run_id: str):
    return _action(runner.cancel_run, run_id)


@pipeline_bp.route("/api/pipeline/runs/<run_id>/items/<item_id>/retry", methods=["POST"])
def pipeline_retry_item(run_id: str, item_id: str):
    data = request.get_json(silent=True) or {}
    return _action(runner.retry_item, run_id, item_id, data.get("fromStage") or None)


@pipeline_bp.route("/api/pipeline/runs/<run_id>/items/<item_id>/cancel", methods=["POST"])
def pipeline_cancel_item(run_id: str, item_id: str):
    return _action(runner.cancel_item, run_id, item_id)


# --- cong duyet (loai 1) -------------------------------------------------------------

@pipeline_bp.route("/api/pipeline/runs/<run_id>/items/<item_id>/review", methods=["GET"])
def pipeline_review(run_id: str, item_id: str):
    from src.utils import pipeline_type1

    gate = request.args.get("gate") or "prompts"
    try:
        return jsonify(pipeline_type1.review(run_id, item_id, gate))
    except runner.PipelineError as exc:
        return _pipeline_error(exc)
    except (ServiceUnavailable, ServiceError) as exc:
        return _error(f"Không đọc được truyện từ TST Studio: {exc}", "tst_unavailable", 503)


@pipeline_bp.route("/api/pipeline/runs/<run_id>/items/<item_id>/approve", methods=["POST"])
def pipeline_approve(run_id: str, item_id: str):
    from src.utils import pipeline_type1

    data = request.get_json(silent=True) or {}
    return _action(pipeline_type1.approve, run_id, item_id, str(data.get("gate") or ""))


@pipeline_bp.route("/api/pipeline/runs/<run_id>/items/<item_id>/reject", methods=["POST"])
def pipeline_reject(run_id: str, item_id: str):
    from src.utils import pipeline_type1

    data = request.get_json(silent=True) or {}
    try:
        run = pipeline_type1.reject(
            run_id, item_id, str(data.get("gate") or ""), str(data.get("action") or ""),
            note=str(data.get("note") or ""),
            batch_nos=data.get("batchNos") or [],
            character_ids=data.get("characterIds") or [],
        )
    except runner.PipelineError as exc:
        return _pipeline_error(exc)
    return jsonify(_with_media(run))


# --- preset cau hinh loai 2 ------------------------------------------------------------

# Pipeline tu dat cac key nay cho moi run, khong lay tu preset.
_PRESET_DROP_KEYS = {"optimizeMode", "outputSubdir", "voiceId", "introId"}


@pipeline_bp.route("/api/pipeline/presets", methods=["GET"])
def pipeline_list_presets():
    from src.utils.pipeline_store import list_presets

    return jsonify({"presets": list_presets()})


@pipeline_bp.route("/api/pipeline/presets", methods=["POST"])
def pipeline_create_preset():
    from src.utils.pipeline_store import save_preset

    data = request.get_json(silent=True) or {}
    shared = data.get("sharedConfig")
    if not isinstance(shared, dict):
        return _error("Thiếu sharedConfig.", "shared_config_required")
    if not [x for x in shared.get("libraryIds") or [] if str(x).strip()]:
        return _error("Preset phải có ít nhất 1 thư viện clip.", "library_required")
    shared = {k: v for k, v in shared.items() if k not in _PRESET_DROP_KEYS}
    return jsonify(save_preset(str(data.get("name") or ""), shared)), 201


@pipeline_bp.route("/api/pipeline/presets/<preset_id>", methods=["DELETE"])
def pipeline_delete_preset(preset_id: str):
    from src.utils.pipeline_store import delete_preset

    if not delete_preset(preset_id):
        return _error("Không tìm thấy preset.", "preset_not_found", 404)
    return jsonify({"deleted": preset_id})
