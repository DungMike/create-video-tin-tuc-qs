"""Resource-priority guard for batch story-video renders.

While a batch render is active, suspends configured competing desktop apps (e.g. an
anti-detect browser farm) so the render gets the CPU, and always resumes them when
the batch ends -- even on crash or cancellation. `FFmpegHelper` checks
`is_batch_render_active()` to bump ffmpeg's OS scheduling priority meanwhile.

Safety:
- Suspend/resume goes through psutil (NtSuspendProcess/NtResumeProcess on Windows,
  SIGSTOP/SIGCONT on POSIX) -- reversible, never kills anything.
- A background watcher re-scans every few seconds for newly-spawned matching
  processes (browser-automation suites spawn child processes on demand) and
  suspends those too.
- `__exit__` always resumes every PID it suspended, even on exception, via
  try/finally in the caller.
- Chrome Remote Desktop's `remoting_host.exe` is ALWAYS excluded, regardless of
  config, since suspending a remote-access channel could strand a remote operator
  with no way to resume it themselves.
- Escape hatch if this process is killed before it can resume anything (crash,
  force-kill, power loss): `python tests/benchmarks/resume_all.py` force-resumes
  any currently-suspended process by name.
"""
import threading

import psutil

from src.config import Config
from src.utils.logger import logger

ALWAYS_EXCLUDED_PROCESS_NAMES = {"remoting_host", "remoting_host.exe"}
_ALWAYS_EXCLUDED = ALWAYS_EXCLUDED_PROCESS_NAMES  # internal alias used below

_batch_active = threading.Event()


def is_batch_render_active() -> bool:
    """True while a RenderResourcePriority context is active anywhere in-process."""
    return _batch_active.is_set()


def suspend_target_names() -> set[str]:
    raw = getattr(Config, "RENDER_SUSPEND_PROCESS_NAMES", "") or ""
    names = {n.strip().lower() for n in raw.split(",") if n.strip()}
    return names - _ALWAYS_EXCLUDED


def process_name_matches(process_name: str, names: set[str]) -> bool:
    nm = (process_name or "").lower()
    if nm in _ALWAYS_EXCLUDED:
        return False
    base = nm[:-4] if nm.endswith(".exe") else nm
    return nm in names or base in names


def _find_matching(names: set[str]) -> list:
    if not names:
        return []
    out = []
    for p in psutil.process_iter(["name"]):
        if process_name_matches(p.info.get("name") or "", names):
            out.append(p)
    return out


class RenderResourcePriority:
    """Context manager: suspend configured competing apps for a batch render's duration.

    No-op (does nothing, suspends nothing) if RENDER_SUSPEND_PROCESS_NAMES is empty.
    """

    def __init__(self, poll_seconds: float = 10.0, label: str = ""):
        self._poll_seconds = poll_seconds
        self._label = label
        self._names = suspend_target_names()
        self._suspended: dict[int, psutil.Process] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _suspend_new(self):
        for p in _find_matching(self._names):
            with self._lock:
                if p.pid in self._suspended:
                    continue
            try:
                name = p.name()
                p.suspend()
                with self._lock:
                    self._suspended[p.pid] = p
                logger.info(f"[RenderPriority{self._label}] Suspended {name} (pid={p.pid}).")
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                logger.warning(f"[RenderPriority{self._label}] Could not suspend pid={p.pid}: {exc}")

    def _watch(self):
        while not self._stop.wait(self._poll_seconds):
            try:
                self._suspend_new()
            except Exception as exc:
                logger.warning(f"[RenderPriority{self._label}] Watcher error: {exc}")

    def __enter__(self):
        if not self._names:
            logger.info(
                f"[RenderPriority{self._label}] RENDER_SUSPEND_PROCESS_NAMES is empty; not suspending anything."
            )
            return self
        _batch_active.set()
        self._suspend_new()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        with self._lock:
            n = len(self._suspended)
        logger.info(
            f"[RenderPriority{self._label}] Render started; targeting {sorted(self._names)} "
            f"({n} process(es) suspended so far; watching for more every {self._poll_seconds:.0f}s)."
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            procs = list(self._suspended.values())
            self._suspended.clear()
        resumed = 0
        for p in procs:
            try:
                if p.is_running() and p.status() == psutil.STATUS_STOPPED:
                    p.resume()
                resumed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc2:
                logger.warning(f"[RenderPriority{self._label}] Could not resume pid={p.pid}: {exc2}")
        _batch_active.clear()
        if procs:
            logger.info(f"[RenderPriority{self._label}] Render ended; resumed {resumed}/{len(procs)} process(es).")
        return False  # never swallow the caller's exception
