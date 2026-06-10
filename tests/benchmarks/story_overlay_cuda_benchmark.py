from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK_DIR = ROOT / "storage" / "overlay_cuda_bench"
DEFAULT_DURATIONS = [60, 300, 600]
DEFAULT_MODES = ["cpu", "cpu_pts_n", "cpu_pack", "cpu_pack_pts_n"]
GPU_QUERY = (
    "timestamp,name,utilization.gpu,utilization.memory,"
    "encoder.stats.sessionCount,encoder.stats.averageFps,"
    "memory.used,memory.total,power.draw"
)


@dataclass(frozen=True)
class OverlayInput:
    path: Path
    kind: str
    x: str = "0"
    y: str = "0"
    source_path: Path | None = None
    chroma_color: str = "black"
    similarity: float = 0.08
    blend: float = 0.02
    scale_w: str | None = None
    scale_h: str | None = None


def _json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _duration_label(seconds: int) -> str:
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text in {"N/A", "[Not Supported]"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _run_text(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def ffmpeg_version(ffmpeg_bin: str) -> str:
    try:
        completed = _run_text([ffmpeg_bin, "-version"], timeout=10)
    except Exception as exc:
        return f"unavailable: {exc}"
    return (completed.stdout.splitlines() or [completed.stderr.strip() or "unknown"])[0]


def available_filters(ffmpeg_bin: str) -> set[str]:
    completed = _run_text([ffmpeg_bin, "-hide_banner", "-filters"], timeout=15)
    text = f"{completed.stdout}\n{completed.stderr}"
    found: set[str] = set()
    for name in ("overlay_cuda", "hwupload_cuda", "scale_cuda", "scale_npp", "chromakey_cuda"):
        if name in text:
            found.add(name)
    return found


def probe_video(ffprobe_bin: str, path: Path) -> dict[str, Any]:
    completed = _run_text(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height,r_frame_rate,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        timeout=20,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {completed.stderr.strip()}")
    data = json.loads(completed.stdout)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    return {
        "path": str(path),
        "codec": stream.get("codec_name"),
        "pix_fmt": stream.get("pix_fmt"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": stream.get("r_frame_rate"),
        "duration": _parse_float(stream.get("duration")) or _parse_float(fmt.get("duration")),
    }


def default_source_video(max_duration: int) -> Path:
    candidates = sorted(
        (ROOT / "storage" / "story_video").glob("sv-*/renders/video_*.mp4"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No pre-overlay story render found under storage/story_video/sv-*/renders.")
    return candidates[0]


def default_overlays() -> list[OverlayInput]:
    overlays: list[OverlayInput] = []
    tv_index = ROOT / "storage" / "story_tv_noise_overlays" / "index.json"
    if tv_index.exists():
        records = _json_load(tv_index).get("overlays", [])
        enabled = [item for item in records if item.get("enabled") and item.get("processedRelativePath")]
        enabled.sort(key=lambda item: item.get("order", 0))
        for item in enabled:
            source_path = ROOT / "storage" / item["relativePath"] if item.get("relativePath") else None
            overlays.append(
                OverlayInput(
                    ROOT / "storage" / item["processedRelativePath"],
                    "tv_noise",
                    source_path=source_path,
                    chroma_color="black",
                    similarity=float(item.get("tolerance") or 0.08),
                    blend=float(item.get("softness") or 0.02),
                    scale_w="1920",
                    scale_h="1080",
                )
            )

    wave_index = ROOT / "storage" / "waveform_overlays" / "index.json"
    if wave_index.exists():
        records = _json_load(wave_index).get("overlays", [])
        default = next((item for item in records if item.get("isDefault")), None)
        if default and default.get("processedRelativePath"):
            margin = int(default.get("margin") or 15)
            source_path = ROOT / "storage" / default["relativePath"] if default.get("relativePath") else None
            overlays.append(
                OverlayInput(
                    ROOT / "storage" / default["processedRelativePath"],
                    "waveform",
                    f"W-w-{margin}",
                    f"H-h-{margin}",
                    source_path=source_path,
                    chroma_color=str(default.get("keyColor") or "0x2baa40"),
                    similarity=float(default.get("similarity") or 0.12),
                    blend=float(default.get("blend") or 0.03),
                    scale_w=str(int(default.get("scaleWidth") or 420)),
                    scale_h="236",
                )
            )
    return overlays


def mode_uses_raw_overlays(mode: str) -> bool:
    return mode in {"cuda_chromakey_raw", "cuda_chromakey_raw_nvdec"}


def mode_uses_pack(mode: str) -> bool:
    return mode in {"cpu_pack", "cpu_pack_pts_n", "cuda_pack_yuva"}


def mode_uses_nvdec(mode: str) -> bool:
    return mode in {"cuda_yuva_nvdec", "cuda_chromakey_raw_nvdec"}


def selected_overlays_for_mode(mode: str, processed_overlays: list[OverlayInput], pack_path: Path | None) -> list[OverlayInput]:
    if mode_uses_pack(mode):
        if not pack_path:
            raise ValueError(f"Mode {mode} requires a precomposed pack path.")
        return [OverlayInput(pack_path, "precomposed_pack")]
    if mode_uses_raw_overlays(mode):
        raw: list[OverlayInput] = []
        for overlay in processed_overlays:
            if not overlay.source_path:
                raise ValueError(f"Overlay {overlay.path} does not have a source path for raw chromakey mode.")
            raw.append(
                OverlayInput(
                    overlay.source_path,
                    overlay.kind,
                    overlay.x,
                    overlay.y,
                    source_path=overlay.source_path,
                    chroma_color=overlay.chroma_color,
                    similarity=overlay.similarity,
                    blend=overlay.blend,
                    scale_w=overlay.scale_w,
                    scale_h=overlay.scale_h,
                )
            )
        return raw
    return processed_overlays


def build_filter_complex(mode: str, overlays: list[OverlayInput]) -> str:
    if mode in {"cpu", "cpu_pts_n", "cpu_pack", "cpu_pack_pts_n"}:
        overlay_setpts = "setpts=N/30/TB" if mode in {"cpu_pts_n", "cpu_pack_pts_n"} else "setpts=PTS-STARTPTS"
        parts = ["[0:v]setpts=PTS-STARTPTS[base]"]
        chain = "[base]"
        for index, overlay in enumerate(overlays, start=1):
            label = f"ov{index}"
            out = f"out{index}"
            parts.append(f"[{index}:v]{overlay_setpts}[{label}]")
            parts.append(
                f"{chain}[{label}]overlay={overlay.x}:{overlay.y}:"
                f"format=auto:eof_action=repeat:eval=init[{out}]"
            )
            chain = f"[{out}]"
        parts.append(f"{chain}format=yuv420p[v]")
        return ";".join(parts)

    if mode in {"cuda_chromakey_raw", "cuda_chromakey_raw_nvdec"}:
        if mode_uses_nvdec(mode):
            parts = ["[0:v]scale_cuda=format=yuv420p[base]"]
        else:
            parts = ["[0:v]setpts=PTS-STARTPTS,format=yuv420p,hwupload_cuda[base]"]
        chain = "[base]"
        for index, overlay in enumerate(overlays, start=1):
            label = f"ov{index}"
            out = f"out{index}"
            scale = ""
            if overlay.scale_w or overlay.scale_h:
                scale = f",scale_cuda=w={overlay.scale_w or 'iw'}:h={overlay.scale_h or 'ih'}"
            parts.append(
                f"[{index}:v]setpts=PTS-STARTPTS,format=yuv420p,hwupload_cuda"
                f"{scale},chromakey_cuda=color={overlay.chroma_color}:"
                f"similarity={overlay.similarity}:blend={overlay.blend}[{label}]"
            )
            parts.append(f"{chain}[{label}]overlay_cuda={overlay.x}:{overlay.y}:eof_action=repeat:eval=init[{out}]")
            chain = f"[{out}]"
        parts.append(f"{chain}scale_cuda=format=yuv420p[v]")
        return ";".join(parts)

    if mode not in {"cuda_rgba", "cuda_yuva", "cuda_yuva_nvdec", "cuda_pack_yuva"}:
        raise ValueError(f"Unsupported mode: {mode}")

    if mode == "cuda_rgba":
        main_format = "rgba"
        overlay_format = "rgba"
    else:
        main_format = "yuv420p"
        overlay_format = "yuva420p"

    if mode_uses_nvdec(mode):
        parts = [f"[0:v]scale_cuda=format={main_format}[base]"]
    else:
        parts = [f"[0:v]setpts=PTS-STARTPTS,format={main_format},hwupload_cuda[base]"]
    chain = "[base]"
    for index, overlay in enumerate(overlays, start=1):
        label = f"ov{index}"
        out = f"out{index}"
        parts.append(f"[{index}:v]setpts=PTS-STARTPTS,format={overlay_format},hwupload_cuda[{label}]")
        parts.append(f"{chain}[{label}]overlay_cuda={overlay.x}:{overlay.y}:eof_action=repeat:eval=init[{out}]")
        chain = f"[{out}]"
    parts.append(f"{chain}scale_cuda=format=yuv420p[v]")
    return ";".join(parts)


def build_ffmpeg_command(
    ffmpeg_bin: str,
    source_video: Path,
    overlays: list[OverlayInput],
    output_path: Path,
    duration: int,
    mode: str,
    preset: str,
    bitrate: str,
    include_audio: bool,
) -> list[str]:
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-benchmark",
        "-y",
    ]
    if mode_uses_nvdec(mode):
        cmd.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
    cmd.extend(["-i", str(source_video)])
    for overlay in overlays:
        cmd.extend(["-stream_loop", "-1", "-i", str(overlay.path)])

    cmd.extend(
        [
            "-filter_complex",
            build_filter_complex(mode, overlays),
            "-map",
            "[v]",
        ]
    )
    if include_audio:
        cmd.extend(["-map", "0:a?"])
    cmd.extend(["-t", str(duration), "-c:v", "h264_nvenc", "-preset", preset, "-b:v", bitrate])
    if include_audio:
        cmd.extend(["-c:a", "copy"])
    else:
        cmd.append("-an")
    cmd.extend(["-movflags", "+faststart", str(output_path)])
    return cmd


def build_precompose_filter_complex(overlays: list[OverlayInput]) -> str:
    parts = ["[0:v]format=rgba,colorchannelmixer=aa=0[canvas]"]
    chain = "[canvas]"
    for index, overlay in enumerate(overlays, start=1):
        label = f"ov{index}"
        out = f"out{index}"
        parts.append(f"[{index}:v]setpts=N/30/TB[{label}]")
        parts.append(
            f"{chain}[{label}]overlay={overlay.x}:{overlay.y}:"
            f"format=auto:eof_action=repeat:eval=init[{out}]"
        )
        chain = f"[{out}]"
    parts.append(f"{chain}format=argb[v]")
    return ";".join(parts)


def build_precompose_pack_command(
    ffmpeg_bin: str,
    overlays: list[OverlayInput],
    output_path: Path,
    duration: int,
    width: int,
    height: int,
    fps: int,
) -> list[str]:
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-benchmark",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black@0:s={width}x{height}:r={fps}:d={duration}",
    ]
    for overlay in overlays:
        cmd.extend(["-stream_loop", "-1", "-i", str(overlay.path)])
    cmd.extend(
        [
            "-filter_complex",
            build_precompose_filter_complex(overlays),
            "-map",
            "[v]",
            "-t",
            str(duration),
            "-an",
            "-c:v",
            "qtrle",
            str(output_path),
        ]
    )
    return cmd


def parse_ffmpeg_metrics(stderr: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    frame_matches = re.findall(r"frame=\s*(\d+)", stderr)
    fps_matches = re.findall(r"fps=\s*([0-9.]+)", stderr)
    speed_matches = re.findall(r"speed=\s*([0-9.]+)x", stderr)
    bench_match = re.search(r"bench:\s+utime=([0-9.]+)s\s+stime=([0-9.]+)s\s+rtime=([0-9.]+)s", stderr)
    if frame_matches:
        metrics["frames"] = int(frame_matches[-1])
    if fps_matches:
        metrics["ffmpeg_fps"] = _parse_float(fps_matches[-1])
    if speed_matches:
        metrics["ffmpeg_speed_x"] = _parse_float(speed_matches[-1])
    if bench_match:
        metrics["bench_utime_s"] = float(bench_match.group(1))
        metrics["bench_stime_s"] = float(bench_match.group(2))
        metrics["bench_rtime_s"] = float(bench_match.group(3))
    return metrics


def _gpu_sample_once(nvidia_smi: str) -> dict[str, Any] | None:
    try:
        completed = _run_text(
            [
                nvidia_smi,
                f"--query-gpu={GPU_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            timeout=5,
        )
    except Exception:
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    parts = [part.strip() for part in completed.stdout.splitlines()[0].split(",")]
    if len(parts) < 9:
        return None
    return {
        "timestamp": parts[0],
        "name": parts[1],
        "gpu_util_pct": _parse_float(parts[2]),
        "mem_util_pct": _parse_float(parts[3]),
        "encoder_sessions": _parse_float(parts[4]),
        "encoder_avg_fps": _parse_float(parts[5]),
        "memory_used_mb": _parse_float(parts[6]),
        "memory_total_mb": _parse_float(parts[7]),
        "power_w": _parse_float(parts[8]),
    }


def _summarize_gpu(samples: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"samples": len(samples)}
    for field in ("gpu_util_pct", "mem_util_pct", "encoder_avg_fps", "memory_used_mb", "power_w"):
        values = [sample[field] for sample in samples if sample.get(field) is not None]
        if values:
            summary[f"{field}_avg"] = round(sum(values) / len(values), 2)
            summary[f"{field}_max"] = round(max(values), 2)
    return summary


def _sample_gpu_until_stopped(
    nvidia_smi: str,
    interval: float,
    stop_event: threading.Event,
    samples: list[dict[str, Any]],
) -> None:
    while not stop_event.is_set():
        sample = _gpu_sample_once(nvidia_smi)
        if sample:
            samples.append(sample)
        stop_event.wait(interval)


def run_ffmpeg(
    cmd: list[str],
    output_path: Path,
    timeout: int | None,
    nvidia_smi: str,
    gpu_sample_interval: float,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    stop_event = threading.Event()
    sampler = threading.Thread(
        target=_sample_gpu_until_stopped,
        args=(nvidia_smi, gpu_sample_interval, stop_event, samples),
        daemon=True,
    )

    started = time.perf_counter()
    sampler.start()
    try:
        process = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return_code = process.returncode if process.returncode is not None else -9
            timed_out = True
        else:
            return_code = process.returncode
            timed_out = False
    finally:
        stop_event.set()
        sampler.join(timeout=5)
    elapsed = time.perf_counter() - started
    if return_code is not None and return_code > 2**31 - 1:
        return_code = return_code - 2**32

    result = {
        "command": cmd,
        "return_code": return_code,
        "success": return_code == 0 and output_path.exists() and output_path.stat().st_size > 0,
        "timed_out": timed_out,
        "elapsed_seconds": round(elapsed, 3),
        "stdout_tail": stdout[-2000:] if stdout else "",
        "stderr_tail": stderr[-4000:] if stderr else "",
        "metrics": parse_ffmpeg_metrics(stderr or ""),
        "gpu": _summarize_gpu(samples),
        "output_path": str(output_path),
        "output_size_bytes": output_path.stat().st_size if output_path.exists() else 0,
    }
    return result


def validate_output_duration(
    ffprobe_bin: str,
    result: dict[str, Any],
    expected_duration: int,
    min_ratio: float,
) -> None:
    output_path = Path(str(result.get("output_path") or ""))
    if not output_path.exists() or output_path.stat().st_size <= 0:
        result["success"] = False
        result["validation_error"] = "output_missing"
        return
    try:
        meta = probe_video(ffprobe_bin, output_path)
    except Exception as exc:
        result["success"] = False
        result["validation_error"] = f"ffprobe_failed: {exc}"
        return
    actual_duration = float(meta.get("duration") or 0)
    result["output_probe"] = meta
    result["output_duration_seconds"] = round(actual_duration, 3)
    min_duration = float(expected_duration) * min_ratio
    if actual_duration < min_duration:
        result["success"] = False
        result["validation_error"] = (
            f"output_too_short: actual={actual_duration:.3f}s, expected>={min_duration:.3f}s"
        )


def compare_ssim(
    ffmpeg_bin: str,
    cpu_output: Path,
    candidate_output: Path,
    seconds: int,
    timeout: int | None,
) -> dict[str, Any]:
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-t",
        str(seconds),
        "-i",
        str(cpu_output),
        "-t",
        str(seconds),
        "-i",
        str(candidate_output),
        "-lavfi",
        "[0:v][1:v]ssim",
        "-f",
        "null",
        "-",
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - started
        match = re.search(r"All:([0-9.]+)", completed.stderr)
        return {
            "command": cmd,
            "return_code": completed.returncode,
            "elapsed_seconds": round(elapsed, 3),
            "ssim_all": float(match.group(1)) if match else None,
            "stderr_tail": completed.stderr[-2000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": cmd,
            "return_code": -9,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "ssim_all": None,
            "stderr_tail": str(exc),
        }


def running_ffmpeg_processes() -> list[dict[str, Any]]:
    if os.name == "nt":
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process -Filter \"name='ffmpeg.exe'\" | "
                "Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Depth 4"
            ),
        ]
        completed = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        text = completed.stdout.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return [{"raw": text}]
        return data if isinstance(data, list) else [data]

    ffmpeg_path = shutil.which("pgrep")
    if not ffmpeg_path:
        return []
    completed = subprocess.run(["pgrep", "-af", "ffmpeg"], text=True, stdout=subprocess.PIPE)
    return [{"raw": line} for line in completed.stdout.splitlines() if line.strip()]


def write_report(report_path: Path, payload: dict[str, Any]) -> None:
    def fmt(value: Any, suffix: str = "") -> str:
        if value is None:
            return "-"
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return "-"
            return f"{value:.3f}{suffix}"
        return f"{value}{suffix}"

    lines: list[str] = []
    lines.append("# Story Overlay CUDA Benchmark")
    lines.append("")
    lines.append(f"- Generated: `{payload['generated_at']}`")
    lines.append(f"- Source: `{payload['source']['path']}`")
    lines.append(f"- FFmpeg: `{payload['ffmpeg_version']}`")
    lines.append(f"- CUDA filters: `{', '.join(payload['available_filters']) or 'none'}`")
    if payload.get("precompose"):
        precompose = payload["precompose"]
        status = "ok" if precompose.get("success") else "failed"
        lines.append(
            f"- Precompose pack: `{status}`, elapsed `{fmt(precompose.get('elapsed_seconds'), 's')}`, "
            f"output `{precompose.get('output_path')}`"
        )
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    lines.append("| Kind | Path | Codec | Pixel Format | Size | Duration |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for item in [payload["source"], *payload["overlays"]]:
        lines.append(
            "| {kind} | `{path}` | {codec} | {pix_fmt} | {width}x{height} | {duration} |".format(
                kind=item.get("kind", "source"),
                path=item.get("path"),
                codec=item.get("codec"),
                pix_fmt=item.get("pix_fmt"),
                width=item.get("width"),
                height=item.get("height"),
                duration=item.get("duration"),
            )
        )
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append(
        "| Duration | Mode | Status | Elapsed | Speed | FFmpeg FPS | GPU Avg/Max | Enc FPS Avg | SSIM | Speedup | Qualified | Output |"
    )
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for result in payload["results"]:
        metrics = result.get("metrics") or {}
        gpu = result.get("gpu") or {}
        comparison = result.get("comparison") or {}
        status = "ok" if result.get("success") else "failed"
        lines.append(
            "| {duration} | `{mode}` | {status} | {elapsed:.2f}s | {speed} | {fps} | {gpu_avg}/{gpu_max} | {enc} | {ssim} | {speedup} | {qualified} | `{output}` |".format(
                duration=result.get("duration_label"),
                mode=result.get("mode"),
                status=status,
                elapsed=float(result.get("elapsed_seconds") or 0),
                speed=fmt(metrics.get("ffmpeg_speed_x"), "x"),
                fps=fmt(metrics.get("ffmpeg_fps")),
                gpu_avg=fmt(gpu.get("gpu_util_pct_avg"), "%"),
                gpu_max=fmt(gpu.get("gpu_util_pct_max"), "%"),
                enc=fmt(gpu.get("encoder_avg_fps_avg")),
                ssim=fmt(comparison.get("ssim_all")),
                speedup=fmt(result.get("speedup_vs_cpu"), "x"),
                qualified=result.get("qualified") if result.get("qualified") is not None else "-",
                output=result.get("output_path"),
            )
        )
    lines.append("")
    failed = [item for item in payload["results"] if not item.get("success")]
    if failed:
        lines.append("## Failures")
        lines.append("")
        for item in failed:
            lines.append(f"### {item.get('duration_label')} `{item.get('mode')}`")
            lines.append("")
            lines.append("```text")
            lines.append((item.get("stderr_tail") or "").strip()[-2000:])
            lines.append("```")
            lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def persist_results(run_dir: Path, payload: dict[str, Any]) -> None:
    (run_dir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_report(run_dir / "report.md", payload)


def run_benchmark(args: argparse.Namespace) -> int:
    active_ffmpeg = running_ffmpeg_processes()
    if args.skip_if_ffmpeg_running and active_ffmpeg:
        print("Skipping benchmark because ffmpeg.exe is already running:", file=sys.stderr)
        print(json.dumps(active_ffmpeg, indent=2), file=sys.stderr)
        return 2

    ffmpeg_bin = args.ffmpeg
    ffprobe_bin = args.ffprobe
    filters = available_filters(ffmpeg_bin)
    required_cuda = {"overlay_cuda", "hwupload_cuda", "scale_cuda"}
    if any(mode.startswith("cuda") for mode in args.modes) and not required_cuda.issubset(filters):
        missing = sorted(required_cuda - filters)
        print(f"Missing required CUDA filters: {', '.join(missing)}", file=sys.stderr)
        return 3
    if any(mode_uses_raw_overlays(mode) for mode in args.modes) and "chromakey_cuda" not in filters:
        print("Missing required CUDA filter: chromakey_cuda", file=sys.stderr)
        return 3

    durations = [int(item) for item in args.durations]
    source = Path(args.source_video).resolve() if args.source_video else default_source_video(max(durations))
    processed_overlays = default_overlays()
    if not processed_overlays:
        print("No processed overlays found.", file=sys.stderr)
        return 4
    missing_overlays = [str(item.path) for item in processed_overlays if not item.path.exists()]
    for mode in args.modes:
        if mode_uses_raw_overlays(mode):
            missing_overlays.extend(
                str(item.source_path)
                for item in processed_overlays
                if not item.source_path or not item.source_path.exists()
            )
    if missing_overlays:
        print(f"Missing overlays: {missing_overlays}", file=sys.stderr)
        return 4

    run_dir = Path(args.work_dir).resolve() / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = run_dir / "outputs"
    run_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_meta = probe_video(ffprobe_bin, source)
    source_meta["kind"] = "source"
    overlay_meta = []
    for overlay in processed_overlays:
        meta = probe_video(ffprobe_bin, overlay.path)
        meta["kind"] = overlay.kind
        if overlay.source_path:
            meta["sourcePath"] = str(overlay.source_path)
        overlay_meta.append(meta)

    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "ffmpeg_version": ffmpeg_version(ffmpeg_bin),
        "available_filters": sorted(filters),
        "source": source_meta,
        "overlays": overlay_meta,
        "durations_seconds": durations,
        "modes": args.modes,
        "pack_duration_seconds": args.pack_duration,
        "results": [],
    }

    pack_path: Path | None = None
    if any(mode_uses_pack(mode) for mode in args.modes):
        pack_path = output_dir / f"overlay_pack_{_duration_label(args.pack_duration)}.mov"
        pack_cmd = build_precompose_pack_command(
            ffmpeg_bin,
            processed_overlays,
            pack_path,
            args.pack_duration,
            args.target_width,
            args.target_height,
            args.target_fps,
        )
        if args.dry_run:
            payload["precompose"] = {
                "command": pack_cmd,
                "output_path": str(pack_path),
                "duration_seconds": args.pack_duration,
            }
        else:
            print(f"Precomposing overlay pack {_duration_label(args.pack_duration)}...")
            precompose = run_ffmpeg(
                pack_cmd,
                pack_path,
                args.timeout,
                args.nvidia_smi,
                args.gpu_sample_interval,
            )
            precompose["mode"] = "precompose_pack"
            precompose["duration_seconds"] = args.pack_duration
            precompose["duration_label"] = _duration_label(args.pack_duration)
            validate_output_duration(ffprobe_bin, precompose, args.pack_duration, args.min_duration_ratio)
            payload["precompose"] = precompose
            persist_results(run_dir, payload)
            if not precompose.get("success"):
                print("Precompose pack failed; pack modes will be skipped.", file=sys.stderr)
                pack_path = None

    if args.dry_run:
        for duration in durations:
            for mode in args.modes:
                output = output_dir / f"{_duration_label(duration)}_{mode}.mp4"
                selected_overlays = selected_overlays_for_mode(mode, processed_overlays, pack_path)
                payload["results"].append(
                    {
                        "duration_seconds": duration,
                        "duration_label": _duration_label(duration),
                        "mode": mode,
                        "command": build_ffmpeg_command(
                            ffmpeg_bin,
                            source,
                            selected_overlays,
                            output,
                            duration,
                            mode,
                            args.preset,
                            args.bitrate,
                            not args.no_audio,
                        ),
                    }
                )
        (run_dir / "dry_run_commands.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(run_dir)
        return 0

    cpu_outputs: dict[int, Path] = {}
    for duration in durations:
        for mode in args.modes:
            if mode_uses_pack(mode) and not pack_path:
                payload["results"].append(
                    {
                        "duration_seconds": duration,
                        "duration_label": _duration_label(duration),
                        "mode": mode,
                        "success": False,
                        "validation_error": "precompose_pack_failed_or_missing",
                    }
                )
                persist_results(run_dir, payload)
                continue
            output = output_dir / f"{_duration_label(duration)}_{mode}.mp4"
            selected_overlays = selected_overlays_for_mode(mode, processed_overlays, pack_path)
            cmd = build_ffmpeg_command(
                ffmpeg_bin,
                source,
                selected_overlays,
                output,
                duration,
                mode,
                args.preset,
                args.bitrate,
                not args.no_audio,
            )
            print(f"Running {mode} {_duration_label(duration)}...")
            result = run_ffmpeg(
                cmd,
                output,
                args.timeout,
                args.nvidia_smi,
                args.gpu_sample_interval,
            )
            result["duration_seconds"] = duration
            result["duration_label"] = _duration_label(duration)
            result["mode"] = mode
            validate_output_duration(ffprobe_bin, result, duration, args.min_duration_ratio)
            payload["results"].append(result)
            if mode == "cpu" and result.get("success"):
                cpu_outputs[duration] = output
            elif mode != "cpu" and result.get("success") and args.compare and duration in cpu_outputs:
                compare_seconds = min(args.compare_seconds, duration)
                result["comparison"] = compare_ssim(
                    ffmpeg_bin,
                    cpu_outputs[duration],
                    output,
                    compare_seconds,
                    args.timeout,
                )
            if mode != "cpu" and result.get("success"):
                cpu_result = next(
                    (
                        item
                        for item in payload["results"]
                        if item.get("duration_seconds") == duration and item.get("mode") == "cpu" and item.get("success")
                    ),
                    None,
                )
                if cpu_result and result.get("elapsed_seconds"):
                    speedup = float(cpu_result["elapsed_seconds"]) / float(result["elapsed_seconds"])
                    result["speedup_vs_cpu"] = round(speedup, 3)
                    ssim = (result.get("comparison") or {}).get("ssim_all")
                    result["qualified"] = bool(speedup >= args.min_speedup and (ssim is None or ssim >= args.min_ssim))
            persist_results(run_dir, payload)

    persist_results(run_dir, payload)
    print(run_dir)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark CPU overlay vs CUDA overlay for story-video overlays.")
    parser.add_argument("--durations", nargs="+", type=int, default=DEFAULT_DURATIONS)
    parser.add_argument(
        "--modes",
        nargs="+",
        default=DEFAULT_MODES,
        choices=[
            "cpu",
            "cpu_pts_n",
            "cpu_pack",
            "cpu_pack_pts_n",
            "cuda_rgba",
            "cuda_yuva",
            "cuda_yuva_nvdec",
            "cuda_pack_yuva",
            "cuda_chromakey_raw",
            "cuda_chromakey_raw_nvdec",
        ],
    )
    parser.add_argument("--source-video")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--nvidia-smi", default="nvidia-smi")
    parser.add_argument("--preset", default="p4")
    parser.add_argument("--bitrate", default="8M")
    parser.add_argument("--timeout", type=int, default=0, help="Per-run timeout in seconds; 0 disables timeout.")
    parser.add_argument("--gpu-sample-interval", type=float, default=2.0)
    parser.add_argument("--compare", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--compare-seconds", type=int, default=10)
    parser.add_argument("--min-speedup", type=float, default=1.25)
    parser.add_argument("--min-ssim", type=float, default=0.985)
    parser.add_argument("--min-duration-ratio", type=float, default=0.98)
    parser.add_argument("--pack-duration", type=int, default=80)
    parser.add_argument("--target-width", type=int, default=1920)
    parser.add_argument("--target-height", type=int, default=1080)
    parser.add_argument("--target-fps", type=int, default=30)
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--skip-if-ffmpeg-running", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.timeout = args.timeout or None
    return args


if __name__ == "__main__":
    raise SystemExit(run_benchmark(parse_args(sys.argv[1:])))
