"""Reconciler cua pipeline kich ban -> video.

Mot luong nen duy nhat chay moi ``PIPELINE_TICK_SECONDS``: voi moi item chua ket
thuc, goi handler cua stage hien tai. Handler hoi trang thai he thong ben ngoai
(DGT, TST Studio, hang doi batch) roi lam buoc ke tiep theo kieu idempotent, nen
restart bat ky app nao cung khong sinh viec trung (docs/integration/04 §11).

Handler chay tren BAN SAO item, ngoai khoa run; ket qua chi duoc ghi lai neu ``rev``
cua item chua bi route (approve/cancel/retry...) doi trong luc do.
"""

from __future__ import annotations

import copy
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Callable

from src.config import Config
from src.utils.logger import logger
from src.utils.pipeline_clients import DgtClient, ServiceError, ServiceUnavailable, dgt_artifact_paths
from src.utils.pipeline_ingest import IngestError, prepare_item_text, slugify
from src.utils.pipeline_store import (
    ITEM_TERMINAL,
    RUN_TERMINAL,
    append_event,
    find_item,
    item_dir,
    list_run_ids,
    load_run,
    refresh_run_status,
    run_dir,
    run_lock,
    save_run,
    touch_item,
    update_run,
    utc_now,
)

STAGES_TYPE1 = [
    "INGEST", "TTS", "SRT_CHECK", "TST_STORY", "AI", "BUILD", "GATE_PROMPTS",
    "CHARACTERS", "GATE_CHARACTERS", "SCENES", "VIDEO", "COLLECT", "DONE",
]
STAGES_TYPE2 = ["INGEST", "TTS", "SRT_CHECK", "SV_RENDER", "DONE"]
# Item dau vao "media" (co san audio + srt) khong co cac buoc nay.
SCRIPT_ONLY_STAGES = ("INGEST", "TTS")

# Trang thai reconciler bo qua (cho nguoi / da ket thuc).
IDLE_STATUSES = {"awaiting_review", "needs_attention"} | ITEM_TERMINAL

# Lane gioi han so viec chay cung luc (docs/integration/04 §11).
LANE_OF_STAGE = {"AI": "ai", "CHARACTERS": "tst_images", "SCENES": "tst_images", "VIDEO": "render1"}

DGT_STAGE_LABELS = {
    "queued": "Chờ trong hàng TTS",
    "starting": "Đang khởi động app TTS",
    "validating_input": "Kiểm tra kịch bản",
    "reconciling_output": "Dùng lại kết quả lần trước",
    "selecting_voice": "Chọn giọng đọc",
    "importing_txt": "Nạp kịch bản vào app TTS",
    "creating_audio": "Đang tạo giọng đọc",
    "verifying_audio": "Kiểm tra audio",
    "saving_output": "Lưu file audio + srt",
    "completed": "Xong",
}

Handler = Callable[["Context", dict, dict], None]
HANDLERS: dict[str, Handler] = {}
CANCEL_HOOKS: list[Callable[["Context", dict, dict], None]] = []


def stage_handler(stage: str):
    def register(fn: Handler) -> Handler:
        HANDLERS[stage] = fn
        return fn
    return register


def cancel_hook(fn):
    CANCEL_HOOKS.append(fn)
    return fn


class PipelineError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


# --- item helpers -----------------------------------------------------------------

def stages_for(item: dict) -> list[str]:
    stages = STAGES_TYPE1 if int(item.get("type") or 2) == 1 else STAGES_TYPE2
    if item.get("input") == "media":
        return [stage for stage in stages if stage not in SCRIPT_ONLY_STAGES]
    return stages


def set_status(item: dict, status: str, message: str | None = None, *,
               percent: float | None = None, waiting: str | None = None) -> None:
    item["status"] = status
    item["waitingReason"] = waiting if status == "waiting_service" else None
    progress = item.setdefault("progress", {})
    if message is not None:
        progress["message"] = message
    if percent is not None:
        progress["percent"] = round(max(0.0, min(100.0, percent)), 1)
    if status == "running" and not item.get("startedAt"):
        item["startedAt"] = utc_now()


def advance(item: dict, message: str = "") -> None:
    stages = stages_for(item)
    index = stages.index(item["stage"]) if item["stage"] in stages else len(stages) - 2
    item["stage"] = stages[min(index + 1, len(stages) - 1)]
    item["progress"] = {"percent": 0, "message": message}
    set_status(item, "pending")


def fail(item: dict, code: str, message: str) -> None:
    item["error"] = {"stage": item.get("stage"), "code": code, "message": message, "at": utc_now()}
    set_status(item, "failed", message)


def needs_attention(item: dict, code: str, message: str) -> None:
    item["error"] = {"stage": item.get("stage"), "code": code, "message": message, "at": utc_now()}
    set_status(item, "needs_attention", message)


# --- context ----------------------------------------------------------------------

@dataclass
class Context:
    paused: bool = False
    dgt: DgtClient = field(default_factory=DgtClient)
    tst: object = None
    lanes_used: dict[str, int] = field(default_factory=dict)
    run_cache: dict = field(default_factory=dict)  # du lieu dung chung trong 1 tick (vd batch SV)
    acquired_now: set = field(default_factory=set)  # lane item dang xu ly vua xin trong tick nay

    @staticmethod
    def lane_limit(lane: str) -> int:
        return {
            "ai": Config.PIPELINE_AI_CONCURRENCY,
            "tst_images": Config.PIPELINE_TST_IMAGE_CONCURRENCY,
            "render1": Config.PIPELINE_TYPE1_RENDER_CONCURRENCY,
        }.get(lane, 1_000_000)

    def can_start(self) -> bool:
        return not self.paused

    def release(self, lane: str) -> None:
        """Item vua xong job cua chinh no (van dang tinh la 'running' trong lane) tra lai cho."""
        self.lanes_used[lane] = max(0, self.lanes_used.get(lane, 0) - 1)

    def acquire(self, lane: str) -> bool:
        """Xin 1 cho trong lane truoc khi BAT DAU viec moi. False = cho tick sau."""
        if self.paused:
            return False
        used = self.lanes_used.get(lane, 0)
        if used >= self.lane_limit(lane):
            return False
        self.lanes_used[lane] = used + 1
        self.acquired_now.add(lane)
        return True


def _make_context(paused: bool, lanes_used: dict[str, int], shared: dict) -> Context:
    from src.utils.pipeline_clients import TstClient

    if shared.get("_dgt") is None:
        shared["_dgt"] = DgtClient()
    if shared.get("_tst") is None:
        shared["_tst"] = TstClient()
    return Context(paused=paused, dgt=shared["_dgt"], tst=shared["_tst"], lanes_used=lanes_used, run_cache=shared)


# Id chu de cua TST Studio (safeId trong studio/server/lib.js).
TST_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)

# Cho phep test thay client gia.
_client_overrides: dict[str, object] = {}


def set_client_overrides(**clients) -> None:
    _client_overrides.clear()
    _client_overrides.update(clients)


# --- run tao moi -------------------------------------------------------------------

def validate_config(config: dict, types: set[int], *, needs_tts: bool = True, needs_project: bool | None = None) -> dict:
    """``needs_project``: co video loai 1 chua chon chu de rieng -> bat buoc chu de mac dinh (mac dinh: co loai 1)."""
    config = copy.deepcopy(config or {})
    tts = config.setdefault("tts", {})
    if needs_tts and not str(tts.get("voiceRef") or "").strip():
        raise PipelineError("voice_required", "Chưa chọn giọng đọc (voiceRef).")
    tts.setdefault("format", "mp3")
    if tts["format"] not in ("mp3", "wav"):
        raise PipelineError("invalid_format", "format phải là mp3 hoặc wav.")
    tts.setdefault("autoRetry", True)
    config.setdefault("ingest", {}).setdefault("blankLineBetweenParagraphs", False)

    type1 = config.setdefault("type1", {})
    type1.setdefault("gates", {})
    type1["gates"]["prompts"] = True  # bat buoc (CLAUDE.md cua TST: hoi truoc khi tao anh)
    type1["gates"].setdefault("characters", False)
    type1.setdefault("ai", {}).setdefault("model", "opus")
    type1.setdefault("video", {}).setdefault("from", "0")
    if (1 in types if needs_project is None else needs_project) and not str(type1.get("tstProjectId") or "").strip():
        raise PipelineError("tst_project_required", "Loại 1 cần chọn chủ đề TST (tstProjectId).")
    if type1.get("tstProjectId") and not TST_PROJECT_ID.match(str(type1["tstProjectId"])):
        raise PipelineError("invalid_tst_project", f"Chủ đề TST không hợp lệ: {type1['tstProjectId']}")

    type2 = config.setdefault("type2", {})
    if type2.get("presetId"):
        # Preset luu tu trang Story Video: dung nguyen sharedConfig cua no (o xuat lay tu config.output).
        from src.utils.pipeline_store import get_preset

        preset = get_preset(str(type2["presetId"]))
        if preset is None:
            raise PipelineError("preset_not_found", "Không tìm thấy preset cấu hình loại 2.", 404)
        type2["sharedConfig"] = copy.deepcopy(preset.get("sharedConfig") or {})
        type2["presetName"] = preset.get("name")
    shared = type2.setdefault("sharedConfig", {})
    if 2 in types:
        library_ids = shared.get("libraryIds") or []
        if not isinstance(library_ids, list) or not [x for x in library_ids if str(x).strip()]:
            raise PipelineError("library_required", "Loại 2 cần chọn ít nhất 1 thư viện clip.")
    return config


def create_run(name: str, config: dict, sources: list[dict], *, run_id: str) -> dict:
    """Tao run + INGEST dong bo. ``sources``: [{path, type, title?, originalName?, uploaded?}].

    Nguon co ``srtPath`` (``path`` la audio) = co san audio + srt: bat dau tu SRT_CHECK.
    """
    if not sources:
        raise PipelineError("empty_items", "Cần ít nhất 1 kịch bản.")
    types = set()
    for source in sources:
        try:
            source["type"] = int(source.get("type") or config.get("defaultType") or 2)
        except (TypeError, ValueError) as exc:
            raise PipelineError("invalid_type", "Loại video phải là 1 hoặc 2.") from exc
        if source["type"] not in (1, 2):
            raise PipelineError("invalid_type", "Loại video phải là 1 hoặc 2.")
        types.add(source["type"])
        project = source.get("tstProjectId")
        if project and not TST_PROJECT_ID.match(str(project)):
            raise PipelineError("invalid_tst_project", f"Chủ đề TST không hợp lệ: {project}")
    needs_tts = any(not source.get("srtPath") for source in sources)
    needs_project = any(source["type"] == 1 and not source.get("tstProjectId") for source in sources)
    config = validate_config(config, types, needs_tts=needs_tts, needs_project=needs_project)
    config.setdefault("output", {})
    config["output"].setdefault("drive", "")
    config["output"]["subdir"] = f"pipeline-{run_id}"

    now = utc_now()
    run = {
        "runId": run_id,
        "name": (name or "").strip() or f"Run {now[:16].replace('T', ' ')}",
        "status": "running",
        "createdAt": now,
        "updatedAt": now,
        "config": config,
        "items": [],
        "eventSeq": 0,
    }
    os.makedirs(run_dir(run_id), exist_ok=True)

    used_slugs: set[str] = set()
    for index, source in enumerate(sources, start=1):
        item_id = f"it-{index:02d}"
        original = source.get("originalName") or os.path.basename(source["path"])
        title = source.get("title") or os.path.splitext(original)[0]
        slug = slugify(title) or f"item-{index:02d}"
        base, n = slug, 2
        while slug in used_slugs:
            slug = f"{base[:44]}-{n}"
            n += 1
        used_slugs.add(slug)
        item = {
            "itemId": item_id,
            "index": index,
            "title": title,
            "slug": slug,
            "type": source["type"],
            "input": "script",  # "media" = co san audio + srt (_attach_media); run cu khong co -> script
            "source": {
                "path": os.path.abspath(source["path"]),
                "originalName": original,
                "kind": os.path.splitext(original)[1].lower().lstrip("."),
                "uploaded": bool(source.get("uploaded")),
            },
            "stage": "INGEST",
            "status": "pending",
            "waitingReason": None,
            "progress": {"percent": 0, "message": ""},
            "files": {"txt": os.path.join(item_dir(run_id, item_id), f"{slug}.txt"),
                      "audio": None, "srt": None, "video": None},
            "tts": {"attempt": 1},
            "error": None,
            "warnings": [],
            "rev": 0,
            "createdAt": now,
        }
        if source["type"] == 1:
            # Chu de TST (bo skill dung de viet prompt anh) cua video nay; TST_STORY tao truyen trong chu de do.
            item["tst"] = {"projectId": source.get("tstProjectId") or config["type1"].get("tstProjectId")}
        if source.get("srtPath"):
            _attach_media(item, source)
        else:
            _ingest(run, item)
        run["items"].append(item)

    with run_lock(run_id):
        for item in run["items"]:
            append_event(run, item["itemId"], item["stage"], item["status"],
                         item["progress"].get("message") or "Đã tạo")
        refresh_run_status(run)
        save_run(run)
    wake()
    return run


def _ingest(run: dict, item: dict) -> None:
    try:
        info = prepare_item_text(
            item["source"]["path"],
            item["files"]["txt"],
            blank_line_between_paragraphs=bool(run["config"]["ingest"].get("blankLineBetweenParagraphs")),
        )
    except IngestError as exc:
        fail(item, exc.code, str(exc))
        return
    except OSError as exc:
        fail(item, "read_error", f"Không đọc được file: {exc}")
        return
    item["source"]["sha256"] = info["sha256"]
    item["text"] = {"chars": info["chars"], "paragraphs": info["paragraphs"]}
    item["warnings"] = list(info["warnings"])
    advance(item, f"Kịch bản {info['chars']} ký tự, {info['paragraphs']} đoạn")


def _attach_media(item: dict, source: dict) -> None:
    """Co san audio + srt: khong tao txt, khong goi TTS; SRT_CHECK kiem tra cap file."""
    srt = os.path.abspath(source["srtPath"])
    item["input"] = "media"
    item["source"]["srtPath"] = srt
    item["source"]["srtName"] = source.get("srtName") or os.path.basename(srt)
    item["files"] = {"txt": None, "audio": item["source"]["path"], "srt": srt, "video": None}
    item.pop("tts", None)
    item["stage"] = "SRT_CHECK"
    item["progress"] = {"percent": 0, "message": "Có sẵn audio + srt, bỏ qua tạo giọng đọc"}


@stage_handler("INGEST")
def handle_ingest(ctx: Context, run: dict, item: dict) -> None:
    _ingest(run, item)


# --- TTS ---------------------------------------------------------------------------

@stage_handler("TTS")
def handle_tts(ctx: Context, run: dict, item: dict) -> None:
    tts = item.setdefault("tts", {"attempt": 1})
    tts_config = run["config"]["tts"]
    if not tts.get("jobId"):
        if not ctx.can_start():
            return
        attempt = int(tts.get("attempt") or 1)
        key = f"{run['runId']}-{item['itemId']}-tts-{attempt}"
        response = ctx.dgt.run_file(
            item["files"]["txt"],
            tts_config["voiceRef"],
            idempotency_key=key,
            name=f"{run['runId']}/{item['itemId']} {item.get('title') or ''}",
            audio_format=tts_config.get("format") or "mp3",
            auto_retry=bool(tts_config.get("autoRetry", True)),
        )
        job_ids = response.get("job_ids") or []
        if not job_ids:
            raise ServiceError("dgt", 500, "no_job", "App TTS không trả về job_id.")
        tts.update(idempotencyKey=key, batchId=response.get("batch_id"), jobId=job_ids[0], state="queued")
        set_status(item, "running", "Đã gửi kịch bản sang app TTS", percent=0)
        return

    job = ctx.dgt.job(tts["jobId"])
    state = job.get("state") or ""
    tts["state"] = state
    stage = job.get("stage") or state
    if state in ("queued", "running", "cancel_requested"):
        label = DGT_STAGE_LABELS.get(stage, stage)
        if state == "queued" and job.get("waiting_reason") == "queue_order":
            label = "Chờ tới lượt trong hàng TTS (1 file một lúc)"
        elif state == "queued" and job.get("waiting_reason"):
            label = f"{label} ({job['waiting_reason']})"
        set_status(item, "running", f"TTS: {label}")
    elif state == "needs_attention":
        code = job.get("error_code") or "needs_attention"
        set_status(item, "waiting_service", f"App TTS cần xử lý: {job.get('error_message') or code}",
                   waiting=f"dgt:{code}")
    elif state == "succeeded":
        audio, srt = dgt_artifact_paths(job)
        if not audio or not os.path.isfile(audio) or not srt or not os.path.isfile(srt):
            fail(item, "tts_output_missing", "App TTS báo xong nhưng không thấy file mp3/srt.")
            return
        item["files"]["audio"] = audio
        item["files"]["srt"] = srt
        advance(item, "Đã có audio + srt")
    elif state == "failed":
        attempt = int(tts.get("attempt") or 1)
        message = job.get("error_message") or job.get("error_code") or "TTS lỗi"
        if attempt <= Config.PIPELINE_TTS_RETRY:
            tts.update(attempt=attempt + 1, jobId=None, batchId=None, state=None)
            set_status(item, "pending", f"TTS lỗi ({message}), gửi lại lần {attempt + 1}")
        else:
            fail(item, job.get("error_code") or "tts_failed", message)
    elif state == "cancelled":
        fail(item, "tts_cancelled", "Job TTS đã bị huỷ ở app TTS.")
    else:
        set_status(item, "running", f"TTS: {state}")


@cancel_hook
def _cancel_tts(ctx: Context, run: dict, item: dict) -> None:
    tts = item.get("tts") or {}
    if item.get("stage") == "TTS" and tts.get("jobId") and tts.get("state") not in ("succeeded", "failed", "cancelled"):
        ctx.dgt.cancel_job(tts["jobId"])


# --- SRT_CHECK -----------------------------------------------------------------------

@stage_handler("SRT_CHECK")
def handle_srt_check(ctx: Context, run: dict, item: dict) -> None:
    from src.processors.audio_utils import get_audio_duration
    from src.utils.story_subtitles import SubtitleParseError, parse_srt

    srt, audio = item["files"].get("srt"), item["files"].get("audio")
    try:
        cues = parse_srt(srt)
    except SubtitleParseError as exc:
        fail(item, "srt_invalid", f"SRT không hợp lệ: {exc}")
        return
    duration = get_audio_duration(audio) if audio else 0.0
    if duration <= 0:
        fail(item, "audio_invalid", "Không đọc được thời lượng audio.")
        return
    last_end = max(cue["end"] for cue in cues)
    if last_end > duration + 2.0:
        fail(item, "srt_mismatch", f"SRT dài {last_end:.1f}s nhưng audio chỉ {duration:.1f}s.")
        return
    if last_end < duration - 2.0:
        item.setdefault("warnings", []).append(
            f"SRT kết thúc ở {last_end:.1f}s, audio dài {duration:.1f}s"
        )
    item["media"] = {"durationSeconds": round(duration, 3), "cues": len(cues)}
    advance(item, f"Audio {duration / 60:.1f} phút, {len(cues)} câu phụ đề")


@stage_handler("DONE")
def handle_done(ctx: Context, run: dict, item: dict) -> None:
    item["finishedAt"] = item.get("finishedAt") or utc_now()
    set_status(item, "done", "Hoàn tất", percent=100)


# --- reconciler -----------------------------------------------------------------------

def lane_usage(runs: list[dict]) -> dict[str, int]:
    used: dict[str, int] = {}
    for run in runs:
        for item in run.get("items", []):
            lane = LANE_OF_STAGE.get(item.get("stage"))
            if lane and item.get("status") == "running":
                used[lane] = used.get(lane, 0) + 1
    return used


def _active_runs() -> list[dict]:
    runs = [run for run in (load_run(rid) for rid in list_run_ids()) if run]
    runs = [run for run in runs if run.get("status") not in RUN_TERMINAL]
    runs.sort(key=lambda run: run.get("createdAt") or "")
    return runs


def _run_handler(ctx: Context, run: dict, work: dict) -> None:
    handler = HANDLERS.get(work.get("stage"))
    if handler is None:
        needs_attention(work, "stage_unsupported", f"Bước {work.get('stage')} chưa được hỗ trợ.")
        return
    try:
        handler(ctx, run, work)
    except ServiceUnavailable as exc:
        set_status(work, "waiting_service", str(exc), waiting=exc.waiting_reason)
    except ServiceError as exc:
        needs_attention(work, exc.code, f"{exc.service}: {exc}")
    except Exception as exc:  # noqa: BLE001 — 1 item loi khong duoc lam chet ca reconciler
        logger.exception(f"[Pipeline] {run['runId']}/{work.get('itemId')} {work.get('stage')} loi")
        needs_attention(work, "internal_error", f"Lỗi nội bộ: {exc}")


def process_item(ctx: Context, run: dict, item: dict) -> tuple[dict, list[tuple[str, str, str]]]:
    """Chay handler (toi da vai buoc lien tiep neu buoc vua xong ngay).

    Tra ve (ban sao moi, cac buoc chuyen trang thai de ghi events.jsonl).
    """
    work = copy.deepcopy(item)
    transitions: list[tuple[str, str, str]] = []
    # Item dang giu 1 cho trong lane: tra lai truoc, handler tu xin lai neu bat dau viec moi;
    # neu chi poll job cu (van chay) thi giu lai cho o cuoi ham.
    held = LANE_OF_STAGE.get(item.get("stage")) if item.get("status") == "running" else None
    if held:
        ctx.release(held)
    ctx.acquired_now = set()
    for _ in range(6):
        before = (work.get("stage"), work.get("status"), work.get("waitingReason"))
        _run_handler(ctx, run, work)
        after = (work.get("stage"), work.get("status"), work.get("waitingReason"))
        if after != before:
            message = (work.get("progress") or {}).get("message") or ""
            if work.get("status") in ("failed", "needs_attention") and work.get("error"):
                message = work["error"].get("message") or message
            transitions.append((work.get("stage"), work.get("status"), message))
        if work.get("stage") == before[0] or work.get("status") != "pending":
            break
    final = LANE_OF_STAGE.get(work.get("stage")) if work.get("status") == "running" else None
    if final and final not in ctx.acquired_now:
        ctx.lanes_used[final] = ctx.lanes_used.get(final, 0) + 1
    return work, transitions


def _commit_item(run_id: str, before: dict, after: dict, transitions: list[tuple[str, str, str]]) -> None:
    changed = any(before.get(k) != after.get(k) for k in after.keys() if k not in ("rev", "updatedAt"))
    if not changed:
        return
    with run_lock(run_id):
        run = load_run(run_id)
        if run is None:
            return
        current = find_item(run, before["itemId"])
        if current is None or current.get("rev") != before.get("rev"):
            return  # route vua sua item: bo ket qua, tick sau lam lai
        touch_item(after)
        run["items"][run["items"].index(current)] = after
        for stage, status, message in transitions:
            append_event(run, after["itemId"], stage, status, message)
        refresh_run_status(run)
        save_run(run)


def tick() -> None:
    runs = _active_runs()
    lanes = lane_usage(runs)
    shared: dict = dict(_client_overrides)
    for run in runs:
        ctx = _make_context(run.get("status") == "paused", lanes, shared)
        for item in sorted(run.get("items", []), key=lambda it: it.get("index") or 0):
            if item.get("status") in IDLE_STATUSES:
                continue
            after, transitions = process_item(ctx, run, item)
            _commit_item(run["runId"], item, after, transitions)
        # Viec gom theo run (vd micro-batch loai 2) chay sau khi xu ly tung item.
        for hook in RUN_HOOKS:
            try:
                hook(ctx, run["runId"])
            except Exception:  # noqa: BLE001
                logger.exception(f"[Pipeline] run hook loi cho {run['runId']}")


RUN_HOOKS: list[Callable[[Context, str], None]] = []


def run_hook(fn):
    RUN_HOOKS.append(fn)
    return fn


_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_wake_event = threading.Event()
_stop_event = threading.Event()


def wake() -> None:
    _wake_event.set()


def _worker_loop() -> None:
    logger.info("[Pipeline] reconciler started")
    while not _stop_event.is_set():
        try:
            tick()
        except Exception:  # noqa: BLE001
            logger.exception("[Pipeline] tick loi")
        _wake_event.wait(Config.PIPELINE_TICK_SECONDS)
        _wake_event.clear()


def start_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _stop_event.clear()
        _worker_thread = threading.Thread(target=_worker_loop, name="pipeline-reconciler", daemon=True)
        _worker_thread.start()


def stop_worker(timeout: float = 5.0) -> None:
    _stop_event.set()
    _wake_event.set()
    if _worker_thread:
        _worker_thread.join(timeout)


def resume_pipeline_runs() -> int:
    """Goi tu entry point that (web_app __main__, wsgi): chay reconciler cho run dang do."""
    active = len(_active_runs())
    start_worker()
    if active:
        logger.info(f"[Pipeline] resume {active} run dang do")
    return active


# --- thao tac tu route ---------------------------------------------------------------

def _require_run(run_id: str) -> dict:
    run = load_run(run_id)
    if run is None:
        raise PipelineError("run_not_found", "Không tìm thấy run.", 404)
    return run


def _require_item(run: dict, item_id: str) -> dict:
    item = find_item(run, item_id)
    if item is None:
        raise PipelineError("item_not_found", "Không tìm thấy item.", 404)
    return item


def pause_run(run_id: str) -> dict:
    def mutate(run):
        if run.get("status") in RUN_TERMINAL:
            raise PipelineError("run_finished", "Run đã kết thúc.", 409)
        run["status"] = "paused"
        append_event(run, None, "", "paused", "Tạm dừng run")
    _require_run(run_id)
    return update_run(run_id, mutate)


def resume_run(run_id: str) -> dict:
    def mutate(run):
        if run.get("status") == "paused":
            run["status"] = "running"
            append_event(run, None, "", "running", "Tiếp tục run")
            refresh_run_status(run)
    _require_run(run_id)
    run = update_run(run_id, mutate)
    wake()
    return run


def _cancel_external(run: dict, item: dict) -> None:
    ctx = _make_context(False, {}, dict(_client_overrides))
    for hook in CANCEL_HOOKS:
        try:
            hook(ctx, run, item)
        except Exception as exc:  # noqa: BLE001 — huy best-effort
            logger.warning(f"[Pipeline] huy viec ngoai cho {item.get('itemId')} loi: {exc}")


def cancel_item(run_id: str, item_id: str) -> dict:
    run = _require_run(run_id)
    item = _require_item(run, item_id)
    if item.get("status") in ITEM_TERMINAL:
        raise PipelineError("item_finished", "Item đã kết thúc.", 409)
    _cancel_external(run, item)

    def mutate(run):
        current = _require_item(run, item_id)
        if current.get("status") in ITEM_TERMINAL:
            return
        set_status(current, "cancelled", "Đã huỷ")
        touch_item(current)
        append_event(run, item_id, current.get("stage"), "cancelled", "Người dùng huỷ")
        refresh_run_status(run)
    return update_run(run_id, mutate)


def cancel_run(run_id: str) -> dict:
    run = _require_run(run_id)
    for item in run.get("items", []):
        if item.get("status") not in ITEM_TERMINAL:
            _cancel_external(run, item)

    def mutate(run):
        for item in run.get("items", []):
            if item.get("status") not in ITEM_TERMINAL:
                set_status(item, "cancelled", "Đã huỷ")
                touch_item(item)
        run["status"] = "cancelled"
        run["finishedAt"] = utc_now()
        append_event(run, None, "", "cancelled", "Huỷ cả run")
    return update_run(run_id, mutate)


RETRY_RESETS: dict[str, Callable[[dict, str], None]] = {}


def retry_item(run_id: str, item_id: str, from_stage: str | None = None) -> dict:
    run = _require_run(run_id)
    item = _require_item(run, item_id)
    stages = stages_for(item)
    stage = from_stage or item.get("stage")
    if stage not in stages or stage == "DONE":
        raise PipelineError("invalid_stage", f"Không chạy lại được từ bước {stage}.")
    if item.get("status") in ("running", "pending", "waiting_service") and not from_stage:
        raise PipelineError("item_busy", "Item đang chạy, không cần thử lại.", 409)
    if stages.index(stage) > stages.index(item.get("stage")) and item.get("status") != "done":
        raise PipelineError("invalid_stage", "Không thể nhảy tới bước chưa chạy.")

    def mutate(run):
        _reset_for_retry(run, _require_item(run, item_id), stage)
    run = update_run(run_id, mutate)
    wake()
    return run


def _reset_for_retry(run: dict, current: dict, stage: str) -> None:
    current["stage"] = stage
    current["error"] = None
    if stage in ("INGEST", "TTS"):
        # Lan TTS moi phai co khoa idempotency moi, neu khong DGT tra lai job cu.
        tts = current.setdefault("tts", {})
        if tts.get("jobId"):
            tts.update(attempt=int(tts.get("attempt") or 1) + 1, jobId=None, batchId=None, state=None)
    for reset in RETRY_RESETS.values():
        reset(current, stage)
    current["progress"] = {"percent": 0, "message": "Chạy lại"}
    current.pop("finishedAt", None)
    set_status(current, "pending")
    touch_item(current)
    if run.get("status") in RUN_TERMINAL:
        run["status"] = "running"
        run.pop("finishedAt", None)
    append_event(run, current["itemId"], stage, "pending", f"Chạy lại từ bước {stage}")


RETRY_ALL_STATUSES = ("needs_attention", "failed")


def retry_items(run_id: str) -> dict:
    """Chay lai (tai buoc dang dung) moi item can xu ly / loi cua run. Item da huy giu nguyen."""
    run = _require_run(run_id)
    if not any(item.get("status") in RETRY_ALL_STATUSES for item in run.get("items", [])):
        raise PipelineError("nothing_to_retry", "Không có mục nào lỗi hoặc cần xử lý.", 409)

    def mutate(run):
        for item in run.get("items", []):
            if item.get("status") in RETRY_ALL_STATUSES and item.get("stage") in stages_for(item):
                _reset_for_retry(run, item, item["stage"])
    run = update_run(run_id, mutate)
    wake()
    return run


# Dang ky handler cua tung loai (cac module nay import cac ham o tren).
from src.utils import pipeline_sv, pipeline_type1  # noqa: E402,F401
