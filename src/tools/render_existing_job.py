import argparse
import traceback

from src.composer.renderer import Renderer
from src.composer.timeline import TimelineComposer
from src.config import Config
from src.processors.image_processor import ImageProcessor
from src.utils.effects_library import load_active_animation_presets, load_active_transition_presets
from src.utils.file_manager import (
    cleanup_job_files,
    cleanup_review_video_assets,
    clear_directory,
    load_job_manifest,
    save_job_manifest,
    setup_directories,
    storage_absolute_path,
    storage_relative_path,
)
from src.utils.job_progress import init_job_progress, update_job_progress
from src.utils.logger import logger


def _image_only_chunk_count(total_segments: int) -> int:
    if not total_segments:
        return 0
    limit = max(1, Config.IMAGE_ONLY_CHUNK_SEGMENT_LIMIT)
    return (total_segments + limit - 1) // limit


def _progress_extra(event: dict) -> dict:
    allowed_keys = ("cacheHits", "cacheMisses", "ffmpegPercent", "outTimeSeconds")
    return {key: event[key] for key in allowed_keys if key in event}


def _image_progress_callback(job_id: str, image_paths: list[str]):
    def _callback(event: dict):
        total_images = max(int(event.get("totalImages") or len(image_paths) or 1), 1)
        current_image = int(event.get("currentImage") or 0)
        update_job_progress(
            job_id,
            status="running",
            stage="image_processing",
            percent=5 + (30 * current_image / total_images),
            message=event.get("message"),
            level=event.get("level", "info"),
            totals={"images": total_images},
            current={"image": current_image},
            extra=_progress_extra(event),
        )

    return _callback


def _render_progress_callback(job_id: str):
    def _callback(event: dict):
        stage = event.get("stage") or "render_video"
        total_segments = max(int(event.get("totalSegments") or 1), 1)
        current_segment = max(int(event.get("currentSegment") or 0), 0)
        total_chunks = max(int(event.get("totalChunks") or 1), 1)
        current_chunk = max(int(event.get("currentChunk") or 0), 0)
        ffmpeg_percent = event.get("ffmpegPercent")
        ffmpeg_fraction = 0.0
        if isinstance(ffmpeg_percent, (int, float)):
            ffmpeg_fraction = max(0.0, min(1.0, float(ffmpeg_percent) / 100))

        if stage == "join_chunks":
            percent = 90 + (8 * ffmpeg_fraction) if ffmpeg_percent is not None else 95
        elif stage == "finalize":
            percent = 98
        elif stage == "render_chunks" and total_chunks > 1:
            percent = 40 + (50 * (current_chunk + ffmpeg_fraction) / total_chunks)
        elif total_chunks > 1:
            percent = 40 + (50 * current_chunk / total_chunks)
        else:
            percent = 45 + (45 * current_segment / total_segments)

        update_job_progress(
            job_id,
            status="running",
            stage=stage,
            percent=percent,
            message=event.get("message"),
            level=event.get("level", "info"),
            totals={"segments": total_segments, "chunks": total_chunks},
            current={"segment": current_segment, "chunk": current_chunk},
            extra=_progress_extra(event),
        )

    return _callback


def render_existing_job(job_id: str) -> str:
    dirs = setup_directories(job_id)
    manifest = load_job_manifest(job_id)
    image_paths = [storage_absolute_path(path) for path in manifest.get("image_paths", [])]
    audio_path = storage_absolute_path(manifest["audio_relative_path"])

    init_job_progress(
        job_id,
        totals={
            "images": len(image_paths),
            "reviewClips": 0,
            "libraryAssets": 0,
            "segments": 0,
            "chunks": 0,
        },
        message="Bat dau render job bang worker truc tiep.",
    )

    clear_directory(dirs["img_clips"])
    clear_directory(dirs["temp"])

    animation_presets = load_active_animation_presets()
    transition_presets = load_active_transition_presets()
    img_processor = ImageProcessor(
        job_id,
        dirs,
        animation_presets=animation_presets,
        image_render_plan=manifest.get("image_render_plan"),
    )
    image_clips = img_processor.process_images(
        image_paths,
        progress_callback=_image_progress_callback(job_id, image_paths),
    )
    manifest["image_render_plan"] = img_processor.updated_image_render_plan
    save_job_manifest(job_id, manifest)
    if not image_clips:
        update_job_progress(
            job_id,
            status="failed",
            stage="failed",
            percent=0,
            message="Khong co anh hop le de render.",
            level="error",
        )
        raise RuntimeError("No valid image clips")

    update_job_progress(
        job_id,
        status="running",
        stage="timeline",
        percent=35,
        message="Dang tao timeline render.",
        current={"image": len(image_clips)},
    )

    timeline_data = TimelineComposer(job_id, dirs).create_timeline([], image_clips, manifest["audio_duration"])
    segments = timeline_data.get("segments", [])
    total_segments = len(segments)
    total_chunks = _image_only_chunk_count(total_segments)

    update_job_progress(
        job_id,
        status="running",
        stage="timeline",
        percent=40,
        message=f"Timeline san sang voi {total_segments} segment.",
        totals={"segments": total_segments, "chunks": total_chunks},
        current={"segment": 0, "chunk": 0},
        extra={"timelineMode": timeline_data.get("mode")},
    )

    output_path = Renderer(job_id, dirs, transition_presets=transition_presets).render(
        timeline_data,
        audio_path,
        manifest["audio_duration"],
        progress_callback=_render_progress_callback(job_id),
    )
    if not output_path:
        update_job_progress(
            job_id,
            status="failed",
            stage="failed",
            message="Render that bai. Kiem tra logs/app.log.",
            level="error",
        )
        raise RuntimeError("Renderer returned no output")

    manifest["review_clips"] = []
    manifest["selected_clip_ids"] = []
    manifest["selected_library_asset_ids"] = []
    manifest["clip_tags"] = {}
    manifest["render_mode"] = "image_audio_only"
    manifest["output_video"] = storage_relative_path(output_path)
    save_job_manifest(job_id, manifest)
    cleanup_review_video_assets(dirs, [])
    cleanup_job_files(job_id)

    update_job_progress(
        job_id,
        status="completed",
        stage="completed",
        percent=100,
        message="Render hoan tat.",
        current={"segment": total_segments, "chunk": total_chunks, "image": len(image_clips)},
        extra={"outputVideo": manifest["output_video"]},
    )
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an existing prepared job.")
    parser.add_argument("job_id")
    args = parser.parse_args()

    try:
        output_path = render_existing_job(args.job_id)
        print(output_path)
        return 0
    except Exception as exc:
        logger.exception(f"Direct render failed for {args.job_id}: {exc}")
        try:
            update_job_progress(
                args.job_id,
                status="failed",
                stage="failed",
                message=f"Render worker loi: {exc}",
                level="error",
            )
        except Exception:
            pass
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
