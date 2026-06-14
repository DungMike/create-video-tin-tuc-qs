"""Tests for the Story CTA overlay (Like/Subscribe corner decoration)."""

import src.utils.story_cta_overlay as cta
from src.config import Config
from src.utils.story_overlay_packs import build_pack_signature, build_precompose_command


def test_cta_position_expr_all_corners():
    assert cta._position_expr("top_left", 24) == ("24", "24")
    assert cta._position_expr("top_right", 24) == ("W-w-24", "24")
    assert cta._position_expr("bottom_left", 24) == ("24", "H-h-24")
    assert cta._position_expr("bottom_right", 24) == ("W-w-24", "H-h-24")


def test_overlay_position_expr_falls_back_to_config(monkeypatch):
    monkeypatch.setattr(Config, "STORY_CTA_OVERLAY_POSITION", "top_left")
    monkeypatch.setattr(Config, "STORY_CTA_OVERLAY_MARGIN", 24)
    assert cta.overlay_position_expr({}) == ("24", "24")


def test_pack_signature_includes_cta(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "TARGET_RESOLUTION", "1920x1080")
    monkeypatch.setattr(Config, "TARGET_FPS", 30)
    monkeypatch.setattr(Config, "STORY_OVERLAY_PACK_DURATION_SECONDS", 80)

    cta_path = tmp_path / "cta_alpha.mov"
    cta_path.write_bytes(b"cta")
    cta_record = {"id": "cta-1", "durationSeconds": 10.0, "position": "top_left", "margin": 24}

    signature = build_pack_signature([], None, None, cta_record, str(cta_path))

    assert len(signature["overlays"]) == 1
    entry = signature["overlays"][0]
    assert entry["kind"] == "cta"
    assert entry["x"] == "24"
    assert entry["y"] == "24"


def test_pack_signature_orders_tv_waveform_cta(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "TARGET_RESOLUTION", "1920x1080")
    monkeypatch.setattr(Config, "TARGET_FPS", 30)
    monkeypatch.setattr(Config, "STORY_OVERLAY_PACK_DURATION_SECONDS", 80)

    tv_path = tmp_path / "tv.mov"
    wave_path = tmp_path / "wave.mov"
    cta_path = tmp_path / "cta.mov"
    for path in (tv_path, wave_path, cta_path):
        path.write_bytes(b"x")

    tv_record = {"id": "tv-1", "order": 1, "durationSeconds": 15.0}
    wave_record = {"id": "wave-1", "durationSeconds": 20.0, "position": "bottom_right", "margin": 15}
    cta_record = {"id": "cta-1", "durationSeconds": 10.0, "position": "top_left", "margin": 24}

    signature = build_pack_signature(
        [(tv_record, str(tv_path))], wave_record, str(wave_path), cta_record, str(cta_path)
    )

    kinds = [overlay["kind"] for overlay in signature["overlays"]]
    assert kinds == ["tv_noise", "waveform", "cta"]
    # tv + waveform + cta => 3 looped inputs
    command = build_precompose_command(signature, str(tmp_path / "pack.mov"))
    assert command.count("-stream_loop") == 3


def test_ensure_default_seed(tmp_path, monkeypatch):
    cta_dir = tmp_path / "story_cta_overlays"
    seed = tmp_path / "seed.mp4"
    seed.write_bytes(b"fake-video")

    monkeypatch.setattr(Config, "STORY_CTA_OVERLAY_DIR", str(cta_dir))
    monkeypatch.setattr(Config, "STORY_CTA_DEFAULT_VIDEO", str(seed))
    monkeypatch.setattr(Config, "STORY_CTA_OVERLAY_DEFAULT_ENABLED", True)

    # Avoid invoking ffmpeg/ffprobe: stub preprocess + duration.
    def fake_preprocess(source_path, record):
        processed = cta._absolute(f"{record['id']}_alpha.mov")
        with open(processed, "wb") as fobj:
            fobj.write(b"alpha")
        return f"{record['id']}_alpha.mov"

    monkeypatch.setattr(cta, "preprocess_cta_overlay", fake_preprocess)
    monkeypatch.setattr(cta.FFmpegHelper, "probe_duration", staticmethod(lambda path: 10.0))

    record = cta.ensure_default_cta_overlay()
    assert record is not None
    assert record["isDefault"] is True
    assert record["enabled"] is True
    assert record["position"] == "top_left"

    index = cta.load_cta_index()
    assert len(index["overlays"]) == 1

    # Idempotent: a second call must not create a duplicate.
    again = cta.ensure_default_cta_overlay()
    assert again["id"] == record["id"]
    assert len(cta.load_cta_index()["overlays"]) == 1

    # The active getter returns the enabled default with a usable processed file.
    active = cta.get_active_cta_overlay()
    assert active is not None and active["id"] == record["id"]


def test_ensure_default_missing_asset(tmp_path, monkeypatch):
    cta_dir = tmp_path / "story_cta_overlays"
    monkeypatch.setattr(Config, "STORY_CTA_OVERLAY_DIR", str(cta_dir))
    monkeypatch.setattr(Config, "STORY_CTA_DEFAULT_VIDEO", str(tmp_path / "does_not_exist.mp4"))

    assert cta.ensure_default_cta_overlay() is None
    assert cta.load_cta_index() == {"overlays": []}
    assert cta.get_active_cta_overlay() is None


def test_disabled_overlay_not_active(tmp_path, monkeypatch):
    cta_dir = tmp_path / "story_cta_overlays"
    cta_dir.mkdir()
    monkeypatch.setattr(Config, "STORY_CTA_OVERLAY_DIR", str(cta_dir))

    processed = cta_dir / "abc_alpha.mov"
    processed.write_bytes(b"alpha")
    cta.save_cta_index(
        {
            "overlays": [
                {
                    "id": "abc",
                    "isDefault": True,
                    "enabled": False,
                    "processedFilename": "abc_alpha.mov",
                }
            ]
        }
    )

    assert cta.get_active_cta_overlay() is None
