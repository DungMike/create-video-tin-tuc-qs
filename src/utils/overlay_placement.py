"""Where a Story Video overlay (waveform, CTA) sits on the frame.

Two ways to say it, and a record uses whichever it carries:

* **Free coordinates** — ``x`` / ``y``, the overlay's top-left corner in the
  output frame's own pixel space. This is what the settings page writes when you
  drag an overlay around, and it can express any position at all.
* **Corner + margin** — the original scheme: one of four corners, inset by a
  single margin. Every record predating the drag editor has only this, so it
  stays the fallback and must keep producing byte-identical expressions: the
  overlay pack cache is keyed on these strings, and a changed string means every
  cached pack gets rebuilt.

Both collapse to the ``x:y`` pair ffmpeg's ``overlay`` / ``overlay_cuda`` want.
The corner form has to stay symbolic (``W-w-24``) because it depends on the
overlay's size, which only ffmpeg knows at that point; free coordinates are
resolved here to plain numbers.
"""

from __future__ import annotations

from src.config import Config

_CORNERS = {"top_left", "top_right", "bottom_left", "bottom_right"}


def target_size() -> tuple[int, int]:
    width, height = Config.TARGET_RESOLUTION.split("x", 1)
    return int(width), int(height)


def probe_overlay_size(path: str) -> tuple[int, int] | None:
    """Pixel size of a processed overlay file, or None when it cannot be read.

    The processed alpha MOV is built with ``scale={width}:-2``, so its height is
    whatever preserved the source aspect — nobody knows it until ffmpeg has run.
    Both the drag editor (to draw the overlay at its true size) and the clamping
    below need it, so it is probed once after preprocessing and stored.
    """
    from src.utils.clip_spec_validation import probe_clip_spec

    spec = probe_clip_spec(path)
    if not spec:
        return None
    try:
        return int(spec["w"]), int(spec["h"])
    except (KeyError, TypeError, ValueError):
        return None


def backfill_processed_sizes(records: list[dict], processed_path) -> bool:
    """Probe and store the size of any record predating ``processedWidth``.

    Overlays uploaded before the drag editor existed have no size recorded, and
    the editor cannot draw them without one. Called when the settings page lists
    overlays, so the migration happens once, on the screen that needs it.

    ``processed_path`` maps a record to its processed file, or None when missing.
    Returns whether anything changed, i.e. whether the index needs saving.
    """
    changed = False
    for record in records:
        if record.get("processedWidth") and record.get("processedHeight"):
            continue
        path = processed_path(record)
        if not path:
            continue
        size = probe_overlay_size(path)
        if size:
            record["processedWidth"], record["processedHeight"] = size
            changed = True
    return changed


def corner_expr(position: str, margin: int) -> tuple[str, str]:
    """ffmpeg x:y expressions for one of the four corners, inset by ``margin``."""
    safe_margin = max(0, int(margin))
    if position == "top_left":
        return str(safe_margin), str(safe_margin)
    if position == "top_right":
        return f"W-w-{safe_margin}", str(safe_margin)
    if position == "bottom_left":
        return str(safe_margin), f"H-h-{safe_margin}"
    return f"W-w-{safe_margin}", f"H-h-{safe_margin}"


def _coord(record: dict, key: str) -> int | None:
    value = record.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _even_down(value: int) -> int:
    """Round down to an even number.

    Chroma-subsampled formats site their colour samples on even pixels, so an odd
    overlay offset can shift the colour plane by half a pixel against the luma.
    Rounding down (never up) keeps a clamped coordinate inside the bound it was
    just clamped to.
    """
    return value - (value % 2)


def clamp_position(record: dict, x: int, y: int) -> tuple[int, int]:
    """Snap free coordinates to an even offset that keeps the overlay on-frame.

    Fully on-frame, deliberately: the GPU path composites with ``overlay_cuda``,
    which has no meaning for a negative offset, so an overlay allowed to hang off
    the edge would render differently on the two paths.
    """
    frame_w, frame_h = target_size()
    overlay_w = _coord(record, "processedWidth") or 0
    overlay_h = _coord(record, "processedHeight") or 0
    max_x = max(0, frame_w - overlay_w) if overlay_w else frame_w
    max_y = max(0, frame_h - overlay_h) if overlay_h else frame_h
    return (
        _even_down(max(0, min(x, max_x))),
        _even_down(max(0, min(y, max_y))),
    )


def apply_placement_updates(record: dict, updates: dict) -> None:
    """Move an overlay in place: corner, margin, or free coordinates.

    None of these touch the processed file, so moving an overlay never costs a
    re-encode. Coordinates are clamped as they are stored, not only as they are
    rendered, so the number the settings page shows back is the one that ends up
    in the video.
    """
    for key in ("position", "margin"):
        if key in updates and updates[key] is not None:
            record[key] = updates[key]

    if "x" not in updates and "y" not in updates:
        return

    # An explicit null drops the free placement, handing the overlay back to the
    # corner + margin above.
    if updates.get("x") is None or updates.get("y") is None:
        record.pop("x", None)
        record.pop("y", None)
        return

    record["x"], record["y"] = clamp_position(record, int(updates["x"]), int(updates["y"]))


def placement_expr(
    record: dict,
    *,
    default_position: str,
    default_margin: int,
) -> tuple[str, str]:
    """Resolve a record's placement to an ffmpeg x:y pair."""
    x = _coord(record, "x")
    y = _coord(record, "y")
    if x is not None and y is not None:
        clamped_x, clamped_y = clamp_position(record, x, y)
        return str(clamped_x), str(clamped_y)

    position = str(record.get("position") or default_position)
    if position not in _CORNERS:
        position = default_position
    return corner_expr(position, int(record.get("margin") or default_margin))
