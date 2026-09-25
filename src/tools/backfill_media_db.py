"""Nap du lieu cu vao MongoDB: clip + video goc cua moi thu vien, tu khoa da tim.

    venv/Scripts/python -m src.tools.backfill_media_db --dry-run   # chi dem, khong ghi
    venv/Scripts/python -m src.tools.backfill_media_db             # ghi that

Chi DOC cac file JSON (libraries.json, index.json, progress/manifest cua harvest,
_prefetch.json con tren dia) -- khong ghi lai file nao. Chay lai bao nhieu lan cung
duoc: clip/video goc la upsert, tu khoa chi them khi chua co (khong de len ban ghi
that cua luong dang chay). ``use_count`` cua clip moi nap luon la 0: truoc day
khong co du lieu clip nao da nam trong video nao.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

from src.config import Config
from src.db import keyword_repo, media_repo, mongo
from src.utils.story_library import load_libraries, load_story_library_index


def _load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


_PROVIDERS = ("pixabay", "pexels")


def _library_ids() -> list[str]:
    return [str(record.get("id")) for record in load_libraries() if record.get("id")]


# ------------------------------------------------------------------- keywords
def _harvest_keyword_pairs() -> tuple[dict[tuple[str, str], dict], dict[str, dict]]:
    """``({(provider, keyword): info}, {source_key: meta})`` tu cac job harvest."""
    pairs: dict[tuple[str, str], dict] = {}
    source_meta: dict[str, dict] = {}
    jobs_root = os.path.join(Config.STORY_RAW_DIR, "_harvest_jobs")
    for progress_path in sorted(glob.glob(os.path.join(jobs_root, "*", "progress.json"))):
        progress = _load_json(progress_path) or {}
        keywords = [str(k) for k in progress.get("keywords") or [] if str(k).strip()]
        providers = [str(p) for p in progress.get("providers") or []]
        done = int(progress.get("keywordIndex") or 0)
        if progress.get("status") == "completed":
            done = len(keywords)
        quota_stopped = progress.get("quotaStopped") or {}
        for keyword in keywords[:done]:
            for provider in providers:
                # Provider het quota giua job: khong biet dung o tu khoa nao -> "partial".
                status = "partial" if provider in quota_stopped else "completed"
                pairs.setdefault((provider, keyword), {"status": status, "flow": "harvest"})

        manifest = _load_json(os.path.join(os.path.dirname(progress_path), "manifest.json")) or {}
        for item in manifest.get("items") or []:
            provider = str(item.get("provider") or "")
            video_id = str(item.get("videoId") or "")
            if provider and video_id:
                source_meta[f"{provider}:{video_id}"] = item
    return pairs, source_meta


def _prefetch_keyword_pairs() -> tuple[dict[tuple[str, str], dict], dict[str, dict]]:
    pairs: dict[tuple[str, str], dict] = {}
    source_meta: dict[str, dict] = {}
    for manifest_path in sorted(glob.glob(os.path.join(Config.STORY_RAW_DIR, "pf-*", "_prefetch.json"))):
        manifest = _load_json(manifest_path) or {}
        provider = str(manifest.get("provider") or "")
        query = str(manifest.get("query") or "").strip()
        if provider and query and manifest.get("status") in {"ready", "committing", "completed"}:
            status = "partial" if manifest.get("truncated") else "completed"
            pairs.setdefault((provider, query), {"status": status, "flow": "prefetch"})
        for item in manifest.get("items") or []:
            video_id = str(item.get("videoId") or "")
            if provider and video_id:
                source_meta[f"{provider}:{video_id}"] = {**item, "keyword": query}
    return pairs, source_meta


def _keyword_insert_updates(pairs: dict[tuple[str, str], dict]) -> list[tuple[dict, dict]]:
    now = datetime.now(timezone.utc)
    updates = []
    for (provider, keyword), info in pairs.items():
        normalized = keyword_repo.normalize_keyword(keyword)
        if not normalized:
            continue
        updates.append((
            {"_id": keyword_repo.keyword_id(provider, keyword)},
            {"$setOnInsert": {
                "provider": keyword_repo.normalize_provider(provider),
                "keyword": keyword.strip(),
                "normalized": normalized,
                "status": info["status"],
                "last_status": info["status"],
                "flows": [info["flow"]],
                "sweep_count": 1,
                "videos_downloaded": 0,
                "first_searched_at": now,
                "last_searched_at": now,
                "backfilled": True,
                "schema_version": mongo.SCHEMA_VERSION,
            }},
        ))
    return updates


def _source_meta_updates(source_meta: dict[str, dict]) -> list[tuple[dict, dict]]:
    fields = {
        "title": "title", "pageUrl": "page_url", "author": "author", "duration": "duration",
        "width": "width", "height": "height", "thumbnailUrl": "thumbnail_url", "keyword": "keyword",
    }
    updates = []
    for source_key, item in source_meta.items():
        to_set = {db: item[src] for src, db in fields.items() if item.get(src) not in (None, "", 0)}
        if to_set:
            updates.append(({"_id": source_key}, {"$set": to_set}))
    return updates


# ----------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Chi dem, khong ghi Mongo.")
    parser.add_argument("--skip-clips", action="store_true", help="Bo qua clip/video goc.")
    parser.add_argument("--skip-keywords", action="store_true", help="Bo qua tu khoa.")
    args = parser.parse_args(argv)

    if not args.dry_run:
        if not mongo.is_available(force=True):
            print(mongo.unavailable_message(), file=sys.stderr)
            return 2
        db = mongo.get_db()
        print(f"Mongo: {Config.MONGODB_URI} / {Config.MONGODB_DB}")

    started = time.monotonic()
    totals = Counter()
    source_providers: dict[str, str] = {}
    all_clips: set[str] = set()
    tag_pairs: dict[tuple[str, str], dict] = {}

    if not args.skip_clips:
        for library_id in _library_ids():
            assets = load_story_library_index(library_id).get("assets", [])
            source_updates, clip_updates = media_repo.build_ingest_updates(library_id, None, assets)
            for flt, update in source_updates:
                source_providers[flt["_id"]] = update["$set"].get("provider") or "?"
            all_clips.update(flt["_id"] for flt, _update in clip_updates)
            for asset in assets:
                tags = [str(t) for t in asset.get("tags") or []]
                keyword = next((t.split(":", 1)[1] for t in tags if t.startswith("keyword:")), "")
                provider = str(asset.get("source_type") or "")
                if keyword.strip() and provider in _PROVIDERS:
                    tag_pairs.setdefault((provider, keyword), {"status": "completed", "flow": "harvest"})
            totals["assets"] += len(assets)
            print(f"  {library_id}: {len(assets)} clip, {len(source_updates)} video goc")
            if not args.dry_run:
                mongo.bulk_upsert(db[mongo.SOURCE_VIDEOS], source_updates)
                mongo.bulk_upsert(db[mongo.CLIPS], clip_updates)

    if not args.skip_keywords:
        harvest_pairs, harvest_meta = _harvest_keyword_pairs()
        prefetch_pairs, prefetch_meta = _prefetch_keyword_pairs()
        pairs = {**tag_pairs, **prefetch_pairs, **harvest_pairs}
        keyword_updates = _keyword_insert_updates(pairs)
        meta_updates = _source_meta_updates({**prefetch_meta, **harvest_meta})
        by_provider = Counter(provider for provider, _kw in pairs)
        by_status = Counter(info["status"] for info in pairs.values())
        print(
            f"Tu khoa: {len(pairs)} cap (harvest {len(harvest_pairs)}, prefetch {len(prefetch_pairs)}, "
            f"tag clip {len(tag_pairs)}) theo provider {dict(by_provider)}, trang thai {dict(by_status)}"
        )
        print(f"Metadata video goc tu manifest: {len(meta_updates)}")
        if not args.dry_run:
            mongo.bulk_upsert(db[mongo.SEARCH_KEYWORDS], keyword_updates)
            # Khong upsert: chi bo sung metadata cho video goc da co (tu buoc clip),
            # khong tao ban ghi cho video da tai nhung chua bao gio vao thu vien.
            for flt, update in meta_updates:
                db[mongo.SOURCE_VIDEOS].update_one(flt, update)

    if not args.dry_run:
        from src.utils.clip_usage import get_clip_usage_ledger

        get_clip_usage_ledger().flush_outbox()

    if not args.skip_clips:
        print(
            f"Tong: {totals['assets']} asset -> {len(all_clips)} clip key, {len(source_providers)} video goc "
            f"(theo provider: {dict(Counter(source_providers.values()))})"
        )
    print(f"{'[dry-run] ' if args.dry_run else ''}Xong trong {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
