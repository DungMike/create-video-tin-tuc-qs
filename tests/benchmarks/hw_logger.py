"""Continuously log hardware metrics (GPU/NVENC/NVDEC + CPU/RAM + disk I/O) and the
running batch's stage to a CSV, so we have a record of a batch's overlay-render
performance.

Samples every INTERVAL seconds. Also prints a line whenever the running story's
stage changes (e.g. entering story_overlays = subtitle+CTA+waveform). Stops when the
batch is no longer running, or when a stop-file appears next to the CSV.

Disk columns (appended after the pre-existing ones, see disk_probe.py) cover the
volume holding STORAGE_DIR and the output volume: bytes/sec, per-op latency,
queue depth and % idle. They exist because this pipeline is disk-bound, and a
CPU/GPU-only log hid that: idle_pct hitting 0 with a 3-8 deep queue is the tell,
even while cpu_pct and enc_util look comfortable.
"""
import csv
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.config import Config  # noqa: E402
import psutil  # noqa: E402
import disk_probe  # noqa: E402

BATCH_ROOT = os.path.join(Config.STORY_VIDEO_DIR, "batches")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(OUT_DIR, exist_ok=True)
INTERVAL = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0

GPU_Q = "utilization.gpu,utilization.encoder,utilization.decoder,memory.used,power.draw,temperature.gpu,clocks.sm"


def newest_running_batch():
    best = None
    for d in glob.glob(os.path.join(BATCH_ROOT, "*")):
        pj = os.path.join(d, "progress.json")
        if not os.path.isfile(pj):
            continue
        m = os.path.getmtime(pj)
        if best is None or m > best[0]:
            best = (m, os.path.basename(d))
    return best[1] if best else None


def gpu():
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={GPU_Q}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=6).stdout.strip().splitlines()[0]
        return [x.strip() for x in out.split(",")]
    except Exception:
        return [""] * 7


def _mbps(v):
    """bytes/sec -> MB/s for console prints; '' stays '' (not measurable)."""
    try:
        return round(float(v) / 1e6, 1)
    except (TypeError, ValueError):
        return ""


def batch_state(batch_id):
    try:
        d = json.load(open(os.path.join(BATCH_ROOT, batch_id, "progress.json"), encoding="utf-8"))
    except Exception:
        return None, None, None, {}
    from collections import Counter
    stories = d.get("stories", [])
    counts = dict(Counter(s.get("status") for s in stories))
    run = next((s for s in stories if s.get("status") == "running"), None)
    stage = run.get("stage") if run else ""
    pct = run.get("percent") if run else ""
    return d.get("status"), stage, pct, counts


def resolve_csv_path(path, header):
    """Keep already-written CSVs parseable.

    This logger appends across restarts. A file written before the disk columns
    existed has a shorter header, so appending wider rows to it would produce a
    ragged CSV; start a fresh file in that case instead.
    """
    if not os.path.isfile(path):
        return path
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            old = next(csv.reader(fh), None)
    except Exception:
        return path
    if old is None or old == header:
        return path
    root, ext = os.path.splitext(path)
    new_path = f"{root}_{datetime.now().strftime('%H%M%S')}{ext}"
    print(f"existing CSV has the old (pre-disk) columns; writing a new file -> {new_path}")
    return new_path


def main():
    batch_id = sys.argv[1] if len(sys.argv) > 1 else newest_running_batch()
    if not batch_id:
        print("No batch found.")
        return
    stop_file = os.path.join(OUT_DIR, f"hwlog_{batch_id}.stop")
    probe = disk_probe.DiskProbe([("stor", Config.STORAGE_DIR), ("out", Config.OUTPUT_DIR)])
    header = ["ts", "batch_status", "story_stage", "story_pct", "done", "running", "pending",
              "cpu_pct", "ram_pct", "ram_used_mb", "gpu_util", "enc_util", "dec_util",
              "gpu_mem_mb", "power_w", "temp_c", "sm_clk", "ffmpeg_procs"] + probe.header()
    csv_path = resolve_csv_path(os.path.join(OUT_DIR, f"hwlog_{batch_id}.csv"), header)
    print(f"Logging batch {batch_id} -> {csv_path} (every {INTERVAL}s). Stop-file: {stop_file}")
    print(probe.describe())
    new = not os.path.isfile(csv_path)
    psutil.cpu_percent(interval=None)
    last_stage = None
    idle = 0
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(header)
            while True:
                if os.path.isfile(stop_file):
                    print("stop-file seen, exiting.")
                    break
                status, stage, pct, counts = batch_state(batch_id)
                if status is None:
                    break
                cpu = psutil.cpu_percent(interval=None)
                vm = psutil.virtual_memory()
                g = gpu()
                d = probe.sample()
                nff = sum(1 for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower() == 'ffmpeg.exe')
                ts = datetime.now().strftime("%H:%M:%S")
                w.writerow([ts, status, stage, pct, counts.get("completed", 0), counts.get("running", 0),
                            counts.get("pending", 0), cpu, vm.percent, round(vm.used / 1e6), *g, nff, *d])
                fh.flush()
                if stage != last_stage:
                    dm = dict(zip(probe.header(), d))
                    print(f"[{ts}] stage -> {stage} ({pct}%)  status={status}  "
                          f"done={counts.get('completed',0)} run={counts.get('running',0)} pend={counts.get('pending',0)}  "
                          f"gpu={g[0]}% enc={g[1]}% dec={g[2]}% mem={g[3]}MB cpu={cpu}%  "
                          f"disk r={_mbps(dm.get('stor_read_bps'))} w={_mbps(dm.get('stor_write_bps'))}MB/s "
                          f"lat={dm.get('stor_read_ms_op')}ms q={dm.get('stor_queue')} idle={dm.get('stor_idle_pct')}%")
                    last_stage = stage
                if status not in ("running", "cancelling"):
                    idle += 1
                    if idle > 3:
                        print(f"batch status={status}, exiting logger.")
                        break
                else:
                    idle = 0
                time.sleep(INTERVAL)
    finally:
        probe.close()
    print(f"CSV -> {csv_path}")


if __name__ == "__main__":
    main()
