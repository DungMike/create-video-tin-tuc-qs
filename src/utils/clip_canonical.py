"""Single source of truth for the *canonical* story-library clip format.

``clip_spec_validation`` decides whether a clip matches the format the render
pipeline requires (TARGET_RESOLUTION + Config.CLIP_EXPECTED_* pix_fmt / color
range / color space); this module is the other half: it produces clips in that
format. Every path that writes a clip into a library must go through here, so a
clip can never enter the library already off-spec:

- ``video_source_downloader.split_into_clips`` (Pixabay/Pexels download + upload)
- ``story_library_bake`` (style bake, both new-library and append modes)
- ``story_library_normalize`` (repairing clips already on disk)
- ``story_intro_library`` (intros are concatenated onto finished renders)

Two things are needed to land exactly on the expected spec:

1. **Conversion** — ``scale`` with ``out_color_matrix``/``out_range`` (plus the
   probed ``in_*`` values when the source is known to differ) actually converts
   the pixel data, so a full-range (``yuvj420p``/``pc``) or ``smpte170m`` source
   doesn't just get relabelled and shift on screen.
2. **Tagging** — ``setparams`` stamps primaries/trc/colorspace/range onto the
   frames. Output options like ``-colorspace``/``-color_range`` are NOT reliable
   here: measured on ffmpeg 8.1, frame properties coming out of the filter graph
   win and an untagged source stays untagged ("unknown"), which is exactly the
   combo the render excludes.
"""

import os

from src.config import Config
from src.utils.clip_spec_validation import (
    expected_dimensions,
    expected_spec,
    probe_clip_spec,
)
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger

# ffprobe reports missing color metadata as "unknown"; treat every spelling of
# "not set" the same so we never feed a bogus in_color_matrix to ffmpeg.
_UNSPECIFIED = {"", "unknown", "unspecified", "reserved", "n/a", None}


def _is_known(value) -> bool:
    return str(value).lower() not in _UNSPECIFIED if value is not None else False


def _canonical_range() -> str:
    return Config.CLIP_EXPECTED_COLOR_RANGE or "tv"


def _canonical_matrix() -> str:
    return Config.CLIP_EXPECTED_COLOR_SPACE or "bt709"


def _canonical_pix_fmt() -> str:
    return Config.CLIP_EXPECTED_PIX_FMT or "yuv420p"


def source_conversion_options(spec: dict | None) -> list[str]:
    """``scale`` ``in_*`` options describing a probed source's real color layout.

    Only emitted when the source demonstrably differs from the canonical spec:
    an unknown/matching tag yields nothing, so swscale leaves the pixels alone
    instead of guessing a matrix and shifting colors on the ~90% of clips that
    are already correct.
    """
    if not spec:
        return []

    options: list[str] = []
    color_space = spec.get("color_space")
    if _is_known(color_space) and color_space != _canonical_matrix():
        options.append(f"in_color_matrix={color_space}")

    pix_fmt = str(spec.get("pix_fmt") or "")
    color_range = str(spec.get("color_range") or "").lower()
    if color_range in {"pc", "full"} or pix_fmt.startswith("yuvj"):
        options.append("in_range=full")
    return options


def canonical_scale_filter(spec: dict | None = None) -> str:
    """scale+crop to TARGET_RESOLUTION while converting color to the canonical spec."""
    width, height = expected_dimensions()
    options = [
        "force_original_aspect_ratio=increase",
        f"out_color_matrix={_canonical_matrix()}",
        f"out_range={_canonical_range()}",
        *source_conversion_options(spec),
    ]
    return f"scale={width}:{height}:{':'.join(options)},crop={width}:{height}"


def canonical_tag_filter() -> str:
    """setparams stamping the canonical color tags onto every output frame."""
    return (
        f"setparams=color_primaries={Config.CLIP_EXPECTED_COLOR_PRIMARIES}"
        f":color_trc={Config.CLIP_EXPECTED_COLOR_TRC}"
        f":colorspace={_canonical_matrix()}"
        f":range={_canonical_range()}"
    )


def canonical_video_filter(spec: dict | None = None, *, prefix: str = "") -> str:
    """Full -vf chain that lands on the canonical spec.

    ``prefix`` is an optional filter chain applied first (e.g. a bake's TV style),
    so callers never have to remember to append the canonical tail themselves.
    """
    chain = [part for part in (prefix.strip().rstrip(","),) if part]
    chain.append(canonical_scale_filter(spec))
    chain.append(f"fps={max(1, int(Config.TARGET_FPS))}")
    chain.append("setsar=1")
    chain.append(f"format={_canonical_pix_fmt()}")
    chain.append(canonical_tag_filter())
    return ",".join(chain)


def canonical_output_args() -> list[str]:
    """Encoder args pinning the output pixel format.

    The color tags themselves ride on the frames via ``canonical_tag_filter``;
    this only pins pix_fmt so an encoder can't pick a different one.
    """
    return ["-pix_fmt", _canonical_pix_fmt()]


def keyframe_args(segment_seconds: float | int | None) -> list[str]:
    """Force a keyframe every ``segment_seconds`` so the render's concat-demuxer
    ``outpoint`` trim (stream copy) always cuts on a clean boundary."""
    try:
        seconds = float(segment_seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return []
    interval = max(1, int(round(seconds * max(1, int(Config.TARGET_FPS)))))
    return [
        "-force_key_frames", f"expr:gte(t,n_forced*{seconds})",
        "-g", str(interval),
        "-keyint_min", str(interval),
    ]


def normalize_clip_file(
    src_path: str,
    out_path: str,
    *,
    spec: dict | None = None,
    segment_seconds: float | int | None = None,
    style_filter: str = "",
) -> bool:
    """Re-encode one clip to the canonical spec. Returns True when ``out_path`` exists.

    ``spec`` is the source's probed ``{w,h,pix_fmt,color_range,color_space}``; it
    is probed here when not supplied (callers that already probed pass it in to
    avoid a second ffprobe).
    """
    if spec is None:
        spec = probe_clip_spec(src_path)

    cmd = ["ffmpeg", "-y", "-i", src_path,
           "-vf", canonical_video_filter(spec, prefix=style_filter), "-an"]
    cmd.extend(FFmpegHelper.get_nvenc_flags())
    cmd.extend(keyframe_args(segment_seconds))
    cmd.extend(canonical_output_args())
    cmd.extend(["-movflags", "+faststart", out_path])

    if not FFmpegHelper.run_command(cmd) or not os.path.isfile(out_path):
        logger.error(f"[ClipCanonical] Normalize failed: {src_path}")
        return False
    return True


def is_canonical(path: str) -> bool:
    """True when the file on disk already matches the expected spec exactly."""
    spec = probe_clip_spec(path)
    if not spec:
        return False
    return all(spec.get(key) == value for key, value in expected_spec().items())


def describe_spec(spec: dict | None) -> str:
    """Compact human label for a probed spec, e.g. ``1920x1080 yuv420p/tv/bt709``."""
    if not spec:
        return "unreadable"
    return (
        f"{spec.get('w')}x{spec.get('h')} {spec.get('pix_fmt')}"
        f"/{spec.get('color_range')}/{spec.get('color_space')}"
    )
