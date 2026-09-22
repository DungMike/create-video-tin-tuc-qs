"""Saved subtitle styles: named font/size/colour/effect combos a batch rotates through.

One list, two kinds of entry:

- built-in: one virtual record per ``SUBTITLE_PRESETS`` entry, id ``preset:<presetId>``.
  It carries no font or size, so the batch form fills those in — a Thai batch keeps
  its Thai font. "Deleting" one only hides it (from this list and from the effect
  dropdown); :func:`restore_builtin_styles` brings it back.
- custom: a snapshot of the subtitle form, stored in ``index.json`` under the same
  camelCase keys as the batch payload, so the route can feed a record straight into
  ``_subtitle_config_from_payload``. Deleting one removes it for good.
"""

import json
import os
import threading
import uuid
from datetime import datetime

from src.config import Config
from src.utils.asset_rotation import deal_rotation
from src.utils.story_subtitles import SUBTITLE_PRESETS, _hex_to_ass_colour, get_subtitle_preset

BUILTIN_PREFIX = "preset:"

# Everything a style may carry, spelled as in the /batch/create payload.
STYLE_FIELD_KEYS = (
    "subtitleFont",
    "subtitlePreset",
    "subtitleFontScale",
    "subtitleTextColor",
    "subtitleOutlineColor",
    "subtitleOutlineWidth",
    "subtitleBackgroundEnabled",
    "subtitleBackColor",
    "subtitleBackOpacity",
)

_NAME_MAX_CHARS = 80
_FONT_MAX_CHARS = 120

# Flask serves requests on threads; every read-modify-write of index.json holds this.
_INDEX_LOCK = threading.Lock()


def _index_path() -> str:
    os.makedirs(Config.STORY_SUBTITLE_STYLE_DIR, exist_ok=True)
    return os.path.join(Config.STORY_SUBTITLE_STYLE_DIR, "index.json")


def _load_index() -> dict:
    data: dict = {}
    path = _index_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                loaded = json.load(file_obj)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
    styles = data.get("styles")
    hidden = data.get("hiddenPresetIds")
    return {
        "styles": [item for item in styles if isinstance(item, dict)] if isinstance(styles, list) else [],
        "hiddenPresetIds": [str(item) for item in hidden] if isinstance(hidden, list) else [],
    }


def _save_index(data: dict):
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _builtin_record(preset: dict) -> dict:
    return {
        "id": f"{BUILTIN_PREFIX}{preset['id']}",
        "name": preset["name"],
        "description": preset.get("description", ""),
        "builtin": True,
        "subtitlePreset": preset["id"],
    }


def hidden_preset_ids() -> set[str]:
    """Built-in presets the user deleted (ids that no longer exist in code are dropped)."""
    with _INDEX_LOCK:
        hidden = _load_index()["hiddenPresetIds"]
    return {preset_id for preset_id in hidden if get_subtitle_preset(preset_id)}


def list_subtitle_styles() -> list[dict]:
    """Visible built-in styles first, then custom ones in the order they were saved."""
    with _INDEX_LOCK:
        data = _load_index()
    hidden = set(data["hiddenPresetIds"])
    builtins = [_builtin_record(preset) for preset in SUBTITLE_PRESETS if preset["id"] not in hidden]
    customs = [{**item, "builtin": False} for item in data["styles"]]
    return builtins + customs


def get_subtitle_style(style_id: str) -> dict | None:
    """A style by id, or None when unknown or a built-in the user has hidden."""
    wanted = str(style_id or "").strip()
    if not wanted:
        return None
    if wanted.startswith(BUILTIN_PREFIX):
        preset_id = wanted[len(BUILTIN_PREFIX):]
        preset = get_subtitle_preset(preset_id)
        if not preset or preset_id in hidden_preset_ids():
            return None
        return _builtin_record(preset)
    with _INDEX_LOCK:
        styles = _load_index()["styles"]
    record = next((item for item in styles if item.get("id") == wanted), None)
    return {**record, "builtin": False} if record else None


def _clean_colour(value) -> str | None:
    # _hex_to_ass_colour is the same gate build_ass applies, so a stored colour is
    # always one the renderer will accept.
    if not isinstance(value, str) or _hex_to_ass_colour(value) is None:
        return None
    return "#" + value.strip().lstrip("#").upper()


def _parse_float(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return None if parsed != parsed else parsed  # NaN


def clean_style_fields(data: dict) -> dict:
    """Validate a style payload into the stored subset of STYLE_FIELD_KEYS.

    An unknown preset is an error; everything else that is malformed is dropped, so
    the preset's own value applies — the same rule the render form follows.
    """
    preset_id = str(data.get("subtitlePreset") or "").strip() or "clean"
    if not get_subtitle_preset(preset_id):
        raise ValueError(f"Hieu ung phu de khong ton tai: {preset_id}")
    fields: dict = {"subtitlePreset": preset_id}

    font = " ".join(str(data.get("subtitleFont") or "").split())[:_FONT_MAX_CHARS]
    if font:
        fields["subtitleFont"] = font

    # Same rule as the render route: a non-positive scale means "not set".
    font_scale = _parse_float(data.get("subtitleFontScale"))
    if font_scale is not None and font_scale > 0:
        fields["subtitleFontScale"] = max(0.3, min(4.0, font_scale))

    for key in ("subtitleTextColor", "subtitleOutlineColor", "subtitleBackColor"):
        colour = _clean_colour(data.get(key))
        if colour:
            fields[key] = colour

    outline_width = data.get("subtitleOutlineWidth")
    if outline_width is not None and str(outline_width).strip() != "":
        try:
            fields["subtitleOutlineWidth"] = max(0, min(20, int(outline_width)))
        except (TypeError, ValueError):
            pass

    if data.get("subtitleBackgroundEnabled") is not None:
        fields["subtitleBackgroundEnabled"] = bool(data.get("subtitleBackgroundEnabled"))

    back_opacity = _parse_float(data.get("subtitleBackOpacity"))
    if back_opacity is not None:
        fields["subtitleBackOpacity"] = max(0.0, min(1.0, back_opacity))

    return fields


def create_subtitle_style(data: dict) -> dict:
    fields = clean_style_fields(data)
    name = " ".join(str(data.get("name") or "").split())[:_NAME_MAX_CHARS]
    if not name:
        preset = get_subtitle_preset(fields["subtitlePreset"])
        font = fields.get("subtitleFont") or Config.STORY_SUBTITLE_DEFAULT_FONT
        name = f"{font} · {preset['name']}"

    record = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        **fields,
        "createdAt": datetime.now().isoformat(),
    }
    with _INDEX_LOCK:
        index = _load_index()
        index["styles"].append(record)
        _save_index(index)
    return {**record, "builtin": False}


def delete_subtitle_style(style_id: str) -> bool:
    """Remove a custom style, or hide a built-in one. False when there is nothing to delete."""
    wanted = str(style_id or "").strip()
    if not wanted:
        return False
    with _INDEX_LOCK:
        index = _load_index()
        if wanted.startswith(BUILTIN_PREFIX):
            preset_id = wanted[len(BUILTIN_PREFIX):]
            if not get_subtitle_preset(preset_id) or preset_id in index["hiddenPresetIds"]:
                return False
            index["hiddenPresetIds"].append(preset_id)
        else:
            remaining = [item for item in index["styles"] if item.get("id") != wanted]
            if len(remaining) == len(index["styles"]):
                return False
            index["styles"] = remaining
        _save_index(index)
    return True


def restore_builtin_styles() -> int:
    """Un-hide every built-in style. Returns how many came back."""
    with _INDEX_LOCK:
        index = _load_index()
        restored = sum(1 for preset_id in index["hiddenPresetIds"] if get_subtitle_preset(preset_id))
        index["hiddenPresetIds"] = []
        _save_index(index)
    return restored


def build_subtitle_style_rotation(style_ids: list[str], count: int) -> list[str]:
    """Chia cac cau hinh phu de da chon cho ``count`` video (bo bai xao).

    Id khong tra cuu duoc (da xoa, preset co san da an) bi loai truoc khi chia,
    dung nhu :func:`src.utils.waveform_overlays.build_waveform_rotation`.
    """
    usable = [style_id for style_id in dict.fromkeys(style_ids) if get_subtitle_style(style_id)]
    return deal_rotation(usable, count)
