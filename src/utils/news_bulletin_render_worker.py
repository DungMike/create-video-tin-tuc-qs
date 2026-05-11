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


def _pre_render_image_clip(src_path: str, dst_path: str, duration: float) -> bool:
    """Create a still video from an image, scaled+padded to 1080p."""
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", src_path,
        "-t", str(duration),
        "-vf", f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
               f"pad={TARGET_W}:{TARGET_H}:-1:-1,fps={TARGET_FPS},format=yuv420p,setsar=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-an",
        dst_path,
    ]
    ok = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if ok.returncode != 0:
        logger.error(f"pre_render_image_clip failed: {ok.stderr[:200]}")
        return False
    return True


def _create_silence(output_path: str, duration: float):
    """Create an mp3 silence file."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(duration),
        "-c:a", "libmp3lame", "-b:a", "128k",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=10)


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
    transition_path = channel.get("transitionVideoPath", "")

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
        transition_duration = 0.5
        timeline = composer.build_full_timeline(
            parsed_script=parsed_script,
            audio_durations=audio_durations,
            resource_pools=resource_pools,
            transition_clip_path=transition_path if transition_path and os.path.isfile(transition_path) else None,
            transition_duration=transition_duration if transition_path else 0,
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

        # Find a fallback image from resource pools
        fallback_image = None
        for pool in resource_pools.values():
            for img in pool.get("img_clips", []):
                if os.path.isfile(img["path"]):
                    fallback_image = img["path"]
                    break
            if fallback_image:
                break

        for seg_idx, seg in enumerate(timeline["segments"]):
            seg_type = seg["segmentType"]
            seg_key = seg["segmentKey"]
            pct = 35 + int(25 * (seg_idx + 1) / total_segs)

            if seg_type == "transition":
                flat_segments.append({
                    "kind": "video",
                    "path": seg["clipPath"],
                    "duration": seg["duration"],
                    "id": seg_key,
                })
                silence_path = os.path.join(audio_dir, f"silence_{seg_key}.mp3")
                if not os.path.isfile(silence_path):
                    _create_silence(silence_path, seg["duration"])
                audio_order.append(silence_path)

            elif seg_type in ("intro", "outro"):
                dur = seg.get("audioDuration", 3.0)
                if fallback_image:
                    clip_path = os.path.join(prerender_dir, f"pr_{seg_key}.mp4")
                    if not os.path.isfile(clip_path):
                        _pre_render_image_clip(fallback_image, clip_path, dur)
                    flat_segments.append({
                        "kind": "video", "path": clip_path,
                        "duration": dur, "id": seg_key,
                    })
                if seg_key in segment_audio_map:
                    audio_order.append(segment_audio_map[seg_key][0])

            elif seg_type in ("resume", "detail"):
                dur = seg.get("audioDuration", 5.0)
                clips = seg.get("clips", [])

                if not clips:
                    if fallback_image:
                        clip_path = os.path.join(prerender_dir, f"pr_{seg_key}_bg.mp4")
                        if not os.path.isfile(clip_path):
                            _pre_render_image_clip(fallback_image, clip_path, dur)
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

        relative_output = os.path.relpath(final_path, Config.STORAGE_DIR).replace("\\", "/")

        _update_channel_progress(
            bulletin_id, channel_id,
            stage="completed", percent=100,
            message=f"Hoan tat! {master_duration:.0f}s",
            status="completed",
            output_video=relative_output,
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
