"""End-to-end verify: run the real StoryVideoPipelineRunner with the NEW GPU overlay
code + OVERLAY_USE_GPU_PIPELINE=true (in-process, separate from the dev server).

Measures the story_overlays stage duration and GPU encoder/util during it, then
validates the output video. Compare overlay stage vs the CPU baseline (~487s).
"""
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

os.environ["OVERLAY_USE_GPU_PIPELINE"] = "true"  # ensure flag on for this process

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.story_video_pipeline import StoryVideoPipelineRunner, load_story_progress

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
LIBRARY_ID = "thai-11-15-ky-uc-vang-507787"

SAMPLE = sys.argv[1] if len(sys.argv) > 1 else "sample_10a"


def gpu_sample():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()[0]
        g = [float(x) for x in out.split(",")]
        return g  # gpu_util, enc_util, mem_mb
    except Exception:
        return [0.0, 0.0, 0.0]


def main():
    print(f"OVERLAY_USE_GPU_PIPELINE = {Config.OVERLAY_USE_GPU_PIPELINE}")
    print(f"cuda_overlay_available   = {FFmpegHelper.cuda_overlay_available()}")

    mp3 = os.path.join(SAMPLES, SAMPLE + ".mp3")
    srt = os.path.join(SAMPLES, SAMPLE + ".srt")
    story_id = f"sv-vgpu{int(time.time()) % 100000}"
    story_dir = os.path.join(Config.STORY_VIDEO_DIR, story_id)
    os.makedirs(story_dir, exist_ok=True)

    config_dict = {
        "input_type": "audio_file",
        "input_value": mp3,
        "output_name": f"verify_gpu_{SAMPLE}",
        "library_id": LIBRARY_ID,
        "subtitle_path": srt,
    }
    runner = StoryVideoPipelineRunner(story_id, config_dict)

    result = {}

    def _run():
        result["path"] = runner.run()

    t = threading.Thread(target=_run, daemon=True)
    t0 = time.time()
    t.start()

    # Poll progress, time each stage, and sample GPU during story_overlays.
    stage_start = {}
    stage_end = {}
    last_stage = None
    ov_samples = []
    while t.is_alive():
        prog = load_story_progress(story_id) or {}
        stage = prog.get("stage")
        now = time.time()
        if stage != last_stage:
            if last_stage is not None:
                stage_end[last_stage] = now
            stage_start.setdefault(stage, now)
            last_stage = stage
        if stage == "story_overlays":
            gu, eu, mem = gpu_sample()
            ov_samples.append((gu, eu, mem))
        time.sleep(1.0)
    now = time.time()
    if last_stage is not None:
        stage_end[last_stage] = now
    t.join()

    wall = time.time() - t0
    ov_dur = None
    if "story_overlays" in stage_start:
        ov_dur = stage_end.get("story_overlays", now) - stage_start["story_overlays"]

    print("\n=== RESULT ===")
    print(f"status      : {(load_story_progress(story_id) or {}).get('status')}")
    print(f"output      : {result.get('path')}")
    print(f"wall        : {wall:.1f}s")
    for s in ("prepare_audio", "select_clips", "render_video", "story_overlays", "finalize"):
        if s in stage_start:
            d = stage_end.get(s, now) - stage_start[s]
            print(f"  stage {s:16s}: {d:6.1f}s")
    if ov_samples:
        n = len(ov_samples)
        gu = sum(x[0] for x in ov_samples) / n
        eu = sum(x[1] for x in ov_samples) / n
        mx_gu = max(x[0] for x in ov_samples)
        mx_eu = max(x[1] for x in ov_samples)
        mx_mem = max(x[2] for x in ov_samples)
        print(f"\nGPU during overlay ({n} samples):")
        print(f"  gpu_util mean/max = {gu:.1f}/{mx_gu:.0f}%")
        print(f"  enc_util mean/max = {eu:.1f}/{mx_eu:.0f}%")
        print(f"  gpu_mem max       = {mx_mem:.0f} MB")
    print(f"\nstory_overlays stage = {ov_dur:.1f}s  (CPU baseline was ~487s)" if ov_dur else "")

    # validate output
    path = result.get("path")
    if path and os.path.isfile(path):
        dur = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True,
        ).stdout.strip()
        size = os.path.getsize(path) / 1e6
        print(f"\nOUTPUT OK: {path}\n  duration={dur}s size={size:.0f}MB")
    else:
        print(f"\nOUTPUT MISSING: {path}")


if __name__ == "__main__":
    main()
