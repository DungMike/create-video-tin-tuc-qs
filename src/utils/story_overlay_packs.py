"""Precompose Story Video overlays into a reusable alpha pack."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import threading
from pathlib import Path
from typing import Callable

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

_pack_lock = threading.Lock()
_PACK_VERSION = 1


def _target_size() -> tuple[int, int]:
    width, height = Config.TARGET_RESOLUTION.split("x", 1)
    return int(width), int(height)


def _pack_dir() -> str:
    os.makedirs(Config.STORY_OVERLAY_PACK_DIR, exist_ok=True)
    return Config.STORY_OVERLAY_PACK_DIR


def _file_fingerprint(path: str) -> dict:
    stat = os.stat(path)
    return {
        "path": str(Path(path).resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _duration_from_record(record: dict) -> float:
    try:
        return float(record.get("durationSeconds") or 0)
    except (TypeError, ValueError):
        return 0.0


def build_pack_signature(
    tv_noise_paths: list[tuple[dict, str]],
    waveform_record: dict | None,
    waveform_path: str | None,
) -> dict:
    """Build a stable signature for the currently active overlay stack."""
    width, height = _target_size()
    overlays: list[dict] = []
    max_duration = 0.0

    for record, overlay_path in tv_noise_paths:
        overlay_duration = _duration_from_record(record)
        if overlay_duration <= 0:
            overlay_duration = FFmpegHelper.probe_duration(overlay_path)
        max_duration = max(max_duration, overlay_duration)
        overlays.append(
            {
                "kind": "tv_noise",
                "id": str(record.get("id") or ""),
                "order": int(record.get("order") or 0),
                "x": "0",
                "y": "0",
                "file": _file_fingerprint(overlay_path),
            }
        )

    if waveform_record and waveform_path:
        from src.utils.waveform_overlays import overlay_position_expr

        x_expr, y_expr = overlay_position_expr(waveform_record)
        overlay_duration = _duration_from_record(waveform_record)
        if overlay_duration <= 0:
            overlay_duration = FFmpegHelper.probe_duration(waveform_path)
        max_duration = max(max_duration, overlay_duration)
        overlays.append(
            {
                "kind": "waveform",
                "id": str(waveform_record.get("id") or ""),
                "x": x_expr,
                "y": y_expr,
                "file": _file_fingerprint(waveform_path),
            }
        )

    pack_duration = max(1, int(Config.STORY_OVERLAY_PACK_DURATION_SECONDS))
    if max_duration > 0:
        pack_duration = max(pack_duration, int(math.ceil(max_duration)))

    return {
        "version": _PACK_VERSION,
        "target": {"width": width, "height": height, "fps": int(Config.TARGET_FPS)},
        "durationSeconds": pack_duration,
        "overlays": overlays,
    }


def pack_hash(signature: dict) -> str:
    payload = json.dumps(signature, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_precompose_filter(signature: dict) -> str:
    parts = ["[0:v]format=rgba,colorchannelmixer=aa=0[canvas]"]
    chain_label = "[canvas]"
    fps = int(signature["target"]["fps"])
    for index, overlay in enumerate(signature["overlays"], start=1):
        input_index = index
        overlay_label = f"ov{index}"
        out_label = f"out{index}"
        parts.append(f"[{input_index}:v]setpts=N/{fps}/TB[{overlay_label}]")
        parts.append(
            f"{chain_label}[{overlay_label}]overlay={overlay['x']}:{overlay['y']}:"
            f"format=auto:eof_action=repeat:eval=init[{out_label}]"
        )
        chain_label = f"[{out_label}]"
    parts.append(f"{chain_label}format=argb[v]")
    return ";".join(parts)


def build_precompose_command(signature: dict, output_path: str) -> list[str]:
    target = signature["target"]
    duration = int(signature["durationSeconds"])
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black@0:s={target['width']}x{target['height']}:r={target['fps']}:d={duration}",
    ]
    for overlay in signature["overlays"]:
        cmd.extend(["-stream_loop", "-1", "-i", overlay["file"]["path"]])
    cmd.extend(
        [
            "-filter_complex",
            build_precompose_filter(signature),
            "-map",
            "[v]",
            "-t",
            str(duration),
            "-an",
            "-c:v",
            "qtrle",
            output_path,
        ]
    )
    return cmd


def _pack_is_valid(pack_path: str, expected_duration: int) -> bool:
    if not os.path.isfile(pack_path) or os.path.getsize(pack_path) <= 0:
        return False
    duration = FFmpegHelper.probe_duration(pack_path)
    return duration >= max(1.0, expected_duration - 0.5)


def _pack_meta_matches(meta_path: str, signature: dict) -> bool:
    if not os.path.isfile(meta_path):
        return False
    try:
        with open(meta_path, "r", encoding="utf-8") as file_obj:
            existing = json.load(file_obj)
        return existing == signature
    except (OSError, json.JSONDecodeError):
        return False


def get_or_create_story_overlay_pack(
    tv_noise_paths: list[tuple[dict, str]],
    waveform_record: dict | None,
    waveform_path: str | None,
    *,
    cancel_callback: Callable[[], bool] | None = None,
) -> str | None:
    """Return a reusable alpha MOV containing all configured Story Video overlays."""
    if not Config.STORY_OVERLAY_PRECOMPOSE_ENABLED:
        return None

    signature = build_pack_signature(tv_noise_paths, waveform_record, waveform_path)
    if not signature["overlays"]:
        return None

    signature_hash = pack_hash(signature)
    pack_path = os.path.join(_pack_dir(), f"story_overlay_pack_{signature_hash}.mov")
    meta_path = os.path.join(_pack_dir(), f"story_overlay_pack_{signature_hash}.json")
    expected_duration = int(signature["durationSeconds"])

    with _pack_lock:
        if (
            os.path.isfile(pack_path)
            and os.path.getsize(pack_path) > 0
            and _pack_meta_matches(meta_path, signature)
        ):
            return pack_path

        if _pack_is_valid(pack_path, expected_duration):
            return pack_path

        if cancel_callback and cancel_callback():
            return None

        tmp_path = pack_path + ".tmp.mov"
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass

        logger.info(
            "[StoryOverlayPack] Building precomposed overlay pack: "
            f"path={pack_path}, duration={expected_duration}s, overlays={len(signature['overlays'])}"
        )
        ok = FFmpegHelper.run_command(
            build_precompose_command(signature, tmp_path),
            progress_callback=(lambda _payload: None) if cancel_callback else None,
            progress_total_seconds=expected_duration,
            cancel_callback=cancel_callback,
        )
        if not ok or not _pack_is_valid(tmp_path, expected_duration):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            logger.warning("[StoryOverlayPack] Failed to build overlay pack; falling back to direct overlays.")
            return None

        try:
            if os.path.exists(pack_path):
                os.remove(pack_path)
        except OSError:
            pass
        shutil.move(tmp_path, pack_path)
        with open(meta_path, "w", encoding="utf-8") as file_obj:
            json.dump(signature, file_obj, ensure_ascii=False, indent=2)
        return pack_path
