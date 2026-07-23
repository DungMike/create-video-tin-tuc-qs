"""Render performance benchmark for the story-video pipeline.

Submits render jobs through the real HTTP API (POST /api/story-video/create),
polls progress, and samples GPU (nvidia-smi) + CPU/RAM (psutil) once per second
into a single time-series CSV. Then computes per-scenario summary stats.

Scenarios (selectable via CLI):
  single10   : 1 x 10-min render
  par10      : 2 x 10-min renders in parallel
  single30   : 1 x 30-min render
  par30      : 2 x 30-min renders in parallel

Usage:
  python bench.py single10 par10 single30 par30
  python bench.py all
"""
import csv
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import psutil
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)

API = "http://127.0.0.1:5005"
LIBRARY_ID = "thai-11-15-ky-uc-vang-507787"

RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
CSV_PATH = os.path.join(OUT, f"metrics_{RUN_ID}.csv")
SUMMARY_PATH = os.path.join(OUT, f"summary_{RUN_ID}.json")

GPU_FIELDS = "utilization.gpu,utilization.encoder,utilization.decoder,memory.used,power.draw,temperature.gpu,clocks.sm"


def now():
    return time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%H:%M:%S")


# --------------------------------------------------------------------------
# Monitor: 1 Hz system + GPU sampler writing to CSV
# --------------------------------------------------------------------------
class Monitor(threading.Thread):
    HEADER = [
        "ts", "elapsed", "phase",
        "cpu_pct", "ram_pct", "ram_used_mb",
        "gpu_util", "enc_util", "dec_util", "gpu_mem_mb", "power_w", "temp_c", "sm_clk",
        "ffmpeg_procs", "ffmpeg_cpu_pct",
    ]

    def __init__(self, csv_path):
        super().__init__(daemon=True)
        self.csv_path = csv_path
        self._stop = threading.Event()
        self.phase = "idle"
        self.t0 = now()
        self._proc_cache = {}
        psutil.cpu_percent(interval=None)  # prime

    def set_phase(self, name):
        self.phase = name

    def _gpu(self):
        try:
            out = subprocess.run(
                ["nvidia-smi", f"--query-gpu={GPU_FIELDS}",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip().splitlines()[0]
            return [x.strip() for x in out.split(",")]
        except Exception:
            return ["", "", "", "", "", "", ""]

    def _ffmpeg(self):
        procs = 0
        cpu = 0.0
        seen = set()
        for p in psutil.process_iter(["name"]):
            try:
                nm = (p.info["name"] or "").lower()
                if "ffmpeg" in nm:
                    procs += 1
                    seen.add(p.pid)
                    if p.pid not in self._proc_cache:
                        self._proc_cache[p.pid] = p
                        try:
                            p.cpu_percent(interval=None)
                        except Exception:
                            pass
                    else:
                        try:
                            cpu += self._proc_cache[p.pid].cpu_percent(interval=None)
                        except Exception:
                            pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for pid in list(self._proc_cache):
            if pid not in seen:
                del self._proc_cache[pid]
        return procs, round(cpu, 1)

    def run(self):
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(self.HEADER)
            while not self._stop.is_set():
                t = now()
                cpu = psutil.cpu_percent(interval=None)
                vm = psutil.virtual_memory()
                g = self._gpu()
                fp, fc = self._ffmpeg()
                w.writerow([
                    round(t, 2), round(t - self.t0, 1), self.phase,
                    cpu, vm.percent, round(vm.used / 1e6),
                    *g, fp, fc,
                ])
                f.flush()
                # keep ~1s cadence accounting for nvidia-smi latency
                dt = now() - t
                time.sleep(max(0.2, 1.0 - dt))

    def stop(self):
        self._stop.set()
        self.join(timeout=5)


# --------------------------------------------------------------------------
# API helpers
# --------------------------------------------------------------------------
def submit(sample, output_name):
    mp3 = os.path.join(SAMPLES, sample + ".mp3")
    srt = os.path.join(SAMPLES, sample + ".srt")
    payload = {
        "inputType": "audio_file",
        "outputName": output_name,
        "libraryId": LIBRARY_ID,
        "inputValue": "",
    }
    files = {
        "payload": (None, json.dumps(payload)),
        "audio": (os.path.basename(mp3), open(mp3, "rb"), "audio/mpeg"),
        "subtitle": (os.path.basename(srt), open(srt, "rb"), "application/x-subrip"),
    }
    r = requests.post(f"{API}/api/story-video/create", files=files, timeout=60)
    r.raise_for_status()
    return r.json()["storyId"]


def poll(story_id, on_stage=None):
    """Poll until terminal. Returns (status, stage_times, final_progress)."""
    stage_times = {}
    last_stage = None
    t_stage = now()
    while True:
        try:
            r = requests.get(f"{API}/api/story-video/{story_id}/progress", timeout=15)
            p = r.json()
        except Exception:
            time.sleep(1.5)
            continue
        stage = p.get("stage")
        status = p.get("status")
        if stage != last_stage:
            t = now()
            if last_stage is not None:
                stage_times[last_stage] = stage_times.get(last_stage, 0) + (t - t_stage)
            last_stage = stage
            t_stage = t
            if on_stage:
                on_stage(story_id, stage, p.get("percent"))
        if status in ("completed", "failed", "cancelled"):
            stage_times[last_stage] = stage_times.get(last_stage, 0) + (now() - t_stage)
            return status, stage_times, p
        time.sleep(1.5)


# --------------------------------------------------------------------------
# Scenario runner
# --------------------------------------------------------------------------
def run_scenario(mon, name, samples, video_secs):
    """samples: list of sample basenames. Submits all, waits for all."""
    print(f"\n=== SCENARIO {name}: {len(samples)} job(s) -> {samples} ===", flush=True)
    mon.set_phase(name)
    t_start = now()
    ids = []
    for i, s in enumerate(samples):
        sid = submit(s, f"bench_{name}_{i}_{RUN_ID}")
        ids.append(sid)
        print(f"  submitted {s} -> {sid}", flush=True)

    results = {}
    lock = threading.Lock()

    def worker(sid, sample):
        def on_stage(_id, stage, pct):
            print(f"  [{_id}] -> {stage} ({pct}%) @ {iso(now())}", flush=True)
        st, stage_times, prog = poll(sid, on_stage)
        with lock:
            results[sid] = {
                "sample": sample,
                "status": st,
                "stage_times": {k: round(v, 1) for k, v in stage_times.items()},
                "videoPath": (prog.get("result") or {}).get("videoPath"),
                "error": prog.get("error"),
            }

    threads = [threading.Thread(target=worker, args=(sid, s)) for sid, s in zip(ids, samples)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t_end = now()
    mon.set_phase("idle")

    wall = t_end - t_start
    scenario = {
        "name": name,
        "n_jobs": len(samples),
        "video_secs_each": video_secs,
        "video_secs_total": video_secs * len(samples),
        "wall_s": round(wall, 1),
        "realtime_factor": round((video_secs * len(samples)) / wall, 2) if wall else None,
        "t_start": t_start,
        "t_end": t_end,
        "jobs": results,
    }
    print(f"  WALL={wall:.1f}s  RTF={scenario['realtime_factor']}x  "
          f"(total video {scenario['video_secs_total']}s)", flush=True)
    return scenario


def summarize_window(csv_path, t0, t1):
    """Aggregate metric stats for rows within [t0, t1]."""
    cols = ["cpu_pct", "ram_pct", "gpu_util", "enc_util", "dec_util",
            "gpu_mem_mb", "power_w", "temp_c", "ffmpeg_cpu_pct"]
    data = {c: [] for c in cols}
    n = 0
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts = float(row["ts"])
            except ValueError:
                continue
            if t0 <= ts <= t1:
                n += 1
                for c in cols:
                    try:
                        data[c].append(float(row[c]))
                    except (ValueError, KeyError):
                        pass
    out = {"samples": n}
    for c in cols:
        v = data[c]
        if v:
            out[c] = {"mean": round(sum(v) / len(v), 1), "max": round(max(v), 1)}
        else:
            out[c] = {"mean": None, "max": None}
    return out


SCENARIO_DEFS = {
    "single10": (["sample_10a"], 600),
    "par10": (["sample_10a", "sample_10b"], 600),
    "single30": (["sample_30a"], 1800),
    "par30": (["sample_30a", "sample_30b"], 1800),
}


def main():
    args = sys.argv[1:] or ["all"]
    if args == ["all"]:
        args = ["single10", "par10", "single30", "par30"]

    # sanity: API up
    requests.get(f"{API}/api/story-video/libraries", timeout=10).raise_for_status()
    print(f"CSV -> {CSV_PATH}")
    mon = Monitor(CSV_PATH)
    mon.start()
    time.sleep(3)  # idle baseline

    scenarios = []
    for a in args:
        if a not in SCENARIO_DEFS:
            print(f"skip unknown scenario {a}")
            continue
        samples, vsec = SCENARIO_DEFS[a]
        sc = run_scenario(mon, a, samples, vsec)
        scenarios.append(sc)
        time.sleep(8)  # settle / separate windows

    time.sleep(2)
    mon.stop()

    for sc in scenarios:
        sc["hw"] = summarize_window(CSV_PATH, sc["t_start"], sc["t_end"])

    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump({"run_id": RUN_ID, "csv": CSV_PATH, "scenarios": scenarios},
                  f, indent=2, ensure_ascii=False)
    print(f"\nSUMMARY -> {SUMMARY_PATH}")
    # compact print
    for sc in scenarios:
        hw = sc["hw"]
        print(f"\n[{sc['name']}] wall={sc['wall_s']}s rtf={sc['realtime_factor']}x")
        print(f"   GPU util mean/max = {hw['gpu_util']['mean']}/{hw['gpu_util']['max']}%  "
              f"ENC {hw['enc_util']['mean']}/{hw['enc_util']['max']}%  "
              f"mem {hw['gpu_mem_mb']['max']}MB  pow {hw['power_w']['max']}W  temp {hw['temp_c']['max']}C")
        print(f"   CPU sys mean/max = {hw['cpu_pct']['mean']}/{hw['cpu_pct']['max']}%  "
              f"ffmpeg {hw['ffmpeg_cpu_pct']['max']}%  RAM {hw['ram_pct']['max']}%")


if __name__ == "__main__":
    main()
