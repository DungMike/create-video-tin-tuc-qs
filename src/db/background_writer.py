"""Hang doi ghi nen cho nhung ban ghi Mongo "thu dong".

Luong tai/cat clip va harvest/prefetch chi *bao* cho Mongo biet chuyen gi da xay
ra (video goc nao, tu khoa nao); chung khong bao gio duoc cho Mongo, va Mongo tat
cung khong duoc lam hong chung. Vi vay moi ban ghi thu dong di qua day:
``submit()`` tra ve ngay, mot daemon thread ghi lan luot, loi chi duoc log.

Mongo tat thi viec bi bo (co log) thay vi xep hang vo han; script
``src/tools/backfill_media_db.py`` bu lai tu cac file JSON tren dia.
"""

from __future__ import annotations

import queue
import threading
import time

from src.db import mongo
from src.utils.logger import logger

_MAX_QUEUE = 5000


class BackgroundWriter:
    def __init__(self, max_queue: int = _MAX_QUEUE):
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._dropped = 0

    def submit(self, fn, *args, **kwargs) -> bool:
        """Xep ``fn(*args, **kwargs)`` de chay nen. Khong bao gio raise."""
        try:
            if not mongo.is_configured():
                return False
            self._ensure_thread()
            self._queue.put_nowait((fn, args, kwargs))
            return True
        except queue.Full:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
                logger.warning(f"[MongoWriter] Hang doi day, da bo {self._dropped} ban ghi.")
            return False
        except Exception as exc:  # pragma: no cover - phong thu
            logger.warning(f"[MongoWriter] Khong xep duoc ban ghi: {exc}")
            return False

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Cho hang doi rong (dung cho test va script). True neu rong kip."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return self._queue.unfinished_tasks == 0

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="mongo-background-writer", daemon=True
                )
                self._thread.start()

    def _run(self) -> None:
        while True:
            fn, args, kwargs = self._queue.get()
            try:
                if not mongo.is_available():
                    name = getattr(fn, "__name__", "task")
                    logger.warning(f"[MongoWriter] Mongo khong san sang, bo qua {name}.")
                    continue
                fn(*args, **kwargs)
            except Exception as exc:
                logger.warning(f"[MongoWriter] {getattr(fn, '__name__', 'task')} that bai: {exc}")
            finally:
                self._queue.task_done()


_writer = BackgroundWriter()


def get_background_writer() -> BackgroundWriter:
    return _writer


def submit(fn, *args, **kwargs) -> bool:
    return _writer.submit(fn, *args, **kwargs)
