"""Produce a REAL pipeline base video (select_clips + render_simple_video) and keep it,
so overlay-pass optimizations can be measured on a representative base."""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.config import Config
from src.utils.story_video_pipeline import StoryVideoPipelineRunner

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
LIBRARY_ID = "thai-11-15-ky-uc-vang-507787"
SAMPLE = "sample_10a"


def main():
    mp3 = os.path.join(SAMPLES, SAMPLE + ".mp3")
    story_id = f"sv-genbase{int(time.time()) % 100000}"
    os.makedirs(os.path.join(Config.STORY_VIDEO_DIR, story_id), exist_ok=True)
    runner = StoryVideoPipelineRunner(story_id, {
        "input_type": "audio_file",
        "input_value": mp3,
        "output_name": "genbase",
        "library_id": LIBRARY_ID,
    })
    audio = runner._prepare_audio()
    dur = 600.0
    clips = runner._select_clips(dur)
    print("clips:", len(clips))
    t0 = time.time()
    base = runner._render_simple_video(clips, audio, dur)
    print(f"render_video: {time.time()-t0:.1f}s -> {base}")
    dst = os.path.join(SAMPLES, "base_pipeline.mp4")
    import shutil
    shutil.copy2(base, dst)
    print("BASE ->", dst)
    # cleanup story dir (we copied the base out)
    from src.utils.story_video_pipeline import _story_dir
    shutil.rmtree(_story_dir(story_id), ignore_errors=True)


if __name__ == "__main__":
    main()
