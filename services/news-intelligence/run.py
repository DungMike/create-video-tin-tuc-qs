"""News Intelligence Service -- CLI Entry Point.

Usage:
    # Full pipeline: YouTube URL -> transcript -> keywords
    python run.py mine --url "https://www.youtube.com/watch?v=..."

    # Extract entities from existing transcript file
    python run.py extract --transcript storage/transcripts/VIDEO_ID.json

    # Mine transcript only (no entity extraction)
    python run.py transcript --url "https://www.youtube.com/watch?v=..."
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import get_config, SERVICE_ROOT
from src.miners.transcript_miner import TranscriptMiner
from src.miners.entity_extractor import EntityExtractor
from src.models import TranscriptResult


def _safe_print(*args, **kwargs) -> None:
    """Print with fallback for Windows console encoding."""
    import io, sys
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_transcript(args: argparse.Namespace) -> None:
    """Extract transcript only from YouTube URL."""
    miner = TranscriptMiner()
    result = miner.extract(args.url)
    _safe_print(f"\n[OK] Transcript extracted: {len(result.transcript_text)} chars")
    _safe_print(f"   Title: {result.title}")
    _safe_print(f"   Language: {result.language}")
    _safe_print(f"   Duration: {result.duration_seconds:.0f}s")


def cmd_extract(args: argparse.Namespace) -> None:
    """Extract entities from an existing transcript JSON file."""
    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        _safe_print(f"[ERROR] Transcript file not found: {transcript_path}")
        sys.exit(1)

    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript = TranscriptResult(**data)

    extractor = EntityExtractor()
    keywords = extractor.extract(transcript)
    _print_keywords(keywords)


def cmd_mine(args: argparse.Namespace) -> None:
    """Full pipeline: URL -> transcript -> entity extraction."""
    _safe_print(f"[MINING] {args.url}\n")

    # Step 1: Transcript
    miner = TranscriptMiner()
    transcript = miner.extract(args.url)
    _safe_print(f"[OK] Transcript: {len(transcript.transcript_text)} chars")

    if not transcript.transcript_text.strip():
        _safe_print("[ERROR] No transcript content found. Cannot extract entities.")
        sys.exit(1)

    # Step 2: Entity extraction
    extractor = EntityExtractor()
    keywords = extractor.extract(transcript)
    _print_keywords(keywords)


def _print_keywords(keywords) -> None:
    _safe_print(f"\n{'='*60}")
    _safe_print("EXTRACTED ENTITIES")
    _safe_print(f"{'='*60}")
    if keywords.weapons:
        _safe_print(f"\n[WEAPONS] ({len(keywords.weapons)}):")
        for w in keywords.weapons:
            _safe_print(f"   - {w}")
    if keywords.locations:
        _safe_print(f"\n[LOCATIONS] ({len(keywords.locations)}):")
        for loc in keywords.locations:
            _safe_print(f"   - {loc}")
    if keywords.events:
        _safe_print(f"\n[EVENTS] ({len(keywords.events)}):")
        for ev in keywords.events:
            _safe_print(f"   - {ev}")
    if keywords.context:
        _safe_print(f"\n[CONTEXT] ({len(keywords.context)}):")
        for ctx in keywords.context:
            _safe_print(f"   - {ctx}")
    total = len(keywords.all_keywords)
    _safe_print(f"\n{'='*60}")
    _safe_print(f"Total: {total} keywords ready for crawling")
    _safe_print(f"{'='*60}")


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(
        description="News Intelligence Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # mine: full pipeline
    p_mine = sub.add_parser("mine", help="Full pipeline: URL → transcript → keywords")
    p_mine.add_argument("--url", required=True, help="YouTube video URL")

    # transcript: extract only
    p_trans = sub.add_parser("transcript", help="Extract transcript only")
    p_trans.add_argument("--url", required=True, help="YouTube video URL")

    # extract: entities from file
    p_ext = sub.add_parser("extract", help="Extract entities from transcript file")
    p_ext.add_argument("--transcript", required=True, help="Path to transcript JSON")

    args = parser.parse_args()

    if args.command == "mine":
        cmd_mine(args)
    elif args.command == "transcript":
        cmd_transcript(args)
    elif args.command == "extract":
        cmd_extract(args)


if __name__ == "__main__":
    main()
