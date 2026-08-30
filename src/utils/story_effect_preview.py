"""Render the whole effect stack onto your own short clips, before a real render.

The TV-effect preview (``generate_tv_effect_style_preview``) shows the colour
style alone, and ``/tv-noise-demo`` shows one overlay alone. Neither answers the
question you actually have before launching a batch: *what does the finished
frame look like* with this style, these sparkle/noise layers, the waveform and
the CTA all stacked together — on footage like the library's, not on one clip.

So: upload a handful of 3-5s clips once, then re-render the preview as often as
you like while tweaking settings. The uploads are normalised to the canonical
library format and concatenated ONCE into a base video (``base.mp4``); each
re-render only re-runs the effect pass over that base, which is the cheap part.

The overlay chain here mirrors ``StoryVideoPipeline._apply_story_overlays``'s
direct (non-pack) chain filter-for-filter, so the preview and the render agree.
It deliberately stays on the CPU path: previews are short, and the CPU chain is
the one that can express every blend mode.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime

from werkzeug.utils import secure_filename

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

_index_lock = threading.Lock()

MAX_SOURCE_CLIPS = 24
MAX_PREVIEW_SECONDS = 90.0


class EffectPreviewError(Exception):
    """Raised when a preview source or render cannot be produced."""


def _now() -> str:
    return datetime.now().isoformat()


def _root_dir() -> str:
    os.makedirs(Config.STORY_EFFECT_PREVIEW_DIR, exist_ok=True)
    return Config.STORY_EFFECT_PREVIEW_DIR


def _source_dir(source_id: str) -> str:
    path = os.path.join(_root_dir(), source_id)
    os.makedirs(path, exist_ok=True)
    return path


def _index_path() -> str:
    return os.path.join(_root_dir(), "index.json")


def _relative(*parts: str) -> str:
    return "/".join(("story_effect_previews",) + parts)


def _load_index_unlocked() -> dict:
    path = _index_path()
    if not os.path.isfile(path):
        return {"sources": []}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {"sources": []}
    except (OSError, json.JSONDecodeError):
        return {"sources": []}


def _save_index_unlocked(data: dict):
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    shutil.move(tmp, path)


def load_sources() -> list[dict]:
    with _index_lock:
        sources = _load_index_unlocked().get("sources", [])
    return sorted(sources, key=lambda item: str(item.get("createdAt") or ""), reverse=True)


def get_source(source_id: str) -> dict | None:
    return next((item for item in load_sources() if item.get("id") == source_id), None)


def _upsert_source(record: dict):
    with _index_lock:
        data = _load_index_unlocked()
        sources = [item for item in data.get("sources", []) if item.get("id") != record["id"]]
        sources.append(record)
        data["sources"] = sources
        _save_index_unlocked(data)


def delete_source(source_id: str) -> bool:
    with _index_lock:
        data = _load_index_unlocked()
        sources = data.get("sources", [])
        record = next((item for item in sources if item.get("id") == source_id), None)
        if not record:
            return False
        data["sources"] = [item for item in sources if item.get("id") != source_id]
        _save_index_unlocked(data)
    shutil.rmtree(os.path.join(_root_dir(), source_id), ignore_errors=True)
    return True


# --------------------------------------------------------------------------- #
# Source set: normalise + concat the uploaded clips once
# --------------------------------------------------------------------------- #
def build_source_set(file_storages, name: str = "") -> dict:
    """Normalise each uploaded clip to the canonical library format and concat.

    Normalising through ``clip_canonical`` is what makes the preview honest: the
    render only ever sees clips in that exact format, so a preview built from raw
    uploads (different fps / colour range / SAR) would not be comparable.
    """
    from src.utils.clip_canonical import canonical_output_args, canonical_video_filter
    from src.utils.clip_spec_validation import probe_clip_spec

    uploads = [item for item in (file_storages or []) if item and item.filename]
    if not uploads:
        raise EffectPreviewError("Chua chon video nao.")
    if len(uploads) > MAX_SOURCE_CLIPS:
        raise EffectPreviewError(f"Toi da {MAX_SOURCE_CLIPS} clip moi lan.")

    source_id = str(uuid.uuid4())[:8]
    directory = _source_dir(source_id)
    raw_dir = os.path.join(directory, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    normalized: list[str] = []
    clips: list[dict] = []
    total_seconds = 0.0
    try:
        for index, upload in enumerate(uploads):
            safe = secure_filename(upload.filename) or f"clip_{index}.mp4"
            raw_path = os.path.join(raw_dir, f"{index:02d}_{safe}")
            upload.save(raw_path)

            out_path = os.path.join(directory, f"norm_{index:02d}.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", raw_path,
                "-vf", canonical_video_filter(probe_clip_spec(raw_path)),
                "-an",
            ]
            cmd.extend(FFmpegHelper.get_nvenc_flags())
            cmd.extend(canonical_output_args())
            cmd.extend(["-movflags", "+faststart", out_path])
            if not FFmpegHelper.run_command(cmd) or not os.path.isfile(out_path):
                raise EffectPreviewError(f"Khong chuan hoa duoc clip: {upload.filename}")

            duration = FFmpegHelper.probe_duration(out_path)
            total_seconds += duration
            normalized.append(out_path)
            clips.append({"name": safe, "durationSeconds": round(duration, 2)})

        base_path = os.path.join(directory, "base.mp4")
        if len(normalized) == 1:
            shutil.copy2(normalized[0], base_path)
        else:
            list_path = os.path.join(directory, "concat.txt")
            with open(list_path, "w", encoding="utf-8") as handle:
                for path in normalized:
                    handle.write(f"file '{os.path.abspath(path).replace(os.sep, '/')}'\n")
            # Every part came out of canonical_video_filter with identical params,
            # so the concat is a stream copy.
            ok = FFmpegHelper.run_command(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                 "-c", "copy", "-movflags", "+faststart", base_path]
            )
            if not ok or not os.path.isfile(base_path):
                raise EffectPreviewError("Khong ghep duoc cac clip thanh video nen.")
    except EffectPreviewError:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    for path in normalized:
        try:
            os.remove(path)
        except OSError:
            pass
    shutil.rmtree(raw_dir, ignore_errors=True)

    record = {
        "id": source_id,
        "name": (name or "").strip() or f"Bo {len(clips)} clip",
        "clipCount": len(clips),
        "clips": clips,
        "durationSeconds": round(min(total_seconds, MAX_PREVIEW_SECONDS), 2),
        "basePath": _relative(source_id, "base.mp4"),
        "previewPath": None,
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    _upsert_source(record)
    logger.info(f"[EffectPreview] Built source set {source_id}: {len(clips)} clips, {total_seconds:.1f}s")
    return record


# --------------------------------------------------------------------------- #
# Preview render: the same chain the render pass builds
# --------------------------------------------------------------------------- #
def collect_active_layers() -> dict:
    """Everything the render would composite, resolved to on-disk paths."""
    from src.utils.story_cta_overlay import (
        get_active_cta_overlay,
        overlay_position_expr as cta_position_expr,
        processed_abs_path as cta_processed_abs_path,
    )
    from src.utils.story_tv_noise_overlays import (
        get_active_tv_noise_overlays,
        overlay_blend_mode,
        processed_abs_path as noise_processed_abs_path,
    )
    from src.utils.waveform_overlays import (
        get_default_waveform_overlay,
        overlay_position_expr,
        processed_abs_path as waveform_processed_abs_path,
    )

    noise: list[tuple[dict, str]] = []
    for record in get_active_tv_noise_overlays():
        path = noise_processed_abs_path(record)
        if path:
            noise.append((record, path))

    waveform_record = get_default_waveform_overlay()
    waveform_path = waveform_processed_abs_path(waveform_record) if waveform_record else None
    cta_record = get_active_cta_overlay()
    cta_path = cta_processed_abs_path(cta_record) if cta_record else None

    return {
        "noise": noise,
        "noiseLabels": [
            {
                "id": record.get("id"),
                "name": record.get("name"),
                "kind": record.get("kind") or "upload",
                "blendMode": overlay_blend_mode(record),
                "opacity": record.get("opacity"),
            }
            for record, _path in noise
        ],
        "waveform": (waveform_record, waveform_path) if waveform_path else None,
        "waveformPos": overlay_position_expr(waveform_record) if waveform_path else None,
        "cta": (cta_record, cta_path) if cta_path else None,
        "ctaPos": cta_position_expr(cta_record) if cta_path else None,
    }


def build_preview_command(
    base_path: str,
    output_path: str,
    *,
    include_style: bool = True,
    include_overlays: bool = True,
    duration: float | None = None,
    compare: bool = False,
    style_filter: str | None = None,
    layers: dict | None = None,
) -> list[str]:
    """Mirror of the render's direct overlay chain, over the preview base.

    With ``compare`` the untouched base is stacked beside the result (each at half
    width, so the output stays 1920 wide) — the fastest way to tell whether an
    effect is actually doing what you wanted.
    """
    from src.processors.crt_effect_processor import get_tv_effect_filter
    from src.utils.story_tv_noise_overlays import overlay_blend_mode

    if style_filter is None:
        style_filter = get_tv_effect_filter() if include_style else ""
    if not include_style:
        style_filter = ""
    layers = layers if layers is not None else collect_active_layers()

    noise = layers["noise"] if include_overlays else []
    waveform = layers["waveform"] if include_overlays else None
    cta = layers["cta"] if include_overlays else None

    cmd = ["ffmpeg", "-y", "-i", base_path]
    for _record, path in noise:
        cmd.extend(["-stream_loop", "-1", "-i", path])
    waveform_index = None
    if waveform:
        waveform_index = 1 + len(noise)
        cmd.extend(["-stream_loop", "-1", "-i", waveform[1]])
    cta_index = None
    if cta:
        cta_index = 1 + len(noise) + (1 if waveform else 0)
        cmd.extend(["-stream_loop", "-1", "-i", cta[1]])

    parts: list[str] = []
    chain = "[0:v]"
    if compare:
        parts.append("[0:v]split=2[cmp_raw][cmp_src]")
        chain = "[cmp_src]"
    if style_filter:
        parts.append(f"{chain}{style_filter}[styled]")
        chain = "[styled]"

    for index, (record, _path) in enumerate(noise):
        input_index = index + 1
        label = f"noise{index}"
        out_label = f"noiseout{index}"
        if overlay_blend_mode(record) == "screen":
            opacity = max(0.0, min(1.0, float(record.get("opacity") or Config.STORY_TV_NOISE_OPACITY)))
            parts.append(f"[{input_index}:v]setpts=PTS-STARTPTS,format=yuv420p[{label}]")
            parts.append(f"{chain}format=yuv420p[{label}base]")
            parts.append(
                f"[{label}base][{label}]"
                f"blend=all_mode=screen:all_opacity={opacity}:eof_action=repeat[{out_label}]"
            )
        else:
            parts.append(f"[{input_index}:v]setpts=PTS-STARTPTS[{label}]")
            parts.append(
                f"{chain}[{label}]overlay=0:0:format=auto:eof_action=repeat:eval=init[{out_label}]"
            )
        chain = f"[{out_label}]"

    if waveform and waveform_index is not None:
        x_expr, y_expr = layers["waveformPos"]
        parts.append(f"[{waveform_index}:v]setpts=PTS-STARTPTS[wave]")
        parts.append(
            f"{chain}[wave]overlay={x_expr}:{y_expr}:format=auto:eof_action=repeat:eval=init[waveout]"
        )
        chain = "[waveout]"

    if cta and cta_index is not None:
        x_expr, y_expr = layers["ctaPos"]
        parts.append(f"[{cta_index}:v]setpts=PTS-STARTPTS[cta]")
        parts.append(
            f"{chain}[cta]overlay={x_expr}:{y_expr}:format=auto:eof_action=repeat:eval=init[ctaout]"
        )
        chain = "[ctaout]"

    if compare:
        width, height = _target_size()
        half_w, half_h = (width // 2) // 2 * 2, (height // 2) // 2 * 2
        box = f"box=1:boxcolor=black@0.55:boxborderw=6:fontsize={max(16, half_h // 22)}"
        parts.append(
            f"[cmp_raw]scale={half_w}:{half_h},"
            f"drawtext=text='GOC':x=14:y=h-th-14:fontcolor=white:{box}[cmp_a]"
        )
        parts.append(
            f"{chain}scale={half_w}:{half_h},"
            f"drawtext=text='CO HIEU UNG':x=14:y=h-th-14:fontcolor=white:{box}[cmp_b]"
        )
        parts.append("[cmp_a][cmp_b]hstack=inputs=2,format=yuv420p[v]")
    else:
        parts.append(f"{chain}format=yuv420p[v]")

    cmd.extend(["-filter_complex", ";".join(parts), "-map", "[v]", "-an"])
    if duration and duration > 0:
        cmd.extend(["-t", str(min(duration, MAX_PREVIEW_SECONDS))])
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(["-movflags", "+faststart", output_path])
    return cmd


def _target_size() -> tuple[int, int]:
    width, height = Config.TARGET_RESOLUTION.split("x", 1)
    return int(width), int(height)


def render_preview(
    source_id: str,
    *,
    include_style: bool = True,
    include_overlays: bool = True,
    compare: bool = False,
    max_seconds: float | None = None,
) -> dict:
    """Re-render the preview for a stored source set. Returns the updated record."""
    record = get_source(source_id)
    if not record:
        raise EffectPreviewError("Khong tim thay bo clip preview.")

    directory = _source_dir(source_id)
    base_path = os.path.join(directory, "base.mp4")
    if not os.path.isfile(base_path):
        raise EffectPreviewError("Video nen cua bo clip khong con tren dia.")

    layers = collect_active_layers()
    # New filename per render so the browser cannot serve a stale cached preview.
    filename = f"preview_{uuid.uuid4().hex[:8]}.mp4"
    output_path = os.path.join(directory, filename)

    duration = float(record.get("durationSeconds") or 0)
    if max_seconds and max_seconds > 0:
        duration = min(duration, float(max_seconds)) if duration else float(max_seconds)

    cmd = build_preview_command(
        base_path,
        output_path,
        include_style=include_style,
        include_overlays=include_overlays,
        duration=duration,
        compare=compare,
        layers=layers,
    )
    logger.info(
        f"[EffectPreview] Rendering preview for {source_id} "
        f"(style={include_style}, overlays={include_overlays}, compare={compare}, {duration:.1f}s)"
    )
    if not FFmpegHelper.run_command(cmd) or not os.path.isfile(output_path):
        raise EffectPreviewError("FFmpeg khong render duoc preview.")

    old = record.get("previewFilename")
    if old and old != filename:
        try:
            os.remove(os.path.join(directory, str(old)))
        except OSError:
            pass

    record = {
        **record,
        "previewFilename": filename,
        "previewPath": _relative(source_id, filename),
        "appliedStyle": include_style,
        "appliedOverlays": include_overlays,
        "appliedCompare": compare,
        "previewSeconds": round(duration, 2),
        "appliedLayers": layers["noiseLabels"] if include_overlays else [],
        "updatedAt": _now(),
    }
    _upsert_source(record)
    return record
