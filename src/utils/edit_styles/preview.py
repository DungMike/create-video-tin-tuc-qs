"""Short preview clip of an edit style, rendered by the real overlay pass.

The preview builds a tiny story (a sample library clip looped to a few seconds,
silent audio, a three-line sample subtitle) and runs the pipeline's own
``_build_edit_plan`` -> ``_prepare_subtitle_ass`` -> ``_apply_story_overlays``,
so what the settings page shows is exactly what a batch would render.
"""

from __future__ import annotations

import glob
import os
import shutil
import uuid

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

PREVIEW_SECONDS = 10.0
_SAMPLE_LINES = [
    "Dòng phụ đề mẫu để xem vị trí và cỡ chữ",
    "Câu thứ hai hiện lên sau vài giây\nvà có thể xuống hai dòng",
    "Câu cuối của đoạn xem thử",
]


def _previews_dir() -> str:
    path = os.path.join(Config.STORY_EDIT_STYLE_DIR, "previews")
    os.makedirs(path, exist_ok=True)
    return path


def _sample_srt(path: str, seconds: float):
    step = seconds / len(_SAMPLE_LINES)

    def stamp(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    with open(path, "w", encoding="utf-8") as f:
        for i, text in enumerate(_SAMPLE_LINES):
            f.write(f"{i + 1}\n{stamp(i * step)} --> {stamp((i + 1) * step)}\n{text}\n\n")


def _first_decor_id() -> str:
    """A decor image to preview with. Prefers an unused one, but a preview never
    consumes anything — so when the library is spent it still shows one."""
    from src.utils.story_decor_images import get_enabled_decor_images

    images = get_enabled_decor_images(unused_only=True) or get_enabled_decor_images()
    return str(images[0]["id"]) if images else ""


def render_preview(style_id: str, sample_clip: str, *, library_id: str = "", decor_image_id: str = "",
                   modifier_ids: list[str] | None = None, seconds: float = PREVIEW_SECONDS) -> str:
    """Render the preview and return its path relative to STORAGE_DIR."""
    from src.utils.edit_styles import store
    from src.utils.story_video_pipeline import StoryVideoPipelineRunner

    record = store.get_edit_style(style_id)
    if not record:
        raise ValueError("Kieu dung khong ton tai.")
    is_layout = record.get("group") == "layout"
    story_id = f"es-prev-{uuid.uuid4().hex[:8]}"
    story_dir = os.path.join(Config.STORY_VIDEO_DIR, story_id)
    try:
        work = os.path.join(story_dir, "temp")
        os.makedirs(work, exist_ok=True)
        base = os.path.join(work, "preview_base.mp4")
        ok = FFmpegHelper.run_command(
            ["ffmpeg", "-y", "-stream_loop", "-1", "-i", sample_clip,
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{seconds:.3f}",
             "-map", "0:v:0", "-map", "1:a:0", *FFmpegHelper.get_nvenc_flags(), "-c:a", "aac", base],
            timeout_seconds=120,
        )
        if not ok or not os.path.isfile(base):
            raise RuntimeError("Khong tao duoc clip nen cho ban xem thu.")
        srt = os.path.join(work, "preview.srt")
        _sample_srt(srt, seconds)

        if is_layout and store.type_flag(style_id, "requiresDecor") and not decor_image_id:
            decor_image_id = _first_decor_id()
        config = {
            "input_type": "audio_file", "input_value": base, "output_name": story_id,
            "library_ids": [library_id] if library_id else [], "skip_tv_effect": True,
            "subtitle_path": srt, "decor_image_id": decor_image_id,
            "layout_id": style_id if is_layout else "",
            "modifier_ids": list(modifier_ids or []) + ([] if is_layout else [style_id]),
        }
        runner = StoryVideoPipelineRunner(story_id, config)
        runner._edit = runner._build_edit_plan(base, seconds)
        plan = runner._edit
        if plan and (plan.needs_clip_timing or plan.needs_clip_media):
            # The preview loops one sample clip: every loop is a "cut".
            period = FFmpegHelper.probe_duration(sample_clip) or 3.0
            starts = [k * period for k in range(max(1, int(seconds / period) + 1)) if k * period < seconds]
            plan.set_clip_starts(starts)
            plan.start_clip_media([sample_clip] * len(starts))
            plan.finish_clip_media()
        runner._subtitle_ass_path = runner._prepare_subtitle_ass(seconds) or ""
        rendered = runner._apply_story_overlays(base, seconds)
        if not rendered or not os.path.isfile(rendered):
            raise RuntimeError("Render ban xem thu that bai.")

        for old in glob.glob(os.path.join(_previews_dir(), f"{style_id}_*.mp4")):
            try:
                os.remove(old)
            except OSError:
                pass
        final = os.path.join(_previews_dir(), f"{style_id}_{uuid.uuid4().hex[:6]}.mp4")
        ok = FFmpegHelper.run_command(["ffmpeg", "-y", "-i", rendered, "-c", "copy", "-movflags", "+faststart",
                                       final])
        if not ok:
            shutil.copy2(rendered, final)
        logger.info(f"[EditStyles] Preview {style_id} -> {final}")
        return os.path.relpath(final, Config.STORAGE_DIR).replace("\\", "/")
    finally:
        shutil.rmtree(story_dir, ignore_errors=True)
