"""Disk I/O telemetry shared by the benchmark monitors (hw_logger.py, bench.py).

Why this exists
---------------
The render pipeline is disk-I/O bound: STORAGE_DIR lives on a 7200rpm SATA HDD,
and 73% of a batch's wall time is pure stream-copy ffmpeg (-c copy) that does no
encoding at all. Both monitors sampled CPU/GPU only, so the real constraint was
invisible during the 2026-07-31 tuning pass. This module adds per-sample disk
metrics for the volume holding STORAGE_DIR and for the output volume.

Columns emitted per volume prefix (see PREFIXES / columns()):

    <p>_disk         volume + physical drive actually measured (constant per run)
    <p>_read_bps     bytes/sec read      (delta of psutil's cumulative counter)
    <p>_write_bps    bytes/sec written   (delta)
    <p>_read_ms_op   read latency, ms per operation  (blank when unknown, never 0)
    <p>_write_ms_op  write latency, ms per operation (blank when unknown, never 0)
    <p>_queue        current disk queue length (logical volume)
    <p>_idle_pct     % idle time; 0 = fully saturated. Clearest saturation signal.

Data sources
------------
bytes/sec: psutil.disk_io_counters(perdisk=True), keyed on Windows by
"PhysicalDriveN". The drive-letter -> PhysicalDriveN mapping is resolved ONCE at
startup via PowerShell (Get-Partition, falling back to a CIM association query)
and cached; no subprocess runs per sample. The counters are cumulative, so every
value reported here is a per-interval delta.

queue depth, % idle, latency: psutil exposes no queue depth, and this psutil
build's sdiskio has no busy_time field on Windows (fields are read/write
count/bytes/time only), so the busy_time-derived %busy route is unavailable here.
Instead ONE long-lived `typeperf -si 1` process is launched at startup and a
reader thread keeps the latest value per volume; sampling just reads memory. If
typeperf is missing, fails, is localized (non-English counter names), or goes
stale, those columns go blank rather than reporting a fake zero.

Latency falls back to psutil's delta read_time/read_count when PDH is
unavailable, but PDH is preferred for two reasons, both verified on this host:
  1. psutil's Windows read_time/write_time are documented as milliseconds but are
     actually SECONDS (the C extension divides the 100ns FILETIME by 1e7 instead
     of 1e4) - hence PSUTIL_IO_TIME_TO_MS below. Measured proof: a 178MB read of
     408 ops in 3.64s wall moved read_time by 11. As ms that is 0.027 ms/op and a
     0.3% busy HDD while sustaining 49MB/s (impossible); as seconds it is 27
     ms/op, matching the independently measured 26-167ms.
  2. Those counters are whole-number seconds, so over a 1s sample they quantize
     to 0/1 and the derived latency is very coarse. PDH's "Avg. Disk sec/Read" is
     exact, and it costs nothing extra - same typeperf process.

Everything degrades to blank cells. sample() never raises.

Two reading caveats:
  - typeperf needs ~2s to emit its first row, so the first sample or two of a run
    have blank queue/idle (latency falls back to psutil there).
  - PDH values are the newest 1s window, so on a slower sample interval the
    queue/idle/latency cells describe the last second, not the whole interval.
    The bytes/sec columns are true interval averages. When that last second was
    idle PDH's latency is 0.0, which is not a measurement: it is discarded in
    favour of the psutil fallback (or a blank cell), never written to the CSV.

Note: psutil counters are per *physical* drive, so if STORAGE_DIR and the output
directory sit on the same disk (they do by default: both on D:), the two column
groups carry identical bytes/latency numbers.
"""
import csv as _csv
import os
import re
import subprocess
import threading
import time

import psutil

# Column layout ------------------------------------------------------------
PREFIXES = ("stor", "out")
COLUMN_SUFFIXES = ("disk", "read_bps", "write_bps", "read_ms_op", "write_ms_op",
                   "queue", "idle_pct")
NUMERIC_SUFFIXES = COLUMN_SUFFIXES[1:]  # everything except the "<p>_disk" label

# psutil's Windows read_time/write_time are seconds despite the "in ms" docstring
# (see module docstring for the measurement). Used only for the fallback latency.
PSUTIL_IO_TIME_TO_MS = 1000.0 if os.name == "nt" else 1.0

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
_COUNTER_RE = re.compile(r"LogicalDisk\(([^)]+)\)\\(.+?)\s*$", re.IGNORECASE)


def columns(prefixes=PREFIXES):
    """Full ordered column names for the given volume prefixes."""
    return ["%s_%s" % (p, s) for p in prefixes for s in COLUMN_SUFFIXES]


def numeric_columns(prefixes=PREFIXES):
    """Only the numeric columns (safe to feed into mean/max aggregation)."""
    return ["%s_%s" % (p, s) for p in prefixes for s in NUMERIC_SUFFIXES]


def drive_letter(path):
    """'D:/storage' -> 'D:'. None for UNC paths or anything without a letter."""
    try:
        d = os.path.splitdrive(os.path.abspath(path))[0] or ""
    except Exception:
        return None
    d = d.strip()
    if len(d) == 2 and d[1] == ":" and d[0].isalpha():
        return d.upper()
    return None


# Drive letter -> PhysicalDriveN, resolved once ------------------------------
_map_lock = threading.Lock()
_map_cache = None


def _run_ps(script, timeout):
    try:
        p = subprocess.run(_PS + [script], capture_output=True, text=True,
                           timeout=timeout, creationflags=_NO_WINDOW)
        return p.stdout or ""
    except Exception:
        return ""


def _map_via_get_partition(timeout):
    out = _run_ps(
        "Get-Partition | Where-Object { $_.DriveLetter } | "
        "ForEach-Object { '{0}={1}' -f $_.DriveLetter, $_.DiskNumber }", timeout)
    m = {}
    for line in out.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        letter, num = line.split("=", 1)
        letter = letter.strip().rstrip(":").upper()
        num = num.strip()
        if len(letter) == 1 and letter.isalpha() and num.isdigit():
            m.setdefault(letter + ":", "PhysicalDrive" + num)
    return m


def _map_via_cim(timeout):
    """Fallback for hosts without the Storage module: Win32 associations."""
    out = _run_ps(
        "Get-CimInstance Win32_LogicalDiskToPartition | ForEach-Object { "
        "'{0}|{1}' -f $_.Dependent.DeviceID, $_.Antecedent.DeviceID }", timeout)
    m = {}
    for line in out.splitlines():
        if "|" not in line:
            continue
        dep, ant = line.split("|", 1)
        letter = dep.strip().rstrip(":").upper()
        hit = re.search(r"Disk\s*#\s*(\d+)", ant)
        if hit and len(letter) == 1 and letter.isalpha():
            m.setdefault(letter + ":", "PhysicalDrive" + hit.group(1))
    return m


def letter_to_physical_map(timeout=30.0, refresh=False):
    """Cached {'D:': 'PhysicalDrive0'}. Returns {} if it cannot be resolved."""
    global _map_cache
    with _map_lock:
        if _map_cache is not None and not refresh:
            return _map_cache
        m = {}
        if os.name == "nt":
            for probe in (_map_via_get_partition, _map_via_cim):
                try:
                    m = probe(timeout)
                except Exception:
                    m = {}
                if m:
                    break
        _map_cache = m
        return _map_cache


# typeperf stream: one process for the whole run ----------------------------
class _PerfCounterStream:
    """Streams idle%, queue depth and per-op latency per logical volume.

    Launches a single `typeperf -si 1`; a daemon reader thread keeps only the
    most recent value per volume. Readers get an empty dict when unavailable or
    stale, so a dead/hung typeperf shows as blank cells, never as a fake 0.
    """

    METRICS = {
        "% idle time": "idle",
        "current disk queue length": "queue",
        "avg. disk sec/read": "rlat",   # seconds/op -> converted to ms on read
        "avg. disk sec/write": "wlat",
    }

    def __init__(self, letters, stale_after=12.0):
        self.stale_after = stale_after
        self.letters = [x for x in dict.fromkeys(letters) if x]
        self.error = None
        self._latest = {}
        self._cols = None
        self._lock = threading.Lock()
        self.proc = None
        self._thread = None
        if not self.letters or os.name != "nt":
            self.error = "no local drive letters to sample"
            return
        counters = []
        for lt in self.letters:
            counters.append(r"\LogicalDisk(%s)\%% Idle Time" % lt)
            counters.append(r"\LogicalDisk(%s)\Current Disk Queue Length" % lt)
            counters.append(r"\LogicalDisk(%s)\Avg. Disk sec/Read" % lt)
            counters.append(r"\LogicalDisk(%s)\Avg. Disk sec/Write" % lt)
        try:
            self.proc = subprocess.Popen(
                ["typeperf", *counters, "-si", "1"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                creationflags=_NO_WINDOW)
        except Exception as exc:  # typeperf missing / blocked
            self.proc = None
            self.error = "typeperf unavailable: %s" % exc
            return
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    # -- internals
    def _parse_header(self, fields):
        cols = {}
        for idx, name in enumerate(fields):
            if idx == 0:
                continue  # timestamp column
            hit = _COUNTER_RE.search(name or "")
            if not hit:
                continue
            letter = hit.group(1).strip().upper()
            metric = self.METRICS.get(hit.group(2).strip().lower())
            if metric:
                cols[idx] = (letter, metric)
        self._cols = cols
        if not cols:
            self.error = "typeperf returned no usable counters (localized names?)"

    def _read_loop(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    fields = next(_csv.reader([line]))
                except Exception:
                    continue
                if self._cols is None:
                    self._parse_header(fields)
                    continue
                if not self._cols:
                    break
                now = time.monotonic()
                with self._lock:
                    for idx, (letter, metric) in self._cols.items():
                        if idx >= len(fields):
                            continue
                        try:
                            val = float(fields[idx])
                        except (TypeError, ValueError):
                            continue
                        if metric == "idle":
                            val = max(0.0, min(100.0, val))
                        elif metric in ("rlat", "wlat"):
                            val = max(0.0, val) * 1000.0  # sec/op -> ms/op
                        else:
                            val = max(0.0, val)
                        slot = self._latest.setdefault(letter, {})
                        slot[metric] = val
                        slot["t"] = now
        except Exception as exc:
            self.error = "typeperf reader stopped: %s" % exc

    # -- public
    def get(self, letter):
        """Latest {'idle','queue','rlat','wlat'} for a drive letter.

        Empty dict when the stream never started, the volume is unknown, or the
        newest sample is older than stale_after (typeperf died/hung).
        """
        if not letter or self.proc is None:
            return {}
        with self._lock:
            slot = self._latest.get((letter or "").upper())
            if not slot:
                return {}
            if time.monotonic() - slot.get("t", 0.0) > self.stale_after:
                return {}
            return dict(slot)

    def close(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=3)
            except Exception:
                pass
            try:
                if self.proc.stdout:
                    self.proc.stdout.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)


class _Volume:
    __slots__ = ("prefix", "path", "letter", "physical", "label", "prev", "prev_t")

    def __init__(self, prefix, path):
        self.prefix = prefix
        self.path = path
        self.letter = drive_letter(path)
        self.physical = letter_to_physical_map().get(self.letter) if self.letter else None
        self.label = "|".join(x for x in (self.letter, self.physical) if x) or ""
        self.prev = None
        self.prev_t = None


class DiskProbe:
    """Per-interval disk telemetry for a fixed set of volumes.

    probe = DiskProbe([("stor", Config.STORAGE_DIR), ("out", Config.OUTPUT_DIR)])
    header = base_header + probe.header()
    row    = base_row    + probe.sample()
    probe.close()
    """

    def __init__(self, paths, perf_counters=True, stale_after=12.0):
        self.volumes = [_Volume(prefix, path) for prefix, path in paths]
        self.stream = None
        if perf_counters:
            try:
                self.stream = _PerfCounterStream(
                    [v.letter for v in self.volumes if v.letter], stale_after=stale_after)
            except Exception:
                self.stream = None
        self._prime()

    def _prime(self):
        """Seed the cumulative counters so the first sample is a real delta."""
        per = self._counters()
        t = time.monotonic()
        for v in self.volumes:
            v.prev = per.get(v.physical) if v.physical else None
            v.prev_t = t

    @staticmethod
    def _counters():
        try:
            return psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            return {}

    def header(self):
        return columns(tuple(v.prefix for v in self.volumes))

    def numeric_header(self):
        return numeric_columns(tuple(v.prefix for v in self.volumes))

    def describe(self):
        """One-line human summary of what will actually be measured."""
        parts = []
        for v in self.volumes:
            where = v.label or "unmapped"
            parts.append("%s=%s (%s)" % (v.prefix, v.path, where))
        note = ""
        if self.stream is None or self.stream.error:
            err = self.stream.error if self.stream else "disabled"
            note = "  [queue/idle unavailable: %s]" % err
        return "disk telemetry: " + ", ".join(parts) + note

    def sample(self):
        """Row of values matching header(). Unmeasurable fields come back ''."""
        try:
            per = self._counters()
        except Exception:
            per = {}
        now = time.monotonic()
        row = []
        for v in self.volumes:
            read_bps = write_bps = ""
            read_lat = write_lat = ""
            cur = per.get(v.physical) if v.physical else None
            prev = v.prev
            dt = (now - v.prev_t) if v.prev_t is not None else 0.0
            if cur is not None and prev is not None and dt > 0:
                try:
                    d_rb = cur.read_bytes - prev.read_bytes
                    d_wb = cur.write_bytes - prev.write_bytes
                    d_rc = cur.read_count - prev.read_count
                    d_wc = cur.write_count - prev.write_count
                    d_rt = cur.read_time - prev.read_time
                    d_wt = cur.write_time - prev.write_time
                    # negative == counter reset/wrap: report 0 rather than garbage
                    read_bps = round(max(0, d_rb) / dt)
                    write_bps = round(max(0, d_wb) / dt)
                    # Coarse fallback (see module docstring): whole-second counter,
                    # overwritten below by the exact PDH value when available.
                    # Blank (not 0) whenever latency is unknowable, so the sample
                    # is dropped from means instead of dragging them down. Two
                    # such cases: no ops in the window (d_*c == 0), and a time
                    # counter that did not tick (d_*t == 0 means "under one
                    # second of accumulated I/O time", not "zero latency" - at a
                    # 1-5s interval that is common and would emit a fake 0.00).
                    if d_rc > 0 and d_rt > 0:
                        read_lat = round(d_rt * PSUTIL_IO_TIME_TO_MS / d_rc, 2)
                    if d_wc > 0 and d_wt > 0:
                        write_lat = round(d_wt * PSUTIL_IO_TIME_TO_MS / d_wc, 2)
                except Exception:
                    read_bps = write_bps = read_lat = write_lat = ""
            if cur is not None:
                v.prev = cur
                v.prev_t = now
            idle = queue = ""
            if self.stream is not None:
                try:
                    pdh = self.stream.get(v.letter)
                except Exception:
                    pdh = {}
                if pdh.get("idle") is not None:
                    idle = round(pdh["idle"], 1)
                if pdh.get("queue") is not None:
                    queue = round(pdh["queue"], 2)
                # PDH reports 0.0 when *its* window saw no ops, and that window
                # is only the newest 1 second - so on a 5s sampler a genuinely
                # busy interval can still land on a PDH 0.0 (up to 4 of every 5
                # seconds are unrepresented, and the windows are not aligned).
                # 0.0 therefore means "PDH has nothing to say about this sample",
                # never "the disk answered instantly": it must not overwrite the
                # psutil fallback, and it must not turn a blank cell into a zero
                # that then pulls the mean latency down. Only a positive PDH
                # value is a measurement.
                rlat, wlat = pdh.get("rlat"), pdh.get("wlat")
                if rlat is not None and rlat > 0:
                    read_lat = round(rlat, 2)
                if wlat is not None and wlat > 0:
                    write_lat = round(wlat, 2)
            row.extend([v.label, read_bps, write_bps, read_lat, write_lat, queue, idle])
        return row

    def close(self):
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
