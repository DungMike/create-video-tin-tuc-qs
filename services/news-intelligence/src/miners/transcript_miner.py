"""Script 1: Transcript Miner — extract transcript from YouTube video via yt-dlp Python API."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

import yt_dlp

from src.config import get_config, SERVICE_ROOT
from src.models import TranscriptResult

logger = logging.getLogger("news-intel")


class TranscriptMiner:
    """Extract transcript + metadata from a YouTube video URL.

    Strategy (no Whisper needed):
    1. Try to get manual subtitles in preferred languages
    2. Fallback to auto-generated subtitles
    3. Extract title + description as supplementary context
    """

    def __init__(self):
        self.cfg = get_config()
        mining_cfg = self.cfg.mining.get("transcript", {})
        self.preferred_langs = mining_cfg.get(
            "preferred_languages", ["vi", "en", "ru", "uk"]
        )
        self.use_auto = mining_cfg.get("fallback_auto_generated", True)
        self.output_dir = SERVICE_ROOT / "storage" / "transcripts"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, video_url: str) -> TranscriptResult:
        """Main entry — extract transcript from a YouTube URL."""
        logger.info("Mining transcript from: %s", video_url)

        metadata = self._fetch_metadata(video_url)
        transcript_text = self._extract_subtitles(video_url, metadata)

        if not transcript_text:
            logger.warning(
                "No subtitles found, using title + description as fallback"
            )
            transcript_text = self._fallback_from_metadata(metadata)

        result = TranscriptResult(
            video_url=video_url,
            title=metadata.get("title", ""),
            description=metadata.get("description", ""),
            transcript_text=transcript_text,
            language=self._detect_language(metadata),
            duration_seconds=metadata.get("duration", 0.0) or 0.0,
        )

        video_id = metadata.get("id", "unknown")
        out_path = self.output_dir / f"{video_id}.json"
        out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Transcript saved to %s (%d chars)", out_path, len(transcript_text))

        return result

    def _fetch_metadata(self, url: str) -> dict[str, Any]:
        """Use yt-dlp Python API to fetch video metadata."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info or {}
        except Exception as exc:
            logger.error("yt-dlp metadata error: %s", exc)
            return {}

    def _extract_subtitles(self, url: str, metadata: dict) -> str:
        """Download subtitles via yt-dlp Python API and read the text."""
        available_subs = metadata.get("subtitles") or {}
        auto_subs = metadata.get("automatic_captions") or {}

        sub_lang = None
        use_auto_flag = False

        # 1. Try manual subtitles first
        for lang in self.preferred_langs:
            if lang in available_subs:
                sub_lang = lang
                break

        # 2. Fallback to auto-generated
        if not sub_lang and self.use_auto:
            for lang in self.preferred_langs:
                if lang in auto_subs:
                    sub_lang = lang
                    use_auto_flag = True
                    break
            if not sub_lang and auto_subs:
                sub_lang = next(iter(auto_subs))
                use_auto_flag = True

        if not sub_lang:
            logger.warning("No subtitles available for %s", url)
            return ""

        logger.info(
            "Downloading %s subtitles (lang=%s) for %s",
            "auto" if use_auto_flag else "manual", sub_lang, url,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            out_tmpl = str(Path(tmpdir) / "subs.%(ext)s")
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                "outtmpl": out_tmpl,
                "subtitlesformat": "vtt/srt/best",
                "writesubtitles": not use_auto_flag,
                "writeautomaticsub": use_auto_flag,
                "subtitleslangs": [sub_lang],
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as exc:
                logger.error("yt-dlp subtitle download error: %s", exc)
                return ""

            # Find the downloaded subtitle file
            tmppath = Path(tmpdir)
            sub_files = (
                list(tmppath.glob("*.vtt"))
                + list(tmppath.glob("*.srt"))
                + list(tmppath.glob("*.json3"))
            )
            if not sub_files:
                logger.warning("No subtitle file written to %s", tmpdir)
                return ""

            raw_text = sub_files[0].read_text(encoding="utf-8", errors="replace")
            return self._clean_vtt(raw_text)

    def _clean_vtt(self, raw: str) -> str:
        """Strip VTT/SRT timestamps and formatting, return plain text."""
        lines = raw.split("\n")
        cleaned = []
        seen = set()

        for line in lines:
            line = line.strip()
            if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
                continue
            if re.match(r"^\d{2}:\d{2}", line):
                continue
            if re.match(r"^\d+$", line):
                continue
            # Strip HTML-like tags
            line = re.sub(r"<[^>]+>", "", line)
            # Strip VTT positioning cues
            line = re.sub(r"align:\w+ position:\d+%", "", line).strip()
            if line and line not in seen:
                seen.add(line)
                cleaned.append(line)

        return " ".join(cleaned)

    def _fallback_from_metadata(self, metadata: dict) -> str:
        """Combine title + description when no subtitles exist."""
        parts = []
        title = metadata.get("title", "")
        desc = metadata.get("description", "")
        if title:
            parts.append(title)
        if desc:
            parts.append(desc)
        return "\n\n".join(parts)

    def _detect_language(self, metadata: dict) -> str:
        """Best-effort language detection from metadata."""
        subs = metadata.get("subtitles") or {}
        for lang in self.preferred_langs:
            if lang in subs:
                return lang
        auto = metadata.get("automatic_captions") or {}
        for lang in self.preferred_langs:
            if lang in auto:
                return lang
        return metadata.get("language") or ""
