"""HTTP client goi cac app ben ngoai cua pipeline.

- ``DgtClient``: DGT Automation API (app TTS), docs/integration/01-app-tts-dgt.md
- ``TstClient``: TST Studio (app tao anh), docs/integration/02-app-tst-studio.md

Loi duoc chia hai loai de reconciler xu ly dung:
- ``ServiceUnavailable``: khong ket noi duoc / 5xx / chua dang nhap -> item ``waiting_service``
- ``ServiceError``: request bi tu choi (4xx) -> tuy handler (thuong la ``failed``)
"""

from __future__ import annotations

import os

import requests

from src.config import Config


class ServiceUnavailable(Exception):
    def __init__(self, service: str, reason: str, message: str = ""):
        super().__init__(message or f"{service}: {reason}")
        self.service = service
        self.reason = reason

    @property
    def waiting_reason(self) -> str:
        return f"{self.service}:{self.reason}"


class ServiceError(Exception):
    def __init__(self, service: str, status: int, code: str, message: str):
        super().__init__(message)
        self.service = service
        self.status = status
        self.code = code


def _error_fields(response: requests.Response) -> tuple[str, str]:
    """Doc {code,message} (DGT) hoac {error:{code?,message}} (TST/SVS)."""
    try:
        body = response.json()
    except ValueError:
        return "http_error", (response.text or "").strip()[:300] or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        return str(err.get("code") or "http_error"), str(err.get("message") or response.reason)
    return "http_error", str(body)[:300]


class _HttpClient:
    service = "http"

    def __init__(self, base_url: str, *, timeout: float = 15.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = session or requests.Session()

    def _request(self, method: str, path: str, *, timeout: float | None = None, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            response = self.http.request(method, url, timeout=timeout or self.timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise ServiceUnavailable(self.service, "api_down", f"Không kết nối được {self.base_url}: {exc}") from exc
        if response.status_code >= 500:
            code, message = _error_fields(response)
            raise ServiceUnavailable(self.service, code, message)
        if response.status_code >= 400:
            code, message = _error_fields(response)
            raise ServiceError(self.service, response.status_code, code, message)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text


class DgtClient(_HttpClient):
    """DGT Automation API — base http://127.0.0.1:8787, JSON snake_case."""

    service = "dgt"

    def __init__(self, base_url: str | None = None, **kwargs):
        super().__init__(base_url or Config.DGT_API_URL, **kwargs)

    def health(self) -> dict:
        return self._request("GET", "/health", timeout=4)

    def runtime(self) -> dict:
        return self._request("GET", "/api/v1/runtime", timeout=6)

    def voices(self) -> list[dict]:
        return self._request("GET", "/api/v1/voices") or []

    def queue(self) -> dict:
        return self._request("GET", "/api/v1/queue")

    def run_file(self, file_path: str, voice_ref: str, *, idempotency_key: str, name: str = "",
                 audio_format: str = "mp3", auto_retry: bool = True) -> dict:
        body = {
            "file_path": file_path,
            "voice_ref": voice_ref,
            "output": {"format": audio_format, "create_srt": True},
            "failure_policy": "continue",
            "auto_retry": bool(auto_retry),
        }
        if name:
            body["name"] = name[:120]
        return self._request(
            "POST", "/api/v1/runs/file", json=body, headers={"Idempotency-Key": idempotency_key}, timeout=30
        )

    def job(self, job_id: str) -> dict:
        return self._request("GET", f"/api/v1/jobs/{job_id}")

    def cancel_job(self, job_id: str) -> dict:
        return self._request("POST", f"/api/v1/jobs/{job_id}/cancel")


class TstClient(_HttpClient):
    """TST Studio (Express) — base http://127.0.0.1:5180. Loi dang {error:{message}}."""

    service = "tst"

    def __init__(self, base_url: str | None = None, **kwargs):
        super().__init__(base_url or Config.TST_STUDIO_URL, **kwargs)

    def tst_state(self) -> dict:
        """{online, loggedIn, ...} cua app TST (qua bridge)."""
        return self._request("GET", "/api/tst", timeout=10)

    def ai_state(self) -> dict:
        return self._request("GET", "/api/ai", timeout=15)

    def projects(self) -> list[dict]:
        return self._request("GET", "/api/projects") or []

    def story(self, project_id: str, story_id: str) -> dict | None:
        try:
            return self._request("GET", f"/api/projects/{project_id}/stories/{story_id}", timeout=40)
        except ServiceError as exc:
            if exc.status in (400, 404):
                return None
            raise

    def create_story(self, project_id: str, story_id: str, title: str, srt_path: str, audio_path: str) -> dict:
        # Upload chay luon split (dong bo, toi 120 s phia TST) nen timeout dai.
        with open(srt_path, "rb") as srt_file, open(audio_path, "rb") as audio_file:
            files = {
                "srt": (f"{story_id}.srt", srt_file, "application/x-subrip"),
                "audio": (f"{story_id}{_ext(audio_path)}", audio_file, "audio/mpeg"),
            }
            return self._request(
                "POST", f"/api/projects/{project_id}/stories",
                data={"id": story_id, "title": title}, files=files, timeout=240,
            )

    def patch_story(self, project_id: str, story_id: str, body: dict) -> dict:
        return self._request("PATCH", f"/api/projects/{project_id}/stories/{story_id}", json=body)

    def run_step(self, project_id: str, story_id: str, step: str, **params) -> dict:
        body = {"step": step, **{k: v for k, v in params.items() if v is not None}}
        return self._request("POST", f"/api/projects/{project_id}/stories/{story_id}/run", json=body, timeout=30)

    def jobs(self, project_id: str, story_id: str) -> list[dict]:
        return self._request("GET", "/api/jobs", params={"project": project_id, "story": story_id}) or []

    def all_jobs(self) -> list[dict]:
        """Moi job cua studio (ca job nguoi chay tay), moi nhat truoc."""
        return self._request("GET", "/api/jobs") or []

    def job(self, job_id: str, since: int = 0) -> dict | None:
        try:
            return self._request("GET", f"/api/jobs/{job_id}", params={"since": since})
        except ServiceError as exc:
            if exc.status == 404:
                return None  # studio restart -> job trong RAM mat
            raise

    def cancel_job(self, job_id: str) -> dict:
        return self._request("POST", f"/api/jobs/{job_id}/cancel")

    def ai_reset(self, project_id: str, story_id: str, scope: str, batch_nos: list[int] | None = None) -> dict:
        body = {"scope": scope}
        if batch_nos:
            body["batchNos"] = batch_nos
        return self._request("POST", f"/api/projects/{project_id}/stories/{story_id}/ai/reset", json=body)

    def story_video(self, project_id: str, story_id: str) -> dict:
        return self._request("GET", f"/api/projects/{project_id}/stories/{story_id}/video")


def _ext(path: str) -> str:
    return os.path.splitext(path)[1] or ".mp3"


def dgt_artifact_paths(job: dict) -> tuple[str, str]:
    """(audio, srt) tu artifacts cua job DGT — luon dung saved_path, khong tu suy ten."""
    paths = {a.get("kind"): a.get("saved_path") for a in job.get("artifacts") or []}
    return paths.get("audio") or "", paths.get("subtitles") or ""
