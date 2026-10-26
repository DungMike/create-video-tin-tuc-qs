"""Reconciler pipeline: INGEST -> TTS -> SRT_CHECK, idempotency, trang thai, thao tac."""

import os

import pytest

from pipeline_fakes import FakeDgt, FakeTst
from src.config import Config
from src.utils import pipeline_runner as runner
from src.utils import pipeline_store as store

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "pipeline")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DIR", str(tmp_path / "pipeline"))
    monkeypatch.setattr(Config, "PIPELINE_TTS_RETRY", 1)
    dgt, tst = FakeDgt(), FakeTst()
    runner.set_client_overrides(_dgt=dgt, _tst=tst)
    # ffprobe khong can trong test: audio gia dai 10 s.
    monkeypatch.setattr("src.processors.audio_utils.get_audio_duration", lambda _p: 10.0)
    yield {"tmp": tmp_path, "dgt": dgt, "tst": tst}
    runner.set_client_overrides()


def _src(tmp_path, name="Tập 1.txt", text="Xin chào.\n\nĐoạn hai."):
    path = tmp_path / "in" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _config(**extra):
    config = {"tts": {"voiceRef": "app-clone:1"}, "type2": {"sharedConfig": {"libraryIds": ["lib-1"]}}}
    config.update(extra)
    return config


def _create(env, sources=None, config=None):
    sources = sources or [{"path": _src(env["tmp"]), "type": 2}]
    return runner.create_run("Thử", config or _config(), sources, run_id=store.new_run_id())


def _item(run_id, index=0):
    return store.load_run(run_id)["items"][index]


def test_create_run_ingests_and_reaches_tts(env):
    docx = env["tmp"] / "in" / "Tập 2.docx"
    docx.parent.mkdir(parents=True, exist_ok=True)
    docx.write_bytes(open(os.path.join(FIXTURES, "basic.docx"), "rb").read())
    run = _create(env, [{"path": _src(env["tmp"]), "type": 2}, {"path": str(docx), "type": 2}])
    items = run["items"]
    assert [i["slug"] for i in items] == ["tap-1", "tap-2"]
    assert all(i["stage"] == "TTS" and i["status"] == "pending" for i in items)
    assert open(items[1]["files"]["txt"], "rb").read() == open(os.path.join(FIXTURES, "basic.expected.txt"), "rb").read()
    assert docx.exists()
    assert os.path.dirname(items[0]["files"]["txt"]).endswith(os.path.join("items", "it-01"))


def test_create_run_validation(env):
    with pytest.raises(runner.PipelineError) as info:
        runner.create_run("x", {"tts": {}}, [{"path": _src(env["tmp"]), "type": 2}], run_id=store.new_run_id())
    assert info.value.code == "voice_required"
    with pytest.raises(runner.PipelineError) as info:
        runner.create_run("x", {"tts": {"voiceRef": "v"}}, [{"path": _src(env["tmp"]), "type": 1}],
                          run_id=store.new_run_id())
    assert info.value.code == "tst_project_required"
    # Loại 1: video có chủ đề riêng thì không cần chủ đề mặc định; id chủ đề phải hợp lệ
    run = runner.create_run("x", {"tts": {"voiceRef": "v"}}, [{"path": _src(env["tmp"]), "type": 1, "tstProjectId": "an-thai"}],
                            run_id=store.new_run_id())
    assert run["items"][0]["tst"] == {"projectId": "an-thai"}
    with pytest.raises(runner.PipelineError) as info:
        runner.create_run("x", {"tts": {"voiceRef": "v"}}, [{"path": _src(env["tmp"]), "type": 1, "tstProjectId": "../x"}],
                          run_id=store.new_run_id())
    assert info.value.code == "invalid_tst_project"
    with pytest.raises(runner.PipelineError) as info:
        runner.create_run("x", {"tts": {"voiceRef": "v"}}, [{"path": _src(env["tmp"]), "type": 2}],
                          run_id=store.new_run_id())
    assert info.value.code == "library_required"


def test_ingest_failure_marks_item_failed_only(env):
    bad = env["tmp"] / "in" / "rong.txt"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("   ", encoding="utf-8")
    run = _create(env, [{"path": str(bad), "type": 2}, {"path": _src(env["tmp"]), "type": 2}])
    assert run["items"][0]["status"] == "failed" and run["items"][0]["error"]["code"] == "empty_text"
    assert run["items"][1]["stage"] == "TTS"


def test_tts_submit_poll_and_srt_check(env):
    run = _create(env)
    dgt = env["dgt"]
    runner.tick()
    item = _item(run["runId"])
    assert item["status"] == "running" and item["tts"]["jobId"] == "job1"
    assert dgt.calls[0][3] == f"{run['runId']}-it-01-tts-1"
    assert dgt.calls[0][1] == item["files"]["txt"]

    runner.tick()
    assert "Chờ tới lượt" in _item(run["runId"])["progress"]["message"]

    dgt.finish("job1")
    runner.tick()
    item = _item(run["runId"])
    assert item["files"]["audio"].endswith("tap-1.mp3") and item["files"]["srt"].endswith("tap-1.srt")
    assert item["media"]["cues"] == 1
    # SRT_CHECK xong -> SV_RENDER (loai 2)
    assert item["stage"] == "SV_RENDER"
    events = store.read_events(run["runId"])
    assert any(e["stage"] == "SRT_CHECK" or e["stage"] == "SV_RENDER" for e in events)


def test_tts_resubmit_after_restart_reuses_idempotency_key(env):
    run = _create(env)
    dgt = env["dgt"]
    runner.tick()
    # Gia lap server chet sau khi gui ma chua ghi jobId: xoa jobId khoi run.json.
    store.update_run(run["runId"], lambda r: r["items"][0]["tts"].update(jobId=None))
    runner.tick()
    assert len(dgt.jobs) == 1  # khoa giong het -> DGT tra lai job cu
    assert _item(run["runId"])["tts"]["jobId"] == "job1"


def test_dgt_down_and_needs_attention_are_waiting_service(env):
    run = _create(env)
    dgt = env["dgt"]
    dgt.down = True
    runner.tick()
    item = _item(run["runId"])
    assert item["status"] == "waiting_service" and item["waitingReason"] == "dgt:api_down"

    dgt.down = False
    runner.tick()
    dgt.set_state("job1", "needs_attention", error_code="desktop_app_not_running", error_message="DGT chưa mở")
    runner.tick()
    item = _item(run["runId"])
    assert item["status"] == "waiting_service" and item["waitingReason"] == "dgt:desktop_app_not_running"

    dgt.finish("job1")
    runner.tick()
    assert _item(run["runId"])["stage"] == "SV_RENDER"


def test_tts_failure_retries_with_new_key_then_fails(env):
    run = _create(env)
    dgt = env["dgt"]
    runner.tick()
    dgt.set_state("job1", "failed", error_code="audio_invalid", error_message="hỏng")
    runner.tick()
    item = _item(run["runId"])
    assert item["tts"]["attempt"] == 2 and item["status"] == "pending"
    runner.tick()
    item = _item(run["runId"])
    assert item["tts"]["jobId"] == "job2"
    assert dgt.calls[-1][3].endswith("-tts-2")
    dgt.set_state("job2", "failed", error_code="audio_invalid", error_message="hỏng")
    runner.tick()
    item = _item(run["runId"])
    assert item["status"] == "failed" and item["error"]["code"] == "audio_invalid"
    assert store.load_run(run["runId"])["status"] == "completed_with_errors"


def test_srt_mismatch_fails(env, monkeypatch):
    monkeypatch.setattr("src.processors.audio_utils.get_audio_duration", lambda _p: 3.0)
    run = _create(env)
    runner.tick()
    env["dgt"].finish("job1")
    runner.tick()
    item = _item(run["runId"])
    assert item["status"] == "failed" and item["error"]["code"] == "srt_mismatch"


def test_pause_blocks_new_work(env):
    run = _create(env)
    runner.pause_run(run["runId"])
    runner.tick()
    assert env["dgt"].calls == []
    runner.resume_run(run["runId"])
    runner.tick()
    assert len(env["dgt"].calls) == 1


def test_cancel_item_cancels_dgt_job(env):
    run = _create(env)
    runner.tick()
    runner.cancel_item(run["runId"], "it-01")
    assert env["dgt"].cancelled == ["job1"]
    assert _item(run["runId"])["status"] == "cancelled"
    assert store.load_run(run["runId"])["status"] == "completed_with_errors"


def test_retry_from_tts_uses_new_attempt(env):
    run = _create(env)
    runner.tick()
    env["dgt"].set_state("job1", "failed", error_code="x")
    runner.tick()  # danh dau gui lai lan 2 (PIPELINE_TTS_RETRY=1)
    runner.tick()  # gui job2
    env["dgt"].set_state("job2", "failed", error_code="x")
    runner.tick()
    assert _item(run["runId"])["status"] == "failed"
    runner.retry_item(run["runId"], "it-01")
    item = _item(run["runId"])
    assert item["status"] == "pending" and item["tts"]["attempt"] == 3 and item["tts"]["jobId"] is None
    runner.tick()
    assert env["dgt"].calls[-1][3].endswith("-tts-3")


def test_route_change_during_handler_wins(env):
    run = _create(env)
    original = runner.HANDLERS["TTS"]

    def racing_handler(ctx, run_snapshot, item):
        store.update_run(run["runId"], lambda r: store.touch_item(r["items"][0]))  # route sua item
        original(ctx, run_snapshot, item)

    runner.HANDLERS["TTS"] = racing_handler
    try:
        runner.tick()
    finally:
        runner.HANDLERS["TTS"] = original
    item = _item(run["runId"])
    assert item.get("tts", {}).get("jobId") is None  # ket qua handler bi bo
    runner.tick()
    assert _item(run["runId"])["tts"]["jobId"] == "job1"  # tick sau lam lai, cung khoa


def test_lane_limits(env, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_AI_CONCURRENCY", 1)
    ctx = runner.Context(lanes_used={"ai": 1})
    assert ctx.acquire("ai") is False
    ctx = runner.Context(lanes_used={})
    assert ctx.acquire("ai") is True and ctx.acquire("ai") is False
    assert runner.Context(paused=True).acquire("tst_images") is False


def _media(tmp_path, stem="Tập 7", srt_text="1\n00:00:00,000 --> 00:00:09,000\nXin chào\n"):
    folder = tmp_path / "media"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.mp3").write_bytes(b"ID3fake")
    (folder / f"{stem}.srt").write_text(srt_text, encoding="utf-8")
    return {"path": str(folder / f"{stem}.mp3"), "srtPath": str(folder / f"{stem}.srt"), "type": 2}


def test_media_item_skips_tts_and_cannot_retry_into_it(env):
    run = runner.create_run("x", {"tts": {}, "type2": {"sharedConfig": {"libraryIds": ["lib-1"]}}},
                            [_media(env["tmp"])], run_id=store.new_run_id())
    rid = run["runId"]
    assert run["config"]["tts"].get("voiceRef") is None
    assert runner.stages_for(run["items"][0]) == ["SRT_CHECK", "SV_RENDER", "DONE"]
    events = store.read_events(rid)
    assert events[0]["stage"] == "SRT_CHECK" and "bỏ qua tạo giọng đọc" in events[0]["message"]

    ctx = runner._make_context(False, {}, {"_dgt": env["dgt"], "_tst": env["tst"]})
    after, _ = runner.process_item(ctx, run, run["items"][0])
    assert after["stage"] == "SV_RENDER" and after["media"]["cues"] == 1
    assert not env["dgt"].jobs  # khong goi app TTS

    for stage in ("INGEST", "TTS"):
        with pytest.raises(runner.PipelineError) as info:
            runner.retry_item(rid, "it-01", stage)
        assert info.value.code == "invalid_stage"


def test_media_item_bad_srt_fails_at_srt_check(env, monkeypatch):
    source = _media(env["tmp"], "Hỏng", "1\n00:00:00,000 --> 00:00:30,000\nquá dài\n")
    run = runner.create_run("x", {"type2": {"sharedConfig": {"libraryIds": ["lib-1"]}}}, [source],
                            run_id=store.new_run_id())
    runner.tick()
    item = _item(run["runId"])
    assert item["status"] == "failed" and item["error"]["code"] == "srt_mismatch"
    retried = runner.retry_item(run["runId"], "it-01")
    assert retried["items"][0]["stage"] == "SRT_CHECK" and retried["items"][0]["status"] == "pending"


def test_retry_items_restarts_attention_and_failed_but_not_cancelled(env):
    sources = [{"path": _src(env["tmp"], f"Tập {i}.txt"), "type": 2} for i in (1, 2, 3, 4)]
    run = _create(env, sources)
    rid = run["runId"]

    def mark(r):
        r["items"][0].update(status="needs_attention", error={"code": "x"})
        r["items"][1].update(status="failed", stage="SRT_CHECK", error={"code": "y"})
        r["items"][2].update(status="cancelled")
    store.update_run(rid, mark)

    after = runner.retry_items(rid)
    assert [(i["stage"], i["status"]) for i in after["items"]] == [
        ("TTS", "pending"), ("SRT_CHECK", "pending"), ("TTS", "cancelled"), ("TTS", "pending")]
    assert after["items"][0]["error"] is None
    with pytest.raises(runner.PipelineError) as info:
        runner.retry_items(rid)
    assert info.value.code == "nothing_to_retry"
