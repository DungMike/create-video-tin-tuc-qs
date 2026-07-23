"""Snapshot the running story-video batch and score overlay performance against the
segmented-GPU code baseline. Run repeatedly (e.g. every 15 min) during a batch.

Parses logs/app.log per story-id for stage timings and detects which overlay path
ran (N parallel GPU segments / single GPU / CPU fallback). Normalises the overlay
stage to seconds-per-video-minute so long clips compare against the 10-min baselines:
    CPU (pre-GPU)      ~48.7 s/min
    GPU inline         ~31.7 s/min
    GPU segmented      ~19    s/min   <- current integrated code (target)
"""
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.config import Config  # noqa: E402

APP_LOG = os.path.join(os.path.dirname(Config.STORAGE_DIR), "logs", "app.log")
if not os.path.isfile(APP_LOG):
    APP_LOG = "logs/app.log"
BATCH_ROOT = os.path.join(Config.STORY_VIDEO_DIR, "batches")

TS_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d+)\s+-\s+auto_video\s+-\s+\w+\s+-\s+(.*)$")


def parse_ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


def newest_running_batch() -> str | None:
    best = None
    for d in glob.glob(os.path.join(BATCH_ROOT, "*")):
        pj = os.path.join(d, "progress.json")
        if not os.path.isfile(pj):
            continue
        try:
            prog = json.load(open(pj, encoding="utf-8"))
        except Exception:
            continue
        mtime = os.path.getmtime(pj)
        if best is None or mtime > best[0]:
            best = (mtime, os.path.basename(d), prog)
    return best[1] if best else None


def load_events_for(story_ids: set[str]) -> dict:
    """Return {story_id: [(ts, message)]} for the given ids from app.log."""
    events: dict[str, list] = {sid: [] for sid in story_ids}
    id_re = re.compile("|".join(re.escape(s) for s in story_ids)) if story_ids else None
    if not id_re:
        return events
    with open(APP_LOG, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not id_re.search(line):
                continue
            m = TS_RE.match(line)
            if not m:
                continue
            ts = parse_ts(m.group(1))
            msg = m.group(3)
            for sid in story_ids:
                if sid in line:
                    events[sid].append((ts, msg))
    return events


def analyse_story(sid: str, evs: list) -> dict:
    """Extract render/overlay/total timings + overlay path from a story's log events."""
    r = {"story": sid, "audio_s": None, "render_video_s": None, "overlay_s": None,
          "total_s": None, "path": "?", "fallback": False, "segments": None,
          "first_ts": None, "last_ts": None, "completed": False}
    if not evs:
        return r
    evs = sorted(evs)
    r["first_ts"] = evs[0][0]
    r["last_ts"] = evs[-1][0]
    t_render_start = t_overlay_start = t_overlay_end = t_start = t_end = None
    for ts, msg in evs:
        if "Selected" in msg and "audio=" in msg:
            mm = re.search(r"audio=([\d.]+)s", msg)
            if mm:
                r["audio_s"] = float(mm.group(1))
            t_start = t_start or ts
        if "Running FFmpeg: ffmpeg -y -f concat" in msg and "story_segments.txt" in msg:
            t_render_start = ts
        # overlay start: first segment or single-pass gpu/cpu command on the base video
        if ("Applying story overlays on GPU" in msg
                or "hwupload_cuda" in msg and "-ss 0.0 -i" in msg
                or "Applying overlay pack" in msg):
            t_overlay_start = t_overlay_start or ts
        if "Subtitle ASS ready" in msg and t_overlay_start is None:
            t_overlay_start = ts  # overlay work begins right after ass prep
        if "parallel GPU segments" in msg:
            mm = re.search(r"via (\d+) parallel", msg)
            r["segments"] = int(mm.group(1)) if mm else None
            r["path"] = f"segmented x{r['segments']}"
            t_overlay_end = ts
        if "Applying story overlays on GPU (overlay_cuda)" in msg and r["path"] == "?":
            r["path"] = "gpu-single"
        if "falling back to CPU overlay" in msg or "GPU overlay pass failed" in msg:
            r["fallback"] = True
        if "segment failed" in msg or "segment concat failed" in msg or "segment audio mux failed" in msg:
            r["fallback"] = True
        if "Final output" in msg or "Pipeline completed" in msg:
            t_end = ts
            r["completed"] = True
        if "Applied precomposed overlay pack" in msg and r["path"] == "?":
            r["path"] = "pack"
    # overlay end: prefer the segments/completed marker; else last event
    if t_overlay_end is None and t_end is not None:
        t_overlay_end = t_end
    if t_render_start and t_overlay_start:
        r["render_video_s"] = round(t_overlay_start - t_render_start, 1)
    if t_overlay_start and t_overlay_end:
        r["overlay_s"] = round(t_overlay_end - t_overlay_start, 1)
    start = t_start or (t_render_start or r["first_ts"])
    if start and t_end:
        r["total_s"] = round(t_end - start, 1)
    if r["path"] == "?" and r["segments"] is None and not r["fallback"]:
        r["path"] = "in-progress"
    return r


def gpu_sample() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,memory.used,power.draw,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6).stdout.strip().splitlines()[0]
        g = [x.strip() for x in out.split(",")]
        return f"gpu={g[0]}% enc={g[1]}% mem={g[2]}MB pow={g[3]}W temp={g[4]}C"
    except Exception as exc:
        return f"(nvidia-smi err: {exc})"


def load_story_progress(sid: str) -> dict:
    """Live per-story status/stage/percent lives in its own progress.json, not in
    the batch progress.json's `stories` array (that only holds the story config)."""
    path = os.path.join(Config.STORY_VIDEO_DIR, sid, "progress.json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def ffmpeg_count() -> int:
    """Exact count of running ffmpeg.exe processes (psutil, not a text-match
    heuristic -- `ps -W`/`tasklist` output text can contain "ffmpeg" as a
    substring of unrelated paths/args and overcount)."""
    import psutil
    return sum(1 for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == "ffmpeg.exe")


BASELINES = {"CPU (pre-GPU)": 48.7, "GPU inline": 31.7, "GPU segmented (target)": 19.0}


def main():
    batch_id = sys.argv[1] if len(sys.argv) > 1 else newest_running_batch()
    if not batch_id:
        print("No batch found.")
        return
    prog = json.load(open(os.path.join(BATCH_ROOT, batch_id, "progress.json"), encoding="utf-8"))
    stories = prog.get("stories", [])
    ids = {str(s.get("storyId")) for s in stories if s.get("storyId")}
    events = load_events_for(ids)

    from collections import Counter
    status_counts = Counter(s.get("status") for s in stories)
    now = datetime.now().strftime("%H:%M:%S")
    print("=" * 78)
    print(f"BATCH {batch_id}  @ {now}   status={prog.get('status')}  "
          f"stories={len(stories)} {dict(status_counts)}")
    print(f"GPU: {gpu_sample()}   ffmpeg_procs={ffmpeg_count()}")
    print("=" * 78)

    rows = []
    for s in stories:
        sid = str(s.get("storyId"))
        r = analyse_story(sid, events.get(sid, []))
        live = load_story_progress(sid)
        # batch progress.json's `stories` entries don't carry live status; the
        # per-story progress.json does. Fall back to the batch-level status
        # (e.g. "cancelled"/"pending") only when the story has no progress file yet.
        r["status"] = live.get("status") or s.get("status")
        r["stage"] = live.get("stage")
        r["percent"] = live.get("percent")
        rows.append(r)

    hdr = f"{'story':11} {'stat':10} {'stage':14} {'aud_min':>7} {'rndr_s':>7} {'ovl_s':>7} {'ovl_s/min':>9} {'path':14}"
    print(hdr)
    print("-" * len(hdr))
    done = []
    for r in rows:
        if r["status"] in ("pending",) and not r["overlay_s"]:
            continue
        am = f"{r['audio_s']/60:.1f}" if r["audio_s"] else "-"
        rs = f"{r['render_video_s']:.0f}" if r["render_video_s"] else "-"
        os_ = f"{r['overlay_s']:.0f}" if r["overlay_s"] else "-"
        opm = "-"
        if r["overlay_s"] and r["audio_s"]:
            v = r["overlay_s"] / (r["audio_s"] / 60)
            opm = f"{v:.1f}"
            if r["completed"] or r["overlay_s"]:
                done.append((r, v))
        path = r["path"] + ("+FALLBACK" if r["fallback"] else "")
        print(f"{r['story']:11} {str(r['status']):10} {str(r['stage'] or ''):14} {am:>7} {rs:>7} {os_:>7} {opm:>9} {path:14}")

    print("-" * len(hdr))
    if done:
        vals = [v for _, v in done]
        avg = sum(vals) / len(vals)
        print(f"\nOVERLAY throughput: {len(vals)} video(s) measured, "
              f"avg = {avg:.1f} s of overlay per video-minute")
        for name, base in BASELINES.items():
            mark = "  <== current code" if "segmented" in name else ""
            rel = f"({avg/base:.2f}x of this)" if base else ""
            print(f"   vs {name:24} {base:>5} s/min  {rel}{mark}")
        seg_used = sum(1 for r, _ in done if r["segments"])
        fb = sum(1 for r, _ in done if r["fallback"])
        print(f"   overlay path: {seg_used}/{len(done)} used parallel segments, {fb} had a CPU/segment fallback")
    else:
        print("\nNo overlay stage completed yet (videos still in render_video / early overlay).")

    # completed video throughput
    comp = [r for r in rows if r["completed"] and r["total_s"] and r["audio_s"]]
    if comp:
        print("\nCompleted videos (end-to-end):")
        for r in comp:
            rtf = r["audio_s"] / r["total_s"]
            print(f"   {r['story']}: {r['audio_s']/60:.1f} min video in {r['total_s']/60:.1f} min  "
                  f"(RTF {rtf:.2f}x)  path={r['path']}")


if __name__ == "__main__":
    main()
