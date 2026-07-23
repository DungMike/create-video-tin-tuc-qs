"""Cut audio samples + re-based SRT for render benchmarking.

Produces:
  sample_10a  = [00:00 - 10:00]
  sample_10b  = [10:00 - 20:00]
  sample_30a  = [00:00 - 30:00]
  sample_30b  = [30:00 - 60:00]
Each has a .mp3 (accurate re-encode) and a matching .srt (timestamps rebased to 0).
"""
import os
import re
import subprocess
import sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(SRC_DIR, "..", ".."))
SRC_MP3 = os.path.join(ROOT, "tests", "vid 11 -kenh 1_full.mp3")
SRC_SRT = os.path.join(ROOT, "tests", "vid 11 -kenh 1_full.srt")
OUT_DIR = os.path.join(SRC_DIR, "samples")
os.makedirs(OUT_DIR, exist_ok=True)

SAMPLES = [
    ("sample_10a", 0, 600),
    ("sample_10b", 600, 600),
    ("sample_30a", 0, 1800),
    ("sample_30b", 1800, 1800),
]

TS = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d)")


def parse_ts(m):
    h, mm, s, ms = map(int, m.groups())
    return h * 3600 + mm * 60 + s + ms / 1000.0


def fmt_ts(t):
    if t < 0:
        t = 0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    mm, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{mm:02d}:{s:02d},{ms:03d}"


def parse_srt(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    blocks = re.split(r"\n\s*\n", raw.strip())
    cues = []
    for b in blocks:
        lines = [l for l in b.splitlines() if l.strip() != ""]
        if len(lines) < 2:
            continue
        # find the timing line
        timing_idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if timing_idx is None:
            continue
        times = TS.findall(lines[timing_idx])
        if len(times) < 2:
            continue
        start = parse_ts(re.match(TS, lines[timing_idx].split("-->")[0].strip()))
        end = parse_ts(re.match(TS, lines[timing_idx].split("-->")[1].strip()))
        text = "\n".join(lines[timing_idx + 1:])
        cues.append((start, end, text))
    return cues


def slice_srt(cues, start, dur, out_path):
    end = start + dur
    out = []
    idx = 1
    for cs, ce, text in cues:
        if ce <= start or cs >= end:
            continue
        ns = max(0.0, cs - start)
        ne = min(dur, ce - start)
        if ne <= ns:
            continue
        out.append(f"{idx}\n{fmt_ts(ns)} --> {fmt_ts(ne)}\n{text}\n")
        idx += 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return idx - 1


def cut_audio(start, dur, out_path):
    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-t", str(dur),
        "-i", SRC_MP3, "-c:a", "libmp3lame", "-b:a", "192k", out_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    cues = parse_srt(SRC_SRT)
    print(f"Loaded {len(cues)} cues from source SRT")
    for name, start, dur in SAMPLES:
        mp3 = os.path.join(OUT_DIR, name + ".mp3")
        srt = os.path.join(OUT_DIR, name + ".srt")
        print(f"[{name}] cutting audio {start}s +{dur}s ...", flush=True)
        cut_audio(start, dur, mp3)
        n = slice_srt(cues, start, dur, srt)
        size = os.path.getsize(mp3) / 1e6
        print(f"[{name}] mp3={size:.1f}MB  srt_cues={n}")
    print("DONE ->", OUT_DIR)


if __name__ == "__main__":
    main()
