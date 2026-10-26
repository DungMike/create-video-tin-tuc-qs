"""Loai 2 cua pipeline: dung video clip nen bang hang doi batch cua chinh app nay.

- Gui batch qua route co san ``/api/story-video/batch/create`` (goi trong cung process
  bang ``app.test_client()``), de dung lai nguyen phan kiem tra, xoay vong decor/
  waveform/CTA/phu de va hang doi. Cac helper cua route tra ``jsonify`` nen chua tach
  thanh ham service (docs/integration/04 §8, ke hoach P5).
- Micro-batch: moi tick, gom moi item loai 2 da san sang cua 1 run thanh MOT batch,
  chi gui khi run khong con batch nao dang xep hang chua chay.
- Theo doi bang ``load_batch_progress`` (doc progress.json, khong qua HTTP).
"""

from __future__ import annotations

import copy
import json
import os

from src.config import Config
from src.utils.logger import logger
from src.utils.pipeline_runner import (
    RETRY_RESETS,
    Context,
    advance,
    cancel_hook,
    fail,
    needs_attention,
    run_hook,
    set_status,
    stage_handler,
)
from src.utils.pipeline_store import append_event, load_run, run_lock, save_run, touch_item

SV_RESUBMIT_LIMIT = 1  # story loi thi tu gui lai 1 lan trong batch moi


def submit_story_batch(items: list[dict], shared_config: dict) -> tuple[int, dict]:
    """Goi route batch/create trong cung process. Tra ve (status_code, body)."""
    from src.web_app import app  # import muon: web_app import route pipeline -> runner -> module nay

    payload = {"items": items, "sharedConfig": shared_config}
    with app.test_client() as client:
        response = client.post(
            "/api/story-video/batch/create",
            data={"payload": json.dumps(payload, ensure_ascii=False)},
            content_type="multipart/form-data",
        )
    return response.status_code, response.get_json(silent=True) or {}


def _shared_config(run: dict) -> dict:
    config = copy.deepcopy((run.get("config") or {}).get("type2", {}).get("sharedConfig") or {})
    output = (run.get("config") or {}).get("output") or {}
    if output.get("drive"):
        config["outputDrive"] = output["drive"]
    config["outputSubdir"] = output.get("subdir") or f"pipeline-{run['runId']}"
    config["optimizeMode"] = False  # khong tam dung app khac (DGT/TST/claude) khi render
    return config


def _eligible(item: dict) -> bool:
    return (
        int(item.get("type") or 2) == 2
        and item.get("stage") == "SV_RENDER"
        and item.get("status") in ("pending", "waiting_service")
        and not (item.get("sv") or {}).get("batchId")
    )


def _queued_batch_ids() -> set[str]:
    from src.utils.story_video_batch import queue_snapshot

    return set(queue_snapshot().get("queuedIds") or [])


@run_hook
def submit_ready_type2(ctx: Context, run_id: str) -> None:
    run = load_run(run_id)
    if not run or run.get("status") in ("paused", "cancelled") or ctx.paused:
        return
    ready = [item for item in run.get("items", []) if _eligible(item)]
    if not ready:
        return
    run_batches = {(item.get("sv") or {}).get("batchId") for item in run.get("items", [])}
    if run_batches & _queued_batch_ids():
        return  # da co batch cua run dang cho: item moi gom vao batch sau

    _recover_orphans(run, ready)
    ready = [item for item in ready if not (item.get("sv") or {}).get("batchId")]
    if not ready:
        return

    payload_items = [
        {
            "inputType": "audio_file",
            "inputValue": item["files"]["audio"],
            "subtitleFile": item["files"]["srt"],
            "outputName": item["slug"],
        }
        for item in ready
    ]
    status, body = submit_story_batch(payload_items, _shared_config(run))
    ids = {item["itemId"] for item in ready}

    def apply(run):
        for item in run.get("items", []):
            if item["itemId"] not in ids:
                continue
            if status == 202:
                if not _eligible(item):
                    continue
                sv = item.setdefault("sv", {})
                sv.update(batchId=body.get("batchId"), outputName=item["slug"], storyId=None)
                set_status(item, "running", f"Đã vào hàng đợi render (batch {body.get('batchId')})", percent=0)
                append_event(run, item["itemId"], "SV_RENDER", "running", f"Gửi batch {body.get('batchId')}")
            else:
                error = body.get("error") or {}
                message = f"Không tạo được batch render: {error.get('message') or status}"
                if status == 503:
                    set_status(item, "waiting_service", message, waiting=f"sv:{error.get('code') or 'unavailable'}")
                else:
                    needs_attention(item, error.get("code") or "sv_submit_failed", message)
                append_event(run, item["itemId"], "SV_RENDER", item["status"], message)
            touch_item(item)
        save_run(run)

    with run_lock(run_id):
        current = load_run(run_id)
        if current is not None:
            apply(current)
    if status == 202:
        logger.info(f"[Pipeline] {run_id}: gui batch {body.get('batchId')} ({len(ready)} video)")
        _cancel_dropped(run_id, body.get("batchId"))


def _cancel_dropped(run_id: str, batch_id: str | None) -> None:
    """Item bi huy trong luc gui batch: huy luon story tuong ung trong batch."""
    from src.utils.story_video_batch import load_batch_progress, request_batch_story_cancel

    run = load_run(run_id)
    progress = load_batch_progress(batch_id) if batch_id else None
    if not run or not progress:
        return
    attached = {(i.get("sv") or {}).get("outputName") for i in run["items"] if (i.get("sv") or {}).get("batchId") == batch_id}
    for story in progress.get("stories", []):
        if story.get("outputName") not in attached:
            request_batch_story_cancel(batch_id, story["storyId"])


def _recover_orphans(run: dict, ready: list[dict]) -> None:
    """Server chet sau khi gui batch ma chua ghi batchId: tim lai batch theo duong dan audio."""
    from src.utils.story_video_batch import load_batch_progress

    root = os.path.join(Config.STORY_VIDEO_DIR, "batches")
    if not os.path.isdir(root):
        return
    def key_of(audio: str, output_name: str) -> tuple[str, str]:
        return os.path.normcase(os.path.abspath(audio)), output_name

    wanted = {key_of(item["files"]["audio"], item["slug"]): item for item in ready if item["files"].get("audio")}
    if not wanted:
        return
    # Chi batch cua chinh run nay (audio co san co the nam ngoai run va dung o nhieu run).
    subdir = _shared_config(run)["outputSubdir"]
    entries = sorted(
        (e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.stat().st_mtime, reverse=True
    )[:50]
    found: dict[tuple[str, str], str] = {}
    for entry in entries:
        progress = load_batch_progress(entry.name) or {}
        if progress.get("outputSubdir") != subdir:
            continue
        for story in progress.get("stories", []):
            if story.get("status") in ("failed", "cancelled", "cancelling"):
                continue
            key = key_of(story.get("input_value") or "", story.get("outputName") or "")
            item = wanted.get(key)
            if item is None or key in found or entry.name in ((item.get("sv") or {}).get("pastBatches") or []):
                continue
            found[key] = entry.name
    if not found:
        return

    def apply(current):
        for key, batch_id in found.items():
            item = next((i for i in current["items"] if i["itemId"] == wanted[key]["itemId"]), None)
            if item and _eligible(item):
                item.setdefault("sv", {}).update(batchId=batch_id, outputName=item["slug"])
                set_status(item, "running", f"Gắn lại batch {batch_id}")
                touch_item(item)
                wanted[key].setdefault("sv", {})["batchId"] = batch_id
        save_run(current)

    with run_lock(run["runId"]):
        current = load_run(run["runId"])
        if current is not None:
            apply(current)


@stage_handler("SV_RENDER")
def handle_sv_render(ctx: Context, run: dict, item: dict) -> None:
    from src.utils.story_video_batch import load_batch_progress
    from src.utils.story_video_pipeline import load_story_progress

    sv = item.setdefault("sv", {})
    batch_id = sv.get("batchId")
    if not batch_id:
        if item.get("status") != "waiting_service":
            set_status(item, "pending", "Chờ gom vào batch render")
        return

    progress = load_batch_progress(batch_id)
    if progress is None:
        sv.update(batchId=None, storyId=None)
        set_status(item, "pending", f"Không thấy batch {batch_id}, gửi lại")
        return
    story = next((s for s in progress.get("stories", []) if s.get("outputName") == sv.get("outputName")), None)
    if story is None:
        needs_attention(item, "sv_story_missing", f"Batch {batch_id} không có video {sv.get('outputName')}.")
        return
    sv["storyId"] = story.get("storyId")
    status = story.get("status")

    if status in ("pending", "queued"):
        position = progress.get("queuePosition")
        set_status(item, "running", "Chờ render" + (f" (vị trí {position} trong hàng)" if position else ""), percent=0)
    elif status in ("running", "cancelling"):
        story_progress = load_story_progress(sv["storyId"]) or {}
        set_status(item, "running", f"Render: {story_progress.get('message') or story_progress.get('stage') or 'đang chạy'}",
                   percent=float(story_progress.get("percent") or 0))
    elif status == "completed":
        result = next((r for r in progress.get("results", []) if r.get("storyId") == sv["storyId"]), {})
        video = result.get("videoPath")
        if not video or not os.path.isfile(video):
            needs_attention(item, "sv_video_missing", "Batch báo xong nhưng không thấy file video.")
            return
        item["files"]["video"] = video
        advance(item, "Đã render xong video")
    elif status == "failed":
        result = next((r for r in progress.get("results", []) if r.get("storyId") == sv["storyId"]), {})
        message = result.get("error") or "Render lỗi"
        resubmits = int(sv.get("resubmits") or 0)
        if resubmits < SV_RESUBMIT_LIMIT:
            sv["pastBatches"] = [*(sv.get("pastBatches") or []), batch_id]
            sv.update(batchId=None, storyId=None, resubmits=resubmits + 1)
            set_status(item, "pending", f"Render lỗi ({message}), gửi lại")
        else:
            fail(item, "sv_render_failed", message)
    elif status == "cancelled":
        fail(item, "sv_cancelled", "Video đã bị huỷ trong hàng đợi render.")
    else:
        set_status(item, "running", f"Render: {status}")


@cancel_hook
def _cancel_sv(ctx: Context, run: dict, item: dict) -> None:
    sv = item.get("sv") or {}
    if item.get("stage") == "SV_RENDER" and sv.get("batchId") and sv.get("storyId"):
        from src.utils.story_video_batch import request_batch_story_cancel

        request_batch_story_cancel(sv["batchId"], sv["storyId"])


def _reset_sv(item: dict, stage: str) -> None:
    if int(item.get("type") or 2) == 2:
        old = item.get("sv") or {}
        past = [*(old.get("pastBatches") or []), *([old["batchId"]] if old.get("batchId") else [])]
        item["sv"] = {"pastBatches": past}  # chay lai: khong gan lai batch cu khi khoi phuc
        if stage in ("INGEST", "TTS", "SRT_CHECK", "SV_RENDER"):
            item.setdefault("files", {})["video"] = None


RETRY_RESETS["sv"] = _reset_sv
