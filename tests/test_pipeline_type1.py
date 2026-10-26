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


def _render_and_collect(env, video_in_tst_dir=True):
    """Chay toi DONE; video nam trong <tst dir>/<stem>_video (kem _cache) nhu TST that."""
    rid, sid = _to_gate(env)
    tst = env["tst"]
    pipeline_type1.approve(rid, "it-01", "prompts")
    runner.tick()
    tst.finish_job(tst.last_job("characters")["id"])
    tst.set_flow("demo", sid, characters={"done": 2, "total": 2})
    runner.tick()
    tst.finish_job(tst.last_job("scenes")["id"])
    tst.set_flow("demo", sid, images={"done": 5, "total": 5}, next="done")
    runner.tick()
    story_dir = _item(rid)["tst"]["dir"]
    video_dir = os.path.join(story_dir, f"{os.path.basename(story_dir)}_video")
    folder = video_dir if video_in_tst_dir else str(env["tmp"])
    os.makedirs(os.path.join(video_dir, "_cache"), exist_ok=True)
    with open(os.path.join(video_dir, "_cache", "piece_001.mp4"), "wb") as f:
        f.write(b"piece")
    video = os.path.join(folder, "render.mp4")
    with open(video, "wb") as f:
        f.write(b"mp4data")
    tst.finish_job(tst.last_job("video")["id"], lines=["PROGRESS 10/10", f"OUTPUT {video}"])
    for _ in range(50):
        runner.tick()
        if _item(rid)["status"] == "done":
            break
        time.sleep(0.05)
    assert _item(rid)["status"] == "done"
    return rid, video_dir


def test_cleanup_tst_video_after_collect(env, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_CLEANUP_TST_VIDEO", True)
    rid, video_dir = _render_and_collect(env)
    item = _item(rid)
    assert not os.path.exists(video_dir)
    assert open(item["files"]["video"], "rb").read() == b"mp4data"
    assert item["tst"]["videoCleanedAt"]


def test_cleanup_off_keeps_tst_video(env, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_CLEANUP_TST_VIDEO", False)
    rid, video_dir = _render_and_collect(env)
    assert os.path.isfile(os.path.join(video_dir, "_cache", "piece_001.mp4"))
    assert "videoCleanedAt" not in _item(rid)["tst"]


def test_cleanup_skips_render_outside_tst_video_dir(env, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_CLEANUP_TST_VIDEO", True)
    rid, video_dir = _render_and_collect(env, video_in_tst_dir=False)
    assert os.path.isfile(os.path.join(video_dir, "_cache", "piece_001.mp4"))
    assert os.path.isfile(env["tmp"] / "render.mp4")


def test_retry_collect_after_cleanup_points_to_video_step(env, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_CLEANUP_TST_VIDEO", True)
    rid, _video_dir = _render_and_collect(env)
    runner.retry_item(rid, "it-01", "COLLECT")
    runner.tick()
    item = _item(rid)
    assert item["status"] == "needs_attention"
    assert "chạy lại từ bước VIDEO" in item["error"]["message"]


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


def test_each_video_uses_its_own_topic(env):
    # Video 1 chọn chủ đề riêng (bộ skill riêng), video 2 dùng chủ đề mặc định của run.
    sources = []
    for i, project in ((1, "an-thai"), (2, None)):
        src = env["tmp"] / "in" / f"Chu de {i}.txt"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("An đi.", encoding="utf-8")
        sources.append({"path": str(src), "type": 1, "tstProjectId": project})
    run = runner.create_run("Hai chu de", {"tts": {"voiceRef": "v"}, "type1": {"tstProjectId": "demo"}}, sources,
                            run_id=store.new_run_id())
    rid = run["runId"]
    assert [i["tst"]["projectId"] for i in run["items"]] == ["an-thai", "demo"]
    runner.tick()
    for job_id in list(env["dgt"].jobs):
        env["dgt"].finish(job_id, "1\n00:00:00,000 --> 00:00:09,000\nAn đi\n")
    runner.tick()
    assert [c[1] for c in env["tst"].calls if c[0] == "create_story"] == ["an-thai", "demo"]
    assert ["/p/an-thai/" in store.load_run(rid)["items"][0]["tst"]["url"], "/p/demo/" in store.load_run(rid)["items"][1]["tst"]["url"]] == [True, True]


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


def test_ai_waits_for_old_tst_studio_or_missing_claude_then_resumes(env):
    env["tst"].ai = None  # TST Studio chua restart sau khi them buoc ai
    rid = _run(env)
    item = _item(rid)
    assert item["stage"] == "AI" and item["status"] == "waiting_service"
    assert item["waitingReason"] == "tst:ai_step_missing" and "start-studio.cmd" in item["progress"]["message"]
    assert "ai" not in _steps(env)

    env["tst"].ai = {"found": False, "path": None, "version": None}
    runner.tick()
    assert _item(rid)["waitingReason"] == "tst:claude_missing"

    env["tst"].ai = {"found": True, "path": "claude.exe", "version": "test"}
    runner.tick()  # tu chay tiep, khong can bam Thu lai
    assert _item(rid)["status"] == "running" and _steps(env) == ["ai"]


def test_item_detail_reads_live_tst_state(env, monkeypatch):
    from src.web_app import app

    rid = _run(env)
    sid = _sid(rid)
    tst = env["tst"]
    tst.stories[("demo", sid)].update(
        characters=[{"id": "an", "name": "An", "role": "chính", "description": "x", "image": "/media/demo/nv_an.jpg"}],
        segments=[
            {"id": 1, "start": "00:00:00,000", "text": "a", "prompt": "p1", "image": "/media/demo/001.jpg"},
            {"id": 2, "start": "00:00:09,000", "text": "b", "prompt": "p2", "image": None, "error": "TST lỗi"},
            {"id": 3, "start": "00:00:18,000", "text": "c", "prompt": None, "image": None},
        ],
    )
    tst.job_store[tst.last_job("ai")["id"]]["lines"].append("PROGRESS 3/11 (bước prompts)")
    monkeypatch.setattr("src.routes.pipeline_routes.TstClient", lambda *a, **k: tst)
    with app.test_client() as client:
        body = client.get(f"/api/pipeline/runs/{rid}/items/it-01/detail").get_json()
        detail = body["tst"]
        assert detail["storyId"] == sid and detail["url"].endswith(f"/p/demo/s/{sid}")
        assert detail["characters"][0]["image"] == f"{Config.TST_STUDIO_URL}/media/demo/nv_an.jpg"
        assert {k: detail["scenes"][k] for k in ("total", "withPrompt", "withImage", "errors")} == {
            "total": 3, "withPrompt": 2, "withImage": 1, "errors": [2]}
        assert detail["scenes"]["recent"][0]["id"] == 1
        assert detail["job"]["step"] == "ai" and detail["job"]["status"] == "running"
        assert detail["job"]["lines"][-1] == "PROGRESS 3/11 (bước prompts)"
        assert body["events"] and {e["itemId"] for e in body["events"]} == {"it-01"}
        assert body["item"]["itemId"] == "it-01" and "media" in body["item"]

        tst.down = True
        down = client.get(f"/api/pipeline/runs/{rid}/items/it-01/detail").get_json()
        assert down["tst"] is None and "TST Studio" in down["tstError"] and down["events"]
        assert client.get(f"/api/pipeline/runs/{rid}/items/it-99/detail").status_code == 404
