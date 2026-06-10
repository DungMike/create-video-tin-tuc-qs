from pathlib import Path

from tests.benchmarks.story_overlay_cuda_benchmark import (
    OverlayInput,
    build_ffmpeg_command,
    build_filter_complex,
    build_precompose_pack_command,
    parse_ffmpeg_metrics,
)


def test_cpu_filter_matches_story_overlay_shape():
    overlays = [
        OverlayInput(Path("tv0.mov"), "tv_noise"),
        OverlayInput(Path("wave.mov"), "waveform", "W-w-15", "H-h-15"),
    ]

    filter_complex = build_filter_complex("cpu", overlays)

    assert "overlay=0:0:format=auto:eof_action=repeat:eval=init" in filter_complex
    assert "overlay=W-w-15:H-h-15:format=auto:eof_action=repeat:eval=init" in filter_complex
    assert filter_complex.endswith("format=yuv420p[v]")


def test_cuda_filter_uses_cuda_upload_and_overlay():
    overlays = [OverlayInput(Path("tv0.mov"), "tv_noise")]

    filter_complex = build_filter_complex("cuda_rgba", overlays)

    assert "format=rgba,hwupload_cuda" in filter_complex
    assert "overlay_cuda=0:0:eof_action=repeat:eval=init" in filter_complex
    assert filter_complex.endswith("scale_cuda=format=yuv420p[v]")


def test_cuda_nvdec_filter_keeps_main_frames_on_gpu():
    overlays = [OverlayInput(Path("tv0.mov"), "tv_noise")]

    filter_complex = build_filter_complex("cuda_yuva_nvdec", overlays)

    assert filter_complex.startswith("[0:v]scale_cuda=format=yuv420p[base]")
    assert "format=yuva420p,hwupload_cuda" in filter_complex
    assert "overlay_cuda=0:0:eof_action=repeat:eval=init" in filter_complex


def test_cuda_chromakey_raw_filter_keys_source_overlay_on_gpu():
    overlays = [
        OverlayInput(
            Path("raw.mp4"),
            "tv_noise",
            chroma_color="black",
            similarity=0.08,
            blend=0.02,
            scale_w="1920",
            scale_h="1080",
        )
    ]

    filter_complex = build_filter_complex("cuda_chromakey_raw", overlays)

    assert "format=yuv420p,hwupload_cuda" in filter_complex
    assert "scale_cuda=w=1920:h=1080" in filter_complex
    assert "chromakey_cuda=color=black:similarity=0.08:blend=0.02" in filter_complex
    assert "overlay_cuda=0:0:eof_action=repeat:eval=init" in filter_complex


def test_cpu_pack_uses_single_overlay_shape():
    overlays = [OverlayInput(Path("pack.mov"), "precomposed_pack")]

    filter_complex = build_filter_complex("cpu_pack", overlays)

    assert filter_complex.count("overlay=") == 1
    assert filter_complex.endswith("format=yuv420p[v]")


def test_cpu_pts_n_uses_frame_index_timestamps_for_looped_overlays():
    overlays = [OverlayInput(Path("tv0.mov"), "tv_noise")]

    filter_complex = build_filter_complex("cpu_pts_n", overlays)

    assert "[1:v]setpts=N/30/TB[ov1]" in filter_complex
    assert "overlay=0:0:format=auto:eof_action=repeat:eval=init" in filter_complex


def test_command_keeps_benchmark_isolated_from_pipeline_outputs(tmp_path):
    cmd = build_ffmpeg_command(
        "ffmpeg",
        Path("source.mp4"),
        [OverlayInput(Path("tv0.mov"), "tv_noise")],
        tmp_path / "out.mp4",
        60,
        "cpu",
        "p4",
        "8M",
        include_audio=True,
    )

    assert "-benchmark" in cmd
    assert "-c:v" in cmd
    assert "h264_nvenc" in cmd
    assert str(tmp_path / "out.mp4") == cmd[-1]


def test_precompose_command_builds_transparent_pack(tmp_path):
    cmd = build_precompose_pack_command(
        "ffmpeg",
        [OverlayInput(Path("tv0.mov"), "tv_noise")],
        tmp_path / "pack.mov",
        80,
        1920,
        1080,
        30,
    )

    assert "-f" in cmd
    assert "color=c=black@0:s=1920x1080:r=30:d=80" in cmd
    assert any("colorchannelmixer=aa=0" in item for item in cmd)
    assert "qtrle" in cmd
    assert str(tmp_path / "pack.mov") == cmd[-1]


def test_nvdec_command_adds_cuda_decode_flags(tmp_path):
    cmd = build_ffmpeg_command(
        "ffmpeg",
        Path("source.mp4"),
        [OverlayInput(Path("tv0.mov"), "tv_noise")],
        tmp_path / "out.mp4",
        60,
        "cuda_yuva_nvdec",
        "p4",
        "8M",
        include_audio=False,
    )

    assert cmd[4:8] == ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]


def test_parse_ffmpeg_metrics_from_stderr_tail():
    stderr = "frame= 1800 fps= 25 q=18.0 size= 12345kB time=00:01:00.00 bitrate=1685.0kbits/s speed=0.833x\n"
    stderr += "bench: utime=11.000s stime=2.000s rtime=72.000s\n"

    metrics = parse_ffmpeg_metrics(stderr)

    assert metrics["frames"] == 1800
    assert metrics["ffmpeg_fps"] == 25.0
    assert metrics["ffmpeg_speed_x"] == 0.833
    assert metrics["bench_rtime_s"] == 72.0
