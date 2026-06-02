"""Test color grading presets for 1990s TV effect.

Generates 10s sample videos with different color grading presets
using clips from the story library, overlaid with active TV noise.
"""

import json
import os
import subprocess
import sys
import time

STORAGE_DIR = "./storage"
STORY_LIBRARY_DIR = os.path.join(STORAGE_DIR, "story_library")
NOISE_DIR = os.path.join(STORAGE_DIR, "story_tv_noise_overlays")
OUTPUT_DIR = os.path.join(STORAGE_DIR, "output", "story-video")
TARGET_W, TARGET_H = 1920, 1080
TARGET_FPS = 30
TEST_DURATION = 10

# Color grading presets for 1990s TV
COLOR_GRADE_PRESETS = {
    "90s_broadcast": (
        "eq=saturation=0.62:contrast=0.88:brightness=0.03:gamma=1.05,"
        "colorbalance=rs=0.08:gs=0.04:bs=-0.08:rm=0.04:gm=0.02:bm=-0.05,"
        "curves=m='0/0.06|0.5/0.48|1/0.92',"
        "unsharp=3:3:-0.4:3:3:-0.4"
    ),
    "vhs_warm": (
        "eq=saturation=0.55:contrast=0.82:brightness=0.05:gamma=1.08,"
        "colorbalance=rs=0.12:gs=0.06:bs=-0.12:rm=0.06:gm=0.02:bm=-0.08,"
        "curves=m='0/0.10|0.5/0.50|1/0.88',"
        "unsharp=3:3:-0.6:3:3:-0.6"
    ),
    "faded_film": (
        "eq=saturation=0.50:contrast=0.80:brightness=0.04,"
        "colorbalance=rs=0.05:gs=0.05:bs=0.02:rm=0.03:gm=0.03:bm=0.01,"
        "curves=m='0/0.08|0.25/0.22|0.75/0.72|1/0.90'"
    ),
    # Variation: Heavy CRT / 80s era 
    "retro_crt_heavy": (
        "eq=saturation=0.45:contrast=0.78:brightness=0.06:gamma=1.12,"
        "colorbalance=rs=0.15:gs=0.08:bs=-0.15:rm=0.08:gm=0.03:bm=-0.10,"
        "curves=m='0/0.12|0.3/0.30|0.7/0.65|1/0.85',"
        "unsharp=3:3:-0.8:3:3:-0.8"
    ),
}


def find_sample_clip():
    """Find a clip from story library."""
    index_path = os.path.join(STORY_LIBRARY_DIR, "index.json")
    if not os.path.isfile(index_path):
        print("ERROR: Story library index not found.")
        return None
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)
    for asset in index.get("assets", []):
        rel_path = asset.get("relative_path", "")
        full_path = os.path.join(STORY_LIBRARY_DIR, rel_path)
        if os.path.isfile(full_path):
            dur = asset.get("duration", 0)
            print(f"Found clip: {rel_path} (duration={dur}s)")
            return full_path
    return None


def find_active_noise_overlays():
    """Find active TV noise alpha MOV files."""
    index_path = os.path.join(NOISE_DIR, "index.json")
    if not os.path.isfile(index_path):
        return []
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)
    results = []
    for overlay in index.get("overlays", []):
        if not overlay.get("enabled", True):
            continue
        if overlay.get("status") != "ready":
            continue
        processed = overlay.get("processedFilename", "")
        if processed:
            full_path = os.path.join(NOISE_DIR, processed)
            if os.path.isfile(full_path):
                results.append({
                    "path": full_path,
                    "opacity": overlay.get("opacity", 0.35),
                    "name": overlay.get("name", "unknown"),
                })
    return results


def generate_test_video(preset_name, color_filter, clip_path, noise_overlays, output_path):
    """Generate a test video with color grading + noise overlay."""
    print(f"\n{'='*60}")
    print(f"Generating: {preset_name}")
    print(f"  Color filter: {color_filter[:80]}...")
    print(f"  Noise overlays: {len(noise_overlays)}")
    print(f"  Output: {output_path}")

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", clip_path,
    ]

    # Add noise overlay inputs
    for noise in noise_overlays:
        cmd.extend(["-stream_loop", "-1", "-i", noise["path"]])

    # Build filter chain
    filter_parts = []

    # Step 1: Scale and color grade the base video
    base_filter = (
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},fps={TARGET_FPS},setsar=1,"
        f"{color_filter}"
    )
    base_filter += "[base]"
    filter_parts.append(base_filter)

    # Step 2: Overlay noise layers
    chain_label = "[base]"
    for idx, noise in enumerate(noise_overlays):
        input_idx = idx + 1
        noise_label = f"noise{idx}"
        out_label = f"tvn{idx}"
        filter_parts.append(f"[{input_idx}:v]setpts=PTS-STARTPTS[{noise_label}]")
        filter_parts.append(
            f"{chain_label}[{noise_label}]overlay=0:0:format=auto:eof_action=repeat:eval=init[{out_label}]"
        )
        chain_label = f"[{out_label}]"

    # Step 3: Final format
    filter_parts.append(f"{chain_label}format=yuv420p[v]")

    cmd.extend([
        "-filter_complex", ";".join(filter_parts),
        "-map", "[v]",
        "-an",
        "-t", str(TEST_DURATION),
        "-c:v", "h264_nvenc", "-preset", "p2", "-b:v", "8M",
        "-movflags", "+faststart",
        output_path,
    ])

    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"  ERROR: FFmpeg failed!")
        print(f"  stderr: {result.stderr[-500:]}")
        return False

    file_size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0
    print(f"  OK: {elapsed:.1f}s, {file_size / 1024 / 1024:.1f}MB")
    return True


def generate_no_color_grade(clip_path, noise_overlays, output_path):
    """Generate reference video with ONLY noise overlay (no color grading)."""
    print(f"\n{'='*60}")
    print(f"Generating: reference_no_color_grade")

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", clip_path,
    ]

    for noise in noise_overlays:
        cmd.extend(["-stream_loop", "-1", "-i", noise["path"]])

    filter_parts = []
    base_filter = (
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},fps={TARGET_FPS},setsar=1[base]"
    )
    filter_parts.append(base_filter)

    chain_label = "[base]"
    for idx, noise in enumerate(noise_overlays):
        input_idx = idx + 1
        noise_label = f"noise{idx}"
        out_label = f"tvn{idx}"
        filter_parts.append(f"[{input_idx}:v]setpts=PTS-STARTPTS[{noise_label}]")
        filter_parts.append(
            f"{chain_label}[{noise_label}]overlay=0:0:format=auto:eof_action=repeat:eval=init[{out_label}]"
        )
        chain_label = f"[{out_label}]"

    filter_parts.append(f"{chain_label}format=yuv420p[v]")

    cmd.extend([
        "-filter_complex", ";".join(filter_parts),
        "-map", "[v]",
        "-an",
        "-t", str(TEST_DURATION),
        "-c:v", "h264_nvenc", "-preset", "p2", "-b:v", "8M",
        "-movflags", "+faststart",
        output_path,
    ])

    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"  ERROR: FFmpeg failed!")
        print(f"  stderr: {result.stderr[-500:]}")
        return False

    file_size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0
    print(f"  OK: {elapsed:.1f}s, {file_size / 1024 / 1024:.1f}MB")
    return True


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    clip_path = find_sample_clip()
    if not clip_path:
        print("No sample clip found in story library. Aborting.")
        sys.exit(1)

    noise_overlays = find_active_noise_overlays()
    print(f"\nFound {len(noise_overlays)} active noise overlays:")
    for n in noise_overlays:
        print(f"  - {n['name']} (opacity={n['opacity']})")

    results = []

    # Reference: no color grading
    ref_path = os.path.join(OUTPUT_DIR, "test_reference_no_color.mp4")
    ok = generate_no_color_grade(clip_path, noise_overlays, ref_path)
    results.append(("reference_no_color", ok, ref_path))

    # Each preset
    for preset_name, color_filter in COLOR_GRADE_PRESETS.items():
        output_path = os.path.join(OUTPUT_DIR, f"test_{preset_name}_10s.mp4")
        ok = generate_test_video(preset_name, color_filter, clip_path, noise_overlays, output_path)
        results.append((preset_name, ok, output_path))

    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    for name, ok, path in results:
        status = "OK" if ok else "FAILED"
        size = f"{os.path.getsize(path) / 1024 / 1024:.1f}MB" if ok and os.path.isfile(path) else "N/A"
        print(f"  [{status}] {name}: {size} → {path}")

    print(f"\nAll output files in: {os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    main()
