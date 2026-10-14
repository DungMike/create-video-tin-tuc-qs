"""Loai 1 cua pipeline: anh minh hoa qua TST Studio (docs/integration/04 §7, §9, §10).

TST_STORY -> AI -> BUILD -> GATE_PROMPTS -> CHARACTERS -> [GATE_CHARACTERS] -> SCENES -> VIDEO -> COLLECT

- Moi buoc chay qua ``POST /api/projects/<pid>/stories/<sid>/run`` cua TST Studio (job nen) va
  poll ``/api/jobs/<id>``. Job cua TST nam trong RAM: studio restart thi job mat (404) -> handler
  doc lai ``flow --trang-thai`` (qua GET story) va goi lai buoc; moi buoc deu chay tiep an toan.
- Truoc khi tao anh luon co cong duyet prompt (CLAUDE.md cua TST: hoi truoc khi tao anh lan dau).
- Khong bao gio goi ``split`` cho truyen da co segments.json.
"""

from __future__ import annotations

import os
import re
import shutil
import threading

from src.config import Config
from src.utils.logger import logger
from src.utils.pipeline_clients import ServiceError, ServiceUnavailable
from src.utils.pipeline_runner import (
    RETRY_RESETS,
    Context,
    PipelineError,
    advance,
    cancel_hook,
    fail,
    needs_attention,
    set_status,
    stage_handler,
    wake,
)
from src.utils.pipeline_store import append_event, find_item, load_run, touch_item, update_run, utc_now

IMAGE_STEPS = {"characters", "scenes", "regen"}
_PROGRESS_RE = re.compile(r"PROGRESS (\d+)/(\d+)")
_COUNT_RE = re.compile(r"\[(\d+)/(\d+)\]")
_OUTPUT_RE = re.compile(r"^OUTPUT (.+)$")


# --- helpers ---------------------------------------------------------------------------

def _tst(item: dict) -> dict:
    return item.setdefault("tst", {})


def _type1(run: dict) -> dict:
    return (run.get("config") or {}).get("type1") or {}


def _story_id(run: dict, item: dict) -> str:
    tst = _tst(item)
    base = f"{item['slug'][:48]}-{run['runId'][-4:]}"
    attempt = int(tst.get("storyAttempt") or 1)
    return base if attempt <= 1 else f"{base}-r{attempt}"


def _compact_flow(status: dict) -> dict:
    keep = ("next", "segments", "bible", "prompts", "built", "characters", "images")
    return {k: status.get(k) for k in keep if k in status}


def _refresh_flow(ctx: Context, item: dict) -> dict:
    tst = _tst(item)
    story = ctx.tst.story(tst["projectId"], tst["storyId"])
    if story is None:
        raise ServiceError("tst", 404, "story_missing", f"TST không còn truyện {tst['storyId']}.")
    tst["flow"] = _compact_flow(story.get("status") or {})
    tst["dir"] = (story.get("paths") or {}).get("dir") or tst.get("dir")
    return tst["flow"]


def _job_progress(item: dict, job: dict) -> None:
    """Doc dong log moi cua job: PROGRESS a/b (ai, video) hoac [n/total] (tao_anh)."""
    tst = _tst(item)
    lines = job.get("lines") or []
    active = tst.get("activeJob") or {}
    active["since"] = int(active.get("since") or 0) + len(lines)
    tail = (tst.get("logTail") or []) + [line for line in lines if line.strip()]
    tst["logTail"] = tail[-25:]
    for line in reversed(lines):
        match = _PROGRESS_RE.search(line) or _COUNT_RE.search(line)
        if match and int(match.group(2)):
            item.setdefault("progress", {})["percent"] = round(100 * int(match.group(1)) / int(match.group(2)), 1)
            item["progress"]["message"] = line.strip()[:200]
            break
    for line in lines:
        out = _OUTPUT_RE.match(line.strip())
        if out:
            tst["outputPath"] = out.group(1).strip()


def _tail(item: dict, n: int = 8) -> str:
    return "\n".join((_tst(item).get("logTail") or [])[-n:])


def _poll_active(ctx: Context, item: dict) -> str | None:
    """Poll job dang gan voi item. Tra ve status job (running/done/error/cancelled), 'lost', hoac None."""
    tst = _tst(item)
    active = tst.get("activeJob")
    if not active:
        return None
    job = ctx.tst.job(active["jobId"], int(active.get("since") or 0))
    if job is None:
        tst["activeJob"] = None
        return "lost"
    _job_progress(item, job)
    status = job.get("status")
    if status != "running":
        tst["lastJob"] = {"step": active.get("step"), "status": status, "exitCode": job.get("exitCode")}
        tst["activeJob"] = None
    return status


def _start_or_attach(ctx: Context, item: dict, step: str, **params) -> str:
    """Gan vao job dang chay cua truyen (neu dung buoc) hoac chay buoc moi.

    Tra ve 'started', 'attached' hoac 'busy' (truyen dang chay buoc khac).
    """
    tst = _tst(item)
    for job in ctx.tst.jobs(tst["projectId"], tst["storyId"]):
        if job.get("status") == "running":
            if job.get("step") == step:
                tst["activeJob"] = {"step": step, "jobId": job["id"], "since": 0}
                return "attached"
            set_status(item, "running", f"TST đang chạy bước {job.get('step')} cho truyện này")
            return "busy"
    job = ctx.tst.run_step(tst["projectId"], tst["storyId"], step, **params)
    tst["activeJob"] = {"step": step, "jobId": job["id"], "since": 0}
    tst["logTail"] = []
    return "started"


def _other_image_job_running(ctx: Context, item: dict) -> dict | None:
    tst = _tst(item)
    for job in ctx.tst.all_jobs():
        if job.get("status") == "running" and job.get("step") in IMAGE_STEPS and not (
            job.get("projectId") == tst.get("projectId") and job.get("storyId") == tst.get("storyId")
        ):
            return job
    return None


def _require_tst_online(ctx: Context) -> None:
    state = ctx.tst.tst_state()
    if not state.get("online"):
        raise ServiceUnavailable("tst", "app_offline", "App TST chưa mở (tst-bridge\\start-with-api.ps1).")
    if not state.get("loggedIn"):
        raise ServiceUnavailable("tst", "not_logged_in", "App TST chưa đăng nhập license.")


# --- TST_STORY ---------------------------------------------------------------------------

@stage_handler("TST_STORY")
def handle_tst_story(ctx: Context, run: dict, item: dict) -> None:
    config = _type1(run)
    tst = _tst(item)
    pid = tst.get("projectId") or config.get("tstProjectId")
    sid = tst.get("storyId") or _story_id(run, item)
    tst.update(projectId=pid, storyId=sid)
    story = ctx.tst.story(pid, sid)
    if story is None:
        if not ctx.can_start():
            return
        set_status(item, "running", "Tạo truyện + chia timeline trong TST Studio")
        story = ctx.tst.create_story(pid, sid, item.get("title") or item["slug"], item["files"]["srt"], item["files"]["audio"])
        sid = story.get("id") or sid
        tst["storyId"] = sid
    tst["url"] = f"{Config.TST_STUDIO_URL}/p/{pid}/s/{sid}"
    if config.get("lang") and not tst.get("langSet"):
        ctx.tst.patch_story(pid, sid, {"lang": config["lang"]})
        tst["langSet"] = True
    flow = _compact_flow(story.get("status") or {})
    tst["flow"] = flow
    if flow.get("next") in (None, "split", "error"):
        fail(item, "tst_split_failed", "TST chưa chia được timeline cho truyện này.")
        return
    advance(item, f"Truyện {sid}: {flow.get('segments') or '?'} đoạn ảnh")


# --- AI + BUILD --------------------------------------------------------------------------

@stage_handler("AI")
def handle_ai(ctx: Context, run: dict, item: dict) -> None:
    tst = _tst(item)
    status = _poll_active(ctx, item)
    if status == "running":
        set_status(item, "running", item.get("progress", {}).get("message") or "AI đang viết bible + prompt")
        return
    if status == "error":
        last = tst.get("lastJob") or {}
        if last.get("exitCode") == 4:
            needs_attention(item, "claude_missing", "Không tìm thấy Claude CLI trên máy chạy TST Studio (claude install / CLAUDE_BIN).")
        else:
            needs_attention(item, "ai_failed", f"Bước AI lỗi:\n{_tail(item)}")
        return
    if status == "cancelled":
        needs_attention(item, "ai_cancelled", "Job AI đã bị dừng.")
        return

    flow = _refresh_flow(ctx, item)
    if flow.get("next") not in ("bible", "prompts", "build"):
        advance(item, "AI đã viết xong bible + prompt")
        return
    if status == "done":
        needs_attention(item, "ai_incomplete", f"AI chạy xong nhưng vẫn thiếu ({flow.get('next')}).\n{_tail(item)}")
        return
    if not ctx.acquire("ai"):
        set_status(item, "pending", "Chờ lượt AI (mỗi lúc 1 truyện)")
        return
    params = {}
    if tst.get("aiNote"):
        params["note"] = tst["aiNote"]
    if (_type1(run).get("ai") or {}).get("model"):
        params["model"] = _type1(run)["ai"]["model"]
    result = _start_or_attach(ctx, item, "ai", **params)
    if result != "busy":
        set_status(item, "running", "AI đang viết bible + prompt", percent=0)


@stage_handler("BUILD")
def handle_build(ctx: Context, run: dict, item: dict) -> None:
    status = _poll_active(ctx, item)
    if status == "running":
        set_status(item, "running", "Đang build prompts.json")
        return
    if status in ("error", "cancelled"):
        needs_attention(item, "build_failed", f"Build lỗi:\n{_tail(item)}")
        return
    flow = _refresh_flow(ctx, item)
    if flow.get("next") in ("bible", "prompts"):
        item["stage"] = "AI"  # thieu bible/prompt (vd nguoi sua tay): quay lai AI
        set_status(item, "pending", "Thiếu bible/prompt, chạy lại AI")
        return
    if flow.get("next") == "build":
        if status == "done":
            needs_attention(item, "build_failed", f"Build chưa ghi được prompts.json:\n{_tail(item)}")
            return
        if _start_or_attach(ctx, item, "build") != "busy":
            set_status(item, "running", "Đang build prompts.json")
        return
    advance(item, "Đã build prompts.json")


# --- cong duyet ---------------------------------------------------------------------------

def _image_counts(item: dict) -> tuple[int, int]:
    flow = _tst(item).get("flow") or {}
    return int((flow.get("characters") or {}).get("total") or 0), int((flow.get("images") or {}).get("total") or 0)


@stage_handler("GATE_PROMPTS")
def handle_gate_prompts(ctx: Context, run: dict, item: dict) -> None:
    if (_tst(item).get("approvals") or {}).get("prompts"):
        advance(item, "Prompt đã được duyệt")
        return
    _refresh_flow(ctx, item)
    chars, scenes = _image_counts(item)
    set_status(item, "awaiting_review", f"Chờ duyệt prompt: sẽ tạo {chars} ảnh nhân vật + {scenes} ảnh cảnh (tốn lượt TST)")


@stage_handler("GATE_CHARACTERS")
def handle_gate_characters(ctx: Context, run: dict, item: dict) -> None:
    gates = _type1(run).get("gates") or {}
    if not gates.get("characters") or (_tst(item).get("approvals") or {}).get("characters"):
        advance(item, "Bỏ qua duyệt ảnh nhân vật" if not gates.get("characters") else "Ảnh nhân vật đã được duyệt")
        return
    set_status(item, "awaiting_review", "Chờ duyệt ảnh nhân vật trước khi tạo ảnh cảnh")


# --- tao anh -------------------------------------------------------------------------------

def _image_stage(ctx: Context, run: dict, item: dict, *, step: str, key: str, rounds_key: str,
                 max_rounds: int, label: str) -> None:
    tst = _tst(item)
    status = _poll_active(ctx, item)
    if status == "running":
        set_status(item, "running", item.get("progress", {}).get("message") or label)
        return
    if status == "cancelled":
        needs_attention(item, f"{step}_cancelled", "Job tạo ảnh đã bị dừng.")
        return

    flow = _refresh_flow(ctx, item)
    counts = flow.get(key) or {}
    done, total = int(counts.get("done") or 0), int(counts.get("total") or 0)
    if total == 0 or done >= total:
        advance(item, f"{label}: đủ {done}/{total}")
        return
    rounds = int(tst.get(rounds_key) or 0)
    if rounds >= max_rounds:
        needs_attention(item, f"{step}_missing", f"{label}: còn thiếu {total - done}/{total} sau {rounds} lượt.\n{_tail(item)}")
        return
    _require_tst_online(ctx)
    other = _other_image_job_running(ctx, item)
    if other:
        set_status(item, "pending", f"TST đang tạo ảnh cho {other.get('projectId')}/{other.get('storyId')}, chờ lượt")
        return
    if not ctx.acquire("tst_images"):
        set_status(item, "pending", "Chờ lượt tạo ảnh (mỗi lúc 1 truyện)")
        return
    result = _start_or_attach(ctx, item, step)
    if result == "started":
        tst[rounds_key] = rounds + 1
    if result != "busy":
        set_status(item, "running", f"{label}: {done}/{total}", percent=100 * done / total)


@stage_handler("CHARACTERS")
def handle_characters(ctx: Context, run: dict, item: dict) -> None:
    active = (_tst(item).get("activeJob") or {}).get("step")
    if active == "regen":  # tao lai anh nhan vat theo yeu cau o cong duyet
        status = _poll_active(ctx, item)
        if status == "running":
            set_status(item, "running", item.get("progress", {}).get("message") or "Tạo lại ảnh nhân vật")
            return
    _image_stage(ctx, run, item, step="characters", key="characters", rounds_key="charRounds",
                 max_rounds=2, label="Ảnh nhân vật")


@stage_handler("SCENES")
def handle_scenes(ctx: Context, run: dict, item: dict) -> None:
    _image_stage(ctx, run, item, step="scenes", key="images", rounds_key="sceneRounds",
                 max_rounds=Config.PIPELINE_SCENE_RETRY_ROUNDS, label="Ảnh cảnh")


# --- dung video + gom ------------------------------------------------------------------------

def _sv_rendering() -> bool:
    from src.utils.story_video_batch import queue_snapshot

    return bool(queue_snapshot().get("activeBatchId"))


@stage_handler("VIDEO")
def handle_video(ctx: Context, run: dict, item: dict) -> None:
    tst = _tst(item)
    status = _poll_active(ctx, item)
    if status == "running":
        set_status(item, "running", item.get("progress", {}).get("message") or "Đang dựng video")
        return
    if status in ("error", "cancelled"):
        needs_attention(item, "video_failed", f"Dựng video lỗi:\n{_tail(item)}")
        return
    if status == "done":
        output = tst.pop("outputPath", None)
        if not output or not os.path.isfile(output):
            output = _latest_render(ctx, item)
        if not output:
            needs_attention(item, "video_missing", "Dựng video xong nhưng không thấy file mp4.")
            return
        tst["video"] = output
        advance(item, "Đã dựng xong video")
        return
    if status == "lost":
        set_status(item, "pending", "Mất job dựng video (TST Studio khởi động lại?), chạy lại")
    if Config.PIPELINE_RENDER_EXCLUSIVE and _sv_rendering():
        set_status(item, "pending", "Chờ hàng đợi render clip nền rảnh")
        return
    if not ctx.acquire("render1"):
        set_status(item, "pending", "Chờ lượt dựng video (mỗi lúc 1 truyện)")
        return
    video = _type1(run).get("video") or {}
    params = {"from": str(video.get("from") or "0")}
    if video.get("to"):
        params["to"] = str(video["to"])
    if _start_or_attach(ctx, item, "video", **params) != "busy":
        set_status(item, "running", "Đang dựng video", percent=0)


def _latest_render(ctx: Context, item: dict) -> str | None:
    tst = _tst(item)
    renders = (ctx.tst.story_video(tst["projectId"], tst["storyId"]) or {}).get("renders") or []
    if not renders or not tst.get("dir"):
        return None
    name = renders[0].get("file")
    stem = os.path.basename(tst["dir"].rstrip("\\/"))
    candidates = [os.path.join(tst["dir"], f"{stem}_video", name)]
    return next((path for path in candidates if os.path.isfile(path)), None)


_copies: dict[str, threading.Thread] = {}
_copy_errors: dict[str, str] = {}
_copy_lock = threading.Lock()


def _collect_dest(run: dict, item: dict) -> str:
    from src.utils.file_manager import resolve_output_drive
    from src.utils.story_video_batch import batch_output_dir

    output = (run.get("config") or {}).get("output") or {}
    drive = resolve_output_drive(str(output.get("drive") or ""))
    folder = batch_output_dir({"outputSubdir": output.get("subdir") or f"pipeline-{run['runId']}", "outputDrive": drive})
    return os.path.join(folder, f"{item['slug']}.mp4")


def _copy_worker(key: str, src: str, dest: str, mode: str) -> None:
    part = dest + ".part"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if mode == "link":
            try:
                if os.path.exists(dest):
                    os.remove(dest)
                os.link(src, dest)
                return
            except OSError:
                pass  # khac o dia -> copy
        shutil.copyfile(src, part)
        os.replace(part, dest)
    except OSError as exc:
        _copy_errors[key] = str(exc)
        try:
            os.remove(part)
        except OSError:
            pass


@stage_handler("COLLECT")
def handle_collect(ctx: Context, run: dict, item: dict) -> None:
    tst = _tst(item)
    src = tst.get("video")
    mode = Config.PIPELINE_COLLECT_MODE if Config.PIPELINE_COLLECT_MODE in ("link", "copy", "none") else "link"
    if not src or not os.path.isfile(src):
        needs_attention(item, "video_missing", "Không thấy video đã dựng để gom.")
        return
    if mode == "none":
        item["files"]["video"] = src
        advance(item, "Giữ video trong thư mục truyện TST")
        return
    try:
        dest = _collect_dest(run, item)
    except ValueError as exc:
        needs_attention(item, "invalid_output_drive", str(exc))
        return
    key = f"{run['runId']}/{item['itemId']}"
    with _copy_lock:
        thread = _copies.get(key)
        if thread and thread.is_alive():
            set_status(item, "running", "Đang chép video vào thư mục xuất")
            return
        if key in _copy_errors:
            needs_attention(item, "collect_failed", f"Không chép được video: {_copy_errors.pop(key)}")
            _copies.pop(key, None)
            return
        if thread is not None and os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(src):
            _copies.pop(key, None)
            item["files"]["video"] = dest
            advance(item, "Đã gom video vào thư mục xuất")
            return
        if os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(src):
            item["files"]["video"] = dest
            advance(item, "Video đã có trong thư mục xuất")
            return
        thread = threading.Thread(target=_copy_worker, args=(key, src, dest, mode), daemon=True, name=f"collect-{key}")
        _copies[key] = thread
        thread.start()
    set_status(item, "running", "Đang chép video vào thư mục xuất")


# --- review / approve / reject -------------------------------------------------------------

GATES = {"prompts": "GATE_PROMPTS", "characters": "GATE_CHARACTERS"}


def _require_gate(run: dict, item_id: str, gate: str) -> dict:
    if gate not in GATES:
        raise PipelineError("invalid_gate", "gate phải là prompts hoặc characters.")
    item = find_item(run, item_id)
    if item is None:
        raise PipelineError("item_not_found", "Không tìm thấy item.", 404)
    if item.get("stage") != GATES[gate] or item.get("status") != "awaiting_review":
        raise PipelineError("not_awaiting_review", "Item không ở bước duyệt này.", 409)
    return item


def _abs_media(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith("http") else f"{Config.TST_STUDIO_URL}{url}"


def review(run_id: str, item_id: str, gate: str, tst_client=None) -> dict:
    from src.utils.pipeline_clients import TstClient

    run = load_run(run_id)
    if run is None:
        raise PipelineError("run_not_found", "Không tìm thấy run.", 404)
    item = find_item(run, item_id)
    if item is None or int(item.get("type") or 0) != 1 or not _tst(item).get("storyId"):
        raise PipelineError("item_not_found", "Item không phải loại 1 hoặc chưa có truyện TST.", 404)
    tst = _tst(item)
    client = tst_client or TstClient()
    story = client.story(tst["projectId"], tst["storyId"])
    if story is None:
        raise PipelineError("story_missing", "TST không còn truyện này.", 404)
    characters = [
        {**{k: c.get(k) for k in ("id", "name", "prompt_name", "role", "description")}, "image": _abs_media(c.get("image"))}
        for c in story.get("characters") or []
    ]
    scenes = [
        {**{k: s.get(k) for k in ("id", "start", "end", "duration", "text", "prompt", "characters")}, "image": _abs_media(s.get("image"))}
        for s in story.get("segments") or []
    ]
    status = story.get("status") or {}
    return {
        "gate": gate,
        "stage": item.get("stage"),
        "status": item.get("status"),
        "counts": {
            "characters": int((status.get("characters") or {}).get("total") or len(characters)),
            "scenes": int((status.get("images") or {}).get("total") or len(scenes)),
        },
        "setting": story.get("setting"),
        "characters": characters,
        "scenes": scenes,
        "tstUrl": tst.get("url"),
    }


def approve(run_id: str, item_id: str, gate: str) -> dict:
    def mutate(run):
        item = _require_gate(run, item_id, gate)
        chars, scenes = _image_counts(item)
        _tst(item).setdefault("approvals", {})[gate] = {"at": utc_now(), "images": chars + scenes if gate == "prompts" else chars}
        set_status(item, "pending", "Đã duyệt")
        touch_item(item)
        append_event(run, item_id, item["stage"], "approved", f"Duyệt {gate}")

    run = update_run(run_id, mutate)
    if run is None:
        raise PipelineError("run_not_found", "Không tìm thấy run.", 404)
    wake()
    return run


def reject(run_id: str, item_id: str, gate: str, action: str, *, note: str = "",
           batch_nos: list[int] | None = None, character_ids: list[str] | None = None, tst_client=None) -> dict:
    from src.utils.pipeline_clients import TstClient

    run = load_run(run_id)
    if run is None:
        raise PipelineError("run_not_found", "Không tìm thấy run.", 404)
    item = _require_gate(run, item_id, gate)
    tst = _tst(item)
    client = tst_client or TstClient()
    allowed = {"prompts": {"rerun_ai", "redo_batches", "manual"}, "characters": {"regen_characters", "rerun_ai", "manual"}}
    if action not in allowed[gate]:
        raise PipelineError("invalid_action", f"Thao tác {action} không dùng được ở bước duyệt {gate}.")

    job = None
    try:
        if action == "rerun_ai":
            client.ai_reset(tst["projectId"], tst["storyId"], "all")
        elif action == "redo_batches":
            nos = sorted({int(n) for n in batch_nos or []})
            if not nos:
                raise PipelineError("batch_required", "Cần chọn ít nhất 1 lô.")
            client.ai_reset(tst["projectId"], tst["storyId"], "batches", nos)
        elif action == "regen_characters":
            ids = [str(c).strip() for c in character_ids or [] if re.fullmatch(r"[\w-]+", str(c).strip())]
            if not ids:
                raise PipelineError("character_required", "Cần chọn ít nhất 1 nhân vật.")
            job = client.run_step(tst["projectId"], tst["storyId"], "regen", ids=",".join(f"nv:{c}" for c in ids))
    except ServiceError as exc:
        raise PipelineError(exc.code, f"TST Studio từ chối: {exc}", 409) from exc
    except ServiceUnavailable as exc:
        raise PipelineError("tst_unavailable", str(exc), 503) from exc

    def mutate(run):
        current = _require_gate(run, item_id, gate)
        ctst = _tst(current)
        ctst.setdefault("reviews", []).append({"gate": gate, "action": action, "note": note, "at": utc_now()})
        if action in ("rerun_ai", "redo_batches"):
            ctst["aiNote"] = note.strip() or ctst.get("aiNote")
            ctst["activeJob"] = None
            ctst["approvals"] = {}
            current["stage"] = "AI"
            set_status(current, "pending", "Chạy lại AI theo yêu cầu duyệt")
        elif action == "regen_characters":
            ctst["activeJob"] = {"step": "regen", "jobId": job["id"], "since": 0}
            ctst.get("approvals", {}).pop("characters", None)
            current["stage"] = "CHARACTERS"
            set_status(current, "running", "Đang tạo lại ảnh nhân vật")
        else:  # manual: giu cho duyet, nguoi sua trong TST Studio
            current.setdefault("progress", {})["message"] = "Đang sửa tay trong TST Studio — bấm Duyệt khi xong"
        touch_item(current)
        append_event(run, item_id, current["stage"], current["status"], f"Không duyệt {gate}: {action}" + (f" — {note}" if note else ""))

    run = update_run(run_id, mutate)
    wake()
    return run


# --- huy / chay lai ------------------------------------------------------------------------

@cancel_hook
def _cancel_tst(ctx: Context, run: dict, item: dict) -> None:
    active = (item.get("tst") or {}).get("activeJob")
    if int(item.get("type") or 0) == 1 and active and active.get("jobId"):
        ctx.tst.cancel_job(active["jobId"])


def _reset_tst(item: dict, stage: str) -> None:
    if int(item.get("type") or 0) != 1:
        return
    tst = _tst(item)
    tst["activeJob"] = None
    tst.pop("lastJob", None)
    if stage in ("INGEST", "TTS", "SRT_CHECK", "TST_STORY"):
        # Audio/srt moi -> truyen TST moi (khong chia lai timeline cua truyen cu).
        if tst.get("storyId"):
            tst["storyAttempt"] = int(tst.get("storyAttempt") or 1) + 1
        for key in ("storyId", "flow", "approvals", "url", "dir", "langSet", "video"):
            tst.pop(key, None)
    if stage in ("CHARACTERS",):
        tst["charRounds"] = 0
    if stage in ("SCENES",):
        tst["sceneRounds"] = 0
    if stage in ("VIDEO", "COLLECT"):
        item.setdefault("files", {})["video"] = None


RETRY_RESETS["tst"] = _reset_tst
logger.debug("[Pipeline] type1 handlers registered")
