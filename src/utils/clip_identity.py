"""Dinh danh video goc va clip cua mot asset thu vien, chi tu du lieu co san.

Dung cho che do "moi clip 1 lan" va DB Mongo. Ham thuan: khong doc/ghi file,
khong goi Mongo, khong sua asset -- ``index.json`` giu nguyen schema.

``source_key``: ``<provider>:<id>`` khi biet id Pexels/Pixabay, nguoc lai
``file:<prefix>`` (prefix ten file nguon, co hex8 ngau nhien nen van duy nhat).

``clip_key``: ``<source_key>#<piece:03d>@<giay>s``. Doan phim thu N cua cung mot
video goc, cat cung do dai, co cung key du nam o thu vien nao (ban bake giu
nguyen ``source_name``) -- nen luot dung tinh chung cho moi ban sao cua no.

Ten file nguon cua tung luong tai (xem ``video_source_downloader`` /
``story_video_prefetch`` / ``story_bulk_harvest``), phan biet bang tag
``session:<prefix>-...``:

- ``pf-``  prefetch         ``{p}_{idx:04d}_{id}_{hex8}``
- ``imp-`` import-selected  ``{p}_{idx:03d}_{id}_{hex8}``
- ``hvc-`` harvest commit   ``{p}_{id}_{hex8}``
- ``dl-``  dan link         ``{p}_{idx:03d}_{hex8}``   (khong co id)
- ``up-``  upload local     ``local_{idx:03d}_{hex8}`` (khong co id)

Ten dan link va harvest co cung hinh dang (``pixabay_149_ef0f9e99`` la harvest
id 149, ``pixabay_001_1a2b3c4d`` la link thu 2), va hex8 co khi toan chu so, nen
KHONG doan theo do dai: chi tin tag session.
"""

from __future__ import annotations

import re

_CLIP_NAME_RE = re.compile(r"^(.+)_clip_(\d+)\.mp4$", re.IGNORECASE)
_HEX8_RE = re.compile(r"^[0-9a-f]{8}$", re.IGNORECASE)
KNOWN_PROVIDERS = ("pixabay", "pexels")


def _tags(asset: dict) -> list[str]:
    return [str(tag) for tag in (asset.get("tags") or [])]


def _src_tag(tags: list[str]) -> tuple[str, str] | None:
    for tag in tags:
        if tag.startswith("src:"):
            parts = tag.split(":", 2)
            if len(parts) == 3 and parts[1] and parts[2]:
                return parts[1].lower(), parts[2]
    return None


def _session_prefix(tags: list[str]) -> str:
    for tag in tags:
        if tag.startswith("session:"):
            return tag.split(":", 1)[1].split("-", 1)[0].lower()
    return ""


def split_source_name(source_name: str) -> tuple[str, int] | None:
    """``(prefix nguon, so thu tu doan)`` tu ten ``..._clip_NNN.mp4``."""
    match = _CLIP_NAME_RE.match(str(source_name or ""))
    if not match:
        return None
    return match.group(1), int(match.group(2))


def provider_id_from_prefix(prefix: str, session: str) -> tuple[str, str] | None:
    """Doc ``(provider, id)`` tu prefix ten file nguon theo luong ``session``."""
    parts = str(prefix or "").split("_")
    if len(parts) < 3 or parts[0].lower() not in KNOWN_PROVIDERS or not _HEX8_RE.match(parts[-1]):
        return None
    provider = parts[0].lower()
    if session in ("pf", "imp") and len(parts) == 4 and parts[1].isdigit() and parts[2].isdigit():
        return provider, parts[2]
    if session == "hvc" and len(parts) == 3 and parts[1].isdigit():
        return provider, parts[1]
    return None


def source_key_for(provider: str | None, provider_id: str | None, prefix: str | None = None) -> str:
    provider = str(provider or "").strip().lower()
    provider_id = str(provider_id or "").strip()
    if provider and provider_id:
        return f"{provider}:{provider_id}"
    return f"file:{prefix or ''}"


def source_identity(asset: dict) -> dict:
    """``{source_key, provider, provider_id, prefix, piece}`` cua mot asset."""
    tags = _tags(asset)
    split = split_source_name(asset.get("source_name") or "")
    prefix, piece = split if split else (None, None)

    found = _src_tag(tags)
    if found is None and prefix:
        found = provider_id_from_prefix(prefix, _session_prefix(tags))
    provider, provider_id = found if found else (None, None)
    if provider is None:
        source_type = str(asset.get("source_type") or "").strip().lower()
        provider = source_type or None

    if provider_id:
        source_key = source_key_for(provider, provider_id)
    elif prefix:
        source_key = source_key_for(None, None, prefix)
    else:
        source_key = None
    return {
        "source_key": source_key,
        "provider": provider,
        "provider_id": provider_id,
        "prefix": prefix,
        "piece": piece,
    }


def clip_key_for(source_key: str, piece: int, duration) -> str:
    try:
        seconds = int(round(float(duration or 0)))
    except (TypeError, ValueError):
        seconds = 0
    return f"{source_key}#{int(piece):03d}@{seconds}s"


def clip_identity(library_id: str, asset: dict) -> tuple[str, str]:
    """``(source_key, clip_key)`` cua mot asset thu vien. Luon tra ve key.

    Asset khong co ten ``_clip_NNN`` (khong truy duoc nguon) dung key rieng theo
    thu vien + id asset, nen van dem duoc luot dung, chi khong gop voi ban sao.
    """
    info = source_identity(asset)
    source_key = info["source_key"]
    if source_key and info["piece"] is not None:
        return source_key, clip_key_for(source_key, info["piece"], asset.get("duration"))
    fallback = f"asset:{library_id}:{asset.get('id') or asset.get('relative_path') or ''}"
    return source_key or fallback, fallback
