"""clip_identity: source video + clip keys read from the data library assets already carry.

The filename shapes below are the real ones found across the live libraries
(prefetch / import-selected / harvest), plus the two id-less flows.
"""

from src.utils.clip_identity import clip_identity, source_identity


def _asset(source_name: str, *tags: str, duration=3.0, asset_id="a1", source_type="pixabay") -> dict:
    return {
        "id": asset_id,
        "source_type": source_type,
        "source_name": source_name,
        "relative_path": f"clips/{asset_id}.mp4",
        "duration": duration,
        "tags": [source_type, *tags],
    }


def test_prefetch_name_with_src_tag():
    asset = _asset("pixabay_0003_170778_2774aa52_clip_003.mp4", "session:pf-703e242c", "src:pixabay:170778")
    assert clip_identity("lib", asset) == ("pixabay:170778", "pixabay:170778#003@3s")


def test_prefetch_name_without_src_tag_is_parsed():
    asset = _asset("pixabay_0003_170778_2774aa52_clip_003.mp4", "session:pf-703e242c")
    assert clip_identity("lib", asset)[0] == "pixabay:170778"


def test_import_selected_name():
    asset = _asset("pexels_000_32108747_2fd4ef05_clip_000.mp4", "session:imp-0ba1fcad", source_type="pexels")
    assert clip_identity("lib", asset) == ("pexels:32108747", "pexels:32108747#000@3s")


def test_harvest_short_id_is_an_id_not_a_counter():
    # A 3-digit Pixabay id: looks exactly like the links flow's `{p}_{idx:03d}_{hex8}`.
    asset = _asset("pixabay_149_ef0f9e99_clip_000.mp4", "session:hvc-1e561be3", "keyword:Thai police")
    assert clip_identity("lib", asset)[0] == "pixabay:149"


def test_harvest_all_digit_hex_suffix():
    asset = _asset("pexels_11983893_97201009_clip_002.mp4", "session:hvc-6effc909", source_type="pexels")
    assert clip_identity("lib", asset) == ("pexels:11983893", "pexels:11983893#002@3s")


def test_pasted_link_has_no_id_but_a_unique_file_key():
    asset = _asset("pixabay_001_1a2b3c4d_clip_000.mp4", "session:dl-12345678")
    info = source_identity(asset)
    assert info["provider_id"] is None
    assert clip_identity("lib", asset) == ("file:pixabay_001_1a2b3c4d", "file:pixabay_001_1a2b3c4d#000@3s")


def test_local_upload():
    asset = _asset("local_000_1a2b3c4d_clip_001.mp4", "session:up-12345678", source_type="local_upload")
    assert clip_identity("lib", asset)[1] == "file:local_000_1a2b3c4d#001@3s"


def test_src_tag_wins_over_filename():
    asset = _asset("pixabay_0003_170778_2774aa52_clip_000.mp4", "session:pf-1", "src:pixabay:999")
    assert clip_identity("lib", asset)[0] == "pixabay:999"


def test_baked_copy_in_another_library_shares_the_clip_key():
    raw = _asset("pexels_1584712_19fd6fdd_clip_004.mp4", "session:hvc-6effc909", source_type="pexels")
    baked = {**_asset("pexels_1584712_19fd6fdd_clip_004.mp4", "session:hvc-6effc909",
                      source_type="pexels", asset_id="0f3c9a1b2c3d"), "styled_from": "raw-lib"}
    assert clip_identity("raw-lib", raw)[1] == clip_identity("baked-lib", baked)[1]


def test_different_clip_length_is_a_different_clip():
    three = _asset("pixabay_1297_75f26cc5_clip_001.mp4", "session:hvc-1", duration=3.0)
    five = _asset("pixabay_1297_75f26cc5_clip_001.mp4", "session:hvc-1", duration=5.0)
    assert clip_identity("a", three)[1] != clip_identity("b", five)[1]
    assert clip_identity("a", three)[0] == clip_identity("b", five)[0]


def test_asset_without_clip_name_falls_back_to_its_own_key():
    asset = {"id": "x9", "source_name": "weird.mov", "relative_path": "clips/x9.mov", "tags": []}
    assert clip_identity("lib", asset)[1] == "asset:lib:x9"
