"""Filter-graph fragments written once for both overlay paths.

The pipeline renders the overlay pass on the GPU (overlay_cuda/scale_cuda) when it
can and must always keep a CPU chain as the fallback (story_video_pipeline
``_apply_story_overlays``). ``Ops`` emits the matching filter names for either,
so a layout describes its graph once.

Time: parallel segments seek their input with ``-ss``, which restarts ``t`` at 0
in every segment. Expressions therefore use ``T(ss)`` = ``(t+ss)`` so a flash on
every 4th cut or a CTA window lands on the same frame whichever segment draws it.
"""

from __future__ import annotations


def T(ss: float | None) -> str:
    return f"(t+{float(ss or 0.0):.3f})"


def ease(u: str) -> str:
    """Smoothstep of an expression already clipped to [0, 1]."""
    return f"(({u})*({u})*(3-2*({u})))"


def sum_expr(terms) -> str:
    """Add terms as a balanced tree: ``((a+b)+(c+d))``.

    FFmpeg parses expressions recursively, and a flat ``a+b+c+...`` chain stops
    parsing at about 110 terms ("Error while parsing expression"). A ten-minute
    video has ~200 cuts, so anything keyed to cuts or paragraphs has to nest.
    """
    terms = [t for t in terms if t]
    if not terms:
        return "0"
    while len(terms) > 1:
        terms = [f"({terms[i]}+{terms[i + 1]})" if i + 1 < len(terms) else terms[i]
                 for i in range(0, len(terms), 2)]
    return terms[0]


def windows_expr(windows, tx: str) -> str:
    """1 inside any [a, b] window, 0 outside."""
    return sum_expr(f"between({tx},{a:.3f},{b:.3f})" for a, b in windows)


def win_factor(a: float, b: float, tx: str, tin: float = 0.5, tout: float = 0.5) -> str:
    """0 outside [a,b]; eases to 1 over `tin` after a, back to 0 over the last `tout`."""
    u1 = f"clip(({tx}-{a:.3f})/{tin},0,1)"
    u2 = f"clip(({tx}-{b - tout:.3f})/{tout},0,1)"
    return f"between({tx},{a:.3f},{b:.3f})*{ease(u1)}*(1-{ease(u2)})"


class Ops:
    """Emit GPU (CUDA) or CPU filter syntax for the same graph."""

    def __init__(self, gpu: bool):
        self.gpu = bool(gpu)

    def upload(self, index: int, label: str, fmt: str = "yuva420p") -> str:
        """An extra input made ready to overlay (alpha kept)."""
        if self.gpu:
            return f"[{index}:v]setpts=PTS-STARTPTS,format={fmt},hwupload_cuda[{label}]"
        return f"[{index}:v]setpts=PTS-STARTPTS,format={fmt}[{label}]"

    def split(self, src: str, labels: list[str]) -> str:
        return f"{src}split={len(labels)}" + "".join(f"[{name}]" for name in labels)

    def scale(self, src: str, w: int, h: int, out: str, smooth: bool = False) -> str:
        if self.gpu:
            return f"{src}scale_cuda={w}:{h}" + (":interp_algo=bicubic" if smooth else "") + out
        return f"{src}scale={w}:{h}" + (":flags=bicubic" if smooth else "") + out

    def blur(self, src: str, out: str, strength: str = "medium", size=(1920, 1080)) -> str:
        """Real gaussian blur on a small copy, grown back to size.

        Shrinking hard and growing back alone (the lab's first version) leaves a
        visible block grid: scale_cuda does not smooth enough when enlarging a tiny
        frame. A gaussian on the small copy (bilateral_cuda with a flat range kernel)
        removes it and measured slightly cheaper (0.90s vs 1.03s per minute of video).
        """
        sw, sh, sigma, window = {"light": (320, 180, 5, 11), "medium": (240, 136, 6, 13),
                                 "strong": (160, 90, 6, 13)}.get(strength, (240, 136, 6, 13))
        w, h = size
        if self.gpu:
            return (f"{src}scale_cuda={sw}:{sh}:format=yuv444p,"
                    f"bilateral_cuda=sigmaS={sigma}:sigmaR=255:window_size={window},"
                    f"scale_cuda={w}:{h}:interp_algo=bicubic:format=yuv420p{out}")
        return f"{src}scale={sw}:{sh},gblur=sigma={sigma},scale={w}:{h}:flags=bicubic{out}"

    def overlay(self, main: str, over: str, out: str, x="0", y="0", enable: str | None = None,
                dynamic: bool = False, repeat: bool = True) -> str:
        """``dynamic`` evaluates x/y every frame (moving or timed placement)."""
        opts = [f"x='{x}'", f"y='{y}'"]
        if repeat:
            opts.append("eof_action=repeat")
        opts.append("eval=frame" if dynamic else "eval=init")
        if not self.gpu:
            # Pin the output: format=auto would negotiate an alpha format back up the
            # chain and break the overlays after it (see decor_filter_parts).
            opts.append("format=yuv420")
        name = "overlay_cuda" if self.gpu else "overlay"
        tail = f":enable='{enable}'" if enable else ""
        return f"{main}{over}{name}=" + ":".join(opts) + tail + out
