"""Loai 1: TST_STORY -> AI -> BUILD -> duyet -> anh -> video -> gom (TST Studio gia)."""

import os
import time

import pytest

from pipeline_fakes import FakeDgt, FakeTst
from src.config import Config
from src.utils import pipeline_runner as runner
from src.utils import pipeline_store as store
from src.utils import pipeline_type1


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_DIR", str(tmp_path / "pipeline"))
    monkeypatch.setattr(Config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(Config, "OUTPUT_DRIVES", [])
    monkeypatch.setattr(Config, "PIPELINE_SCENE_RETRY_ROUNDS", 2)
    monkeypatch.setattr(Config, "PIPELINE_COLLECT_MODE", "copy")
    monkeypatch.setattr("src.processors.audio_utils.get_audio_duration", lambda _p: 9.0)
    dgt, tst = FakeDgt(), FakeTst()
    runner.set_client_overrides(_dgt=dgt, _tst=tst)
    yield {"tmp": tmp_path, "dgt": dgt, "tst": tst}
    runner.set_client_overrides()


def _run(env, gates=None, lang="th"):
    src = env["tmp"] / "in" / "Tập 1.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("An đi trên đường làng.", encoding="utf-8")
    config = {"tts": {"voiceRef": "v"}, "type1": {"tstProjectId": "demo", "lang": lang, "gates": gates or {}}}
    run = runner.create_run("Loai 1", config, [{"path": str(src), "type": 1}], run_id=store.new_run_id())
    runner.tick()
    env["dgt"].finish("job1", "1\n00:00:00,000 --> 00:00:09,000\nAn đi\n")
    runner.tick()
    return run["runId"]


def _item(rid):
    return store.load_run(rid)["items"][0]


def _steps(env):
    return [c[3] for c in env["tst"].calls if c[0] == "run_step"]


def _sid(rid):
    return _item(rid)["tst"]["storyId"]


def _to_gate(env):
    rid = _run(env)
    tst = env["tst"]
    item = _item(rid)
    assert item["stage"] == "AI" and item["status"] == "running"
    sid = _sid(rid)
    assert sid == f"tap-1-{rid[-4:]}"
    assert ("patch_story", "demo", sid, {"lang": "th"}) in tst.calls
    ai_job = tst.last_job("ai")
    tst.finish_job(ai_job["id"], lines=["PROGRESS 11/11 (bước characters)", "XONG"])
    tst.set_flow("demo", sid, next="characters", built=True, characters={"done": 0, "total": 2},
                 images={"done": 0, "total": 5}, bible={"exists": True, "characters": 2})
    runner.tick()
    return rid, sid


def test_runs_to_prompt_gate_without_creating_images(env):
    rid, sid = _to_gate(env)
    item = _item(rid)
    assert item["stage"] == "GATE_PROMPTS" and item["status"] == "awaiting_review"
    assert "2 ảnh nhân vật + 5 ảnh cảnh" in item["progress"]["message"]
    assert item["tst"]["url"].endswith(f"/p/demo/s/{sid}")
    runner.tick()
    runner.tick()
    assert not any(step in ("characters", "scenes", "regen") for step in _steps(env))


def test_full_flow_after_approval(env):
    rid, sid = _to_gate(env)
    tst = env["tst"]
    pipeline_type1.approve(rid, "it-01", "prompts")
    runner.tick()
    assert _item(rid)["stage"] == "CHARACTERS" and _steps(env)[-1] == "characters"
    assert _item(rid)["tst"]["approvals"]["prompts"]["images"] == 7

    tst.finish_job(tst.last_job("characters")["id"], lines=["[2/2] nv"])
    tst.set_flow("demo", sid, characters={"done": 2, "total": 2})
    runner.tick()  # GATE_CHARACTERS tat -> SCENES
    assert _item(rid)["stage"] == "SCENES" and _steps(env)[-1] == "scenes"

    tst.finish_job(tst.last_job("scenes")["id"], lines=["[3/5] ảnh 3"])
    tst.set_flow("demo", sid, images={"done": 3, "total": 5})
    runner.tick()  # thieu 2 anh -> chay vong 2 ngay (tu nha lane)
    assert _steps(env).count("scenes") == 2

    tst.finish_job(tst.last_job("scenes")["id"])
    tst.set_flow("demo", sid, images={"done": 5, "total": 5}, next="done")
    runner.tick()
    assert _item(rid)["stage"] == "VIDEO" and _steps(env)[-1] == "video"

    video = env["tmp"] / "render.mp4"
    video.write_bytes(b"mp4data")
    tst.finish_job(tst.last_job("video")["id"], lines=["PROGRESS 10/10", f"OUTPUT {video}"])
    runner.tick()
    for _ in range(50):
        runner.tick()
        if _item(rid)["status"] == "done":
            break
        time.sleep(0.05)
    item = _item(rid)
    assert item["status"] == "done"
    dest = env["tmp"] / "output" / "story-video" / f"pipeline-{rid}" / "tap-1.mp4"
    assert item["files"]["video"] == str(dest) and dest.read_bytes() == b"mp4data"
    assert store.load_run(rid)["status"] == "completed"


def test_character_gate_and_regen(env):
    rid = _run(env, gates={"characters": True})
    tst = env["tst"]
    sid = _sid(rid)
    tst.finish_job(tst.last_job("ai")["id"])
    tst.set_flow("demo", sid, next="characters", built=True, characters={"done": 0, "total": 1}, images={"done": 0, "total": 3})
    runner.tick()
    pipeline_type1.approve(rid, "it-01", "prompts")
    runner.tick()
    tst.finish_job(tst.last_job("characters")["id"])
    tst.set_flow("demo", sid, characters={"done": 1, "total": 1})
    runner.tick()
    assert _item(rid)["stage"] == "GATE_CHARACTERS" and _item(rid)["status"] == "awaiting_review"

    pipeline_type1.reject(rid, "it-01", "characters", "regen_characters", character_ids=["an"], tst_client=tst)
    assert tst.last_job("regen")["params"] == {"ids": "nv:an"}
    assert _item(rid)["stage"] == "CHARACTERS"
    tst.finish_job(tst.last_job("regen")["id"])
    runner.tick()
    assert _item(rid)["stage"] == "GATE_CHARACTERS" and _item(rid)["status"] == "awaiting_review"
    pipeline_type1.approve(rid, "it-01", "characters")
    runner.tick()
    assert _item(rid)["stage"] == "SCENES"


def test_reject_rerun_ai_resets_and_passes_note(env):
    rid, sid = _to_gate(env)
    tst = env["tst"]
    pipeline_type1.reject(rid, "it-01", "prompts", "rerun_ai", note="Nhân vật chính là nữ", tst_client=tst)
    assert ("ai_reset", "demo", sid, "all", None) in tst.calls
    tst.set_flow("demo", sid, next="bible", built=False)
    runner.tick()
    item = _item(rid)
    assert item["stage"] == "AI" and item["status"] == "running"
    assert tst.last_job("ai")["params"]["note"] == "Nhân vật chính là nữ"
    with pytest.raises(runner.PipelineError):
        pipeline_type1.approve(rid, "it-01", "prompts")  # khong con o cong duyet


def test_lost_job_after_tst_restart_is_restarted(env):
    rid = _run(env)
    tst = env["tst"]
    tst.job_store.clear()  # studio khoi dong lai: job trong RAM mat
    runner.tick()
    item = _item(rid)
    assert item["stage"] == "AI" and item["status"] in ("running", "pending")
    runner.tick()
    assert len([s for s in _steps(env) if s == "ai"]) == 2


def test_not_logged_in_waits_and_other_story_blocks(env):
    rid, sid = _to_gate(env)
    tst = env["tst"]
    pipeline_type1.approve(rid, "it-01", "prompts")
    tst.logged_in = False
    runner.tick()
    item = _item(rid)
    assert item["status"] == "waiting_service" and item["waitingReason"] == "tst:not_logged_in"

    tst.logged_in = True
    tst.job_store["manual"] = {"id": "manual", "projectId": "khac", "storyId": "x", "step": "scenes",
                               "status": "running", "lines": [], "lineCount": 0, "params": {}}
    runner.tick()
    assert "chờ lượt" in _item(rid)["progress"]["message"]
    tst.job_store["manual"]["status"] = "done"
    runner.tick()
    assert _steps(env)[-1] == "characters"


def test_missing_images_after_max_rounds_needs_attention(env):
    rid, sid = _to_gate(env)
    tst = env["tst"]
    pipeline_type1.approve(rid, "it-01", "prompts")
    tst.set_flow("demo", sid, characters={"done": 2, "total": 2})
    runner.tick()
    for _ in range(2):
        tst.finish_job(tst.last_job("scenes")["id"])
        runner.tick()
    item = _item(rid)
    assert item["status"] == "needs_attention" and item["error"]["code"] == "scenes_missing"
    runner.retry_item(rid, "it-01")
    runner.tick()
    assert _steps(env).count("scenes") == 3


def test_ai_exit_4_reports_missing_claude(env):
    rid = _run(env)
    tst = env["tst"]
    job = tst.last_job("ai")
    tst.finish_job(job["id"], status="error")
    job["exitCode"] = 4
    runner.tick()
    item = _item(rid)
    assert item["status"] == "needs_attention" and item["error"]["code"] == "claude_missing"


def test_retry_from_tts_creates_new_tst_story(env):
    rid = _run(env)
    first_sid = _sid(rid)
    tst = env["tst"]
    tst.finish_job(tst.last_job("ai")["id"], status="error")
    runner.tick()
    runner.retry_item(rid, "it-01", "TTS")
    runner.tick()
    env["dgt"].finish("job2", "1\n00:00:00,000 --> 00:00:09,000\nAn đi\n")
    runner.tick()
    assert _sid(rid) == f"{first_sid}-r2"


def test_review_payload(env):
    rid, sid = _to_gate(env)
    tst = env["tst"]
    tst.stories[("demo", sid)]["characters"] = [{"id": "an", "name": "An", "prompt_name": "An", "role": "kể", "description": "d", "image": None}]
    tst.stories[("demo", sid)]["segments"] = [{"id": 1, "start": "00:00:00,000", "end": "00:00:09,000", "duration": 9, "text": "An đi",
                                               "prompt": "Wide shot", "characters": ["an"], "image": "/media/demo/x.jpg"}]
    data = pipeline_type1.review(rid, "it-01", "prompts", tst_client=tst)
    assert data["counts"] == {"characters": 2, "scenes": 5}
    assert data["scenes"][0]["image"].startswith(Config.TST_STUDIO_URL + "/media/")
    assert data["characters"][0]["name"] == "An"


def test_ai_lane_runs_one_story_at_a_time(env):
    sources = []
    for i in (1, 2):
        src = env["tmp"] / "in" / f"Tap {i}.txt"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("An đi.", encoding="utf-8")
        sources.append({"path": str(src), "type": 1})
    run = runner.create_run("Hai truyen", {"tts": {"voiceRef": "v"}, "type1": {"tstProjectId": "demo"}}, sources,
                            run_id=store.new_run_id())
    rid = run["runId"]
    runner.tick()
    for job_id in list(env["dgt"].jobs):
        env["dgt"].finish(job_id, "1\n00:00:00,000 --> 00:00:09,000\nAn đi\n")
    runner.tick()
    items = store.load_run(rid)["items"]
    assert [i["stage"] for i in items] == ["AI", "AI"]
    assert [i["status"] for i in items] == ["running", "pending"]
    assert _steps(env).count("ai") == 1
    tst = env["tst"]
    first = items[0]["tst"]["storyId"]
    tst.finish_job(tst.last_job("ai")["id"])
    tst.set_flow("demo", first, next="characters", built=True, characters={"done": 0, "total": 1}, images={"done": 0, "total": 2})
    runner.tick()
    items = store.load_run(rid)["items"]
    assert items[0]["stage"] == "GATE_PROMPTS" and items[1]["status"] == "running"
    assert _steps(env).count("ai") == 2
