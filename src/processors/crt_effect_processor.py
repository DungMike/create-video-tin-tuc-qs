import os

from src.config import Config
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.logger import logger


class CRTEffectProcessor:
    PRESETS = {
        "subtle": {
            "noise_strength": 5,
            "scan_opacity": 0.02,
            "vignette": "PI/4",
            "color_bleed": False,
            "flicker": 0.005,
        },
        "light": {
            "noise_strength": 10,
            "scan_opacity": 0.04,
            "vignette": "PI/5",
            "color_bleed": False,
            "flicker": 0.01,
        },
        "medium": {
            "noise_strength": 15,
            "scan_opacity": 0.06,
            "vignette": "PI/5",
            "color_bleed": True,
            "flicker": 0.02,
        },
        "heavy": {
            "noise_strength": 25,
            "scan_opacity": 0.10,
            "vignette": "PI/3",
            "color_bleed": True,
            "flicker": 0.04,
        },
    }

    @staticmethod
    def _get_settings(settings: dict | None = None) -> dict:
        defaults = {
            "noise_strength": Config.CRT_NOISE_STRENGTH,
            "scan_opacity": Config.CRT_SCANLINE_OPACITY,
            "vignette": Config.CRT_VIGNETTE,
            "color_bleed": Config.CRT_COLOR_BLEED,
            "flicker": Config.CRT_FLICKER,
        }
        if settings:
            defaults.update(settings)
        return defaults

    @staticmethod
    def build_crt_filter(settings: dict | None = None, *, for_image: bool = False) -> str:
        s = CRTEffectProcessor._get_settings(settings)

        filters = []

        noise_strength = min(int(s["noise_strength"]), 10)  # cap for perf
        if noise_strength > 0:
            # Use 'allf=t' (temporal only) instead of 't+u' — 3-5x faster
            filters.append(f"noise=alls={noise_strength}:allf=t")

        vignette = s["vignette"]
        if vignette:
            filters.append(f"vignette=angle={vignette}")

        # Skip time-dependent effects for still images
        if not for_image:
            flicker = float(s["flicker"])
            if flicker > 0:
                filters.append(f"eq=brightness='{flicker}*sin(2*PI*t*8)'")

            scan_opacity = float(s["scan_opacity"])
            if scan_opacity > 0:
                filters.append(
                    f"drawgrid=w=0:h=2:t=1:c=black@{scan_opacity}"
                )

        color_bleed = s.get("color_bleed", False)
        if color_bleed:
            filters.append("chromashift=cbh=2:crh=-2")

        if not filters:
            return "null"

        return ",".join(filters)

    @staticmethod
    def apply_to_video(
        input_video: str,
        output_video: str,
        settings: dict | None = None,
        progress_callback=None,
    ) -> bool:
        if not os.path.isfile(input_video):
            logger.error(f"CRT input video not found: {input_video}")
            return False

        os.makedirs(os.path.dirname(output_video) or ".", exist_ok=True)
        duration = FFmpegHelper.probe_duration(input_video) or None

        # For long videos (>120s), skip noise filter — it's extremely CPU-heavy
        effective_settings = dict(settings) if settings else {}
        if duration and duration > 120:
            effective_settings["noise_strength"] = 0
            logger.info(f"CRT: skipping noise filter for long video ({duration:.0f}s)")

        filter_str = CRTEffectProcessor.build_crt_filter(effective_settings)

        cmd = [
            "ffmpeg", "-y",
            "-hwaccel", "cuda",
            "-i", input_video,
            "-filter_threads", str(os.cpu_count() or 4),
            "-vf", filter_str,
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend([
            "-c:a", "copy",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            output_video,
        ])

        logger.info(f"Applying CRT effect: {input_video} -> {output_video}")
        logger.debug(f"CRT filter: {filter_str}")

        success = FFmpegHelper.run_command(
            cmd,
            progress_callback=progress_callback,
            progress_total_seconds=duration,
        )

        if success:
            logger.info(f"CRT effect applied successfully: {output_video}")
        else:
            logger.error(f"CRT effect failed for: {input_video}")

        return success

    @staticmethod
    def generate_demo_image(
        sample_image_path: str,
        settings: dict | None = None,
        output_path: str | None = None,
    ) -> str:
        if not os.path.isfile(sample_image_path):
            logger.error(f"CRT demo sample image not found: {sample_image_path}")
            return ""

        if not output_path:
            os.makedirs(Config.CRT_EFFECT_DIR, exist_ok=True)
            output_path = os.path.join(Config.CRT_EFFECT_DIR, "crt_demo_preview.jpg")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        filter_str = CRTEffectProcessor.build_crt_filter(settings, for_image=True)

        cmd = [
            "ffmpeg", "-y",
            "-i", sample_image_path,
            "-vf", filter_str,
            "-frames:v", "1",
            "-q:v", "2",
            output_path,
        ]

        logger.info(f"Generating CRT demo image: {sample_image_path} -> {output_path}")

        if FFmpegHelper.run_command(cmd):
            logger.info(f"CRT demo image generated: {output_path}")
            return output_path

        logger.error(f"Failed to generate CRT demo image from {sample_image_path}")
        return ""

    @staticmethod
    def generate_demo_video(
        sample_video_path: str,
        settings: dict | None = None,
        output_path: str | None = None,
        duration: float = 3.0,
    ) -> str:
        if not os.path.isfile(sample_video_path):
            logger.error(f"CRT demo sample video not found: {sample_video_path}")
            return ""

        if not output_path:
            os.makedirs(Config.CRT_EFFECT_DIR, exist_ok=True)
            output_path = os.path.join(Config.CRT_EFFECT_DIR, "crt_demo_preview.mp4")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        filter_str = CRTEffectProcessor.build_crt_filter(settings)

        cmd = [
            "ffmpeg", "-y",
            "-i", sample_video_path,
            "-t", str(duration),
            "-vf", filter_str,
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        cmd.extend(["-pix_fmt", "yuv420p", output_path])

        logger.info(f"Generating CRT demo video ({duration}s): {sample_video_path} -> {output_path}")

        if FFmpegHelper.run_command(cmd):
            logger.info(f"CRT demo video generated: {output_path}")
            return output_path

        logger.error(f"Failed to generate CRT demo video from {sample_video_path}")
        return ""
