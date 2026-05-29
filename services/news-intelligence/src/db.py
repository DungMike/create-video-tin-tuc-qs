"""SQLite database for asset index, keyword tracking, and crawl history."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

from src.config import get_config

_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS keywords (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT UNIQUE NOT NULL,
    category    TEXT DEFAULT '',
    first_seen  TEXT NOT NULL,
    last_crawled TEXT,
    total_assets INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS assets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword         TEXT NOT NULL,
    filename        TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_url      TEXT DEFAULT '',
    asset_type      TEXT NOT NULL,
    license_tier    TEXT DEFAULT 'unknown',
    monetizable     INTEGER DEFAULT 0,
    quality_score   INTEGER DEFAULT 0,
    resolution      TEXT DEFAULT '',
    phash           TEXT DEFAULT '',
    file_size_bytes INTEGER DEFAULT 0,
    downloaded_at   TEXT NOT NULL,
    note            TEXT DEFAULT '',
    UNIQUE(keyword, filename)
);

CREATE TABLE IF NOT EXISTS crawl_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT NOT NULL,
    source      TEXT NOT NULL,
    crawled_at  TEXT NOT NULL,
    urls_found  INTEGER DEFAULT 0,
    assets_downloaded INTEGER DEFAULT 0,
    errors      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS failed_urls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL,
    source      TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    error       TEXT DEFAULT '',
    phase       TEXT DEFAULT '',
    attempts    INTEGER DEFAULT 1,
    last_attempt TEXT NOT NULL,
    resolved    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_assets_keyword ON assets(keyword);
CREATE INDEX IF NOT EXISTS idx_assets_phash ON assets(phash);
CREATE INDEX IF NOT EXISTS idx_keywords_keyword ON keywords(keyword);
CREATE INDEX IF NOT EXISTS idx_failed_urls_resolved ON failed_urls(resolved);
"""


class AssetDB:
    """Thin wrapper around SQLite for the asset index."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or get_config().db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA_SQL)
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- Keywords ---

    def upsert_keyword(self, keyword: str, category: str = "") -> None:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO keywords (keyword, category, first_seen)
                   VALUES (?, ?, ?)
                   ON CONFLICT(keyword) DO UPDATE SET category = excluded.category""",
                (keyword, category, now),
            )

    def get_keyword(self, keyword: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM keywords WHERE keyword = ?", (keyword,)
            ).fetchone()
            return dict(row) if row else None

    def update_keyword_crawl(self, keyword: str, total_assets: int) -> None:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE keywords SET last_crawled = ?, total_assets = ?
                   WHERE keyword = ?""",
                (now, total_assets, keyword),
            )

    # --- Assets ---

    def insert_asset(self, keyword: str, filename: str, source: str,
                     source_url: str = "", asset_type: str = "image",
                     license_tier: str = "unknown", monetizable: bool = False,
                     quality_score: int = 0, resolution: str = "",
                     phash: str = "", file_size_bytes: int = 0,
                     note: str = "") -> int | None:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            try:
                cursor = conn.execute(
                    """INSERT INTO assets
                       (keyword, filename, source, source_url, asset_type,
                        license_tier, monetizable, quality_score, resolution,
                        phash, file_size_bytes, downloaded_at, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (keyword, filename, source, source_url, asset_type,
                     license_tier, int(monetizable), quality_score, resolution,
                     phash, file_size_bytes, now, note),
                )
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                return None

    def get_assets_for_keyword(self, keyword: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM assets WHERE keyword = ? ORDER BY quality_score DESC",
                (keyword,),
            ).fetchall()
            return [dict(r) for r in rows]

    def count_assets(self, keyword: str, asset_type: str | None = None) -> int:
        with self._conn() as conn:
            if asset_type:
                row = conn.execute(
                    "SELECT COUNT(*) FROM assets WHERE keyword = ? AND asset_type = ?",
                    (keyword, asset_type),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM assets WHERE keyword = ?",
                    (keyword,),
                ).fetchone()
            return row[0] if row else 0

    def find_by_phash(self, phash: str, threshold: int = 10) -> list[dict]:
        """Find assets with similar perceptual hash (exact match only for now)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM assets WHERE phash = ?", (phash,)
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Crawl History ---

    def log_crawl(self, keyword: str, source: str,
                  urls_found: int = 0, assets_downloaded: int = 0,
                  errors: int = 0) -> None:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO crawl_history
                   (keyword, source, crawled_at, urls_found, assets_downloaded, errors)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (keyword, source, now, urls_found, assets_downloaded, errors),
            )

    # --- Failed URLs ---

    def log_failed_url(self, url: str, source: str, keyword: str,
                       error: str, phase: str = "") -> None:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO failed_urls (url, source, keyword, error, phase, last_attempt)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (url, source, keyword, error, phase, now),
            )

    def get_unresolved_failed_urls(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM failed_urls WHERE resolved = 0"
            ).fetchall()
            return [dict(r) for r in rows]

    def resolve_failed_url(self, url_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE failed_urls SET resolved = 1 WHERE id = ?", (url_id,)
            )
