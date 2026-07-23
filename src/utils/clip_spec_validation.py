"""Guards against story-library clips whose encoded video parameters don't match
the pipeline's dominant format. A concatenated base built from mismatched clips
breaks the CUDA-only overlay filter chain mid-stream when NVDEC hits the boundary:
ffmpeg has to "reconfigure" the filter graph and the static scale_cuda/overlay_cuda
chain can't bridge the change -> "Impossible to convert between formats" /
"Function not implemented". Any mismatched clip is simply excluded from the
selection pool -- the caller's existing random-draw logic then picks a different
clip in its place, so no separate "replace" step is needed.

Checked fields: width/height (Config.TARGET_RESOLUTION) AND pix_fmt/color_range/
color_space (Config.CLIP_EXPECTED_*). The latter three matter just as much as
resolution: a live crash was traced to a clip tagged `smpte170m` (still yuv420p,
1920x1080) sitting next to the library's dominant `bt709` clips -- NVDEC's
mid-stream "Reconfiguring filter graph" event was the actual trigger, not a literal
WxH difference. Strict by design: ties (e.g. "unknown" color tags) are excluded,
not assumed compatible.

Probe results are cached on disk per-library (keyed by absolute clip path + mtime)
so a render only pays the ffprobe cost for clips it hasn't seen before or that
changed since the last check.
"""
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from src.config import Config
from src.utils.logger import logger

_CACHE_FILENAME = "_clip_spec_cache.json"
# Bump when the set of checked fields changes, so old cache entries (written by a
# looser check) are treated as stale and re-probed rather than trusted as-is.
_CACHE_SCHEMA_VERSION = 2

_cache_locks_guard = threading.Lock()
_cache_locks: dict[str, threading.Lock] = {}


def _cache_lock(library_dir: str) -> threading.Lock:
    with _cache_locks_guard:
        lock = _cache_locks.get(library_dir)
        if lock is None:
            lock = threading.Lock()
            _cache_locks[library_dir] = lock
        return lock


def _cache_path(library_dir: str) -> str:
    return os.path.join(library_dir, _CACHE_FILENAME)


def _load_cache(library_dir: str) -> dict:
    try:
        with open(_cache_path(library_dir), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(library_dir: str, cache: dict):
    try:
        with open(_cache_path(library_dir), "w", encoding="utf-8") as handle:
            json.dump(cache, handle)
    except OSError as exc:
        logger.warning(f"[ClipSpecValidation] Could not persist spec cache for {library_dir}: {exc}")


def expected_dimensions() -> tuple[int, int]:
    width, height = Config.TARGET_RESOLUTION.split("x", 1)
    return int(width), int(height)


def expected_spec() -> dict:
    """The single combination of stream parameters a clip must match exactly.

    Defaults reflect the dominant combo measured across the production library
    (thai-11-15-ky-uc-vang-507787: 3467/4037 clips = yuv420p/tv/bt709).
    """
    width, height = expected_dimensions()
    return {
        "w": width,
        "h": height,
        "pix_fmt": Config.CLIP_EXPECTED_PIX_FMT,
        "color_range": Config.CLIP_EXPECTED_COLOR_RANGE,
        "color_space": Config.CLIP_EXPECTED_COLOR_SPACE,
    }


def probe_clip_spec(path: str) -> dict | None:
    """{w, h, pix_fmt, color_range, color_space} of a clip's first video stream,
    or None on failure. One ffprobe call for all fields.

    Uses the `default` writer (one value per line) rather than `csv`: some clips
    carry an extra stream side-data block (observed: HDR "Ambient viewing
    environment" metadata) that makes ffprobe's csv writer emit a spurious extra
    empty field, which would silently break positional parsing.
    """
    fields = ("width", "height", "pix_fmt", "color_range", "color_space")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=" + ",".join(fields),
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15,
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != len(fields):
            return None
        width, height, pix_fmt, color_range, color_space = lines
        return {
            "w": int(width),
            "h": int(height),
            "pix_fmt": pix_fmt,
            "color_range": color_range,
            "color_space": color_space,
        }
    except Exception:
        return None


def _matches_expected(spec: dict | None, expected: dict) -> bool:
    if not spec:
        return False
    return all(spec.get(key) == value for key, value in expected.items())


def filter_valid_clips(
    library_dir: str,
    candidates: list[tuple[str, float]],
    *,
    max_workers: int = 8,
) -> tuple[list[tuple[str, float]], list[str]]:
    """Drop clips whose resolution/pix_fmt/color tags don't exactly match
    `expected_spec()`.

    `candidates` is a list of (absolute_path, duration), the same shape
    StoryVideoPipelineRunner._select_clips builds internally. Returns
    (kept_candidates, excluded_absolute_paths). Uses a per-library on-disk cache
    (path + mtime + schema-version keyed) so unchanged clips are never re-probed.
    """
    expected = expected_spec()
    lock = _cache_lock(library_dir)
    with lock:
        cache = _load_cache(library_dir)

    ok_by_path: dict[str, bool] = {}
    to_probe: list[tuple[str, float]] = []
    for path, _duration in candidates:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            ok_by_path[path] = False
            continue
        entry = cache.get(path)
        if entry and entry.get("mtime") == mtime and entry.get("schema") == _CACHE_SCHEMA_VERSION:
            ok_by_path[path] = bool(entry.get("ok"))
        else:
            to_probe.append((path, mtime))

    if to_probe:
        def _probe_one(item: tuple[str, float]):
            path, mtime = item
            spec = probe_clip_spec(path)
            return path, mtime, spec, _matches_expected(spec, expected)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for path, mtime, spec, ok in executor.map(_probe_one, to_probe):
                ok_by_path[path] = ok
                cache[path] = {
                    "schema": _CACHE_SCHEMA_VERSION,
                    "mtime": mtime,
                    "ok": ok,
                    **(spec or {}),
                }

        with lock:
            _save_cache(library_dir, cache)

    kept: list[tuple[str, float]] = []
    excluded: list[str] = []
    for path, duration in candidates:
        if ok_by_path.get(path, False):
            kept.append((path, duration))
        else:
            excluded.append(path)

    return kept, excluded
