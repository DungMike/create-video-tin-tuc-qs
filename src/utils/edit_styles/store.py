"""Saved edit-style records: one or more variants per type, each with its own params.

Stored in ``Config.STORY_EDIT_STYLE_DIR/index.json``. Every type gets one default
record the first time the list is read, enabled, so a fresh install already has
the whole catalogue to pick from. A record is what a batch rotates through: two
"newsroom" variants in different colours are two separate entries of the deck.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime

from src.config import Config
from src.utils.asset_rotation import deal_rotation
from src.utils.edit_styles import spec

_INDEX_LOCK = threading.Lock()
_NAME_MAX = 80


def _dir() -> str:
    os.makedirs(Config.STORY_EDIT_STYLE_DIR, exist_ok=True)
    return Config.STORY_EDIT_STYLE_DIR


def _index_path() -> str:
    return os.path.join(_dir(), "index.json")


def _now() -> str:
    return datetime.now().isoformat()


def _load_unlocked() -> dict:
    path = _index_path()
    data: dict = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
    records = data.get("styles")
    return {"styles": [r for r in records if isinstance(r, dict)] if isinstance(records, list) else [],
            "seededTypes": [str(t) for t in data.get("seededTypes") or []],
            "migrations": [str(m) for m in data.get("migrations") or []]}


def _save_unlocked(data: dict):
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _new_record(type_id: str, name: str | None = None, params: dict | None = None) -> dict:
    t = spec.get_type(type_id)
    if not t:
        raise ValueError(f"Kieu dung khong ton tai: {type_id}")
    now = _now()
    return {
        "id": uuid.uuid4().hex[:8],
        "type": type_id,
        "name": (" ".join(str(name or "").split())[:_NAME_MAX]) or t["name"],
        "enabled": True,
        "params": spec.sanitize_params(type_id, params),
        "createdAt": now,
        "updatedAt": now,
    }


def _seed_unlocked(data: dict) -> bool:
    """Give each type one default record (plus its ready-made ``presets``), once.
    A type the user emptied stays empty."""
    changed = False
    for t in spec.TYPES:
        if t["id"] in data["seededTypes"]:
            continue
        if not any(r.get("type") == t["id"] for r in data["styles"]):
            data["styles"].append(_new_record(t["id"]))
            for preset in t.get("presets") or []:
                data["styles"].append(_new_record(t["id"], preset["name"], preset["params"]))
        data["seededTypes"].append(t["id"])
        changed = True
    return changed


def _migrate_unlocked(data: dict) -> bool:
    """One-off fixes to records written by an older version of the catalogue."""
    changed = False
    if "chapterTextFromFileOnly" not in data["migrations"]:
        # Chapter text now comes only from the chapter file; records seeded earlier
        # still carried the generated "Phần {n}" title, which would keep printing
        # invented chapter names over videos that have no chapter file.
        for record in data["styles"]:
            params = record.get("params") or {}
            if params.get("autoTitle") == "Phần {n}":
                params["autoTitle"] = ""
        data["migrations"].append("chapterTextFromFileOnly")
        changed = True
    return changed


def _normalized(record: dict) -> dict:
    """Re-sanitize on read so a record written by an older spec still renders."""
    out = dict(record)
    t = spec.get_type(record.get("type"))
    if t:
        out["params"] = spec.sanitize_params(t["id"], record.get("params"))
        out["group"] = t["group"]
    out["enabled"] = bool(record.get("enabled", True))
    return out


def list_edit_styles() -> list[dict]:
    with _INDEX_LOCK:
        data = _load_unlocked()
        if _seed_unlocked(data) | _migrate_unlocked(data):
            _save_unlocked(data)
    order = {t["id"]: i for i, t in enumerate(spec.TYPES)}
    records = [_normalized(r) for r in data["styles"] if spec.get_type(r.get("type"))]
    return sorted(records, key=lambda r: (order.get(r["type"], 999), r.get("createdAt", "")))


def get_edit_style(style_id: str) -> dict | None:
    wanted = str(style_id or "").strip()
    if not wanted:
        return None
    return next((r for r in list_edit_styles() if r["id"] == wanted), None)


def create_edit_style(type_id: str, name: str | None = None, params: dict | None = None,
                      copy_from: str | None = None) -> dict:
    """New variant of a type, optionally copying another record's params."""
    if copy_from:
        source = get_edit_style(copy_from)
        if not source:
            raise ValueError("Ban ghi can nhan ban khong ton tai.")
        type_id = source["type"]
        params = {**source["params"], **(params or {})}
        name = name or f"{source['name']} (bản sao)"
    record = _new_record(type_id, name, params)
    with _INDEX_LOCK:
        data = _load_unlocked()
        _seed_unlocked(data)
        data["styles"].append(record)
        _save_unlocked(data)
    return _normalized(record)


def update_edit_style(style_id: str, updates: dict) -> dict | None:
    with _INDEX_LOCK:
        data = _load_unlocked()
        record = next((r for r in data["styles"] if r.get("id") == style_id), None)
        if not record:
            return None
        if "name" in updates and updates["name"] is not None:
            record["name"] = " ".join(str(updates["name"]).split())[:_NAME_MAX] or record["name"]
        if "enabled" in updates and updates["enabled"] is not None:
            record["enabled"] = bool(updates["enabled"])
        if isinstance(updates.get("params"), dict):
            merged = {**(record.get("params") or {}), **updates["params"]}
            record["params"] = spec.sanitize_params(record["type"], merged)
        record["updatedAt"] = _now()
        _save_unlocked(data)
        return _normalized(record)


def delete_edit_style(style_id: str) -> bool:
    with _INDEX_LOCK:
        data = _load_unlocked()
        remaining = [r for r in data["styles"] if r.get("id") != style_id]
        if len(remaining) == len(data["styles"]):
            return False
        data["styles"] = remaining
        _save_unlocked(data)
        _remove_orphan_images_unlocked(data)
    return True


# --------------------------------------------------------------------------- #
# Uploaded pictures (paper texture, column background, stickers...)
# --------------------------------------------------------------------------- #
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_IMAGE_MAX_SIDE = 3840


def images_dir() -> str:
    path = os.path.join(_dir(), "images")
    os.makedirs(path, exist_ok=True)
    return path


def image_abspath(ref) -> str | None:
    """Absolute path of a stored picture reference, or None if it is not one of ours."""
    clean = spec.clean_image_ref(ref)
    if not clean:
        return None
    path = os.path.join(_dir(), *clean.split("/"))
    return path if os.path.isfile(path) else None


def _referenced_images(data: dict) -> set[str]:
    refs: set[str] = set()
    for record in data["styles"]:
        for field in spec.image_fields(record.get("type")):
            value = (record.get("params") or {}).get(field["key"])
            values = value if isinstance(value, list) else [value]
            refs.update(v for v in values if isinstance(v, str))
    return refs


def _remove_orphan_images_unlocked(data: dict):
    """A picture no record points at any more is deleted (duplicates share files)."""
    keep = _referenced_images(data)
    folder = images_dir()
    for name in os.listdir(folder):
        if f"images/{name}" not in keep:
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def _image_field(record: dict, key: str) -> dict:
    field = next((f for f in spec.image_fields(record["type"]) if f["key"] == key), None)
    if not field:
        raise ValueError(f"Kieu {record['type']} khong co truong anh '{key}'.")
    return field


def add_image(style_id: str, key: str, stream, filename: str) -> dict | None:
    """Store an uploaded picture for an image field. Re-encoded to PNG, so only real images get in."""
    from PIL import Image, UnidentifiedImageError

    ext = os.path.splitext(str(filename or ""))[1].lower()
    if ext not in _IMAGE_EXTENSIONS:
        raise ValueError("Chi nhan anh PNG, JPG hoac WEBP.")
    try:
        img = Image.open(stream)
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("File khong phai anh hop le.") from exc
    img = img.convert("RGBA")
    if max(img.size) > _IMAGE_MAX_SIDE:
        img.thumbnail((_IMAGE_MAX_SIDE, _IMAGE_MAX_SIDE))

    with _INDEX_LOCK:
        data = _load_unlocked()
        record = next((r for r in data["styles"] if r.get("id") == style_id), None)
        if not record:
            return None
        field = _image_field(record, key)
        params = dict(record.get("params") or {})
        current = params.get(key)
        if field["type"] == "imageList" and len(current or []) >= field.get("maxItems", 6):
            raise ValueError(f"Toi da {field.get('maxItems', 6)} anh.")
        name = f"{style_id}_{key}_{uuid.uuid4().hex[:8]}.png"
        img.save(os.path.join(images_dir(), name), "PNG")
        ref = f"images/{name}"
        params[key] = [*(current or []), ref] if field["type"] == "imageList" else ref
        record["params"] = spec.sanitize_params(record["type"], params)
        record["updatedAt"] = _now()
        _save_unlocked(data)
        _remove_orphan_images_unlocked(data)
        return _normalized(record)


def remove_image(style_id: str, key: str, ref: str | None = None) -> dict | None:
    """Clear an image field, or drop one picture (``ref``) from an image list."""
    with _INDEX_LOCK:
        data = _load_unlocked()
        record = next((r for r in data["styles"] if r.get("id") == style_id), None)
        if not record:
            return None
        field = _image_field(record, key)
        params = dict(record.get("params") or {})
        if field["type"] == "imageList":
            params[key] = [v for v in params.get(key) or [] if ref and v != ref]
        else:
            params[key] = None
        record["params"] = spec.sanitize_params(record["type"], params)
        record["updatedAt"] = _now()
        _save_unlocked(data)
        _remove_orphan_images_unlocked(data)
        return _normalized(record)


def _usable(style_id: str, group: str) -> dict | None:
    record = get_edit_style(style_id)
    if not record or not record.get("enabled", True) or record.get("group") != group:
        return None
    return record


def build_layout_rotation(style_ids: list[str], count: int) -> list[str]:
    """Deal enabled layout records across ``count`` videos (shuffled deck, no starving)."""
    usable = [sid for sid in dict.fromkeys(style_ids) if _usable(sid, "layout")]
    return deal_rotation(usable, count)


def resolve_modifiers(style_ids: list[str]) -> list[dict]:
    """Enabled modifier records, at most one per type (a second of the same type is ignored)."""
    out, seen = [], set()
    for sid in dict.fromkeys(style_ids or []):
        record = _usable(sid, "modifier")
        if record and record["type"] not in seen:
            out.append(record)
            seen.add(record["type"])
    return out


def deal_modifier_rotation(style_ids: list[str], count: int) -> list[list[str]]:
    """Modifier ids for each of ``count`` videos.

    A video still gets at most one record per type. Picking several records of
    the same type (three sweep looks, say) deals them across the batch like
    layouts, so neighbouring videos look different; a type picked once goes to
    every video exactly as before.
    """
    by_type: dict[str, list[str]] = {}
    for sid in dict.fromkeys(style_ids or []):
        record = _usable(sid, "modifier")
        if record:
            by_type.setdefault(record["type"], []).append(record["id"])
    out: list[list[str]] = [[] for _ in range(max(0, count))]
    for ids in by_type.values():
        dealt = deal_rotation(ids, count) if len(ids) > 1 else [ids[0]] * count
        for item, sid in zip(out, dealt):
            item.append(sid)
    return out


def type_flag(style_id: str, flag: str) -> bool:
    record = get_edit_style(style_id)
    t = spec.get_type(record["type"]) if record else None
    return bool(t and t.get(flag))
