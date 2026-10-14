"""Client gia cho test pipeline (DGT TTS, TST Studio) — khong goi mang that."""

from __future__ import annotations

import os

from src.utils.pipeline_clients import ServiceError, ServiceUnavailable


class FakeDgt:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.keys: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.cancelled: list[str] = []
        self.down = False

    def _check(self):
        if self.down:
            raise ServiceUnavailable("dgt", "api_down")

    def run_file(self, file_path, voice_ref, *, idempotency_key, name="", audio_format="mp3", auto_retry=True):
        self._check()
        self.calls.append(("run_file", file_path, voice_ref, idempotency_key))
        if idempotency_key in self.keys:
            job_id = self.keys[idempotency_key]
            return {"batch_id": f"b-{job_id}", "job_ids": [job_id], "replayed": True}
        job_id = f"job{len(self.jobs) + 1}"
        self.keys[idempotency_key] = job_id
        self.jobs[job_id] = {
            "job_id": job_id, "state": "queued", "stage": "queued", "waiting_reason": "queue_order",
            "artifacts": [], "file_path": file_path,
        }
        return {"batch_id": f"b-{job_id}", "job_ids": [job_id], "replayed": False}

    def job(self, job_id):
        self._check()
        return dict(self.jobs[job_id])

    def cancel_job(self, job_id):
        self.cancelled.append(job_id)
        self.jobs[job_id]["state"] = "cancelled"
        return self.jobs[job_id]

    def set_state(self, job_id, state, **extra):
        self.jobs[job_id].update(state=state, **extra)

    def finish(self, job_id, srt_text="1\n00:00:00,000 --> 00:00:09,000\nXin chào\n", audio_bytes=b"ID3fake"):
        """Gia lap DGT xuat X.mp3 + X.srt canh X.txt va bao succeeded."""
        stem = os.path.splitext(self.jobs[job_id]["file_path"])[0]
        with open(stem + ".mp3", "wb") as f:
            f.write(audio_bytes)
        with open(stem + ".srt", "w", encoding="utf-8") as f:
            f.write(srt_text)
        self.jobs[job_id].update(state="succeeded", stage="completed", artifacts=[
            {"kind": "audio", "saved_path": stem + ".mp3"},
            {"kind": "subtitles", "saved_path": stem + ".srt"},
            {"kind": "execution_report", "saved_path": None},
        ])


class FakeTst:
    """TST Studio gia: giu truyen + job trong RAM, flow status do test dieu khien."""

    def __init__(self):
        self.down = False
        self.online = True
        self.logged_in = True
        self.stories: dict[tuple[str, str], dict] = {}
        self.job_store: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.cancelled: list[str] = []
        self.renders: dict[tuple[str, str], list[dict]] = {}

    def _check(self):
        if self.down:
            raise ServiceUnavailable("tst", "api_down")

    def tst_state(self):
        self._check()
        return {"online": self.online, "loggedIn": self.logged_in}

    def ai_state(self):
        self._check()
        return {"found": True, "path": "claude.exe", "version": "test"}

    def projects(self):
        self._check()
        return [{"id": "demo", "name": "Demo", "style_skill": "ve-demo"}]

    def story(self, project_id, story_id):
        self._check()
        story = self.stories.get((project_id, story_id))
        return dict(story) if story else None

    def create_story(self, project_id, story_id, title, srt_path, audio_path):
        self._check()
        self.calls.append(("create_story", project_id, story_id, title))
        if (project_id, story_id) in self.stories:
            raise ServiceError("tst", 409, "http_error", "Truyện đã tồn tại")
        self.stories[(project_id, story_id)] = {
            "id": story_id, "title": title,
            "status": {"next": "bible", "segments": 10, "bible": {"exists": False, "characters": 0},
                       "prompts": {"done": 0, "total": 10}, "built": False,
                       "characters": {"done": 0, "total": 0}, "images": {"done": 0, "total": 0}},
            "characters": [], "segments": [],
        }
        return self.stories[(project_id, story_id)]

    def patch_story(self, project_id, story_id, body):
        self._check()
        self.calls.append(("patch_story", project_id, story_id, body))
        self.stories[(project_id, story_id)].update(body)
        return self.stories[(project_id, story_id)]

    def set_flow(self, project_id, story_id, **status):
        self.stories[(project_id, story_id)]["status"].update(status)

    def run_step(self, project_id, story_id, step, **params):
        self._check()
        for job in self.job_store.values():
            if job["projectId"] == project_id and job["storyId"] == story_id and job["status"] == "running":
                raise ServiceError("tst", 409, "http_error", "Truyện đang có job chạy")
        job_id = f"job{len(self.job_store) + 1}"
        self.job_store[job_id] = {"id": job_id, "projectId": project_id, "storyId": story_id, "step": step,
                             "status": "running", "lines": [f"$ python {step}"], "lineCount": 1, "params": params}
        self.calls.append(("run_step", project_id, story_id, step, params))
        return dict(self.job_store[job_id])

    def jobs_for(self, project_id, story_id):
        return [j for j in self.job_store.values() if j["projectId"] == project_id and j["storyId"] == story_id]

    def jobs(self, project_id, story_id):
        self._check()
        return [dict(j, lines=j["lines"][-3:]) for j in reversed(self.jobs_for(project_id, story_id))]

    def job(self, job_id, since=0):
        self._check()
        job = self.job_store.get(job_id)
        if job is None:
            return None
        return dict(job, lines=job["lines"][since:])

    def finish_job(self, job_id, status="done", lines=()):
        job = self.job_store[job_id]
        job["lines"].extend(lines)
        job["lineCount"] = len(job["lines"])
        job["status"] = status

    def last_job(self, step=None):
        jobs = [j for j in self.job_store.values() if step is None or j["step"] == step]
        return jobs[-1] if jobs else None

    def cancel_job(self, job_id):
        self.cancelled.append(job_id)
        self.job_store[job_id]["status"] = "cancelled"
        return self.job_store[job_id]

    def ai_reset(self, project_id, story_id, scope, batch_nos=None):
        self._check()
        self.calls.append(("ai_reset", project_id, story_id, scope, batch_nos))
        return {"ok": True}

    def story_video(self, project_id, story_id):
        self._check()
        return {"renders": self.renders.get((project_id, story_id), [])}
