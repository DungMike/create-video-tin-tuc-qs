"""News Script Parser — Parses marker-based news bulletin scripts.

Markers:
    //intro          → Title / header / intro text
    //resume-news-N  → Summary for news item N  
    //detail-news-N  → Full detail for news item N
    //end-outro      → Closing / outro text

Output structure:
{
    "intro": {"text": "..."},
    "newsItems": [
        {"id": 1, "resumeText": "...", "detailText": "..."},
        ...
    ],
    "outro": {"text": "..."}
}
"""

import re
from typing import Any


_MARKER_PATTERN = re.compile(
    r"^//(intro|resume-news-(\d+)|detail-news-(\d+)|end-outro)\s*$",
    re.IGNORECASE,
)


class NewsScriptParseError(Exception):
    """Raised when the script cannot be parsed."""
    pass


def parse_news_script(raw_text: str) -> dict[str, Any]:
    """Parse a news bulletin script from raw text.

    Returns:
        {
            "intro": {"text": str},
            "newsItems": [{"id": int, "resumeText": str, "detailText": str}, ...],
            "outro": {"text": str},
        }

    Raises:
        NewsScriptParseError on validation failures.
    """
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    segments: list[tuple[str, int | None, list[str]]] = []
    # Lines before any marker are treated as implicit intro
    current_type: str | None = "intro"
    current_id: int | None = None
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        match = _MARKER_PATTERN.match(stripped)
        if match:
            # Save previous segment (skip implicit intro if empty)
            if current_type is not None:
                text = "\n".join(current_lines).strip()
                if text or current_type != "intro":
                    segments.append((current_type, current_id, current_lines))

            full_marker = match.group(1).lower()
            if full_marker == "intro":
                current_type = "intro"
                current_id = None
            elif match.group(2) is not None:
                current_type = "resume"
                current_id = int(match.group(2))
            elif match.group(3) is not None:
                current_type = "detail"
                current_id = int(match.group(3))
            elif full_marker == "end-outro":
                current_type = "outro"
                current_id = None
            current_lines = []
        else:
            current_lines.append(line)

    # Save last segment
    if current_type is not None:
        segments.append((current_type, current_id, current_lines))

    # Build result
    intro_text = ""
    resumes: dict[int, str] = {}
    details: dict[int, str] = {}
    outro_text = ""

    for seg_type, seg_id, seg_lines in segments:
        text = "\n".join(seg_lines).strip()
        if seg_type == "intro":
            intro_text = text
        elif seg_type == "resume" and seg_id is not None:
            resumes[seg_id] = text
        elif seg_type == "detail" and seg_id is not None:
            details[seg_id] = text
        elif seg_type == "outro":
            outro_text = text

    # Validate: must have at least some segments
    if not resumes and not details:
        raise NewsScriptParseError(
            "Khong tim thay marker //resume-news-N hoac //detail-news-N nao trong kich ban."
        )

    # Build news items from the union of resume and detail IDs.
    # Items without a matching resume get empty resumeText; without detail get empty detailText.
    all_ids = sorted(set(resumes.keys()) | set(details.keys()))
    news_items = []
    for news_id in all_ids:
        news_items.append({
            "id": news_id,
            "resumeText": resumes.get(news_id, ""),
            "detailText": details.get(news_id, ""),
        })

    return {
        "intro": {"text": intro_text},
        "newsItems": news_items,
        "outro": {"text": outro_text},
    }


def get_all_tts_segments(parsed_script: dict) -> list[dict]:
    """Convert parsed script into a list of TTS segments for audio generation.

    Returns a list of dicts, each with:
        - segmentType: "intro" | "resume" | "detail" | "outro"
        - segmentKey: unique key e.g. "intro", "resume_1", "detail_1", "outro"
        - newsId: int | None
        - text: str
    """
    segments = []

    intro_text = parsed_script.get("intro", {}).get("text", "")
    if intro_text:
        segments.append({
            "segmentType": "intro",
            "segmentKey": "intro",
            "newsId": None,
            "text": intro_text,
        })

    for item in parsed_script.get("newsItems", []):
        segments.append({
            "segmentType": "resume",
            "segmentKey": f"resume_{item['id']}",
            "newsId": item["id"],
            "text": item["resumeText"],
        })

    for item in parsed_script.get("newsItems", []):
        segments.append({
            "segmentType": "detail",
            "segmentKey": f"detail_{item['id']}",
            "newsId": item["id"],
            "text": item["detailText"],
        })

    outro_text = parsed_script.get("outro", {}).get("text", "")
    if outro_text:
        segments.append({
            "segmentType": "outro",
            "segmentKey": "outro",
            "newsId": None,
            "text": outro_text,
        })

    return segments
