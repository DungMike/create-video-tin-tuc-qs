"""News Bulletin Render Worker — Background rendering for news bulletins.

Runs the full render pipeline per channel in a background thread:
  1. TTS audio generation
  2. Timeline composition
  3. Pre-render clips (scale/pad to 1080p)
  4. Concat audio segments -> master audio
  5. FFmpeg render final video
  6. Overlay post-processing (if applicable)

Progress is written to progress.json for real-time polling.
"""

import os
import random
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

from src.composer.news_timeline import NewsTimelineComposer
from src.composer.renderer import Renderer
from src.config import Config
from src.processors.news_script_parser import get_all_tts_segments
from src.utils.channel_manager import get_channel
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger
from src.utils.news_bulletin_pipeline import (
    get_all_resource_pools,
    get_cached_audio,
    load_bulletin_progress,
    load_bulletin_state,
    save_audio_to_cache,
    save_bulletin_progress,
)
from src.utils.tts_audio import (
    _create_tts_task,
    _poll_tts_task,
    _require_tts_config,
    split_text_into_chunks,
)
from src.utils.decor_images import get_decor_image_absolute_path, get_decor_image

TARGET_W = 1920
TARGET_H = 1080
TARGET_FPS = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pre_render_video_clip(src_path: str, dst_path: str, duration: float) -> bool:
    """Scale+pad a video clip to 1080p, trimmed to duration."""
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-t", str(duration),
        "-vf", f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
               f"pad={TARGET_W}:{TARGET_H}:-1:-1,fps={TARGET_FPS},format=yuv420p,setsar=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-an",
        dst_path,
    ]
    ok = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if ok.returncode != 0:
        logger.error(f"pre_render_video_clip failed: {ok.stderr[:200]}")
        return False
    return True


def _pre_render_looped_video_clip(src_path: str, dst_path: str, duration: float) -> bool:
    """Scale+pad a video clip, looping it when needed to fill the target duration."""
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", src_path,
        "-t", str(duration),
        "-vf", f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
               f"pad={TARGET_W}:{TARGET_H}:-1:-1,fps={TARGET_FPS},format=yuv420p,setsar=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-an",
        dst_path,
    ]
    ok = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if ok.returncode != 0:
        logger.error(f"pre_render_looped_video_clip failed: {ok.stderr[:200]}")
        return False
    return True

def _pre_render_image_clip(src_path: str, dst_path: str, duration: float) -> bool:
    """Create an animated video from an image using the effects_library motion system.

    Reuses the same proven pipeline as batch_pipeline:
    - Upscales image 4x before zoompan to eliminate sub-pixel jitter
    - Uses NVENC encoding for quality
    - Randomly picks from active animation presets (zoom, pan, drift, diagonal, etc.)
    """
    from src.utils.effects_library import (
        create_image_motion_clip,
        load_active_animation_presets,
    )
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    presets = load_active_animation_presets()
    preset = random.choice(presets)
    fade_dur = Config.IMAGE_CLIP_FADE_DURATION if Config.IMAGE_ONLY_SKIP_XFADE else 0.0

    ok = create_image_motion_clip(src_path, dst_path, preset, duration, fade_dur)
    if not ok:
        logger.error(f"pre_render_image_clip failed ({preset.get('id', '?')}): {os.path.basename(src_path)}")
        return False
    logger.debug(f"[ImageAnim] {preset.get('id', '?')}: {os.path.basename(src_path)} -> {duration:.1f}s")
    return True


def _create_silence(output_path: str, duration: float):
    """Create a silence audio file."""
    ext = os.path.splitext(output_path)[1].lower()
    codec_args = ["-c:a", "aac", "-b:a", "192k"] if ext in {".aac", ".m4a"} else ["-c:a", "libmp3lame", "-b:a", "128k"]
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(duration),
        *codec_args,
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def _media_has_audio(src_path: str) -> bool:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        src_path,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return bool(result.stdout.strip())
    except Exception:
        return False


def _extract_or_create_media_audio(src_path: str, output_path: str, duration: float) -> str:
    """Extract original media audio, or create matching silence if the media has no audio stream."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not _media_has_audio(src_path):
        _create_silence(output_path, duration)
        return output_path

    cmd = [
        "ffmpeg", "-y",
        "-i", src_path,
        "-vn",
        "-t", str(duration),
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path,
    ]
    ok = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if ok.returncode != 0:
        logger.warning(f"extract media audio failed, using silence: {ok.stderr[:200]}")
        _create_silence(output_path, duration)
    return output_path


def _tts_segment_audio(text: str, voice_id: str, output_path: str) -> str:
    """Generate TTS audio for a single text segment with retry logic."""
    if not text.strip():
        _create_silence(output_path, 0.5)
        return output_path

    chunks = split_text_into_chunks(text, Config.TTS_MAX_CHARS)
    chunk_paths = []

    for i, chunk_text in enumerate(chunks):
        chunk_path = output_path.replace(".mp3", f"_chunk{i}.mp3")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                task_id = _create_tts_task(chunk_text, voice_id, 1.0, 1.0)
                download_url = _poll_tts_task(task_id, timeout_seconds=180)
                import requests
                resp = requests.get(download_url, timeout=60)
                resp.raise_for_status()
                with open(chunk_path, "wb") as f:
                    f.write(resp.content)
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    logger.warning(f"TTS retry {attempt+2}/{max_retries} for chunk {i}: {exc}")
                    time.sleep(2)
                else:
                    raise
        chunk_paths.append(chunk_path)

    if len(chunk_paths) == 1:
        if chunk_paths[0] != output_path:
            shutil.move(chunk_paths[0], output_path)
    else:
        concat_file = output_path + ".concat.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for cp in chunk_paths:
                f.write(f"file '{os.path.abspath(cp).replace(chr(92), '/')}'\n")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c:a", "copy",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        os.remove(concat_file)
        for cp in chunk_paths:
            if os.path.isfile(cp):
                os.remove(cp)

    return output_path


def _concat_audio_files(audio_paths: list[str], output_path: str) -> str:
    """Concat multiple audio files using filter_complex for mixed codecs."""
    cmd = ["ffmpeg", "-y"]
    for p in audio_paths:
        cmd.extend(["-i", os.path.abspath(p)])
    n = len(audio_paths)
    filter_inputs = "".join(f"[{i}:a]" for i in range(n))
    filter_str = f"{filter_inputs}concat=n={n}:v=0:a=1[outa]"
    cmd.extend(["-filter_complex", filter_str, "-map", "[outa]"])
    cmd.extend(["-c:a", "aac", "-b:a", "192k", output_path])
    ok = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if ok.returncode != 0:
        raise RuntimeError(f"Audio concat failed: {ok.stderr[:500]}")
    return output_path


# ---------------------------------------------------------------------------
# Decor Image Overlay — FFmpeg post-processing pass
# ---------------------------------------------------------------------------

def _compute_overlay_events(
    timeline_segments: list[dict],
    parsed_script: dict,
    gap_seconds: float = 0.5,
) -> list[dict]:
    """Compute overlay timing events from the timeline.

    Each event: {"start": float, "end": float, "title": str, "newsId": int}
    The overlay appears during `resume` and `detail` segments.
    Between consecutive detail segments, a 0.5s gap is inserted (fade-out/in).
    """
    news_map = {item["id"]: item for item in parsed_script.get("newsItems", [])}
    events = []
    cursor = 0.0

    for seg in timeline_segments:
        seg_type = seg["segmentType"]
        dur = seg.get("duration") or seg.get("audioDuration", 0.0)

        if seg_type in ("resume", "detail"):
            news_id = seg.get("newsId")
            news_item = news_map.get(news_id, {})
            title = news_item.get("resumeText", "")
            # Truncate title
            max_len = Config.DECOR_IMAGE_TITLE_MAX_LENGTH
            if len(title) > max_len:
                title = title[: max_len - 3] + "..."

            start = cursor
            end = cursor + dur

            # Apply gap: fade out 0.5s early, fade in 0.5s late
            if events and gap_seconds > 0:
                events[-1]["end"] -= gap_seconds
                start += gap_seconds

            if end > start:
                events.append({
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "title": title,
                    "newsId": news_id,
                })

        cursor += dur

    return events


def _apply_decor_overlay(
    input_video: str,
    output_video: str,
    decor_image_path: str,
    overlay_events: list[dict],
    decor_meta: dict,
    anim_duration: float = 0.5,
) -> bool:
    """Apply decor image overlay with drawtext titles using FFmpeg.

    The decor PNG is positioned at the bottom of the video.
    Each event gets a slide-up + fade-in animation and slide-down + fade-out.
    The news title is rendered via drawtext at configurable offsets.
    """
    if not overlay_events:
        return False

    video_h = TARGET_H
    decor_h = Config.DECOR_IMAGE_HEIGHT
    decor_w = Config.DECOR_IMAGE_WIDTH
    y_final = video_h - decor_h  # Bottom position
    y_start = video_h  # Off-screen below

    title_offset_x = decor_meta.get("titleOffsetX", int(decor_w * 0.5))
    title_offset_y = decor_meta.get("titleOffsetY", int(decor_h * 0.4))
    title_max_width = decor_meta.get("titleMaxWidth", int(decor_w * 0.47))

    # Build filter chain
    # Input 0 = main video, Input 1 = decor PNG
    filter_parts = []
    decor_labels = [f"decor{i}" for i in range(len(overlay_events))]
    if len(decor_labels) == 1:
        filter_parts.append(f"[1:v]format=rgba[{decor_labels[0]}]")
    else:
        split_outputs = "".join(f"[{label}]" for label in decor_labels)
        filter_parts.append(f"[1:v]format=rgba,split={len(decor_labels)}{split_outputs}")
    overlay_chain = "[0:v]"

    for i, event in enumerate(overlay_events):
        t_start = event["start"]
        t_end = event["end"]
        title = event["title"].replace("'", "'\\''").replace(":", r"\:").replace("\\", r"/")
        fade_in_end = t_start + anim_duration
        fade_out_start = max(t_end - anim_duration, t_start)

        # enable expression: show only during this event
        enable = f"between(t,{t_start},{t_end})"

        # Y animation: slide up during fade_in, slide down during fade_out
        # Between fade_in and fade_out: stay at y_final
        y_expr = (
            f"if(lt(t,{fade_in_end}),"
            f"{y_start}+({y_final}-{y_start})*(t-{t_start})/{anim_duration},"
            f"if(gt(t,{fade_out_start}),"
            f"{y_final}+({y_start}-{y_final})*(t-{fade_out_start})/{anim_duration},"
            f"{y_final}))"
        )

        # Alpha animation: fade in/out
        alpha_expr = (
            f"if(lt(t,{fade_in_end}),"
            f"(t-{t_start})/{anim_duration},"
            f"if(gt(t,{fade_out_start}),"
            f"1-(t-{fade_out_start})/{anim_duration},"
            f"1))"
        )

        out_label = f"ov{i}"
        # Overlay the decor image with alpha-aware compositing
        filter_parts.append(
            f"{overlay_chain}[{decor_labels[i]}]overlay=0:y='{y_expr}':enable='{enable}':alpha=premultiplied[{out_label}]"
        )
        overlay_chain = f"[{out_label}]"

    # Resolve font/style from decor_meta → Config fallback
    font_path = Config.DECOR_IMAGE_TITLE_FONT
    font_path_escaped = font_path.replace("\\", "/").replace(":", r"\:")
    font_size = decor_meta.get("titleFontSize", Config.DECOR_IMAGE_TITLE_FONT_SIZE)
    font_color = decor_meta.get("titleColor", Config.DECOR_IMAGE_TITLE_COLOR)

    # Add drawtext for each event — animated Y + alpha synchronized with banner
    for i, event in enumerate(overlay_events):
        t_start = event["start"]
        t_end = event["end"]
        title = event["title"].replace("'", "'\\''").replace(":", r"\:").replace("\\", r"/")
        fade_in_end = t_start + anim_duration
        fade_out_start = max(t_end - anim_duration, t_start)
        enable = f"between(t,{t_start},{t_end})"

        abs_title_x = title_offset_x
        y_final_text = y_final + title_offset_y
        y_start_text = y_start + title_offset_y

        # Y animation: slide up/down in sync with banner image
        dt_y_expr = (
            f"if(lt(t,{fade_in_end}),"
            f"{y_start_text}+({y_final_text}-{y_start_text})*(t-{t_start})/{anim_duration},"
            f"if(gt(t,{fade_out_start}),"
            f"{y_final_text}+({y_start_text}-{y_final_text})*(t-{fade_out_start})/{anim_duration},"
            f"{y_final_text}))"
        )

        # Alpha animation: fade in/out in sync with banner image
        dt_alpha_expr = (
            f"if(lt(t,{fade_in_end}),"
            f"(t-{t_start})/{anim_duration},"
            f"if(gt(t,{fade_out_start}),"
            f"1-(t-{fade_out_start})/{anim_duration},"
            f"1))"
        )

        dt_label = f"dt{i}"
        filter_parts.append(
            f"{overlay_chain}drawtext="
            f"text='{title}':"
            f"fontfile='{font_path_escaped}':"
            f"fontsize={font_size}:"
            f"fontcolor={font_color}:"
            f"borderw=2:bordercolor=black:"
            f"x={abs_title_x}:"
            f"y='{dt_y_expr}':"
            f"alpha='{dt_alpha_expr}':"
            f"enable='{enable}'[{dt_label}]"
        )
        overlay_chain = f"[{dt_label}]"

    # Final output
    filter_complex = ";".join(filter_parts)

    input_duration = FFmpegHelper.probe_duration(input_video)
    duration_args = ["-t", f"{input_duration:.3f}"] if input_duration > 0 else ["-shortest"]

    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-loop", "1",
        "-i", decor_image_path,
        "-filter_complex", filter_complex,
        "-map", overlay_chain,
        "-map", "0:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        *duration_args,
        "-movflags", "+faststart",
        output_video,
    ]

    logger.info(f"[DecorOverlay] Running FFmpeg overlay with {len(overlay_events)} events")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        logger.error(f"[DecorOverlay] FFmpeg overlay failed: {result.stderr[:500]}")
        return False

    logger.info(f"[DecorOverlay] Overlay complete: {output_video}")
    return True


# ---------------------------------------------------------------------------
# Progress updater
# ---------------------------------------------------------------------------

def _update_channel_progress(
    bulletin_id: str,
    channel_id: str,
    *,
    stage: str = "",
    percent: int = 0,
    message: str = "",
    status: str = "running",
    output_video: str | None = None,
    error: str | None = None,
):
    """Thread-safe progress update for a specific channel."""
    progress = load_bulletin_progress(bulletin_id)
    if not progress:
        return
    ch = progress.get("channels", {}).get(channel_id, {})
    if stage:
        ch["stage"] = stage
    ch["percent"] = percent
    ch["message"] = message
    ch["status"] = status
    if output_video is not None:
        ch["outputVideo"] = output_video
    if error is not None:
        ch["error"] = error
    progress["channels"][channel_id] = ch

    # Overall status
    all_channels = progress.get("channels", {})
    statuses = [c.get("status") for c in all_channels.values()]
    if all(s == "completed" for s in statuses):
        progress["status"] = "completed"
    elif any(s == "running" for s in statuses):
        progress["status"] = "running"
    elif any(s == "failed" for s in statuses):
        progress["status"] = "failed" if all(s in ("failed", "completed") for s in statuses) else "running"

    save_bulletin_progress(bulletin_id, progress)


# ---------------------------------------------------------------------------
# Per-channel render
# ---------------------------------------------------------------------------

def _render_channel(bulletin_id: str, channel_id: str, parsed_script: dict):
    """Full render pipeline for one channel."""
    channel = get_channel(channel_id)
    if not channel:
        _update_channel_progress(
            bulletin_id, channel_id,
            stage="failed", percent=0,
            message=f"Channel '{channel_id}' khong ton tai.",
            status="failed", error=f"Channel not found: {channel_id}",
        )
        return

    channel_name = channel.get("channelName", channel_id)
    voice_id = channel.get("voiceId", "")
    intro_path = channel.get("introVideoPath", "")
    transition_path = channel.get("transitionVideoPath", "")
    outro_path = channel.get("outroVideoPath", "")

    bulletin_dir = os.path.join(Config.STORAGE_DIR, "news_bulletin", bulletin_id)
    audio_dir = os.path.join(bulletin_dir, "audio", channel_id)
    prerender_dir = os.path.join(bulletin_dir, "prerender", channel_id)
    output_dir = os.path.join(bulletin_dir, "output")
    temp_dir = os.path.join(bulletin_dir, "temp", channel_id)
    for d in [audio_dir, prerender_dir, output_dir, temp_dir]:
        os.makedirs(d, exist_ok=True)

    try:
        logger.info(f"[BulletinRender] Start channel {channel_name} ({channel_id})")

        # --- Stage 1: TTS Audio (0-30%) ---
        _update_channel_progress(
            bulletin_id, channel_id,
            stage="tts_audio", percent=0,
            message="Dang tao audio TTS...",
        )

        if not voice_id:
            raise ValueError(f"Channel '{channel_name}' khong co voiceId.")
        missing_media = [
            label
            for label, media_path in (
                ("intro", intro_path),
                ("transition", transition_path),
                ("outro", outro_path),
            )
            if not media_path or not os.path.isfile(media_path)
        ]
        if missing_media:
            raise ValueError(f"Channel '{channel_name}' thieu video: {', '.join(missing_media)}.")

        _require_tts_config(voice_id)
        tts_segments = get_all_tts_segments(parsed_script)
        segment_audio_map = {}  # segmentKey -> (path, duration)

        for idx, seg in enumerate(tts_segments):
            seg_key = seg["segmentKey"]
            seg_text = seg["text"]
            audio_path = os.path.join(audio_dir, f"{seg_key}.mp3")

            # Check cache first
            cached = get_cached_audio(bulletin_id, voice_id, seg_key)
            if cached and os.path.isfile(cached):
                dur = FFmpegHelper.probe_duration(cached)
                if dur > 0:
                    segment_audio_map[seg_key] = (cached, dur)
                    pct = int(30 * (idx + 1) / len(tts_segments))
                    _update_channel_progress(
                        bulletin_id, channel_id,
                        stage="tts_audio", percent=pct,
                        message=f"Audio [{seg_key}]: cached {dur:.1f}s",
                    )
                    continue

            # Generate TTS
            _tts_segment_audio(seg_text, voice_id, audio_path)
            dur = FFmpegHelper.probe_duration(audio_path)
            segment_audio_map[seg_key] = (audio_path, dur)

            # Save to cache
            save_audio_to_cache(bulletin_id, voice_id, seg_key, audio_path)

            pct = int(30 * (idx + 1) / len(tts_segments))
            _update_channel_progress(
                bulletin_id, channel_id,
                stage="tts_audio", percent=pct,
                message=f"Audio [{seg_key}]: {dur:.1f}s",
            )

        audio_durations = {k: v[1] for k, v in segment_audio_map.items()}
        total_audio = sum(audio_durations.values())
        logger.info(f"[BulletinRender] TTS done: {len(segment_audio_map)} segments, {total_audio:.1f}s")

        channel_media_durations = {
            "intro": FFmpegHelper.probe_duration(intro_path),
            "transition": FFmpegHelper.probe_duration(transition_path),
            "outro": FFmpegHelper.probe_duration(outro_path),
        }
        invalid_media = [label for label, duration in channel_media_durations.items() if duration <= 0]
        if invalid_media:
            raise ValueError(f"Channel '{channel_name}' co video khong doc duoc duration: {', '.join(invalid_media)}.")

        # --- Stage 2: Timeline (30-35%) ---
        _update_channel_progress(
            bulletin_id, channel_id,
            stage="timeline", percent=30,
            message="Dang tao timeline...",
        )

        resource_pools = get_all_resource_pools(bulletin_id)
        composer = NewsTimelineComposer(
            bulletin_id=bulletin_id,
            channel_id=channel_id,
            vid_clip_duration=5.0,
            img_clip_duration=6.0,
        )
        timeline = composer.build_full_timeline(
            parsed_script=parsed_script,
            audio_durations=audio_durations,
            resource_pools=resource_pools,
            intro_clip_path=intro_path,
            intro_duration=channel_media_durations["intro"],
            transition_clip_path=transition_path,
            transition_duration=channel_media_durations["transition"],
            outro_clip_path=outro_path,
            outro_duration=channel_media_durations["outro"],
        )

        _update_channel_progress(
            bulletin_id, channel_id,
            stage="timeline", percent=35,
            message=f"Timeline: {len(timeline['segments'])} segments, {timeline['totalDuration']:.0f}s",
        )

        # --- Stage 3: Pre-render clips (35-60%) ---
        flat_segments = []
        audio_order = []
        total_segs = len(timeline["segments"])

        # Find a fallback visual from resource pools for spoken intro/detail bridge/outro segments.
        fallback_visual = None
        for pool in resource_pools.values():
            for img in pool.get("img_clips", []):
                if os.path.isfile(img["path"]):
                    fallback_visual = {"type": "image", "path": img["path"]}
                    break
            if fallback_visual:
                break
        if not fallback_visual:
            for pool in resource_pools.values():
                for vid in pool.get("vid_clips", []):
                    if os.path.isfile(vid["path"]):
                        fallback_visual = {"type": "video", "path": vid["path"]}
                        break
                if fallback_visual:
                    break

        # Load dedicated segment resources for intro/detail_intro/outro
        from src.utils.news_bulletin_pipeline import get_segment_resource_pool
        segment_pools = {
            seg_name: get_segment_resource_pool(bulletin_id, seg_name)
            for seg_name in ("intro", "detail_intro", "outro")
        }

        def _pick_segment_visual(seg_type: str) -> dict | None:
            """Pick a visual from dedicated segment resources, fallback to fallback_visual."""
            pool = segment_pools.get(seg_type, {})
            # Prefer video clips
            for vid in pool.get("vid_clips", []):
                if os.path.isfile(vid["path"]):
                    return {"type": "video", "path": vid["path"]}
            # Then images
            for img in pool.get("img_clips", []):
                if os.path.isfile(img["path"]):
                    return {"type": "image", "path": img["path"]}
            return fallback_visual

        for seg_idx, seg in enumerate(timeline["segments"]):
            seg_type = seg["segmentType"]
            seg_key = seg["segmentKey"]
            pct = 35 + int(25 * (seg_idx + 1) / total_segs)

            if seg_type == "channel_media":
                dur = seg["duration"]
                clip_path = os.path.join(prerender_dir, f"pr_{seg_key}.mp4")
                if not _pre_render_video_clip(seg["clipPath"], clip_path, dur):
                    raise RuntimeError(f"Khong the pre-render channel media {seg_key}.")
                flat_segments.append({
                    "kind": "video",
                    "path": clip_path,
                    "duration": dur,
                    "id": seg_key,
                })
                media_audio_path = os.path.join(audio_dir, f"media_{seg_key}.m4a")
                audio_order.append(_extract_or_create_media_audio(seg["clipPath"], media_audio_path, dur))

            elif seg_type in ("intro", "detail_intro", "outro"):
                dur = seg.get("audioDuration", 3.0)
                visual = _pick_segment_visual(seg_type)
                if visual:
                    clip_path = os.path.join(prerender_dir, f"pr_{seg_key}.mp4")
                    if not os.path.isfile(clip_path):
                        if visual["type"] == "video":
                            _pre_render_looped_video_clip(visual["path"], clip_path, dur)
                        else:
                            _pre_render_image_clip(visual["path"], clip_path, dur)
                    flat_segments.append({
                        "kind": "video", "path": clip_path,
                        "duration": dur, "id": seg_key,
                    })
                if seg_key in segment_audio_map:
                    audio_order.append(segment_audio_map[seg_key][0])

            elif seg_type in ("resume", "detail"):
                dur = seg.get("audioDuration", 5.0)
                clips = seg.get("clips", [])

                # --- Resume silence padding ---
                # Resume segments enforce a minimum display duration so the banner
                # animation has enough time to slide in, hold, and slide out cleanly.
                if seg_type == "resume":
                    min_dur = Config.RESUME_MIN_DURATION
                    pad_secs = Config.RESUME_SILENCE_PAD
                    tts_dur = dur
                    # Total target: max(tts_dur, min_dur) + pad at end
                    target_dur = max(tts_dur, min_dur) + pad_secs
                    # Silence needed = target - tts
                    silence_needed = max(0.0, target_dur - tts_dur)
                    dur = tts_dur + silence_needed  # total segment duration with padding
                    if silence_needed > 0.05:
                        silence_path = os.path.join(audio_dir, f"silence_{seg_key}.mp3")
                        if not os.path.isfile(silence_path):
                            _create_silence(silence_path, silence_needed)
                else:
                    silence_needed = 0.0
                    silence_path = None

                if not clips:
                    if fallback_visual:
                        clip_path = os.path.join(prerender_dir, f"pr_{seg_key}_bg.mp4")
                        if not os.path.isfile(clip_path):
                            if fallback_visual["type"] == "video":
                                _pre_render_looped_video_clip(fallback_visual["path"], clip_path, dur)
                            else:
                                _pre_render_image_clip(fallback_visual["path"], clip_path, dur)
                        flat_segments.append({
                            "kind": "video", "path": clip_path,
                            "duration": dur, "id": f"{seg_key}_bg",
                        })
                else:
                    for ci, clip_data in enumerate(clips):
                        c_type = clip_data["type"]
                        c_path = clip_data["path"]
                        c_dur = clip_data["duration"]
                        out_clip = os.path.join(prerender_dir, f"pr_{seg_key}_{ci}.mp4")
                        if not os.path.isfile(out_clip):
                            if c_type == "video":
                                _pre_render_video_clip(c_path, out_clip, c_dur)
                            else:
                                _pre_render_image_clip(c_path, out_clip, c_dur)
                        flat_segments.append({
                            "kind": "video", "path": out_clip,
                            "duration": c_dur, "id": f"{seg_key}_{ci}",
                        })

                if seg_key in segment_audio_map:
                    audio_order.append(segment_audio_map[seg_key][0])
                # Append silence padding after TTS audio for resume segments
                if seg_type == "resume" and silence_needed > 0.05 and silence_path and os.path.isfile(silence_path):
                    audio_order.append(silence_path)

            _update_channel_progress(
                bulletin_id, channel_id,
                stage="prerender", percent=pct,
                message=f"Pre-render: {seg_idx+1}/{total_segs}",
            )

        # --- Stage 4: Concat Audio (60-65%) ---
        _update_channel_progress(
            bulletin_id, channel_id,
            stage="concat_audio", percent=60,
            message="Dang ghep audio...",
        )

        master_audio = os.path.join(audio_dir, "master_audio.m4a")
        _concat_audio_files(audio_order, master_audio)
        master_duration = FFmpegHelper.probe_duration(master_audio)

        _update_channel_progress(
            bulletin_id, channel_id,
            stage="concat_audio", percent=65,
            message=f"Master audio: {master_duration:.0f}s",
        )

        # --- Stage 5: Render Video (65-90%) ---
        _update_channel_progress(
            bulletin_id, channel_id,
            stage="render", percent=65,
            message="Dang render video...",
        )

        dirs = {"output": output_dir, "temp": temp_dir}
        renderer = Renderer(bulletin_id, dirs)
        renderer.source_text_override = channel.get("sourceText", "")

        timeline_for_renderer = {"segments": flat_segments}

        def _on_progress(event):
            pct_ff = event.get("ffmpegPercent")
            if pct_ff is not None:
                render_pct = 65 + int(25 * pct_ff / 100)
                _update_channel_progress(
                    bulletin_id, channel_id,
                    stage="render", percent=render_pct,
                    message=f"Render: {pct_ff:.0f}%",
                )

        output_path = renderer.render(
            timeline_for_renderer,
            master_audio,
            master_duration,
            progress_callback=_on_progress,
        )

        if not output_path:
            raise RuntimeError("Renderer returned None — render failed.")

        # Rename to channel-specific output
        final_name = f"{channel_id}.mp4"
        final_path = os.path.join(output_dir, final_name)
        if output_path != final_path:
            shutil.move(output_path, final_path)

        # --- Stage 6: Decor Image Overlay (90-98%) ---
        state = load_bulletin_state(bulletin_id)
        decor_image_id = (state or {}).get("channelDecorImageIds", {}).get(channel_id)
        if decor_image_id:
            decor_meta = get_decor_image(decor_image_id, channel_id)
            decor_abs_path = get_decor_image_absolute_path(decor_image_id, channel_id)

            if decor_meta and decor_abs_path and os.path.isfile(decor_abs_path):
                _update_channel_progress(
                    bulletin_id, channel_id,
                    stage="overlay", percent=90,
                    message="Dang ap dung overlay decor...",
                )

                overlay_events = _compute_overlay_events(
                    timeline["segments"],
                    parsed_script,
                    gap_seconds=Config.DECOR_IMAGE_GAP_SECONDS,
                )

                if overlay_events:
                    overlay_output = os.path.join(output_dir, f"{channel_id}_overlay.mp4")
                    ok = _apply_decor_overlay(
                        input_video=final_path,
                        output_video=overlay_output,
                        decor_image_path=decor_abs_path,
                        overlay_events=overlay_events,
                        decor_meta=decor_meta,
                        anim_duration=Config.DECOR_IMAGE_ANIM_DURATION,
                    )
                    if ok and os.path.isfile(overlay_output):
                        os.replace(overlay_output, final_path)
                        logger.info(f"[BulletinRender] Decor overlay applied: {final_path}")
                    else:
                        logger.warning(f"[BulletinRender] Decor overlay failed, using base video")
                        if os.path.isfile(overlay_output):
                            os.remove(overlay_output)
            else:
                logger.warning(f"[BulletinRender] Decor image not found: {decor_image_id}")

        relative_output = os.path.relpath(final_path, Config.STORAGE_DIR).replace("\\", "/")

        _update_channel_progress(
            bulletin_id, channel_id,
            stage="completed", percent=100,
            message=f"Hoan tat! {master_duration:.0f}s",
            status="completed",
            output_video=relative_output,
            error="",
        )

        logger.info(f"[BulletinRender] Channel {channel_name} completed: {final_path}")

    except Exception as exc:
        logger.exception(f"[BulletinRender] Channel {channel_name} failed: {exc}")
        _update_channel_progress(
            bulletin_id, channel_id,
            stage="failed", percent=0,
            message=str(exc)[:200],
            status="failed",
            error=str(exc)[:500],
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _validate_channel_ready(channel_ids: list[str]):
    errors = []
    for channel_id in channel_ids:
        channel = get_channel(channel_id)
        if not channel:
            errors.append(f"{channel_id}: channel khong ton tai")
            continue
        channel_name = channel.get("channelName", channel_id)
        if not channel.get("voiceId"):
            errors.append(f"{channel_name}: thieu voiceId")
        for label, field_name in (
            ("intro", "introVideoPath"),
            ("transition", "transitionVideoPath"),
            ("outro", "outroVideoPath"),
        ):
            media_path = channel.get(field_name, "")
            if not media_path or not os.path.isfile(media_path):
                errors.append(f"{channel_name}: thieu video {label}")
    if errors:
        raise ValueError("Channel chua cau hinh du intro/transition/outro: " + "; ".join(errors))


def start_bulletin_render(bulletin_id: str) -> dict:
    """Start background rendering for all channels of a bulletin.

    Returns the updated progress dict.
    """
    state = load_bulletin_state(bulletin_id)
    if not state:
        raise ValueError(f"Bulletin '{bulletin_id}' khong ton tai.")

    parsed_script = state.get("parsedScript")
    if not parsed_script:
        raise ValueError("Bulletin khong co kich ban da parse.")

    channel_ids = state.get("channelIds", [])
    if not channel_ids:
        raise ValueError("Bulletin khong co channel nao duoc chon.")
    _validate_channel_ready(channel_ids)

    # Initialize progress
    progress = load_bulletin_progress(bulletin_id) or {}
    progress["status"] = "running"
    for ch_id in channel_ids:
        ch = get_channel(ch_id)
        progress.setdefault("channels", {})[ch_id] = {
            "channelId": ch_id,
            "channelName": ch.get("channelName", ch_id) if ch else ch_id,
            "status": "running",
            "stage": "pending",
            "percent": 0,
            "message": "Dang khoi tao...",
            "outputVideo": None,
            "error": None,
        }
    save_bulletin_progress(bulletin_id, progress)

    # Launch background threads
    for ch_id in channel_ids:
        t = threading.Thread(
            target=_render_channel,
            args=(bulletin_id, ch_id, parsed_script),
            daemon=True,
            name=f"bulletin-render-{bulletin_id}-{ch_id}",
        )
        t.start()
        logger.info(f"[BulletinRender] Started thread for channel {ch_id}")

    return progress
