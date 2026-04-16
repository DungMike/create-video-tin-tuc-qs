import json
import os
import random
from datetime import datetime
from pathlib import Path

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import storage_absolute_path, storage_relative_path
from src.utils.logger import logger

ANIMATION_PRESETS = [
    {"id": "zoom_in_center_soft", "name": "Zoom In Center Soft", "description": "Zoom nháº¹ vÃ o trung tÃ¢m.", "ffmpeg_filter": "z='min(zoom+0.0013,1.35)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"},
    {"id": "zoom_out_center_soft", "name": "Zoom Out Center Soft", "description": "Zoom out nháº¹ tá»« trung tÃ¢m.", "ffmpeg_filter": "z='max(1.35-0.0013*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"},
    {"id": "pan_left_to_right", "name": "Pan Left To Right", "description": "Pan ngang tá»« trÃ¡i sang pháº£i.", "ffmpeg_filter": "z='1.15':x='min(iw-iw/zoom,(iw-iw/zoom)*on/(fps*6))':y='ih/2-(ih/zoom/2)'"},
    {"id": "pan_right_to_left", "name": "Pan Right To Left", "description": "Pan ngang tá»« pháº£i sang trÃ¡i.", "ffmpeg_filter": "z='1.15':x='max(0,(iw-iw/zoom)*(1-on/(fps*6)))':y='ih/2-(ih/zoom/2)'"},
    {"id": "pan_top_to_bottom", "name": "Pan Top To Bottom", "description": "Pan dá»c tá»« trÃªn xuá»‘ng dÆ°á»›i.", "ffmpeg_filter": "z='1.15':x='iw/2-(iw/zoom/2)':y='min(ih-ih/zoom,(ih-ih/zoom)*on/(fps*6))'"},
    {"id": "pan_bottom_to_top", "name": "Pan Bottom To Top", "description": "Pan dá»c tá»« dÆ°á»›i lÃªn trÃªn.", "ffmpeg_filter": "z='1.15':x='iw/2-(iw/zoom/2)':y='max(0,(ih-ih/zoom)*(1-on/(fps*6)))'"},
    {"id": "diagonal_tl_br", "name": "Diagonal TL BR", "description": "Di chuyá»ƒn chÃ©o tá»« trÃªn trÃ¡i xuá»‘ng dÆ°á»›i pháº£i.", "ffmpeg_filter": "z='1.18':x='min(iw-iw/zoom,(iw-iw/zoom)*on/(fps*6))':y='min(ih-ih/zoom,(ih-ih/zoom)*on/(fps*6))'"},
    {"id": "diagonal_br_tl", "name": "Diagonal BR TL", "description": "Di chuyá»ƒn chÃ©o tá»« dÆ°á»›i pháº£i lÃªn trÃªn trÃ¡i.", "ffmpeg_filter": "z='1.18':x='max(0,(iw-iw/zoom)*(1-on/(fps*6)))':y='max(0,(ih-ih/zoom)*(1-on/(fps*6)))'"},
    {"id": "diagonal_tr_bl", "name": "Diagonal TR BL", "description": "Di chuyá»ƒn chÃ©o tá»« trÃªn pháº£i xuá»‘ng dÆ°á»›i trÃ¡i.", "ffmpeg_filter": "z='1.18':x='max(0,(iw-iw/zoom)*(1-on/(fps*6)))':y='min(ih-ih/zoom,(ih-ih/zoom)*on/(fps*6))'"},
    {"id": "diagonal_bl_tr", "name": "Diagonal BL TR", "description": "Di chuyá»ƒn chÃ©o tá»« dÆ°á»›i trÃ¡i lÃªn trÃªn pháº£i.", "ffmpeg_filter": "z='1.18':x='min(iw-iw/zoom,(iw-iw/zoom)*on/(fps*6))':y='max(0,(ih-ih/zoom)*(1-on/(fps*6)))'"},
    {"id": "zoom_in_left_focus", "name": "Zoom In Left Focus", "description": "Zoom vÃ o vÃ¹ng trÃ¡i khung hÃ¬nh.", "ffmpeg_filter": "z='min(zoom+0.0015,1.4)':x='0':y='ih/2-(ih/zoom/2)'"},
    {"id": "zoom_in_right_focus", "name": "Zoom In Right Focus", "description": "Zoom vÃ o vÃ¹ng pháº£i khung hÃ¬nh.", "ffmpeg_filter": "z='min(zoom+0.0015,1.4)':x='iw-iw/zoom':y='ih/2-(ih/zoom/2)'"},
    {"id": "zoom_in_top_focus", "name": "Zoom In Top Focus", "description": "Zoom vÃ o vÃ¹ng trÃªn.", "ffmpeg_filter": "z='min(zoom+0.0015,1.4)':x='iw/2-(iw/zoom/2)':y='0'"},
    {"id": "zoom_in_bottom_focus", "name": "Zoom In Bottom Focus", "description": "Zoom vÃ o vÃ¹ng dÆ°á»›i.", "ffmpeg_filter": "z='min(zoom+0.0015,1.4)':x='iw/2-(iw/zoom/2)':y='ih-ih/zoom'"},
    {"id": "dolly_in_center_fast", "name": "Dolly In Center Fast", "description": "Zoom nhanh hÆ¡n vÃ o trung tÃ¢m.", "ffmpeg_filter": "z='min(zoom+0.002,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"},
    {"id": "dolly_out_center_fast", "name": "Dolly Out Center Fast", "description": "Zoom out nhanh hÆ¡n tá»« trung tÃ¢m.", "ffmpeg_filter": "z='max(1.5-0.002*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"},
    {"id": "drift_left_zoom_in", "name": "Drift Left Zoom In", "description": "Vá»«a zoom in vá»«a drift vá» bÃªn trÃ¡i.", "ffmpeg_filter": "z='min(zoom+0.0014,1.35)':x='max(0,iw/2-(iw/zoom/2)-1.6*on)':y='ih/2-(ih/zoom/2)'"},
    {"id": "drift_right_zoom_in", "name": "Drift Right Zoom In", "description": "Vá»«a zoom in vá»«a drift vá» bÃªn pháº£i.", "ffmpeg_filter": "z='min(zoom+0.0014,1.35)':x='min(iw-iw/zoom,iw/2-(iw/zoom/2)+1.6*on)':y='ih/2-(ih/zoom/2)'"},
    {"id": "drift_up_zoom_in", "name": "Drift Up Zoom In", "description": "Vá»«a zoom in vá»«a drift lÃªn trÃªn.", "ffmpeg_filter": "z='min(zoom+0.0014,1.35)':x='iw/2-(iw/zoom/2)':y='max(0,ih/2-(ih/zoom/2)-1.6*on)'"},
    {"id": "drift_down_zoom_in", "name": "Drift Down Zoom In", "description": "Vá»«a zoom in vá»«a drift xuá»‘ng dÆ°á»›i.", "ffmpeg_filter": "z='min(zoom+0.0014,1.35)':x='iw/2-(iw/zoom/2)':y='min(ih-ih/zoom,ih/2-(ih/zoom/2)+1.6*on)'"},
]

TRANSITION_PRESETS = [
    {"id": "fade", "name": "Fade", "description": "Fade chuáº©n giá»¯a hai áº£nh.", "xfade_transition": "fade"},
    {"id": "fadeblack", "name": "Fade Black", "description": "Fade qua mÃ n hÃ¬nh Ä‘en.", "xfade_transition": "fadeblack"},
    {"id": "fadewhite", "name": "Fade White", "description": "Fade qua mÃ n hÃ¬nh tráº¯ng.", "xfade_transition": "fadewhite"},
    {"id": "dissolve", "name": "Dissolve", "description": "Dissolve nhiá»…u háº¡t.", "xfade_transition": "dissolve"},
    {"id": "pixelize", "name": "Pixelize", "description": "Pixelize trÆ°á»›c khi chuyá»ƒn.", "xfade_transition": "pixelize"},
    {"id": "radial", "name": "Radial", "description": "Má»Ÿ theo kiá»ƒu radial.", "xfade_transition": "radial"},
    {"id": "circleopen", "name": "Circle Open", "description": "Má»Ÿ vÃ²ng trÃ²n.", "xfade_transition": "circleopen"},
    {"id": "circleclose", "name": "Circle Close", "description": "KhÃ©p vÃ²ng trÃ²n.", "xfade_transition": "circleclose"},
    {"id": "circlecrop", "name": "Circle Crop", "description": "Crop vÃ²ng trÃ²n.", "xfade_transition": "circlecrop"},
    {"id": "rectcrop", "name": "Rect Crop", "description": "Crop hÃ¬nh chá»¯ nháº­t.", "xfade_transition": "rectcrop"},
    {"id": "wipeleft", "name": "Wipe Left", "description": "Wipe sang trÃ¡i.", "xfade_transition": "wipeleft"},
    {"id": "wiperight", "name": "Wipe Right", "description": "Wipe sang pháº£i.", "xfade_transition": "wiperight"},
    {"id": "wipeup", "name": "Wipe Up", "description": "Wipe lÃªn trÃªn.", "xfade_transition": "wipeup"},
    {"id": "wipedown", "name": "Wipe Down", "description": "Wipe xuá»‘ng dÆ°á»›i.", "xfade_transition": "wipedown"},
    {"id": "slideleft", "name": "Slide Left", "description": "Slide sang trÃ¡i.", "xfade_transition": "slideleft"},
    {"id": "slideright", "name": "Slide Right", "description": "Slide sang pháº£i.", "xfade_transition": "slideright"},
    {"id": "slideup", "name": "Slide Up", "description": "Slide lÃªn trÃªn.", "xfade_transition": "slideup"},
    {"id": "slidedown", "name": "Slide Down", "description": "Slide xuá»‘ng dÆ°á»›i.", "xfade_transition": "slidedown"},
    {"id": "smoothleft", "name": "Smooth Left", "description": "Smooth wipe sang trÃ¡i.", "xfade_transition": "smoothleft"},
    {"id": "smoothright", "name": "Smooth Right", "description": "Smooth wipe sang pháº£i.", "xfade_transition": "smoothright"},
]

DEFAULT_TRANSITION_FALLBACK = TRANSITION_PRESETS[0]
DEFAULT_ANIMATION_FALLBACK = ANIMATION_PRESETS[0]
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_METADATA_CACHE: dict[str, list[dict]] = {}


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def ensure_effects_library_dirs() -> dict[str, str]:
    root_dir = os.path.abspath(Config.EFFECTS_LIBRARY_DIR)
    animation_dir = os.path.join(root_dir, "animations")
    transition_dir = os.path.join(root_dir, "transitions")
    os.makedirs(animation_dir, exist_ok=True)
    os.makedirs(transition_dir, exist_ok=True)
    return {"root": root_dir, "animations": animation_dir, "transitions": transition_dir}


def build_zoompan_filter(ffmpeg_filter: str, duration_seconds: float) -> str:
    total_frames = max(1, int(round(Config.TARGET_FPS * duration_seconds)))
    normalized_filter = ffmpeg_filter.replace("fps*6", str(total_frames))
    target_width = Config.TARGET_RESOLUTION.split("x")[0]
    return (
        f"scale={target_width}:-1,zoompan={normalized_filter}:d={total_frames}:"
        f"s={Config.TARGET_RESOLUTION}:fps={Config.TARGET_FPS}"
    )


def build_zoompan_filter_with_fade(ffmpeg_filter: str, duration_seconds: float, fade_duration: float = 0.0) -> str:
    """Build zoompan filter with optional fade-in at start and fade-out at end.

    When ``fade_duration > 0`` the returned filter chain will include
    ``fade=in`` and ``fade=out`` filters so that each clip can be concatenated
    with stream-copy (no re-encode) while still providing smooth visual
    transitions.
    """
    base = build_zoompan_filter(ffmpeg_filter, duration_seconds)
    if fade_duration <= 0:
        return base
    fade_out_start = max(0, duration_seconds - fade_duration)
    fps = Config.TARGET_FPS
    return (
        f"{base},"
        f"fade=t=in:st=0:d={fade_duration},"
        f"fade=t=out:st={fade_out_start}:d={fade_duration}"
    )


def _candidate_images() -> list[str]:
    root_dir = Path(os.path.abspath(Config.STORAGE_DIR)) / "raw_images"
    if not root_dir.exists():
        return []
    candidates = [
        str(path)
        for path in sorted(root_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
    ]
    return candidates


def _pick_reference_images() -> tuple[str | None, str | None]:
    candidates = _candidate_images()
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0], candidates[0]
    return candidates[0], candidates[1]


def _metadata_path(kind: str, preset_id: str) -> str:
    dirs = ensure_effects_library_dirs()
    return os.path.join(dirs["animations" if kind == "animation" else "transitions"], f"{preset_id}.json")


def _preview_path(kind: str, preset_id: str) -> str:
    dirs = ensure_effects_library_dirs()
    return os.path.join(dirs["animations" if kind == "animation" else "transitions"], f"{preset_id}.mp4")


def _load_existing_metadata(kind: str, preset_id: str) -> dict:
    metadata_path = _metadata_path(kind, preset_id)
    if not os.path.isfile(metadata_path):
        return {}
    try:
        with open(metadata_path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning(f"Cannot load effect metadata {metadata_path}: {exc}")
        return {}


def _save_metadata(metadata_path: str, payload: dict):
    with open(metadata_path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)


def _create_animation_preview(
    image_path: str,
    ffmpeg_filter: str,
    output_path: str,
    duration_seconds: float,
    fade_duration: float = 0.0,
) -> bool:
    if fade_duration > 0:
        vf = build_zoompan_filter_with_fade(ffmpeg_filter, duration_seconds, fade_duration)
    else:
        vf = build_zoompan_filter(ffmpeg_filter, duration_seconds)
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        image_path,
        "-vf",
        vf,
        "-t",
        str(duration_seconds),
    ]
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(["-pix_fmt", "yuv420p", output_path])
    return FFmpegHelper.run_command(cmd, timeout_seconds=Config.FFMPEG_IMAGE_CLIP_TIMEOUT_SECONDS)


def _create_transition_preview(
    image_a: str,
    image_b: str,
    transition_name: str,
    output_path: str,
    clip_duration_seconds: float,
    transition_duration_seconds: float,
) -> bool:
    base_filter = DEFAULT_ANIMATION_FALLBACK["ffmpeg_filter"]
    filter_a = build_zoompan_filter(base_filter, clip_duration_seconds)
    filter_b = build_zoompan_filter(base_filter, clip_duration_seconds)
    offset = max(clip_duration_seconds - transition_duration_seconds, 0)
    total_duration = max(clip_duration_seconds * 2 - transition_duration_seconds, clip_duration_seconds)

    filter_complex = (
        f"[0:v]{filter_a},trim=duration={clip_duration_seconds},setpts=PTS-STARTPTS[a0];"
        f"[1:v]{filter_b},trim=duration={clip_duration_seconds},setpts=PTS-STARTPTS[a1];"
        f"[a0][a1]xfade=transition={transition_name}:duration={transition_duration_seconds}:offset={offset},format=yuv420p[outv]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        image_a,
        "-loop",
        "1",
        "-i",
        image_b,
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-t",
        str(total_duration),
    ]
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(["-pix_fmt", "yuv420p", output_path])
    return FFmpegHelper.run_command(cmd, timeout_seconds=Config.FFMPEG_IMAGE_CLIP_TIMEOUT_SECONDS)


def ensure_effect_library_generated(force_preview_regeneration: bool = False):
    animation_image, transition_image_b = _pick_reference_images()
    if not animation_image:
        logger.warning("No raw images found. Skipping effect library generation.")
        ensure_effects_library_dirs()
        invalidate_effect_metadata_cache()
        return

    if not transition_image_b:
        transition_image_b = animation_image

    for preset in ANIMATION_PRESETS:
        preview_path = _preview_path("animation", preset["id"])
        metadata_path = _metadata_path("animation", preset["id"])
        existing = _load_existing_metadata("animation", preset["id"])

        if force_preview_regeneration or not os.path.isfile(preview_path):
            _create_animation_preview(animation_image, preset["ffmpeg_filter"], preview_path, Config.IMG_CLIP_DURATION)

        payload = {
            "id": preset["id"],
            "type": "animation",
            "name": preset["name"],
            "description": preset["description"],
            "ffmpeg_filter": preset["ffmpeg_filter"],
            "preview_relative_path": storage_relative_path(preview_path),
            "source_image_relative_path": storage_relative_path(animation_image),
            "duration_seconds": Config.IMG_CLIP_DURATION,
            "active": existing.get("active", True),
            "created_at": existing.get("created_at", _utc_now()),
            "updated_at": _utc_now(),
        }
        _save_metadata(metadata_path, payload)

    for preset in TRANSITION_PRESETS:
        preview_path = _preview_path("transition", preset["id"])
        metadata_path = _metadata_path("transition", preset["id"])
        existing = _load_existing_metadata("transition", preset["id"])

        if force_preview_regeneration or not os.path.isfile(preview_path):
            _create_transition_preview(
                animation_image,
                transition_image_b,
                preset["xfade_transition"],
                preview_path,
                Config.EFFECT_PREVIEW_CLIP_DURATION,
                Config.IMAGE_TRANSITION_DURATION,
            )

        payload = {
            "id": preset["id"],
            "type": "transition",
            "name": preset["name"],
            "description": preset["description"],
            "xfade_transition": preset["xfade_transition"],
            "preview_relative_path": storage_relative_path(preview_path),
            "source_image_a_relative_path": storage_relative_path(animation_image),
            "source_image_b_relative_path": storage_relative_path(transition_image_b),
            "clip_duration_seconds": Config.EFFECT_PREVIEW_CLIP_DURATION,
            "transition_duration_seconds": Config.IMAGE_TRANSITION_DURATION,
            "active": existing.get("active", True),
            "created_at": existing.get("created_at", _utc_now()),
            "updated_at": _utc_now(),
        }
        _save_metadata(metadata_path, payload)

    invalidate_effect_metadata_cache()


def invalidate_effect_metadata_cache():
    _METADATA_CACHE.clear()


def _fallback_metadata(kind: str) -> list[dict]:
    source = ANIMATION_PRESETS if kind == "animation" else TRANSITION_PRESETS
    return [dict(preset, type=kind, active=True) for preset in source]


def _load_metadata_dir(kind: str, ensure_generated: bool = False) -> list[dict]:
    if ensure_generated:
        ensure_effect_library_generated()

    if kind in _METADATA_CACHE:
        return [dict(item) for item in _METADATA_CACHE[kind]]

    effects_dir = ensure_effects_library_dirs()["animations" if kind == "animation" else "transitions"]
    items = []
    for path in sorted(Path(effects_dir).glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
            if isinstance(data, dict):
                items.append(data)
        except Exception as exc:
            logger.warning(f"Cannot read effect metadata {path}: {exc}")
    if not items:
        items = _fallback_metadata(kind)
    _METADATA_CACHE[kind] = [dict(item) for item in items]
    return [dict(item) for item in items]


def load_animation_presets(ensure_generated: bool = False) -> list[dict]:
    return _load_metadata_dir("animation", ensure_generated=ensure_generated)


def load_transition_presets(ensure_generated: bool = False) -> list[dict]:
    return _load_metadata_dir("transition", ensure_generated=ensure_generated)


def load_active_animation_presets() -> list[dict]:
    presets = [preset for preset in load_animation_presets() if preset.get("active")]
    return presets or [dict(DEFAULT_ANIMATION_FALLBACK, active=True)]


def load_active_transition_presets() -> list[dict]:
    presets = [preset for preset in load_transition_presets() if preset.get("active")]
    return presets or [dict(DEFAULT_TRANSITION_FALLBACK, active=True)]


def choose_random_animation_preset() -> dict:
    return random.choice(load_active_animation_presets())


def choose_random_transition_preset() -> dict:
    return random.choice(load_active_transition_presets())


def update_effect_config(active_animation_ids: list[str], active_transition_ids: list[str]):
    active_animation_set = set(active_animation_ids)
    active_transition_set = set(active_transition_ids)

    for preset in load_animation_presets():
        preset["active"] = preset["id"] in active_animation_set
        preset["updated_at"] = _utc_now()
        _save_metadata(_metadata_path("animation", preset["id"]), preset)

    for preset in load_transition_presets():
        preset["active"] = preset["id"] in active_transition_set
        preset["updated_at"] = _utc_now()
        _save_metadata(_metadata_path("transition", preset["id"]), preset)

    invalidate_effect_metadata_cache()


def create_image_motion_clip(
    image_path: str,
    output_path: str,
    preset: dict,
    duration_seconds: float,
    fade_duration: float = 0.0,
) -> bool:
    ffmpeg_filter = preset.get("ffmpeg_filter", DEFAULT_ANIMATION_FALLBACK["ffmpeg_filter"])
    return _create_animation_preview(image_path, ffmpeg_filter, output_path, duration_seconds, fade_duration)
