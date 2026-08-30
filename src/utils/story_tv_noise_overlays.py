"""Story TV noise overlay storage and preprocessing helpers."""

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

_BLEND_MODES = {"alpha", "screen", "luma"}


def _now() -> str:
    return datetime.now().isoformat()


def _overlay_dir() -> str:
    os.makedirs(Config.STORY_TV_NOISE_OVERLAY_DIR, exist_ok=True)
    return Config.STORY_TV_NOISE_OVERLAY_DIR


def _index_path() -> str:
    return os.path.join(_overlay_dir(), "index.json")


def _relative(filename: str) -> str:
    return f"story_tv_noise_overlays/{filename}" if filename else ""


def _absolute(filename: str) -> str:
    return os.path.join(_overlay_dir(), filename)


def _load_index_unlocked() -> dict:
    path = _index_path()
    if not os.path.isfile(path):
        return {"overlays": []}
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else {"overlays": []}
    except (OSError, json.JSONDecodeError):
        return {"overlays": []}


def _save_index_unlocked(data: dict):
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2, ensure_ascii=False)
    shutil.move(tmp, path)


def load_tv_noise_index() -> dict:
    with _index_lock:
        return _load_index_unlocked()


def save_tv_noise_index(data: dict):
    with _index_lock:
        _save_index_unlocked(data)


def _next_order(overlays: list[dict]) -> int:
    values = []
    for item in overlays:
        try:
            values.append(int(item.get("order", 0)))
        except (TypeError, ValueError):
            continue
    return (max(values) + 1) if values else 1


def _float_setting(record: dict, key: str, default: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    try:
        value = float(record.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def processed_abs_path(record: dict) -> str | None:
    filename = record.get("processedFilename")
    if not filename:
        return None
    path = _absolute(str(filename))
    return path if os.path.isfile(path) else None


def source_abs_path(record: dict) -> str | None:
    filename = record.get("filename")
    if not filename:
        return None
    path = _absolute(str(filename))
    return path if os.path.isfile(path) else None


def _overlay_sort_key(item: dict) -> tuple[int, str]:
    try:
        order = int(item.get("order") or 0)
    except (TypeError, ValueError):
        order = 0
    return order, str(item.get("id") or "")


def _enforce_single_enabled_unlocked(index: dict, keep_id: str) -> bool:
    """Turn off every overlay except ``keep_id``. Returns True if anything changed.

    Activation is exclusive: stacking several noise/sparkle layers piles their
    particles on top of each other (dust + sparkles + light sweep all at once),
    which is never what the render is meant to show.
    """
    changed = False
    for item in index.get("overlays", []):
        if str(item.get("id") or "") == keep_id:
            continue
        if item.get("enabled", True):
            item["enabled"] = False
            item["updatedAt"] = _now()
            changed = True
    return changed


def get_active_tv_noise_overlays() -> list[dict]:
    """The overlay layer(s) to composite at render time — at most ONE.

    An index carrying several enabled overlays (written before activation became
    exclusive) is normalised here: the first by order wins and the rest are
    switched off on disk, so the settings list and the render agree instead of
    the render silently stacking layers the user thought were just "available".
    """
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        enabled = [item for item in overlays if item.get("enabled", True)]
        ready = sorted(
            (
                item
                for item in enabled
                if item.get("status") == "ready" and processed_abs_path(item)
            ),
            key=_overlay_sort_key,
        )
        if len(enabled) <= 1:
            return ready[:1]

        keep = ready[0] if ready else sorted(enabled, key=_overlay_sort_key)[0]
        keep_id = str(keep.get("id") or "")
        if _enforce_single_enabled_unlocked(index, keep_id):
            _save_index_unlocked(index)
            logger.warning(
                f"[StoryTVNoise] {len(enabled)} overlays were enabled at once; keeping "
                f"{keep.get('name')!r} ({keep_id}) and disabling the rest — only one "
                f"effect is applied per render."
            )
        return [keep] if keep.get("status") == "ready" and processed_abs_path(keep) else []


def get_tv_noise_overlay(overlay_id: str) -> dict | None:
    overlays = load_tv_noise_index().get("overlays", [])
    return next((item for item in overlays if item.get("id") == overlay_id), None)


def _target_size() -> tuple[int, int]:
    width, height = Config.TARGET_RESOLUTION.split("x", 1)
    return int(width), int(height)


def overlay_blend_mode(record: dict) -> str:
    """One of:

    - "alpha"  — lumakey + uniform alpha (the default, for TV noise)
    - "screen" — blended at render time; right for black-background textures
      (light leaks, dust, bokeh). Forces the CPU overlay path.
    - "luma"   — alpha taken from the source's own brightness; for generated
      sparkle layers, where the glow around each particle has to fade out
      instead of ending at a hard keyed edge.

    Only "screen" is special downstream ("luma" composites like "alpha"), so
    adding it does not push the render off the GPU pack path.
    """
    mode = str(record.get("blendMode") or "alpha").strip().lower()
    return mode if mode in _BLEND_MODES else "alpha"


def preprocess_tv_noise_overlay(source_path: str, record: dict) -> str:
    overlay_id = str(record["id"])
    width, height = _target_size()
    target_fps = max(1, int(Config.TARGET_FPS))

    if overlay_blend_mode(record) == "screen":
        # Screen blending needs no alpha: just normalize size/fps. Opacity is
        # applied at render time (blend all_opacity), so it never forces a re-encode.
        processed_filename = f"{overlay_id}_screen.mp4"
        output_path = _absolute(processed_filename)
        filter_str = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"fps={target_fps},"
            "format=yuv420p"
        )
        cmd = [
            "ffmpeg", "-y", "-i", source_path, "-vf", filter_str, "-an",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", output_path,
        ]
        logger.info(f"[StoryTVNoise] Preprocessing screen-blend MP4: {output_path}")
        if not FFmpegHelper.run_command(cmd):
            raise RuntimeError("FFmpeg failed to preprocess TV noise overlay.")
        if not os.path.isfile(output_path):
            raise RuntimeError("Processed TV noise overlay was not created.")
        return processed_filename

    if overlay_blend_mode(record) == "luma":
        # Lop sang phu tren nen den (sparkle, light leak). Hai diem khac nhanh
        # "alpha", ca hai deu do bang thuc nghiem:
        #
        # 1. Alpha lay tu chinh do sang, KHONG dung lumakey. lumakey=0:0.08:0.02
        #    bien moi pixel sang hon 0.10 thanh duc hoan toan, nen quang sang
        #    quanh moi hat bi bet thanh mang trang duc thay vi tan dan.
        # 2. Mau va alpha phai TACH nhau. Neu de mau = chinh dai glow thi hat
        #    xam-150 chong len nen xam-126 gan nhu khong doi gi (do duoc: YMAX
        #    chi len 136). Anh sang phai la mau trang, hinh dang do alpha mang.
        #    `lumaGain` day mau len trang va chuan hoa dinh alpha ve 1.0; sau do
        #    `opacity` moi la nut chinh do manh. Voi gain=2: YMAX 136 -> 235.
        processed_filename = f"{overlay_id}_luma.mov"
        output_path = _absolute(processed_filename)
        opacity = _float_setting(record, "opacity", Config.STORY_TV_NOISE_OPACITY)
        gain = _float_setting(
            record, "lumaGain", Config.STORY_TV_NOISE_LUMA_GAIN, min_value=1.0, max_value=8.0
        )
        gain_expr = f"min(255,val*{gain:.3g})"
        filter_str = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"fps={target_fps},"
            "format=rgba,"
            "split[spk_a][spk_b];"
            f"[spk_b]format=gray,lutyuv=y='{gain_expr}'[spk_m];"
            f"[spk_a]lutrgb=r='{gain_expr}':g='{gain_expr}':b='{gain_expr}'[spk_c];"
            "[spk_c][spk_m]alphamerge,"
            f"colorchannelmixer=aa={opacity},"
            "format=argb"
        )
        cmd = [
            "ffmpeg", "-y", "-i", source_path, "-vf", filter_str, "-an",
            # qtrle, khong phai ProRes 4444. Ghi chu trong story_overlay_packs noi
            # qtrle decode cham la voi noi dung nhieu day; voi lop sang thua tren
            # nen den thi nguoc lai — do duoc tren cung file: qtrle 20.9x va
            # 16 MB, ProRes 4444 6.3x va 83 MB.
            "-c:v", "qtrle",
            output_path,
        ]
        logger.info(f"[StoryTVNoise] Preprocessing luma-alpha MOV: {output_path}")
        if not FFmpegHelper.run_command(cmd):
            raise RuntimeError("FFmpeg failed to preprocess luma-alpha overlay.")
        if not os.path.isfile(output_path):
            raise RuntimeError("Processed luma-alpha overlay was not created.")
        return processed_filename

    processed_filename = f"{overlay_id}_alpha.mov"
    output_path = _absolute(processed_filename)
    tolerance = _float_setting(record, "tolerance", Config.STORY_TV_NOISE_TOLERANCE)
    softness = _float_setting(record, "softness", Config.STORY_TV_NOISE_SOFTNESS)
    opacity = _float_setting(record, "opacity", Config.STORY_TV_NOISE_OPACITY)

    filter_str = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"fps={target_fps},"
        "format=rgba,"
        f"lumakey=0:{tolerance}:{softness},"
        f"colorchannelmixer=aa={opacity},"
        "format=argb"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        source_path,
        "-vf",
        filter_str,
        "-an",
        "-c:v",
        "qtrle",
        output_path,
    ]

    logger.info(f"[StoryTVNoise] Preprocessing alpha MOV: {output_path}")
    if not FFmpegHelper.run_command(cmd):
        raise RuntimeError("FFmpeg failed to preprocess TV noise overlay.")
    if not os.path.isfile(output_path):
        raise RuntimeError("Processed TV noise overlay was not created.")
    return processed_filename


def create_tv_noise_overlay(file_storage) -> dict:
    if not file_storage or not file_storage.filename:
        raise ValueError("No TV noise file provided.")

    overlay_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(file_storage.filename)
    filename = f"{overlay_id}_{safe_name}"
    filepath = _absolute(filename)
    file_storage.save(filepath)

    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = {
            "id": overlay_id,
            "name": os.path.splitext(safe_name)[0] or safe_name,
            "filename": filename,
            "relativePath": _relative(filename),
            "durationSeconds": round(FFmpegHelper.probe_duration(filepath), 2),
            "status": "processing",
            "enabled": True,
            "order": _next_order(overlays),
            "blendMode": "alpha",
            "opacity": Config.STORY_TV_NOISE_OPACITY,
            "tolerance": Config.STORY_TV_NOISE_TOLERANCE,
            "softness": Config.STORY_TV_NOISE_SOFTNESS,
            "error": None,
            "createdAt": _now(),
            "updatedAt": _now(),
        }
        overlays.append(record)
        index["overlays"] = overlays
        # A newly added layer becomes THE active effect: without this, every
        # upload/generated sparkle piled onto the ones before it at render time.
        _enforce_single_enabled_unlocked(index, overlay_id)
        _save_index_unlocked(index)
    return record


def create_generated_overlay(
    name: str,
    source_path: str,
    *,
    kind: str = "sparkle",
    meta: dict | None = None,
    blend_mode: str = "luma",
    opacity: float = 0.7,
    luma_gain: float | None = None,
) -> dict:
    """Register an already-generated video file as an overlay layer.

    Mirrors ``create_tv_noise_overlay`` but the source comes from a generator
    (see ``story_sparkle_presets``) instead of an upload, so the record also
    carries ``kind`` + ``meta`` describing how to rebuild it.

    The generated file is cached by parameter hash and may back several records,
    so it is COPIED into a per-record filename rather than moved: deleting one
    record must not take the shared cache with it.
    """
    if not source_path or not os.path.isfile(source_path):
        raise ValueError("Generated overlay source file is missing.")
    if blend_mode not in _BLEND_MODES:
        raise ValueError(f"Invalid blendMode: {blend_mode}")

    if luma_gain is None:
        luma_gain = Config.STORY_TV_NOISE_LUMA_GAIN
    overlay_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(os.path.basename(source_path)) or f"{kind}.mp4"
    filename = f"{overlay_id}_{safe_name}"
    shutil.copy2(source_path, _absolute(filename))

    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = {
            "id": overlay_id,
            "name": (name or "").strip() or os.path.splitext(safe_name)[0],
            "filename": filename,
            "relativePath": _relative(filename),
            "durationSeconds": round(FFmpegHelper.probe_duration(_absolute(filename)), 2),
            "status": "processing",
            "enabled": True,
            "order": _next_order(overlays),
            "kind": kind,
            "meta": meta or {},
            "blendMode": blend_mode,
            "opacity": max(0.0, min(1.0, float(opacity))),
            "lumaGain": max(1.0, min(8.0, float(luma_gain))),
            "tolerance": Config.STORY_TV_NOISE_TOLERANCE,
            "softness": Config.STORY_TV_NOISE_SOFTNESS,
            "error": None,
            "createdAt": _now(),
            "updatedAt": _now(),
        }
        overlays.append(record)
        index["overlays"] = overlays
        # A newly added layer becomes THE active effect: without this, every
        # upload/generated sparkle piled onto the ones before it at render time.
        _enforce_single_enabled_unlocked(index, overlay_id)
        _save_index_unlocked(index)
    return record


def create_tv_noise_placeholder(name: str, source_url: str = "") -> dict:
    overlay_id = str(uuid.uuid4())[:8]
    safe_name = secure_filename(name or f"youtube_tv_noise_{overlay_id}") or f"youtube_tv_noise_{overlay_id}"

    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = {
            "id": overlay_id,
            "name": os.path.splitext(safe_name)[0] or safe_name,
            "filename": "",
            "relativePath": "",
            "durationSeconds": 0,
            "sourceUrl": source_url,
            "status": "processing",
            "enabled": True,
            "order": _next_order(overlays),
            "blendMode": "alpha",
            "opacity": Config.STORY_TV_NOISE_OPACITY,
            "tolerance": Config.STORY_TV_NOISE_TOLERANCE,
            "softness": Config.STORY_TV_NOISE_SOFTNESS,
            "error": None,
            "createdAt": _now(),
            "updatedAt": _now(),
        }
        overlays.append(record)
        index["overlays"] = overlays
        # A newly added layer becomes THE active effect: without this, every
        # upload/generated sparkle piled onto the ones before it at render time.
        _enforce_single_enabled_unlocked(index, overlay_id)
        _save_index_unlocked(index)
    return record


def attach_tv_noise_source_file(overlay_id: str, file_path: str, display_name: str | None = None) -> dict | None:
    filename = os.path.basename(file_path)
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return None
        record["filename"] = filename
        record["relativePath"] = _relative(filename)
        record["durationSeconds"] = round(FFmpegHelper.probe_duration(file_path), 2)
        if display_name:
            record["name"] = display_name
        elif not record.get("name"):
            record["name"] = os.path.splitext(filename)[0]
        record["status"] = "processing"
        record["error"] = None
        record["updatedAt"] = _now()
        _save_index_unlocked(index)
        return record


def mark_tv_noise_processing(overlay_id: str) -> dict | None:
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return None
        record["status"] = "processing"
        record["error"] = None
        record["updatedAt"] = _now()
        _save_index_unlocked(index)
        return record


def run_tv_noise_preprocess(overlay_id: str) -> dict | None:
    record = mark_tv_noise_processing(overlay_id)
    if not record:
        return None

    src_path = source_abs_path(record)
    if not src_path:
        return mark_tv_noise_failed(overlay_id, "Source TV noise file is missing.")

    old_processed = record.get("processedFilename")
    try:
        processed_filename = preprocess_tv_noise_overlay(src_path, record)
    except Exception as exc:
        logger.error(f"[StoryTVNoise] Preprocess failed for {overlay_id}: {exc}", exc_info=True)
        return mark_tv_noise_failed(overlay_id, str(exc))

    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        updated = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not updated:
            return None
        updated["processedFilename"] = processed_filename
        updated["processedRelativePath"] = _relative(processed_filename)
        updated["status"] = "ready"
        updated["error"] = None
        updated["updatedAt"] = _now()
        _save_index_unlocked(index)

    if old_processed and old_processed != processed_filename:
        try:
            os.remove(_absolute(str(old_processed)))
        except OSError:
            pass
    return get_tv_noise_overlay(overlay_id)


def mark_tv_noise_failed(overlay_id: str, error: str) -> dict | None:
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return None
        record["status"] = "failed"
        record["error"] = error
        record["updatedAt"] = _now()
        _save_index_unlocked(index)
        return record


def update_tv_noise_overlay(overlay_id: str, updates: dict) -> tuple[dict | None, bool]:
    regenerate = False
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return None, False

        if "blendMode" in updates and updates["blendMode"] is not None:
            mode = str(updates["blendMode"]).strip().lower()
            if mode not in _BLEND_MODES:
                raise ValueError(f"Invalid blendMode: {mode}")
            if overlay_blend_mode(record) != mode:
                record["blendMode"] = mode
                regenerate = True

        # In screen mode the processed file depends on none of these (opacity is
        # applied at render time), so tweaking them never forces a re-encode.
        # In luma mode only opacity is baked in; tolerance/softness are unused.
        mode = overlay_blend_mode(record)
        settings_affect_file = mode != "screen"
        file_settings = ("opacity", "lumaGain") if mode == "luma" else ("opacity", "tolerance", "softness")
        for key in ("opacity", "tolerance", "softness", "lumaGain"):
            if key in updates and updates[key] is not None:
                bounds = (1.0, 8.0) if key == "lumaGain" else (0.0, 1.0)
                value = max(bounds[0], min(bounds[1], float(updates[key])))
                if record.get(key) != value:
                    record[key] = value
                    regenerate = regenerate or (settings_affect_file and key in file_settings)

        if "enabled" in updates:
            record["enabled"] = bool(updates["enabled"])
            # Exclusive: enabling this layer switches every other one off, so a
            # render never stacks two effects.
            if record["enabled"]:
                _enforce_single_enabled_unlocked(index, str(record.get("id") or ""))
        if "order" in updates and updates["order"] is not None:
            record["order"] = max(0, int(updates["order"]))
        if "name" in updates and str(updates["name"]).strip():
            record["name"] = str(updates["name"]).strip()

        already_processing = record.get("status") == "processing"

        if regenerate and already_processing:
            regenerate = False
        elif regenerate:
            record["status"] = "processing"
            record["error"] = None
        elif not already_processing and not processed_abs_path(record) and source_abs_path(record):
            record["status"] = "processing"
            record["error"] = None
            regenerate = True

        record["updatedAt"] = _now()
        _save_index_unlocked(index)
        return record, regenerate


def delete_tv_noise_overlay_record(overlay_id: str) -> bool:
    with _index_lock:
        index = _load_index_unlocked()
        overlays = index.get("overlays", [])
        record = next((item for item in overlays if item.get("id") == overlay_id), None)
        if not record:
            return False
        remaining = [item for item in overlays if item.get("id") != overlay_id]
        index["overlays"] = remaining
        _save_index_unlocked(index)

    for key in ("filename", "processedFilename"):
        filename = record.get(key)
        if filename:
            try:
                os.remove(_absolute(str(filename)))
            except OSError:
                pass
    return True
