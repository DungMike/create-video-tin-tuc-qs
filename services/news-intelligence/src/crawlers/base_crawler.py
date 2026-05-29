"""Abstract base crawler with exponential backoff, circuit breaker, and rate limiting."""

from __future__ import annotations

import abc
import logging
import time
from typing import Any

import requests

from src.config import get_config
from src.db import AssetDB

logger = logging.getLogger("news-intel")


class CircuitOpen(Exception):
    """Raised when a source has failed too many times consecutively."""


class BaseCrawler(abc.ABC):
    """Base class for all source-specific crawlers.

    Provides:
    - Per-source rate limiting (delay between requests)
    - Exponential backoff on failure
    - Circuit breaker: skip source after N consecutive failures
    - Failed URL logging to DB
    """

    SOURCE_NAME: str = "base"
    CIRCUIT_BREAKER_THRESHOLD = 5

    def __init__(self, db: AssetDB | None = None):
        self.cfg = get_config()
        self.db = db or AssetDB()
        self._settings = self.cfg.get_source_settings(self.SOURCE_NAME)
        self._consecutive_failures = 0
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
        })

    @property
    def delay(self) -> float:
        return self._settings.get("delay", 2)

    @property
    def max_retries(self) -> int:
        return self._settings.get("max_retries", 3)

    @property
    def backoff_multiplier(self) -> float:
        return self._settings.get("backoff_multiplier", 1.5)

    def _check_circuit(self) -> None:
        if self._consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
            raise CircuitOpen(
                f"Circuit open for {self.SOURCE_NAME}: "
                f"{self._consecutive_failures} consecutive failures"
            )

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self, url: str, keyword: str, error: str,
                        phase: str = "") -> None:
        self._consecutive_failures += 1
        logger.warning(
            "[%s] Failure #%d for %s: %s",
            self.SOURCE_NAME, self._consecutive_failures, url, error,
        )
        self.db.log_failed_url(
            url=url, source=self.SOURCE_NAME,
            keyword=keyword, error=error, phase=phase,
        )

    def _rate_limit(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)

    def fetch_with_retry(self, url: str, keyword: str = "",
                         phase: str = "", **kwargs: Any) -> requests.Response | None:
        """GET request with retry + backoff. Returns None on exhausted retries."""
        self._check_circuit()

        for attempt in range(1, self.max_retries + 1):
            try:
                self._rate_limit()
                resp = self._session.get(url, timeout=15, **kwargs)
                resp.raise_for_status()
                self._record_success()
                return resp
            except requests.RequestException as exc:
                wait = self.delay * (self.backoff_multiplier ** (attempt - 1))
                logger.warning(
                    "[%s] Attempt %d/%d failed for %s: %s — retrying in %.1fs",
                    self.SOURCE_NAME, attempt, self.max_retries, url, exc, wait,
                )
                if attempt == self.max_retries:
                    self._record_failure(url, keyword, str(exc), phase)
                    return None
                time.sleep(wait)

        return None

    @abc.abstractmethod
    def search(self, keyword: str) -> list[dict]:
        """Search this source for a keyword. Return list of discovered URL dicts."""

    @abc.abstractmethod
    def download(self, url: str, dest_dir: str, keyword: str) -> str | None:
        """Download an asset. Return local file path or None."""
