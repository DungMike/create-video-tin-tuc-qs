"""
Scale video lên 108% (zoom in 8%, center crop giữ nguyên 1920x1080).
Sử dụng NVENC (GPU) để encode nhanh.

Input:  storage/output/kenh-2-goc-khuat-03-05.mp4
Output: storage/output/kenh-2-goc-khuat-03-05_scaled108.mp4
"""

import subprocess
import sys
import os
import time

# ── Paths ──────────────────────────────────────────────────────────
INPUT  = r"e:\CRAWL VIDEO - AUDIO - QS\storage\output\kenh-2-goc-khuat-03-05.mp4"
OUTPUT = r"e:\CRAWL VIDEO - AUDIO - QS\storage\output\kenh-2-goc-khuat-03-05_scaled108.mp4"

# ── Scale factor ───────────────────────────────────────────────────
SCALE = 1.08  # 108%

# Original resolution
W, H = 1920, 1080

# Scaled resolution (intermediate)
SW = int(W * SCALE)  # 2073
SH = int(H * SCALE)  # 1166

# Make sure scaled dimensions are even (required by h264)
SW = SW + (SW % 2)
SH = SH + (SH % 2)


def main():
    if not os.path.isfile(INPUT):
        print(f"[ERROR] Input file not found: {INPUT}")
        sys.exit(1)

    if os.path.isfile(OUTPUT):
        print(f"[WARN] Output already exists, will overwrite: {OUTPUT}")

    # Filter: scale up to 108%, then center-crop back to 1920x1080
    vf = f"scale={SW}:{SH},crop={W}:{H}"

    cmd = [
        "ffmpeg", "-y",
        "-hwaccel", "cuda",              # GPU-accelerated decode
        "-i", INPUT,
        "-vf", vf,
        "-c:v", "h264_nvenc",            # GPU encode
        "-preset", "p4",                 # balanced speed/quality
        "-cq", "20",                     # constant quality
        "-c:a", "copy",                  # keep audio as-is
        OUTPUT,
    ]

    print("=" * 60)
    print(f"  Scale video: {SCALE * 100:.0f}%")
    print(f"  {W}x{H} -> scale {SW}x{SH} -> crop {W}x{H}")
    print(f"  Input:  {INPUT}")
    print(f"  Output: {OUTPUT}")
    print("=" * 60)
    print()
    print(" ".join(cmd))
    print()

    start = time.time()
    result = subprocess.run(cmd)
    elapsed = time.time() - start

    if result.returncode == 0:
        size_mb = os.path.getsize(OUTPUT) / (1024 * 1024)
        print()
        print(f"\n[OK] Done in {elapsed:.1f}s  |  Output: {size_mb:.1f} MB")
    else:
        print(f"\n[ERROR] FFmpeg failed with code {result.returncode}")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
