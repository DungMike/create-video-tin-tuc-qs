"""Script 2: Entity Extractor — use Gemini 2.5 Flash to extract structured entities from transcript."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from google import genai
from google.genai import types

from src.config import get_config, SERVICE_ROOT
from src.models import TranscriptResult, KeywordSet, ExtractedEntity

logger = logging.getLogger("news-intel")

_EXTRACTION_PROMPT = """\
You are a military/geopolitical intelligence analyst. Analyze the following transcript from a news video and extract structured entities.

## Transcript:
{transcript}

## Video Title:
{title}

## Instructions:
Extract ALL relevant entities from the transcript and categorize them. Be thorough — do not miss any weapons, locations, events, or contextual terms.

For each entity, provide:
- The original name (in whatever language it appears)
- The English name (if the original is not English)
- A relevance score from 0.0 to 1.0 (how central this entity is to the video)

## Output Format (JSON only, no markdown):
{{
  "weapons": ["HIMARS", "Leopard 2", "FPV drone", ...],
  "locations": ["Avdiivka", "Kherson", "Kursk", ...],
  "events": ["counteroffensive", "drone strike", "ammunition delivery", ...],
  "context": ["NATO", "military aid", "F-16 training", ...],
  "entities": [
    {{"name": "HIMARS", "name_en": "HIMARS", "category": "weapons", "relevance": 0.95}},
    {{"name": "Avdiivka", "name_en": "Avdiivka", "category": "locations", "relevance": 0.9}},
    ...
  ]
}}

IMPORTANT:
- Output ONLY valid JSON, no markdown fences, no explanation
- Include both Vietnamese and English names where applicable
- "weapons" includes vehicles, aircraft, missiles, drones, defense systems
- "locations" includes cities, regions, countries, frontlines
- "events" includes military operations, diplomatic events, policy changes
- "context" includes organizations, treaties, political entities, abstract topics
"""


class EntityExtractor:
    """Extract structured entities from video transcript using Gemini 2.5 Flash."""

    def __init__(self):
        cfg = get_config()
        llm_cfg = cfg.mining.get("llm", {})

        api_key = llm_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set in .env")

        self.client = genai.Client(api_key=api_key)
        self.model_name = llm_cfg.get("model", "gemini-2.5-flash-preview-05-20")
        self.gen_config = types.GenerateContentConfig(
            max_output_tokens=llm_cfg.get("max_tokens", 8192),
            temperature=llm_cfg.get("temperature", 0.1),
            response_mime_type="application/json",
        )
        self.output_dir = SERVICE_ROOT / "storage" / "keywords"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, transcript: TranscriptResult) -> KeywordSet:
        """Main entry — analyze transcript and return structured keywords."""
        logger.info("Extracting entities from transcript (%d chars)...",
                     len(transcript.transcript_text))

        if not transcript.transcript_text.strip():
            logger.warning("Empty transcript, returning empty keyword set")
            return KeywordSet(source_video=transcript.video_url)

        prompt = _EXTRACTION_PROMPT.format(
            transcript=transcript.transcript_text[:15000],
            title=transcript.title,
        )

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self.gen_config,
            )
            raw_text = response.text.strip()
            parsed = self._parse_response(raw_text)
        except Exception as exc:
            logger.error("Gemini API error: %s", exc)
            return KeywordSet(source_video=transcript.video_url)

        # Normalize entities — Gemini may return relevance as string
        entities = []
        for e in parsed.get("entities", []):
            rel = e.get("relevance", 0.5)
            if isinstance(rel, str):
                rel = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(rel.lower(), 0.5)
            e["relevance"] = float(rel)
            # Normalize category to our expected values
            cat = e.get("category", "context").lower()
            cat_map = {
                "weapon": "weapons", "weapons": "weapons",
                "location": "locations", "locations": "locations",
                "country": "locations", "city": "locations", "region": "locations",
                "event": "events", "events": "events",
                "person": "context", "organization": "context",
                "general term": "context",
            }
            e["category"] = cat_map.get(cat, "context")
            entities.append(ExtractedEntity(**e))

        keyword_set = KeywordSet(
            source_video=transcript.video_url,
            weapons=parsed.get("weapons", []),
            locations=parsed.get("locations", []),
            events=parsed.get("events", []),
            context=parsed.get("context", []),
            all_entities=entities,
        )

        # Save to file
        video_id = transcript.video_url.split("v=")[-1].split("&")[0][:20]
        out_path = self.output_dir / f"{video_id}_keywords.json"
        out_path.write_text(
            keyword_set.model_dump_json(indent=2), encoding="utf-8"
        )
        logger.info(
            "Extracted %d weapons, %d locations, %d events, %d context -> %s",
            len(keyword_set.weapons), len(keyword_set.locations),
            len(keyword_set.events), len(keyword_set.context), out_path,
        )

        return keyword_set

    def _parse_response(self, raw: str) -> dict:
        """Parse LLM response, stripping markdown fences if present."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM response as JSON: %s", exc)
            logger.debug("Raw response: %s", raw[:1000])
            return {}
