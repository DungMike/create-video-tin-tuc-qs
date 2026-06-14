from pathlib import Path

from src.config import Config
from src.utils.story_overlay_packs import (
    build_pack_signature,
    build_precompose_command,
    build_precompose_filter,
    pack_hash,
)


def test_precompose_pack_signature_and_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "TARGET_RESOLUTION", "1920x1080")
    monkeypatch.setattr(Config, "TARGET_FPS", 30)
    monkeypatch.setattr(Config, "STORY_OVERLAY_PACK_DURATION_SECONDS", 80)

    tv_path = tmp_path / "tv_alpha.mov"
    wave_path = tmp_path / "wave_alpha.mov"
    tv_path.write_bytes(b"tv")
    wave_path.write_bytes(b"wave")

    tv_record = {
        "id": "tv-1",
        "order": 1,
        "durationSeconds": 15.0,
    }
    waveform_record = {
        "id": "wave-1",
        "durationSeconds": 20.0,
        "position": "bottom_right",
        "margin": 15,
    }

    signature = build_pack_signature([(tv_record, str(tv_path))], waveform_record, str(wave_path))

    assert signature["target"] == {"width": 1920, "height": 1080, "fps": 30}
    assert signature["durationSeconds"] == 80
    assert len(signature["overlays"]) == 2
    assert signature["overlays"][1]["x"] == "W-w-15"
    assert signature["overlays"][1]["y"] == "H-h-15"

    filter_complex = build_precompose_filter(signature)
    assert "colorchannelmixer=aa=0" in filter_complex
    assert "[1:v]setpts=N/30/TB[ov1]" in filter_complex
    assert "overlay=0:0:format=auto:eof_action=repeat:eval=init" in filter_complex
    assert filter_complex.endswith("format=argb[v]")

    command = build_precompose_command(signature, str(tmp_path / "pack.mov"))
    assert command.count("-stream_loop") == 2
    assert "prores_ks" in command
    assert str(tmp_path / "pack.mov") == command[-1]
    assert pack_hash(signature) == pack_hash(signature)
