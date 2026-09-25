"""Single Story Video pipeline runner.

Processes one story video: audio -> random 5-second clip sequence -> audio mux -> finalize.
"""

import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from src.config import Config
from src.processors.audio_utils import get_audio_duration, validate_audio
from src.utils.clip_spec_validation import filter_valid_clips
from src.utils.clip_usage import (
    CLIP_USAGE_ONCE,
    get_clip_usage_ledger,
    normalize_clip_usage_mode,
)
from src.utils.ffmpeg_helper import FFmpegHelper
from src.utils.file_manager import storage_absolute_path, storage_relative_path
from src.utils.logger import logger
from src.utils.story_clip_bag import SharedClipBag
from src.utils.story_library import (
    load_story_library_index,
    resolve_library_ids,
    story_library_root,
)
from src.utils.tts_audio import (
    TTSAudioError,
    create_audio_from_google_doc,
)


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# Số lần đọc lại khi gặp file JSON đang bị ghi đè dở. Ba lần cách nhau 50ms là
# thừa cho một lần ghi vài chục KB.
_LOAD_JSON_RETRIES = 3
_LOAD_JSON_RETRY_DELAY = 0.05

# os.replace trên Windows ném WinError 5 nếu file đích đang được ai đó mở đọc:
# Python mở file không kèm FILE_SHARE_DELETE nên một reader đang đọc sẽ chặn việc
# thay thế. Reader chỉ giữ file vài micro giây nên thử lại ngắn là ăn; 10 lần x
# 50ms = 0.5s, thừa sức cho UI poll mỗi giây.
_REPLACE_RETRIES = 10
_REPLACE_RETRY_DELAY = 0.05


def _save_json(path: str, data: dict):
    """Ghi atomic: ra file tạm cùng thư mục rồi ``os.replace``.

    Trước đây mở thẳng bằng ``"w"`` — lệnh đó cắt file về 0 byte NGAY LẬP TỨC rồi
    mới ghi dần nội dung vào. Mọi reader lọt vào cửa sổ đó nhận JSONDecodeError,
    ``_load_json`` trả None, và caller hiểu thành "job không tồn tại": job biến
    mất khỏi danh sách, ``GET /harvest/<id>`` trả 404, cancel im lặng không ăn.
    Không phải phòng xa — đo thực tế trên chính hai hàm này: **290/3000 lần đọc
    hỏng (9.7%)** khi có một writer chạy song song, mà harvest thì ghi progress
    sau MỖI video còn UI thì poll mỗi giây.

    ``os.replace`` là atomic khi nguồn và đích cùng volume — nên file tạm phải
    nằm cùng thư mục với đích, không phải trong temp dir của hệ thống.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, ensure_ascii=False, indent=2)
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError:
                if attempt == _REPLACE_RETRIES - 1:
                    raise
                time.sleep(_REPLACE_RETRY_DELAY)
    except BaseException:
        # Ghi dở thì dọn file tạm, đừng để rác tích lại trong thư mục job.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_json(path: str) -> dict | None:
    """Đọc JSON, thử lại vài lần nếu vớ phải file đang được ghi đè.

    ``_save_json`` ở trên đã ghi atomic nên không còn tự tạo ra cảnh này, nhưng
    cùng một file có thể do process khác ghi (server chạy bản code cũ, hoặc một
    job chạy ngoài web app), nên vẫn thử lại thay vì coi một lần đọc hỏng là
    "không tồn tại" — nhầm lẫn đó chính là thứ làm job biến mất khỏi UI.
    """
    for attempt in range(_LOAD_JSON_RETRIES):
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError):
            if attempt == _LOAD_JSON_RETRIES - 1:
                return None
            time.sleep(_LOAD_JSON_RETRY_DELAY)
    return None


def _story_dir(story_id: str) -> str:
    path = os.path.join(Config.STORY_VIDEO_DIR, story_id)
    os.makedirs(path, exist_ok=True)
    return path


def _progress_path(story_id: str) -> str:
    return os.path.join(_story_dir(story_id), "progress.json")


def _cancel_path(story_id: str) -> str:
    return os.path.join(_story_dir(story_id), "cancel.requested")


def request_story_cancel(story_id: str):
    """Persist a cancellation request so active and queued runners can observe it."""
    cancel_path = _cancel_path(story_id)
    with open(cancel_path, "w", encoding="utf-8") as file_obj:
        file_obj.write(_utc_now())


def is_story_cancel_requested(story_id: str) -> bool:
    return os.path.isfile(_cancel_path(story_id))


def _temp_dir(story_id: str) -> str:
    path = os.path.join(_story_dir(story_id), "temp")
    os.makedirs(path, exist_ok=True)
    return path


def _output_dir(subdir: str = "") -> str:
    """Thu muc chua video thanh pham.

    Batch truyen ``subdir`` = batch id de moi batch co thu muc rieng, video cua cac
    batch khong lan vao nhau; render le khong truyen nen van ra thang ``story-video/``.
    """
    path = os.path.join(Config.OUTPUT_DIR, "story-video")
    if subdir:
        # Chi nhan ten thu muc phang (batch id), khong cho "..", dau gach cheo...
        if not all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in subdir):
            raise ValueError(f"Invalid output subdir: {subdir!r}")
        path = os.path.join(path, subdir)
    os.makedirs(path, exist_ok=True)
    return path


def _overlay_max_concurrent() -> int:
    """Max overlay-pass ffmpeg processes allowed to run at once, app-wide.

    Defaults to (physical cores - 1) so at least one core stays free for the OS and
    the per-frame GPU<->CPU handoff. Overriding via OVERLAY_MAX_CONCURRENT wins."""
    configured = int(getattr(Config, "OVERLAY_MAX_CONCURRENT", 0) or 0)
    if configured > 0:
        return configured
    return max(1, (os.cpu_count() or 2) - 1)


# One shared budget of "overlay slots" across the whole process. Every heavy overlay
# ffmpeg (single-pass or a parallel segment) acquires a slot before running, so batch
# workers x segments can never oversubscribe the CPU — extras queue instead of thrash.
_OVERLAY_SLOTS = threading.BoundedSemaphore(_overlay_max_concurrent())


def _run_overlay_ffmpeg(cmd: list, **kwargs) -> bool:
    """Run an overlay-pass ffmpeg while holding one global overlay slot."""
    with _OVERLAY_SLOTS:
        return FFmpegHelper.run_command(cmd, **kwargs)


_ASS_TS_RE = re.compile(r"^\s*(\d+):(\d\d):(\d\d)\.(\d\d)\s*$")
_FAD_RE = re.compile(r"\\fad\((\d+),(\d+)\)")


def _parse_ass_ts(value: str) -> float:
    match = _ASS_TS_RE.match(value)
    if not match:
        return 0.0
    h, m, s, cs = (int(g) for g in match.groups())
    return h * 3600 + m * 60 + s + cs / 100.0


def _fmt_ass_ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


_CLIP_NAME_RE = re.compile(r"^(.+)_clip_(\d+)\.mp4$", re.IGNORECASE)

# Clips play their FULL length (no per-clip concat ``outpoint``): a library may mix
# lengths (8s video cuts, 3-5s photo clips) and each keeps its own. Only fragments
# too short to read as a shot are left out of the draw.
_MIN_CLIP_SECONDS = 0.5


def _usable_clip_pool(clips: list[tuple[str, float]]) -> list[tuple[str, float]]:
    return [item for item in clips if item[1] >= _MIN_CLIP_SECONDS] or clips


def _clip_source(asset: dict) -> tuple[str, int] | None:
    """(source key, clip index) of a library clip, or None when it can't be told.

    Clips cut from one source share the ``source_name`` prefix before
    ``_clip_NNN`` (the segment muxer writes contiguous pieces), so ``_clip_001``
    continues ``_clip_000``. Older harvests also tag the source as ``src:...``.
    """
    match = _CLIP_NAME_RE.match(str(asset.get("source_name") or ""))
    if match:
        return match.group(1), int(match.group(2))
    return None


def _ass_event_spans(ass_path: str) -> list[tuple[float, float]]:
    """Every subtitle event's (start, end), merged into sorted disjoint spans.

    Only the ``Default`` style counts: layout layers (a progress bar, OSD text)
    span the whole video and would otherwise merge everything into one span,
    leaving the segment cut nowhere to snap to.
    """
    spans: list[tuple[float, float]] = []
    try:
        with open(ass_path, "r", encoding="utf-8-sig") as handle:
            for line in handle:
                if not line.startswith("Dialogue:"):
                    continue
                fields = line[len("Dialogue:"):].split(",", 9)
                if len(fields) < 10 or fields[3].strip() != "Default":
                    continue
                start = _parse_ass_ts(fields[1])
                end = _parse_ass_ts(fields[2])
                if end > start:
                    spans.append((start, end))
    except OSError:
        return []
    spans.sort()
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _snap_segment_boundary(spans, nominal: float, window: float) -> float:
    """Move a segment boundary off any subtitle that is on screen.

    A cue cut in half by a boundary replays its entire animation in the next
    segment: `_rebase_ass_file` shifts the event's Start/End, but the `\\t` and
    `\\kf` offsets inside the text stay relative to the *original* Start, and a
    straddling cue has its Start clamped to 0 — so t=0 lands at the boundary and
    the word pops, the karaoke refills, the fade fades in all over again. Cutting
    only where nothing is on screen sidesteps that for every preset at once.

    Returns the nominal time unchanged when no gap is close enough to use.
    """
    for start, end in spans:
        if not start < nominal < end:
            continue
        # Snap to whichever edge of the cue is nearer, if it is within reach.
        if nominal - start <= end - nominal:
            return start if nominal - start <= window else nominal
        return end if end - nominal <= window else nominal
    return nominal


def _rebase_ass_file(src_ass: str, start: float, dur: float, out_ass: str) -> None:
    """Write a copy of `src_ass` whose Dialogue events are shifted to a segment.

    Events are moved by -start, clipped to [0, dur], and dropped when they fall
    entirely outside the window. Header/style lines are copied verbatim so the
    burned-in subtitle looks identical to the single-pass render."""
    with open(src_ass, "r", encoding="utf-8-sig") as handle:
        lines = handle.readlines()

    out_lines: list[str] = []
    for line in lines:
        if not line.startswith("Dialogue:"):
            out_lines.append(line)
            continue
        # Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        body = line[len("Dialogue:"):]
        fields = body.split(",", 9)
        if len(fields) < 10:
            out_lines.append(line)
            continue
        new_start = _parse_ass_ts(fields[1]) - start
        new_end = _parse_ass_ts(fields[2]) - start
        if new_end <= 0 or new_start >= dur:
            continue
        # A cue cut by the boundary must not fade twice: the half in the next
        # segment drops its fade-in, the half in this one its fade-out. Snapping
        # can't avoid this when the SRT has no gaps between cues (every cue of a
        # typical narration SRT touches the next one).
        text = fields[9]
        if new_start < 0:
            text = _FAD_RE.sub(lambda m: f"\\fad(0,{m.group(2)})", text)
        if new_end > dur:
            text = _FAD_RE.sub(lambda m: f"\\fad({m.group(1)},0)", text)
        fields[1] = _fmt_ass_ts(max(0.0, new_start))
        fields[2] = _fmt_ass_ts(min(dur, new_end))
        fields[9] = text
        out_lines.append("Dialogue:" + ",".join(fields))

    with open(out_ass, "w", encoding="utf-8") as handle:
        handle.writelines(out_lines)


def _append_ass_layers(ass_text: str, styles: list[str], events: list[str]) -> str:
    """Add an edit style's own styles (after Default) and events (at the end)."""
    if styles:
        marker = "\n[Events]"
        head, sep, tail = ass_text.partition(marker)
        if sep:
            ass_text = head.rstrip("\n") + "\n" + "\n".join(styles) + "\n" + sep + tail
    if events:
        ass_text = ass_text.rstrip("\n") + "\n" + "\n".join(events) + "\n"
    return ass_text


def load_story_progress(story_id: str) -> dict | None:
    return _load_json(_progress_path(story_id))


def _load_story_library_index(library_id=None) -> dict:
    return load_story_library_index(library_id)


class StoryVideoPipelineRunner:
    """Runs the simple story video pipeline for a single story."""

    def __init__(self, story_id: str, config_dict: dict, clip_bag: SharedClipBag | None = None):
        self.story_id = story_id
        # Deck shared by every video of a batch; None for standalone renders.
        self.clip_bag = clip_bag
        self.input_type = config_dict.get("input_type", "script_url")
        self.input_value = config_dict.get("input_value", "")
        self.output_name = config_dict.get("output_name", "")
        # Batch gan batch id vao day de moi batch co thu muc output rieng; render le
        # de trong nen video van ra thang story-video/ nhu cu.
        self.output_subdir = str(config_dict.get("output_subdir", "") or "").strip()
        self.clip_tags = config_dict.get("clip_tags", [])
        # A render can draw clips from several libraries at once: their pools are
        # merged into one deck so a clip only repeats after every clip of every
        # selected library has been used. `library_id` (singular) is still read so
        # configs written before multi-select — old batch progress files, retries —
        # keep working.
        self.library_ids = resolve_library_ids(
            config_dict.get("library_ids") or config_dict.get("library_id")
        )
        self.voice_id = config_dict.get("voice_id", "")
        self.waveform_overlay_id = str(config_dict.get("waveform_overlay_id", "") or "").strip()
        # Song am / CTA duoc batch gan cho tung video theo vong xoay; "" = dung
        # ban ghi mac dinh (waveform) / dang bat (CTA) o trang cau hinh.
        self.cta_overlay_id = str(config_dict.get("cta_overlay_id", "") or "").strip()
        self.tv_effect_style_id = str(config_dict.get("tv_effect_style_id", "") or "").strip()
        # Decor image ("khung TV"): a full-frame photo whose green screen the story
        # video is scaled into. Assigned per video by the batch rotation; "" = off.
        self.decor_image_id = str(config_dict.get("decor_image_id", "") or "").strip()
        # Opt-out for the TV style pass on a library whose clips are NOT pre-baked:
        # render the clips as they are (overlays + subtitle only). This also unlocks
        # the GPU overlay path, which the CPU-only style filter would otherwise block.
        self.skip_tv_effect = bool(config_dict.get("skip_tv_effect", False))
        # "once" = moi clip 1 lan (src/utils/clip_usage.py): uu tien clip chua dung,
        # dem luot dung o Mongo. Thieu key (config cu, batch resume) = "reuse" =
        # luong chon clip nhu cu, khong cham toi Mongo.
        self.clip_usage_mode = normalize_clip_usage_mode(config_dict.get("clip_usage_mode"))
        self.subtitle_path = str(config_dict.get("subtitle_path", "") or "").strip()
        # Optional intro clip (already normalized to the canonical output spec on
        # upload) prepended to the front of the finished video. "" = no intro.
        self.intro_video_path = str(config_dict.get("intro_video_path", "") or "").strip()
        self.subtitle_font = str(config_dict.get("subtitle_font", "") or "").strip()
        self.subtitle_preset = str(config_dict.get("subtitle_preset", "") or "").strip() or "clean"
        self.subtitle_max_chars_per_line = self._coerce_positive_int(
            config_dict.get("subtitle_max_chars_per_line"),
            Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE,
        )
        self.subtitle_max_lines = self._coerce_positive_int(
            config_dict.get("subtitle_max_lines"),
            Config.STORY_SUBTITLE_MAX_LINES,
        )
        raw_style_overrides = config_dict.get("subtitle_style_overrides")
        self.subtitle_style_overrides = (
            dict(raw_style_overrides) if isinstance(raw_style_overrides, dict) else {}
        )
        # Edit style: one layout record dealt out by the batch rotation, plus the
        # modifier records ticked for the whole batch (src/utils/edit_styles).
        # "" / [] = the plain layout. A legacy config that only carries a decor id
        # still renders in the TV frame, exactly as before.
        self.layout_id = str(config_dict.get("layout_id", "") or "").strip()
        raw_modifiers = config_dict.get("modifier_ids") or []
        self.modifier_ids = [str(m).strip() for m in raw_modifiers if str(m or "").strip()] \
            if isinstance(raw_modifiers, list) else []
        self.chapters_path = str(config_dict.get("chapters_path", "") or "").strip()
        self._edit = None
        self._subtitle_ass_path = ""
        self._seg_dur_cache: int | None = None
        # path -> (source, clip index) for the pool, used to chain consecutive clips.
        self._clip_meta: dict[str, tuple[str, int]] = {}
        # path -> real clip length for the pool: clips play their full length, so the
        # clip-start fallback (_probe_clip_starts) adds these up instead of k * unit.
        self._clip_durations: dict[str, float] = {}

        self._lock = threading.Lock()
        self.progress = {
            "storyId": story_id,
            "status": "pending",
            "stage": "pending",
            "percent": 0,
            "message": "Cho xu ly...",
            "outputName": self.output_name,
            "result": {"videoPath": None},
            "error": None,
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
        }
        self._save_progress()

    def _segment_duration(self) -> int:
        """NOMINAL clip length of the selected libraries (pre-baked 'full' ones use 10s units).

        Only a starting guess now (edit-plan clip period before the base is measured,
        log lines): the render no longer trims clips to it. Every clip plays its own
        full length, so a library can mix 8s video cuts with 3-5s photo clips.
        """
        if self._seg_dur_cache is None:
            from src.utils.story_library import libraries_clip_duration

            default = max(1, int(Config.STORY_CLIP_DURATION))
            self._seg_dur_cache = max(1, libraries_clip_duration(self.library_ids, default))
        return self._seg_dur_cache

    @staticmethod
    def _coerce_positive_int(value, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def _raise_if_cancel_requested(self):
        if is_story_cancel_requested(self.story_id):
            raise StoryVideoCancelled()

    def _save_progress(self):
        with self._lock:
            self.progress["updatedAt"] = _utc_now()
            _save_json(_progress_path(self.story_id), self.progress)

    def _update_progress(
        self,
        stage: str,
        percent: float,
        message: str,
        *,
        status: str = "running",
        error: str | None = None,
        video_path: str | None = None,
    ):
        self.progress["status"] = status
        self.progress["stage"] = stage
        self.progress["percent"] = round(min(100, max(0, percent)), 1)
        self.progress["message"] = message
        if error is not None:
            self.progress["error"] = error
        if video_path is not None:
            self.progress["result"]["videoPath"] = video_path
        self._save_progress()

    # Files at the story-dir root that must outlive a purge: progress.json is what the
    # status API polls, and the cancel marker is what is_story_cancel_requested() reads.
    _PURGE_KEEP = frozenset({"progress.json", "cancel.requested"})

    def _purge_working_files(self):
        """Drop the working media left by a run that never reached _finalize().

        On success _finalize() rmtree's the whole story dir, but a failed or cancelled
        run used to leave renders/ and temp/ behind for good -- multi-GB of dead
        intermediates accumulating on the render working disk, which is deliberately the
        small fast one (see STORAGE_DIR in .env). Everything except _PURGE_KEEP goes,
        while the story dir itself stays so progress.json remains pollable.

        Never raises: this runs in a finally and must not mask the real outcome.
        """
        try:
            story_dir = _story_dir(self.story_id)
            freed = 0
            for entry in os.scandir(story_dir):
                if entry.is_file() and entry.name in self._PURGE_KEEP:
                    continue
                try:
                    if entry.is_dir():
                        for root, _dirs, files in os.walk(entry.path):
                            for name in files:
                                try:
                                    freed += os.path.getsize(os.path.join(root, name))
                                except OSError:
                                    pass
                        shutil.rmtree(entry.path, ignore_errors=True)
                    else:
                        freed += entry.stat().st_size
                        os.remove(entry.path)
                except OSError:
                    pass
            if freed:
                logger.info(
                    f"[StoryPipeline:{self.story_id}] Purged {freed / 1e6:.1f} MB of "
                    f"working files after an unsuccessful run."
                )
        except Exception as exc:
            logger.warning(
                f"[StoryPipeline:{self.story_id}] Could not purge working files: {exc}"
            )

    def run(self) -> str | None:
        """Main pipeline entry. Returns output video path or None on failure."""
        completed = False
        try:
            self._raise_if_cancel_requested()
            self._update_progress("prepare_audio", 5, "Dang chuan bi audio...")

            audio_path = self._prepare_audio()
            self._raise_if_cancel_requested()
            if not audio_path:
                self._update_progress(
                    "prepare_audio",
                    0,
                    "Khong the chuan bi audio.",
                    status="failed",
                    error="Audio preparation failed.",
                )
                return None

            audio_duration = get_audio_duration(audio_path)
            self._raise_if_cancel_requested()
            if audio_duration <= 0:
                self._update_progress(
                    "prepare_audio",
                    0,
                    "Audio khong hop le hoac khong doc duoc.",
                    status="failed",
                    error="Invalid audio duration.",
                )
                return None

            if self.layout_id or self.modifier_ids:
                self._update_progress("prepare_audio", 10, "Dang chuan bi kieu dung...")
                self._edit = self._build_edit_plan(audio_path, audio_duration)
                self._raise_if_cancel_requested()

            self._update_progress("select_clips", 15, "Dang chon clip ngau nhien tu thu vien...")
            clips = self._select_clips(audio_duration)
            self._raise_if_cancel_requested()
            if not clips:
                library_hint = (
                    f" (thu vien: {', '.join(self.library_ids)})" if self.library_ids else ""
                )
                self._update_progress(
                    "select_clips",
                    15,
                    f"Khong tim thay clip nao trong thu vien{library_hint}.",
                    status="failed",
                    error="No clips available in the selected story library.",
                )
                return None
            if self._edit and self._edit.needs_clip_media:
                # Stills for album/film-strip layouts, extracted while the base concatenates.
                self._edit.start_clip_media(clips)

            self._update_progress("render_video", 35, "Dang ghep clip voi audio...")
            rendered_video = self._render_simple_video(clips, audio_path, audio_duration)
            self._raise_if_cancel_requested()
            if not rendered_video:
                self._update_progress(
                    "render_video",
                    35,
                    "Ghep video voi audio that bai.",
                    status="failed",
                    error="Video render failed.",
                )
                return None
            if self._edit and self._edit.needs_clip_timing:
                self._edit.set_clip_starts(self._probe_clip_starts(
                    rendered_video, len(clips), [self._clip_durations.get(path, 0.0) for path in clips],
                ))
            if self._edit:
                self._edit.finish_clip_media()

            if self.subtitle_path or (self._edit and self._edit.has_ass_layers):
                self._update_progress("story_overlays", 90, "Dang chuan bi phu de...")
                ass_path = self._prepare_subtitle_ass(audio_duration)
                self._raise_if_cancel_requested()
                if not ass_path:
                    return None
                self._subtitle_ass_path = ass_path

            overlay_message = "Dang ap dung TV noise va song am..."
            if self._subtitle_ass_path:
                overlay_message = "Dang ap dung TV noise, song am va phu de..."
            self._update_progress("story_overlays", 90, overlay_message)
            output_video = self._apply_story_overlays(rendered_video, audio_duration)
            self._raise_if_cancel_requested()
            if not output_video:
                return None

            if self.intro_video_path:
                self._update_progress("story_overlays", 97, "Dang gan intro...")
                output_video = self._prepend_intro(output_video, audio_duration)
                self._raise_if_cancel_requested()

            self._update_progress("finalize", 98, "Dang hoan tat...")
            self._raise_if_cancel_requested()
            final_path = self._finalize(output_video)
            final_rel_path = storage_relative_path(final_path)
            if self.clip_usage_mode == CLIP_USAGE_ONCE:
                self._commit_clip_usage(final_path)

            self._update_progress(
                "completed",
                100,
                "Hoan tat thanh cong!",
                status="completed",
                video_path=final_rel_path,
            )
            logger.info(f"[StoryPipeline:{self.story_id}] Pipeline completed: {final_path}")
            completed = True
            return final_path

        except StoryVideoCancelled:
            logger.info(f"[StoryPipeline:{self.story_id}] Pipeline cancelled.")
            self._update_progress(
                "cancelled",
                self.progress.get("percent", 0),
                "Da huy xu ly.",
                status="cancelled",
            )
            return None
        except Exception as exc:
            logger.error(f"[StoryPipeline:{self.story_id}] Pipeline failed: {exc}", exc_info=True)
            self._update_progress(
                self.progress.get("stage", "unknown"),
                0,
                f"Pipeline that bai: {exc}",
                status="failed",
                error=str(exc),
            )
            return None
        finally:
            if self.clip_usage_mode == CLIP_USAGE_ONCE:
                # Failed/cancelled: hand the leased clips back without counting them.
                get_clip_usage_ledger().release(self.story_id)
            if self._edit:
                # Never purge under a still-running stills thread.
                self._edit.finish_clip_media()
            # Runs after the handlers above have written the terminal progress, so the
            # purge sees (and keeps) the final progress.json rather than racing it.
            if not completed:
                self._purge_working_files()

    def _prepare_audio(self) -> str | None:
        """Prepare audio from script URL (TTS) or validate uploaded audio file."""
        temp = _temp_dir(self.story_id)

        if self.input_type == "script_url":
            try:
                result = create_audio_from_google_doc(
                    doc_url=self.input_value,
                    output_name=self.output_name or f"story_{self.story_id}",
                    voice_id=self.voice_id or None,
                )
                audio_item = result.get("audio")
                if not audio_item or not audio_item.get("relativePath"):
                    logger.error(f"[StoryPipeline:{self.story_id}] TTS returned no audio.")
                    return None

                source_path = storage_absolute_path(audio_item["relativePath"])
                if not os.path.isfile(source_path):
                    logger.error(f"[StoryPipeline:{self.story_id}] TTS audio file not found: {source_path}")
                    return None

                dest_path = os.path.join(temp, f"audio_{self.story_id}.mp3")
                shutil.copy2(source_path, dest_path)
                logger.info(f"[StoryPipeline:{self.story_id}] TTS audio ready: {dest_path}")
                return dest_path

            except TTSAudioError as exc:
                logger.error(f"[StoryPipeline:{self.story_id}] TTS failed: {exc}")
                return None

        if self.input_type == "audio_file":
            audio_path = self.input_value
            if not os.path.isfile(audio_path):
                logger.error(f"[StoryPipeline:{self.story_id}] Audio file not found: {audio_path}")
                return None
            if not validate_audio(audio_path):
                logger.error(f"[StoryPipeline:{self.story_id}] Audio validation failed: {audio_path}")
                return None
            dest_path = os.path.join(temp, f"audio_{Path(audio_path).name}")
            shutil.copy2(audio_path, dest_path)
            logger.info(f"[StoryPipeline:{self.story_id}] Audio file validated: {dest_path}")
            return dest_path

        logger.error(f"[StoryPipeline:{self.story_id}] Unknown input_type: {self.input_type}")
        return None

    def _filter_assets_by_tags(self, all_clips: list[dict]) -> list[dict]:
        """Narrow one library's assets to the clip-tag/clip-id selection.

        Ids and tags only mean something inside the index they came from, so this
        runs per library: a selection matching nothing in *this* library falls
        back to all of its clips rather than dropping the library from the pool.
        """
        if not self.clip_tags:
            return all_clips

        selected_values = {str(value).lower() for value in self.clip_tags}
        filtered = [
            clip
            for clip in all_clips
            if str(clip.get("id", "")).lower() in selected_values
        ]
        if not filtered:
            filtered = [
                clip
                for clip in all_clips
                if any(str(tag).lower() in selected_values for tag in clip.get("tags", []))
            ]
        if not filtered:
            logger.warning(
                f"[StoryPipeline:{self.story_id}] No clips match selection {self.clip_tags}, using all clips."
            )
            filtered = all_clips
        return filtered

    def _library_pool(self, library_id: str) -> list[tuple[str, float]]:
        """Usable ``(clip_path, duration)`` candidates from a single library."""
        library_root = story_library_root(library_id)
        filtered = self._filter_assets_by_tags(
            _load_story_library_index(library_id).get("assets", [])
        )

        candidates: list[tuple[str, float]] = []
        for clip in filtered:
            rel_path = clip.get("relative_path", "")
            if not rel_path:
                continue
            clip_path = os.path.join(library_root, rel_path)
            if not os.path.isfile(clip_path):
                continue

            try:
                duration = float(clip.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0
            if duration <= 0:
                duration = FFmpegHelper.probe_duration(clip_path)
            if duration > 0:
                candidates.append((clip_path, duration))
                self._clip_durations[clip_path] = duration
                source = _clip_source(clip)
                if source:
                    self._clip_meta[clip_path] = (f"{library_id}|{source[0]}", source[1])

        # Drop clips whose resolution doesn't match the pipeline target: a base
        # concatenated from mismatched clips breaks the CUDA-only overlay filter
        # chain mid-stream (NVDEC hits the parameter change and scale_cuda/
        # overlay_cuda can't reconfigure -> "Function not implemented"). Excluding
        # them here just shrinks the pool the random draw below picks from, so a
        # different clip is used in its place automatically. Run per library: the
        # probe-spec cache lives in the library's own directory.
        candidates, excluded_clips = filter_valid_clips(library_root, candidates)
        if excluded_clips:
            logger.warning(
                f"[StoryPipeline:{self.story_id}] [{library_id}] Excluded {len(excluded_clips)} clip(s) with "
                f"mismatched resolution/pix_fmt/color tags (expected {Config.TARGET_RESOLUTION} "
                f"{Config.CLIP_EXPECTED_PIX_FMT}/{Config.CLIP_EXPECTED_COLOR_RANGE}/"
                f"{Config.CLIP_EXPECTED_COLOR_SPACE}): "
                f"{excluded_clips[:3]}{' ...' if len(excluded_clips) > 3 else ''}"
            )
        return candidates

    def _select_clips(self, audio_duration: float) -> list[str]:
        """Select prebuilt story-library clips without re-encoding them.

        Clips from every selected library are merged into ONE pool, so the draw
        below spreads picks across all of them and only repeats a clip after the
        whole merged pool has been used.
        """
        if self.clip_usage_mode == CLIP_USAGE_ONCE:
            return self._select_clips_once(audio_duration)

        valid_clips: list[tuple[str, float]] = []
        per_library_counts: list[str] = []
        for library_id in self.library_ids:
            library_clips = self._library_pool(library_id)
            valid_clips.extend(library_clips)
            per_library_counts.append(f"{library_id}={len(library_clips)}")

        if not valid_clips:
            logger.error(
                f"[StoryPipeline:{self.story_id}] No valid clips found in story "
                f"librar{'ies' if len(self.library_ids) > 1 else 'y'} "
                f"{', '.join(self.library_ids) or '(none)'}."
            )
            return []

        if len(self.library_ids) > 1:
            logger.info(
                f"[StoryPipeline:{self.story_id}] Clip pool merged from "
                f"{len(self.library_ids)} libraries ({', '.join(per_library_counts)}), "
                f"total={len(valid_clips)}."
            )

        pool = _usable_clip_pool(valid_clips)
        target_duration = audio_duration + 0.25
        selected: list[str] = []
        selected_duration = 0.0

        if self._edit and self._edit.long_takes:
            return self._select_long_takes(pool, target_duration, audio_duration)

        if self.clip_bag is not None:
            # Batch mode: draw without replacement from the deck shared by the
            # whole batch, so a clip repeats only after the entire pool has
            # been used at least once (ceil(picks/pool) cap instead of the
            # unbounded overlap independent shuffles produce).
            key = SharedClipBag.pool_key(self.library_ids, self.clip_tags)
            picked: set[str] = set()
            while selected_duration < target_duration:
                self._raise_if_cancel_requested()
                clip_path, duration = self.clip_bag.draw(key, pool, exclude=picked)
                picked.add(clip_path)
                selected.append(clip_path)
                selected_duration += duration
        else:
            while selected_duration < target_duration:
                self._raise_if_cancel_requested()
                shuffled = list(pool)
                random.shuffle(shuffled)
                for clip_path, duration in shuffled:
                    selected.append(clip_path)
                    selected_duration += duration
                    if selected_duration >= target_duration:
                        break

        mode = "shared shuffle-bag" if self.clip_bag is not None else "per-video shuffle"
        logger.info(
            f"[StoryPipeline:{self.story_id}] Selected {len(selected)} clips ({mode}), "
            f"selected_duration={selected_duration:.1f}s, audio={audio_duration:.1f}s"
        )
        return selected

    def _library_clip_keys(self, library_id: str) -> dict[str, str]:
        """``clip_path -> clip_key`` (src/utils/clip_identity.py) cho mot thu vien."""
        from src.utils.clip_identity import clip_identity

        library_root = story_library_root(library_id)
        keys: dict[str, str] = {}
        for asset in _load_story_library_index(library_id).get("assets", []):
            rel_path = asset.get("relative_path", "")
            if rel_path:
                keys[os.path.join(library_root, rel_path)] = clip_identity(library_id, asset)[1]
        return keys

    def _select_clips_once(self, audio_duration: float) -> list[str]:
        """Che do "moi clip 1 lan": clip chua dung truoc, roi toi clip dung it nhat.

        Cung pool voi luong thuong (``_library_pool``, loc do dai y het), chi thu tu
        chon lay tu so lan dung o Mongo (src/utils/clip_usage.py). Mongo loi thi
        video failed voi thong bao ro -- khong lang le quay ve boc ngau nhien.
        """
        valid_clips: list[tuple[str, float]] = []
        clip_keys: dict[str, str] = {}
        for library_id in self.library_ids:
            library_clips = self._library_pool(library_id)
            valid_clips.extend(library_clips)
            library_keys = self._library_clip_keys(library_id)
            for clip_path, _duration in library_clips:
                clip_keys[clip_path] = library_keys.get(clip_path) or f"path:{clip_path}"

        if not valid_clips:
            logger.error(
                f"[StoryPipeline:{self.story_id}] No valid clips found in story "
                f"librar{'ies' if len(self.library_ids) > 1 else 'y'} "
                f"{', '.join(self.library_ids) or '(none)'}."
            )
            return []

        pool = _usable_clip_pool(valid_clips)
        target_duration = audio_duration + 0.25

        run_length = successor = None
        long_takes = bool(self._edit and self._edit.long_takes)
        if long_takes:
            rng = random.Random(self.story_id)
            in_pool = {path for path, _dur in pool}
            by_key = {meta: path for path, meta in self._clip_meta.items() if path in in_pool}

            def run_length() -> int:
                return self._edit.run_length(rng)

            def successor(path: str) -> str | None:
                meta = self._clip_meta.get(path)
                return by_key.get((meta[0], meta[1] + 1)) if meta else None

        self._raise_if_cancel_requested()
        try:
            selection = get_clip_usage_ledger().select(
                self.story_id,
                pool,
                lambda path: clip_keys.get(path) or f"path:{path}",
                target_duration,
                None,  # khong cat clip: tinh du do dai tung clip
                run_length=run_length,
                successor=successor,
            )
        except Exception as exc:
            from src.db import mongo

            raise RuntimeError(
                f"Che do moi clip 1 lan khong doc duoc so lan dung clip: {exc}. "
                f"{mongo.unavailable_message()}"
            ) from exc

        if long_takes:
            self._edit.run_starts = selection.run_starts
        self.progress["clipUsage"] = selection.stats()
        logger.info(
            f"[StoryPipeline:{self.story_id}] Selected {len(selection.paths)} clips (once mode: "
            f"{selection.fresh} fresh, {selection.reused} reused, max use {selection.max_count}"
            f"{f', {len(selection.runs)} shots' if long_takes else ''}) from pool={len(pool)}, "
            f"audio={audio_duration:.1f}s"
        )
        return selection.paths

    def _commit_clip_usage(self, final_path: str) -> None:
        """Chi goi o che do once, sau khi video da xong. Khong bao gio raise."""
        try:
            result = get_clip_usage_ledger().commit(self.story_id, {
                "library_ids": list(self.library_ids),
                "batch_id": self.output_subdir or None,
                "output_path": storage_relative_path(final_path),
            })
            usage = self.progress.get("clipUsage")
            if isinstance(usage, dict):
                usage["committed"] = result.get("committed", 0)
                if result.get("queued"):
                    usage["queued"] = result["queued"]
        except Exception as exc:  # pragma: no cover - commit() already swallows
            logger.warning(f"[StoryPipeline:{self.story_id}] Clip usage commit failed: {exc}")

    def _select_long_takes(self, pool, target_duration: float, audio_duration: float) -> list[str]:
        """Runs of 1-3 consecutive clips of one source: seamless 3/6/9s shots.

        The segment muxer cuts a source into contiguous pieces (``..._clip_000``,
        ``_001``...), so playing a clip and its successors back to back is one
        continuous shot. Clips can't be trimmed instead: library clips carry
        B-frames and a mid-clip concat ``outpoint`` loses frames with stream copy.
        """
        in_pool = {path for path, _dur in pool}
        by_key = {meta: path for path, meta in self._clip_meta.items() if path in in_pool}

        def successor(path: str) -> str | None:
            meta = self._clip_meta.get(path)
            return by_key.get((meta[0], meta[1] + 1)) if meta else None

        rng = random.Random(self.story_id)
        durations = dict(pool)
        selected: list[str] = []
        run_starts: list[int] = []
        runs: list[int] = []
        total = 0.0
        if self.clip_bag is not None:
            key = SharedClipBag.pool_key(self.library_ids, self.clip_tags)
            picked: set[str] = set()
            while total < target_duration:
                self._raise_if_cancel_requested()
                run = self.clip_bag.draw_run(key, pool, self._edit.run_length(rng), successor, exclude=picked)
                run_starts.append(len(selected))
                for path, dur in run:
                    picked.add(path)
                    selected.append(path)
                    total += dur
                runs.append(len(run))
        else:
            deck = list(pool)
            rng.shuffle(deck)
            used: set[str] = set()
            while total < target_duration:
                self._raise_if_cancel_requested()
                seed = next((item for item in deck if item[0] not in used), None)
                if seed is None:  # pool exhausted: start over
                    used.clear()
                    continue
                run = [seed[0]]
                want = self._edit.run_length(rng)
                nxt = successor(seed[0])
                while len(run) < want and nxt and nxt not in used:
                    run.append(nxt)
                    nxt = successor(nxt)
                run_starts.append(len(selected))
                for path in run:
                    used.add(path)
                    selected.append(path)
                    total += durations.get(path, 0.0)
                runs.append(len(run))
        # Visual cuts are only where a new shot starts; their real times are
        # measured on the base once it exists (_probe_clip_starts).
        self._edit.run_starts = run_starts
        logger.info(
            f"[StoryPipeline:{self.story_id}] Long takes: {len(selected)} clips in {len(runs)} shots "
            f"(1/2/3-clip: {runs.count(1)}/{runs.count(2)}/{runs.count(3)}), audio={audio_duration:.1f}s"
        )
        return selected

    def _probe_clip_starts(self, base_video: str, clip_count: int, durations: list[float] | None = None) -> list[float]:
        """Real start time of each clip in the concatenated base.

        Every library clip opens on a keyframe, so the base's keyframe packets are
        the clip boundaries (read from the container, no decode: <1s for 10 min).
        If the count doesn't match (a clip with inner keyframes), fall back to the
        running sum of the clips' own lengths (``durations``) -- clips differ in
        length now -- or, without them, spread the clips evenly.
        """
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
                 "-of", "csv=p=0", base_video],
                capture_output=True, text=True, errors="replace", timeout=120,
            )
            keys = []
            for line in res.stdout.splitlines():
                pts, _, flags = line.partition(",")
                if "K" in flags:
                    try:
                        keys.append(float(pts))
                    except ValueError:
                        continue
            keys.sort()
            if len(keys) == clip_count:
                expected = sum(durations[:-1]) if durations and len(durations) == clip_count else None
                logger.info(
                    f"[StoryPipeline:{self.story_id}] Clip starts from {len(keys)} keyframes, "
                    f"last at {keys[-1]:.2f}s"
                    + (f" (sum of clip lengths {expected:.2f}s)" if expected is not None else "")
                )
                return keys
            logger.info(
                f"[StoryPipeline:{self.story_id}] {len(keys)} keyframes for {clip_count} clips, "
                + ("using the clips' own lengths" if durations else "spreading clip starts evenly")
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(f"[StoryPipeline:{self.story_id}] Keyframe probe failed: {exc}")
        if durations and len(durations) == clip_count and all(value > 0 for value in durations):
            starts, position = [], 0.0
            for value in durations:
                starts.append(position)
                position += float(value)
            return starts
        duration = FFmpegHelper.probe_duration(base_video) or clip_count * float(self._segment_duration())
        period = duration / max(1, clip_count)
        return [i * period for i in range(clip_count)]

    def _build_edit_plan(self, audio_path: str, audio_duration: float):
        """Resolve the layout + modifiers and build everything they need (static art,
        voice bars, chapters). A failure here degrades to the plain layout: an edit
        style must never cost a render."""
        from src.utils.edit_styles.runtime import build_plan
        from src.utils.story_library import any_fully_baked_library

        try:
            plan = build_plan(
                self.layout_id, self.modifier_ids, story_id=self.story_id, total=audio_duration,
                clip_seconds=self._segment_duration(), temp_dir=_temp_dir(self.story_id),
            )
            if not plan:
                return None
            sub_cues = []
            if self.subtitle_path:
                from src.utils.story_subtitles import parse_srt, resegment

                try:
                    sub_cues = resegment(parse_srt(self.subtitle_path), self.subtitle_max_chars_per_line,
                                         self.subtitle_max_lines)
                except Exception:  # noqa: BLE001 - chapters degrade to even splits
                    sub_cues = []
            plan.prepare(
                audio_path=audio_path, subtitle_path=self.subtitle_path, chapters_path=self.chapters_path,
                sub_cues=sub_cues, fully_baked=any_fully_baked_library(self.library_ids),
            )
            logger.info(
                f"[StoryPipeline:{self.story_id}] Edit style: layout={plan.layout_type or 'plain'} "
                f"modifiers={sorted(plan.modifiers)} chapters={len(plan.chapters)}"
                + (f" skipped={plan.skipped}" if plan.skipped else "")
            )
            return plan
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[StoryPipeline:{self.story_id}] Edit style failed, rendering plain: {exc}",
                         exc_info=True)
            return None

    def _render_simple_video(self, segments: list[str], audio_path: str, audio_duration: float) -> str | None:
        """Concat prebuilt library clips and mux the main audio.

        No per-clip ``outpoint``: every clip plays its full length (a library can mix
        8s video cuts and 3-5s photo clips), and only the tail is cut by ``-t`` to the
        audio. That also means a clip is never trimmed mid-GOP, which with B-frames
        and stream copy used to drop frames at the trim point.
        """
        temp = _temp_dir(self.story_id)
        output_dir = os.path.join(_story_dir(self.story_id), "renders")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"video_{self.story_id}.mp4")
        concat_file = os.path.join(temp, "story_segments.txt")

        with open(concat_file, "w", encoding="utf-8") as file_obj:
            for segment_path in segments:
                clean_path = os.path.abspath(segment_path).replace("\\", "/").replace("'", "'\\''")
                file_obj.write(f"file '{clean_path}'\n")

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-i",
            audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-b:a",
            "192k",
            "-t",
            str(audio_duration),
            output_path,
        ]

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "render_video",
                35 + (float(ffmpeg_percent) * 0.55),
                "Dang ghep clip voi audio...",
            )

        ok = FFmpegHelper.run_command(
            cmd,
            progress_callback=_progress,
            progress_total_seconds=audio_duration,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if ok and os.path.isfile(output_path):
            return output_path
        return None

    def _prepare_subtitle_ass(self, audio_duration: float) -> str | None:
        """Build the burn-in .ass file from the uploaded .srt subtitle."""
        from src.utils.story_subtitles import (
            SubtitleParseError,
            build_ass,
            parse_srt,
            resegment,
            write_ass_file,
        )

        try:
            # No SRT but a layout with its own text layer (OSD, chapter bar): the
            # ASS then carries only that layer.
            cues = parse_srt(self.subtitle_path) if self.subtitle_path else []
            cues = resegment(
                cues,
                max_chars_per_line=self.subtitle_max_chars_per_line,
                max_lines=self.subtitle_max_lines,
            )
        except SubtitleParseError as exc:
            logger.error(f"[StoryPipeline:{self.story_id}] Subtitle parse failed: {exc}")
            self._update_progress(
                "story_overlays",
                90,
                "File subtitle (.srt) khong hop le.",
                status="failed",
                error=f"Invalid subtitle file: {exc}",
            )
            return None

        clamped: list[dict] = []
        for cue in cues:
            start = float(cue.get("start", 0) or 0)
            end = min(float(cue.get("end", 0) or 0), float(audio_duration))
            if start >= audio_duration or end <= start:
                continue
            clamped.append({**cue, "start": start, "end": end})
        if not clamped and self.subtitle_path:
            logger.warning(
                f"[StoryPipeline:{self.story_id}] No subtitle cues fall within the audio duration."
            )

        overrides = dict(self.subtitle_style_overrides or {})
        if self._edit:
            # Layout placement wins over the subtitle style: a letterbox moves the
            # text into its bottom bar whatever colour the rotation picked.
            overrides.update(self._edit.subtitle_overrides())
        # A layout may set the subtitle font too (handwriting on the polaroid border).
        layout_font = overrides.pop("fontFamily", None)
        width, height = (int(value) for value in Config.TARGET_RESOLUTION.split("x", 1))
        ass_text = build_ass(
            clamped,
            font_family=layout_font or self.subtitle_font or Config.STORY_SUBTITLE_DEFAULT_FONT,
            preset_id=self.subtitle_preset or "clean",
            play_res=(width, height),
            style_overrides=overrides or None,
        )
        if self._edit:
            ass_text = self._edit.transform_subtitles(ass_text)
            ass_text = _append_ass_layers(ass_text, *self._edit.ass_layers())
        ass_path = os.path.abspath(
            os.path.join(_temp_dir(self.story_id), f"subs_{self.story_id}.ass")
        )
        write_ass_file(ass_text, ass_path)
        logger.info(f"[StoryPipeline:{self.story_id}] Subtitle ASS ready: {ass_path}")
        return ass_path

    def _apply_default_waveform_overlay(self, current_video: str, audio_duration: float) -> str | None:
        """Overlay the globally configured preprocessed waveform, if available."""
        from src.utils.waveform_overlays import (
            get_default_waveform_overlay,
            overlay_position_expr,
            processed_abs_path,
        )

        record = get_default_waveform_overlay()
        if not record:
            logger.info(f"[StoryPipeline:{self.story_id}] No default waveform overlay configured.")
            return current_video

        waveform_path = processed_abs_path(record)
        if not waveform_path:
            logger.warning(f"[StoryPipeline:{self.story_id}] Default waveform processed file is missing.")
            return current_video

        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"waveform_overlay_{self.story_id}.mp4")
        x_expr, y_expr = overlay_position_expr(record)
        filter_str = (
            "[1:v]setpts=PTS-STARTPTS[wave];"
            f"[0:v][wave]overlay={x_expr}:{y_expr}:format=auto,format=yuv420p[v]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            current_video,
            "-stream_loop",
            "-1",
            "-i",
            waveform_path,
            "-filter_complex",
            filter_str,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            str(audio_duration),
        ]
        cmd.extend(FFmpegHelper.get_nvenc_flags())
        # No +faststart: this is an intermediate the next ffmpeg step reads, and
        # faststart costs a full extra read+write pass over a multi-GB file.
        cmd.extend([
            "-c:a",
            "copy",
            output_path,
        ])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "waveform_overlay",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ap dung song am...",
            )

        ok = _run_overlay_ffmpeg(
            cmd,
            progress_callback=_progress,
            progress_total_seconds=audio_duration,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if ok and os.path.isfile(output_path):
            return output_path

        self._raise_if_cancel_requested()
        logger.error(f"[StoryPipeline:{self.story_id}] Failed to apply waveform overlay.")
        self._update_progress(
            "waveform_overlay",
            90,
            "Ap dung song am that bai.",
            status="failed",
            error="Waveform overlay failed.",
        )
        return None

    def _tv_effect_filter(self) -> str:
        """Filter chain for the selected 1990s TV effect style (empty when disabled).

        Pre-baked "styled" libraries already have the style burned into every
        clip, so the style pass is skipped to avoid double-styling (and to take
        the much cheaper overlay-only render path). `skip_tv_effect` asks for the
        same cheap path on a library that was never baked. A selection mixing
        baked and unbaked libraries follows the baked profile: styling the whole
        video would style the baked clips twice.
        """
        from src.processors.crt_effect_processor import get_tv_effect_filter
        from src.utils.story_library import any_styled_library

        if self.skip_tv_effect:
            logger.info(
                f"[StoryPipeline:{self.story_id}] TV style disabled for this render; "
                f"skipping the style pass."
            )
            return ""

        if any_styled_library(self.library_ids):
            logger.info(
                f"[StoryPipeline:{self.story_id}] Styled library selected; skipping TV style pass."
            )
            return ""

        return get_tv_effect_filter(self.tv_effect_style_id or None)

    @staticmethod
    def _hwaccel_flags() -> list:
        """NVDEC decode flags for the base video input (CPU fallback handled by caller)."""
        return ["-hwaccel", "cuda"] if Config.USE_GPU_NVENC else []

    def _ass_filter_suffix(self, ass_path: str = "") -> str:
        """Subtitle burn-in snippet ("ass=<path>,") chained right before the final format=yuv420p.

        Defaults to the story's subtitle .ass; pass `ass_path` to burn a per-segment
        rebased .ass instead (parallel-segment overlay path)."""
        path = ass_path or self._subtitle_ass_path
        if not path:
            return ""
        from src.utils.story_subtitles import _list_font_files, ass_filter_path

        ass_value = f"ass={ass_filter_path(path)}"
        if _list_font_files(Config.STORY_FONTS_DIR):
            ass_value += f":fontsdir={ass_filter_path(Config.STORY_FONTS_DIR)}"
        return f"{ass_value},"

    def _gpu_overlay_enabled(self) -> bool:
        """Whether the overlay pass may use the GPU (overlay_cuda) pipeline.

        Requires the feature flag, NVENC enabled, and an FFmpeg build that actually
        exposes the CUDA overlay filters. Callers additionally exclude passes that
        need CPU-only filters (screen blend, CPU TV style)."""
        return (
            Config.OVERLAY_USE_GPU_PIPELINE
            and Config.USE_GPU_NVENC
            and FFmpegHelper.cuda_overlay_available()
        )

    def _resolve_decor_layer(self):
        """(record, keyed PNG path) for this story's decor image, or None."""
        if not self.decor_image_id:
            return None
        from src.utils.story_decor_images import resolve_decor_image

        resolved = resolve_decor_image(self.decor_image_id)
        if not resolved:
            logger.warning(
                f"[StoryPipeline:{self.story_id}] Decor image {self.decor_image_id} "
                f"is unusable; rendering without it."
            )
        return resolved

    def _resolve_edit_decor(self):
        """(record, PNG) of the frame the video is fitted into, or (None, None).

        Without an edit style this is the decor image as before. With one, the
        layout decides: TV layouts use the dealt decor image (tv_glass with its
        glass reflection), the film frame supplies a synthetic frame, and every
        other layout draws no decor even if an id is present.
        """
        decor_layer = self._resolve_decor_layer()
        decor_record, decor_path = decor_layer if decor_layer else (None, None)
        edit = self._edit
        if not edit or not edit.layout:
            return decor_record, decor_path
        if edit.decor_record_override:
            return edit.decor_record_override, edit.decor_png_override
        if not edit.requires_decor:
            return None, None
        if not decor_record:
            logger.warning(
                f"[StoryPipeline:{self.story_id}] Layout {edit.layout_type} needs a decor image; "
                f"none usable, rendering without the frame."
            )
            return None, None
        edit.use_decor(decor_record, decor_path)
        return decor_record, edit.decor_png_override or decor_path

    @staticmethod
    def _overlay_input_indices(tv_noise_paths, decor_path, waveform_path, cta_path, edit_inputs: int = 0) -> dict:
        """Input slots for the overlay pass, shared by the CPU and GPU commands.

        Order is base, tv-noise..., decor, edit-style inputs..., waveform, cta — the
        same order both commands add their `-i` flags in, so the filter labels line
        up whichever path runs."""
        next_index = 1 + len(tv_noise_paths)
        indices = {"decor": None, "edit": None, "waveform": None, "cta": None}
        if decor_path:
            indices["decor"] = next_index
            next_index += 1
        if edit_inputs:
            indices["edit"] = next_index
            next_index += edit_inputs
        for key, path in (("waveform", waveform_path), ("cta", cta_path)):
            if path:
                indices[key] = next_index
                next_index += 1
        indices["next"] = next_index
        return indices

    # ------------------------------------------------------------------ #
    # Edit-style hooks shared by the CPU chain and the GPU builder
    # ------------------------------------------------------------------ #
    def _edit_input_count(self) -> int:
        return self._edit.input_count() if self._edit else 0

    def _edit_input_args(self, ss) -> list:
        args: list = []
        if self._edit:
            for extra in self._edit.video_inputs(ss):
                args.extend(extra)
        return args

    def _waveform_input_args(self, waveform_path: str, ss) -> list:
        """Looped waveform clip; voice bars (drawn from this audio) seek with the segment instead."""
        if self._edit:
            override = self._edit.wave_override(None, ("0", "0"), ss)
            if override and override.get("input"):
                return list(override["input"])
        return ["-stream_loop", "-1", "-i", waveform_path]

    def _edit_video_parts(self, gpu: bool, chain_label: str, indices: dict, ss) -> tuple[list, str]:
        if not self._edit:
            return [], chain_label
        from src.utils.edit_styles.graph import Ops

        return self._edit.video_parts(Ops(gpu), chain_label, indices.get("edit") or 0, ss)

    def _wave_cta_parts(self, gpu: bool, chain_label: str, indices: dict, waveform_path, waveform_record,
                        cta_path, cta_record, ss) -> tuple[list, str]:
        """Waveform then CTA, at the record's placement unless the edit style moves or times them."""
        from src.utils.edit_styles.graph import Ops
        from src.utils.story_cta_overlay import overlay_position_expr as cta_position_expr
        from src.utils.waveform_overlays import overlay_position_expr

        ops = Ops(gpu)
        parts: list = []
        if waveform_path and indices.get("waveform") is not None:
            default = overlay_position_expr(waveform_record) if waveform_record else ("40", "820")
            place = {"x": default[0], "y": default[1], "dynamic": False}
            if self._edit:
                place = self._edit.wave_override(waveform_record, default, ss) or place
            parts.append(ops.upload(indices["waveform"], "wave"))
            parts.append(ops.overlay(chain_label, "[wave]", "[waveout]", place["x"], place["y"],
                                     enable=place.get("enable"), dynamic=place.get("dynamic", False)))
            chain_label = "[waveout]"
        if cta_path and cta_record and indices.get("cta") is not None:
            default = cta_position_expr(cta_record)
            place = {"x": default[0], "y": default[1], "dynamic": False, "enable": None}
            if self._edit:
                place = self._edit.cta_override(cta_record, default, ss)
            parts.append(ops.upload(indices["cta"], "cta"))
            parts.append(ops.overlay(chain_label, "[cta]", "[ctaout]", place["x"], place["y"],
                                     enable=place.get("enable"), dynamic=place.get("dynamic", False)))
            chain_label = "[ctaout]"
        return parts, chain_label

    def _filter_graph_args(self, graph: str, tag) -> list[str]:
        """``-filter_complex`` inline, or from a file when the graph is long.

        Per-clip expressions (film strip, album freezes, cut flashes) grow with the
        video; Windows caps a command line at 32767 characters, so past a safe size
        the graph goes through FFmpeg's ``-/filter_complex <file>``.
        """
        if len(graph) < 12000:
            return ["-filter_complex", graph]
        path = os.path.join(_temp_dir(self.story_id), f"graph_{tag}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(graph)
        return ["-/filter_complex", path]

    def _gpu_overlay_tail(self, chain_label: str, ass_path: str = "") -> str:
        """Closing filter node for a GPU overlay chain.

        With subtitles we must drop back to system memory (no CUDA ass filter):
        hwdownload -> burn ass on CPU -> yuv420p. Without subtitles the frames stay
        on the GPU (scale_cuda=format=yuv420p) and feed h264_nvenc directly."""
        ass_suffix = self._ass_filter_suffix(ass_path)
        if ass_suffix:
            return f"{chain_label}hwdownload,format=yuv420p,{ass_suffix}format=yuv420p[v]"
        return f"{chain_label}scale_cuda=format=yuv420p[v]"

    def _build_story_overlays_cpu_cmd(
        self,
        current_video: str,
        output_path: str,
        audio_duration: float,
        tv_noise_paths: list,
        waveform_path,
        waveform_record,
        cta_path,
        cta_record,
        *,
        decor_path=None,
        decor_record=None,
        style_filter: str = "",
        ss: float | None = None,
        ass_path: str = "",
        with_audio: bool = True,
    ) -> list:
        """CPU (software overlay) variant of the direct overlay chain.

        Same input order as the GPU builder, and the same optional piece arguments
        (``ss``/``ass_path``/``with_audio``), so a timeline can render some pieces on
        the GPU and the ones needing per-frame scaling here, then concatenate them.
        """
        from src.utils.story_tv_noise_overlays import overlay_blend_mode

        hwaccel_flags = self._hwaccel_flags()
        cmd = ["ffmpeg", "-y", *hwaccel_flags]
        if ss is not None:
            cmd.extend(["-ss", f"{ss:.3f}"])
        cmd.extend(["-i", current_video])

        for _record, overlay_path in tv_noise_paths:
            cmd.extend(["-stream_loop", "-1", "-i", overlay_path])

        indices = self._overlay_input_indices(
            tv_noise_paths, decor_path, waveform_path, cta_path, self._edit_input_count()
        )
        if decor_path:
            from src.utils.story_decor_images import decor_input_args

            cmd.extend(decor_input_args(decor_path))
        cmd.extend(self._edit_input_args(ss))
        if waveform_path:
            cmd.extend(self._waveform_input_args(waveform_path, ss))
        if cta_path:
            cmd.extend(["-stream_loop", "-1", "-i", cta_path])

        filter_parts: list[str] = []
        chain_label = "[0:v]"
        if style_filter:
            filter_parts.append(f"[0:v]{style_filter}[styled]")
            chain_label = "[styled]"
        for index, (record, _overlay_path) in enumerate(tv_noise_paths):
            input_index = index + 1
            noise_label = f"tvnoise{index}"
            out_label = f"tvnoiseout{index}"
            if overlay_blend_mode(record) == "screen":
                opacity = max(
                    0.0,
                    min(1.0, float(record.get("opacity") or Config.STORY_TV_NOISE_OPACITY)),
                )
                # blend needs matching pixel formats on both inputs.
                filter_parts.append(
                    f"[{input_index}:v]setpts=PTS-STARTPTS,format=yuv420p[{noise_label}]"
                )
                filter_parts.append(f"{chain_label}format=yuv420p[{noise_label}base]")
                filter_parts.append(
                    f"[{noise_label}base][{noise_label}]"
                    f"blend=all_mode=screen:all_opacity={opacity}:eof_action=repeat[{out_label}]"
                )
            else:
                filter_parts.append(f"[{input_index}:v]setpts=PTS-STARTPTS[{noise_label}]")
                filter_parts.append(
                    f"{chain_label}[{noise_label}]overlay=0:0:format=auto:eof_action=repeat:eval=init[{out_label}]"
                )
            chain_label = f"[{out_label}]"

        # Decor sits between the two halves of the stack: everything above lands
        # inside the screen, everything below goes on top of the photo.
        if decor_path and decor_record and indices["decor"] is not None:
            from src.utils.story_decor_images import decor_filter_parts

            decor_parts, chain_label = decor_filter_parts(
                chain_label, decor_record, indices["decor"]
            )
            filter_parts.extend(decor_parts)

        edit_parts, chain_label = self._edit_video_parts(False, chain_label, indices, ss)
        filter_parts.extend(edit_parts)
        wave_cta_parts, chain_label = self._wave_cta_parts(
            False, chain_label, indices, waveform_path, waveform_record, cta_path, cta_record, ss
        )
        filter_parts.extend(wave_cta_parts)

        filter_parts.append(f"{chain_label}{self._ass_filter_suffix(ass_path)}format=yuv420p[v]")

        cmd.extend([
            *self._filter_graph_args(";".join(filter_parts), f"cpu_{ss if ss is not None else 0}"),
            "-map",
            "[v]",
        ])
        if with_audio:
            cmd.extend(["-map", "0:a?"])
        cmd.extend(["-t", str(audio_duration)])
        cmd.extend(FFmpegHelper.get_overlay_nvenc_flags())
        cmd.extend(["-c:a", "copy"] if with_audio else ["-an"])
        cmd.append(output_path)
        return cmd


    def _build_story_overlays_gpu_cmd(
        self,
        current_video: str,
        output_path: str,
        audio_duration: float,
        tv_noise_paths: list,
        waveform_path,
        waveform_record,
        cta_path,
        cta_record,
        *,
        decor_path=None,
        decor_record=None,
        ss: float | None = None,
        ass_path: str = "",
        with_audio: bool = True,
    ) -> list:
        """GPU (overlay_cuda) variant of the direct overlay chain.

        Input order mirrors the CPU command (base, tv-noise..., decor, waveform, cta)
        so the filter input indices line up. Only alpha overlays reach here —
        screen-blend noise and CPU TV style keep the CPU path (see caller eligibility
        check), as does a decor frame that needs a crop (no CUDA crop/pad filter).

        For a parallel time-segment, pass `ss` (input seek start), a rebased `ass_path`,
        and `with_audio=False` (audio is muxed back once after concatenation)."""
        from src.utils.story_decor_images import decor_fit_geometry, decor_input_args

        fps = max(1, int(Config.TARGET_FPS))
        cmd = ["ffmpeg", "-y", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        if ss is not None:
            cmd.extend(["-ss", str(ss)])
        cmd.extend(["-i", current_video])
        for _record, overlay_path in tv_noise_paths:
            cmd.extend(["-stream_loop", "-1", "-i", overlay_path])

        indices = self._overlay_input_indices(
            tv_noise_paths, decor_path, waveform_path, cta_path, self._edit_input_count()
        )
        if decor_path:
            cmd.extend(decor_input_args(decor_path))
        cmd.extend(self._edit_input_args(ss))
        if waveform_path:
            cmd.extend(self._waveform_input_args(waveform_path, ss))
        if cta_path:
            cmd.extend(["-stream_loop", "-1", "-i", cta_path])

        filter_parts = ["[0:v]scale_cuda=format=yuv420p[base]"]
        chain_label = "[base]"
        for index, (_record, _overlay_path) in enumerate(tv_noise_paths):
            input_index = index + 1
            noise_label = f"tvnoise{index}"
            out_label = f"tvnoiseout{index}"
            filter_parts.append(
                f"[{input_index}:v]setpts=PTS-STARTPTS,format=yuva420p,hwupload_cuda[{noise_label}]"
            )
            filter_parts.append(
                f"{chain_label}[{noise_label}]overlay_cuda=0:0:eof_action=repeat:eval=init[{out_label}]"
            )
            chain_label = f"[{out_label}]"

        if decor_path and decor_record:
            geo = decor_fit_geometry(decor_record)
            # The canvas the shrunk video lands on is a `split` of the chain, NOT a
            # black lavfi source pushed through hwupload_cuda. An uploaded source
            # carries its own CUDA hw frames context, and when a colour-tag change
            # between two concatenated library clips forces a mid-render filter
            # reconfigure, ffmpeg cannot bridge the two contexts: it tries to insert
            # a software auto_scale into the overlay_cuda overlay pad and the whole
            # GPU pass dies ("Impossible to convert ... Error reinitializing
            # filters!"), silently dropping the render onto the ~3x slower CPU
            # chain. Splitting keeps one context, so the reconfigure survives.
            filter_parts.append(
                f"[{indices['decor']}:v]setpts=PTS-STARTPTS,format=yuva420p,hwupload_cuda[decorimg]"
            )
            filter_parts.append(f"{chain_label}split=2[decorbg][decorsrc]")
            filter_parts.append(
                f"[decorsrc]scale_cuda={geo['fitW']}:{geo['fitH']}:format=yuv420p[decorfit]"
            )
            filter_parts.append(
                f"[decorbg][decorfit]overlay_cuda={geo['fitX']}:{geo['fitY']}:eval=init[decorframed]"
            )
            filter_parts.append(
                "[decorframed][decorimg]"
                "overlay_cuda=0:0:eof_action=repeat:eval=init[decorout]"
            )
            chain_label = "[decorout]"

        edit_parts, chain_label = self._edit_video_parts(True, chain_label, indices, ss)
        filter_parts.extend(edit_parts)
        wave_cta_parts, chain_label = self._wave_cta_parts(
            True, chain_label, indices, waveform_path, waveform_record, cta_path, cta_record, ss
        )
        filter_parts.extend(wave_cta_parts)

        filter_parts.append(self._gpu_overlay_tail(chain_label, ass_path))

        cmd.extend([*self._filter_graph_args(";".join(filter_parts), ss), "-map", "[v]"])
        if with_audio:
            cmd.extend(["-map", "0:a?"])
        cmd.extend(["-t", str(audio_duration)])
        # Overlay-pass encoder settings (OVERLAY_NVENC_PRESET / OVERLAY_OUTPUT_BITRATE):
        # fewer bytes written here also means fewer bytes re-read and re-written by the
        # segment concat, the audio mux and the final copy on the storage HDD.
        cmd.extend(FFmpegHelper.get_overlay_nvenc_flags())
        if with_audio:
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.append("-an")
        cmd.append(output_path)
        return cmd

    def _apply_story_overlays_gpu_segmented(
        self,
        current_video: str,
        audio_duration: float,
        tv_noise_paths: list,
        waveform_path,
        waveform_record,
        cta_path,
        cta_record,
        segments: int,
        decor_path=None,
        decor_record=None,
    ) -> str | None:
        """Run the GPU overlay+subtitle pass as N parallel time-segments, then concat.

        The single-threaded libass subtitle burn is the bottleneck while the GPU sits
        mostly idle; rendering several segments at once parallelises libass across CPU
        cores and fills the GPU. Each segment seeks the base (`-ss`) and burns its own
        rebased .ass; the video-only segments are concatenated and the base audio is
        muxed back once. Returns the overlay output path, or None to let the caller
        fall back to the single-pass overlay."""
        temp = _temp_dir(self.story_id)
        seg_dur = audio_duration / segments
        # Cut between cues rather than at exact even offsets: a boundary that lands
        # mid-cue makes that cue replay its animation in the next segment (see
        # `_snap_segment_boundary`). Segments are encoded separately and concatenated,
        # so uneven lengths cost nothing.
        spans = _ass_event_spans(self._subtitle_ass_path)
        bounds = [0.0]
        for i in range(1, segments):
            snapped = _snap_segment_boundary(spans, i * seg_dur, seg_dur * 0.25)
            # Keep boundaries strictly increasing so no segment can come out empty.
            bounds.append(min(max(snapped, bounds[-1] + 1.0), audio_duration - 1.0))
        bounds.append(audio_duration)

        seg_cmds: list[list] = []
        seg_outputs: list[str] = []
        for i in range(segments):
            start = bounds[i]
            dur = bounds[i + 1] - start
            seg_ass = os.path.join(temp, f"segsub_{i}_{self.story_id}.ass")
            _rebase_ass_file(self._subtitle_ass_path, start, dur, seg_ass)
            seg_out = os.path.join(temp, f"segpart_{i}_{self.story_id}.mp4")
            seg_cmds.append(
                self._build_story_overlays_gpu_cmd(
                    current_video, seg_out, dur,
                    tv_noise_paths, waveform_path, waveform_record, cta_path, cta_record,
                    decor_path=decor_path, decor_record=decor_record,
                    ss=start, ass_path=seg_ass, with_audio=False,
                )
            )
            seg_outputs.append(seg_out)

        self._update_progress(
            "story_overlays", 92,
            f"Dang ap dung overlay + phu de ({segments} luong song song)...",
        )

        results: list[bool] = [False] * segments

        def _worker(idx: int):
            results[idx] = _run_overlay_ffmpeg(
                seg_cmds[idx],
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(segments)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self._raise_if_cancel_requested()
        if not all(results) or not all(os.path.isfile(path) for path in seg_outputs):
            logger.warning(
                f"[StoryPipeline:{self.story_id}] A parallel overlay segment failed; "
                f"falling back to single-pass overlay."
            )
            return None

        output_path = self._concat_overlay_parts(seg_outputs, current_video, audio_duration)
        if output_path:
            logger.info(
                f"[StoryPipeline:{self.story_id}] Applied story overlays via {segments} parallel GPU segments."
            )
        return output_path

    def _concat_overlay_parts(self, parts: list[str], current_video: str, audio_duration: float) -> str | None:
        """Join the video-only parts (same encoder settings -> stream copy) and mux the audio back.

        No +faststart on the concat: it is only ever an input to the mux below and is
        then deleted, so it would cost a full extra read+write pass over a big file.
        """
        temp = _temp_dir(self.story_id)
        concat_list = os.path.join(temp, f"segconcat_{self.story_id}.txt")
        with open(concat_list, "w", encoding="utf-8") as handle:
            for path in parts:
                handle.write(f"file '{os.path.abspath(path).replace(os.sep, '/')}'\n")
        concat_video = os.path.join(temp, f"segvideo_{self.story_id}.mp4")
        ok = FFmpegHelper.run_command(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
             "-c", "copy", concat_video],
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if not ok or not os.path.isfile(concat_video):
            logger.warning(f"[StoryPipeline:{self.story_id}] Overlay part concat failed.")
            return None

        output_path = os.path.join(temp, f"story_overlays_{self.story_id}.mp4")
        ok = FFmpegHelper.run_command(
            ["ffmpeg", "-y", "-i", concat_video, "-i", current_video,
             "-map", "0:v:0", "-map", "1:a:0?", "-c", "copy", "-t", str(audio_duration),
             output_path],
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if not ok or not os.path.isfile(output_path):
            logger.warning(f"[StoryPipeline:{self.story_id}] Overlay part audio mux failed.")
            return None
        return output_path

    def _apply_story_overlays_timeline(
        self,
        current_video: str,
        audio_duration: float,
        tv_noise_paths: list,
        waveform_path,
        waveform_record,
        cta_path,
        cta_record,
        pieces: list,
        decor_path=None,
        decor_record=None,
        style_filter: str = "",
        use_gpu: bool = True,
    ) -> str | None:
        """Render a layout whose shape changes over time, piece by piece, then concat.

        Resting states are ordinary GPU passes; a piece that resizes the picture every
        frame runs on the CPU chain (scale_cuda sizes are fixed at init). Each piece
        seeks the base and burns its own rebased .ass, exactly like the parallel
        segments do. Returns None to let the caller fall back to a single pass.
        """
        temp = _temp_dir(self.story_id)
        cmds: list[list] = []
        outputs: list[str] = []
        for index, piece in enumerate(pieces):
            start = float(piece["start"])
            duration = float(piece["end"]) - start
            if duration <= 0.05:
                continue
            # The plan answers for the piece currently being built (single-threaded here).
            self._edit.piece = piece
            piece_ass = ""
            if self._subtitle_ass_path:
                piece_ass = os.path.join(temp, f"piecesub_{index}_{self.story_id}.ass")
                _rebase_ass_file(self._subtitle_ass_path, start, duration, piece_ass)
            piece_decor = (decor_path, decor_record) if self._edit.piece_uses_decor() else (None, None)
            out = os.path.join(temp, f"piecepart_{index}_{self.story_id}.mp4")
            gpu_piece = bool(piece.get("gpu", True)) and use_gpu and not style_filter
            builder = self._build_story_overlays_gpu_cmd if gpu_piece else self._build_story_overlays_cpu_cmd
            extra = {} if gpu_piece else {"style_filter": style_filter}
            cmds.append(builder(
                current_video, out, duration, tv_noise_paths, waveform_path, waveform_record, cta_path,
                cta_record, decor_path=piece_decor[0], decor_record=piece_decor[1], ss=start,
                ass_path=piece_ass, with_audio=False, **extra,
            ))
            outputs.append(out)
        self._edit.piece = None
        if not cmds:
            return None

        self._update_progress(
            "story_overlays", 92,
            f"Dang ap dung overlay + phu de ({len(cmds)} doan bo cuc)...",
        )
        results: list[bool] = [False] * len(cmds)
        workers = max(1, int(Config.OVERLAY_PARALLEL_SEGMENTS))

        def _worker(idx: int):
            results[idx] = _run_overlay_ffmpeg(
                cmds[idx], cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_worker, range(len(cmds))))

        self._raise_if_cancel_requested()
        if not all(results) or not all(os.path.isfile(path) for path in outputs):
            logger.warning(
                f"[StoryPipeline:{self.story_id}] A timeline piece failed; falling back to a single pass."
            )
            return None
        output_path = self._concat_overlay_parts(outputs, current_video, audio_duration)
        if output_path:
            logger.info(
                f"[StoryPipeline:{self.story_id}] Applied story overlays via {len(cmds)} timeline pieces."
            )
        return output_path

    def _apply_story_overlays(self, current_video: str, audio_duration: float) -> str | None:
        """Overlay TV noise layers and the configured waveform in a single FFmpeg pass."""
        from src.utils.story_library import any_fully_baked_library

        decor_record, decor_path = self._resolve_edit_decor()
        edit_video = bool(self._edit and self._edit.has_video_work)
        fully_baked = any_fully_baked_library(self.library_ids)

        # Fully-baked libraries already have style + waveform + CTA burned into the
        # clips, so the only remaining work is subtitle burn-in (the fastest path).
        # A selection mixing one in follows the same path — overlaying again would
        # stack a second waveform/CTA on the baked clips.
        # A decor frame (or any layout that reshapes the picture) has to composite,
        # so it cannot take that shortcut; the routes reject the layouts that would
        # shrink the baked waveform/CTA into a smaller frame.
        if fully_baked and not decor_path and not edit_video:
            if self._subtitle_ass_path:
                logger.info(
                    f"[StoryPipeline:{self.story_id}] Fully-baked library; subtitle-only pass."
                )
                return self._apply_subtitles_only(current_video, audio_duration)
            logger.info(
                f"[StoryPipeline:{self.story_id}] Fully-baked library, no subtitle; using rendered video as-is."
            )
            return current_video

        from src.utils.story_cta_overlay import (
            get_active_cta_overlay,
            get_cta_overlay,
            processed_abs_path as cta_processed_abs_path,
        )
        from src.utils.story_overlay_packs import get_or_create_story_overlay_pack
        from src.utils.story_tv_noise_overlays import (
            get_active_tv_noise_overlays,
            overlay_blend_mode,
            processed_abs_path as tv_noise_processed_abs_path,
        )
        from src.utils.waveform_overlays import (
            get_default_waveform_overlay,
            get_waveform_overlay,
            processed_abs_path as waveform_processed_abs_path,
        )

        tv_noise_records = get_active_tv_noise_overlays()

        waveform_record = get_waveform_overlay(self.waveform_overlay_id)
        if not waveform_record:
            waveform_record = get_default_waveform_overlay()

        waveform_path = waveform_processed_abs_path(waveform_record) if waveform_record else None
        if waveform_record and not waveform_path:
            logger.warning(f"[StoryPipeline:{self.story_id}] Waveform processed file is missing.")
            waveform_record = None

        # Batch gan CTA cho tung video theo vong xoay; id khong tra cuu duoc thi
        # ve lai CTA dang bat o trang cau hinh, giong cach waveform xu ly o tren.
        cta_record = get_cta_overlay(self.cta_overlay_id)
        if not cta_record:
            cta_record = get_active_cta_overlay()
        cta_path = cta_processed_abs_path(cta_record) if cta_record else None
        if cta_record and not cta_path:
            logger.warning(f"[StoryPipeline:{self.story_id}] CTA processed file is missing.")
            cta_record = None

        if fully_baked:
            # Waveform and CTA are already burned into the baked clips.
            waveform_record = waveform_path = cta_record = cta_path = None
        elif self._edit and self._edit.assets.get("voice") and not waveform_path:
            # Voice bars need no waveform record: they replace it, or stand alone.
            waveform_path = self._edit.assets["voice"]

        tv_noise_paths: list[tuple[dict, str]] = []
        for record in tv_noise_records:
            processed_path = tv_noise_processed_abs_path(record)
            if processed_path:
                tv_noise_paths.append((record, processed_path))

        style_filter = self._tv_effect_filter()

        if not tv_noise_paths and not waveform_path and not cta_path and not decor_path and not edit_video:
            if style_filter:
                return self._apply_tv_effect_only(current_video, audio_duration, style_filter)
            if self._subtitle_ass_path:
                return self._apply_subtitles_only(current_video, audio_duration)
            logger.info(f"[StoryPipeline:{self.story_id}] No story overlays configured.")
            return current_video

        # Precompose only when there are full-frame noise layers to merge. With
        # just the small waveform, a direct overlay is much cheaper than
        # blending a full-frame alpha pack every frame (~4% vs 100% of pixels).
        # Screen-blend overlays cannot be premerged into an alpha pack (screen
        # math needs the underlying video), so their presence forces the
        # direct-chain path.
        has_screen_noise = any(
            overlay_blend_mode(record) == "screen" for record, _path in tv_noise_paths
        )
        # A decor frame splits the stack in two — noise belongs inside the screen,
        # waveform/CTA outside on the photo — so the single premerged alpha pack no
        # longer describes it. Direct chain only. Same for any edit style: the pack
        # bakes waveform/CTA at their record positions, which a layout moves.
        pack_path = None
        if tv_noise_paths and not has_screen_noise and not decor_path and not self._edit:
            pack_path = get_or_create_story_overlay_pack(
                tv_noise_paths,
                waveform_record if waveform_path else None,
                waveform_path,
                cta_record if cta_path else None,
                cta_path,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
        self._raise_if_cancel_requested()
        if pack_path:
            packed_output = self._apply_precomposed_story_overlay(
                current_video, audio_duration, pack_path, style_filter
            )
            self._raise_if_cancel_requested()
            if packed_output:
                return packed_output
            logger.warning(
                f"[StoryPipeline:{self.story_id}] Precomposed overlay pack failed; falling back to direct overlays."
            )

        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"story_overlays_{self.story_id}.mp4")
        hwaccel_flags = self._hwaccel_flags()
        cmd = self._build_story_overlays_cpu_cmd(
            current_video, output_path, audio_duration, tv_noise_paths, waveform_path, waveform_record,
            cta_path, cta_record, decor_path=decor_path, decor_record=decor_record, style_filter=style_filter,
        )
        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "story_overlays",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ap dung TV noise va song am...",
            )

        # A decor frame whose rectangle isn't the source aspect needs crop+pad, and
        # neither has a CUDA counterpart — that case stays on the CPU chain.
        decor_gpu_ok = True
        if decor_record:
            from src.utils.story_decor_images import decor_fit_geometry

            decor_gpu_ok = not decor_fit_geometry(decor_record)["needsCrop"]
            if not decor_gpu_ok:
                logger.info(
                    f"[StoryPipeline:{self.story_id}] Decor frame is off-aspect "
                    f"(needs crop); using the CPU overlay chain."
                )

        use_gpu = (
            self._gpu_overlay_enabled()
            and not has_screen_noise
            and not style_filter
            and decor_gpu_ok
        )
        ok = False

        # A layout that changes shape over time renders as its own pieces.
        pieces = self._edit.timeline() if self._edit else None
        if pieces:
            timeline_output = self._apply_story_overlays_timeline(
                current_video, audio_duration, tv_noise_paths, waveform_path, waveform_record,
                cta_path, cta_record, pieces, decor_path=decor_path, decor_record=decor_record,
                style_filter=style_filter, use_gpu=use_gpu,
            )
            if timeline_output and os.path.isfile(timeline_output):
                return timeline_output
            self._raise_if_cancel_requested()

        # Parallel-segment GPU path: only worthwhile when a subtitle burn (the
        # single-threaded libass bottleneck) is present on a long-enough clip.
        segments = max(1, int(Config.OVERLAY_PARALLEL_SEGMENTS))
        if (
            use_gpu
            and self._subtitle_ass_path
            and segments > 1
            and audio_duration >= Config.OVERLAY_SEGMENT_MIN_SECONDS
        ):
            seg_output = self._apply_story_overlays_gpu_segmented(
                current_video,
                audio_duration,
                tv_noise_paths,
                waveform_path,
                waveform_record,
                cta_path,
                cta_record,
                segments,
                decor_path=decor_path,
                decor_record=decor_record,
            )
            if seg_output and os.path.isfile(seg_output):
                return seg_output
            self._raise_if_cancel_requested()
            # segmented path bailed; continue to the single-pass overlay below

        if use_gpu:
            gpu_cmd = self._build_story_overlays_gpu_cmd(
                current_video,
                output_path,
                audio_duration,
                tv_noise_paths,
                waveform_path,
                waveform_record,
                cta_path,
                cta_record,
                decor_path=decor_path,
                decor_record=decor_record,
            )
            logger.info(
                f"[StoryPipeline:{self.story_id}] Applying story overlays on GPU (overlay_cuda)."
            )
            ok = _run_overlay_ffmpeg(
                gpu_cmd,
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
            if not ok:
                self._raise_if_cancel_requested()
                logger.warning(
                    f"[StoryPipeline:{self.story_id}] GPU overlay pass failed; falling back to CPU overlay."
                )

        if not ok:
            ok = _run_overlay_ffmpeg(
                cmd,
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
            if not ok and hwaccel_flags:
                self._raise_if_cancel_requested()
                logger.warning(
                    f"[StoryPipeline:{self.story_id}] CUDA decode failed for overlay pass; retrying with CPU decode."
                )
                ok = _run_overlay_ffmpeg(
                    cmd[:2] + cmd[2 + len(hwaccel_flags):],
                    progress_callback=_progress,
                    progress_total_seconds=audio_duration,
                    cancel_callback=lambda: is_story_cancel_requested(self.story_id),
                )
        if ok and os.path.isfile(output_path):
            return output_path

        self._raise_if_cancel_requested()
        logger.error(f"[StoryPipeline:{self.story_id}] Failed to apply story overlays.")
        self._update_progress(
            "story_overlays",
            90,
            "Ap dung TV noise/song am that bai.",
            status="failed",
            error="Story overlay pass failed.",
        )
        return None

    def _build_pack_overlay_gpu_cmd(
        self,
        current_video: str,
        output_path: str,
        audio_duration: float,
        pack_path: str,
    ) -> list:
        """GPU (overlay_cuda) variant of the precomposed alpha-pack overlay.

        Only reached when there is no CPU TV style filter (see caller). The pack is
        a single alpha layer composited on the base with overlay_cuda."""
        fps = max(1, int(Config.TARGET_FPS))
        cmd = [
            "ffmpeg",
            "-y",
            "-hwaccel",
            "cuda",
            "-hwaccel_output_format",
            "cuda",
            "-i",
            current_video,
            "-stream_loop",
            "-1",
            "-i",
            pack_path,
        ]
        filter_parts = [
            "[0:v]scale_cuda=format=yuv420p[base]",
            f"[1:v]setpts=N/{fps}/TB,format=yuva420p,hwupload_cuda[pack]",
            "[base][pack]overlay_cuda=0:0:eof_action=repeat:eval=init[packed]",
        ]
        filter_parts.append(self._gpu_overlay_tail("[packed]"))
        cmd.extend([
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            str(audio_duration),
        ])
        cmd.extend(FFmpegHelper.get_overlay_nvenc_flags())
        cmd.extend(["-c:a", "copy", output_path])
        return cmd

    def _apply_precomposed_story_overlay(
        self,
        current_video: str,
        audio_duration: float,
        pack_path: str,
        style_filter: str = "",
    ) -> str | None:
        """Overlay a cached precomposed alpha pack in a single FFmpeg overlay layer."""
        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"story_overlay_pack_{self.story_id}.mp4")
        fps = max(1, int(Config.TARGET_FPS))
        base_chain = "setpts=PTS-STARTPTS"
        if style_filter:
            base_chain = f"{base_chain},{style_filter}"
        filter_str = (
            f"[0:v]{base_chain},format=yuv420p[base];"
            f"[1:v]setpts=N/{fps}/TB[pack];"
            "[base][pack]overlay=0:0:format=auto:eof_action=repeat:eval=init[packed];"
            f"[packed]{self._ass_filter_suffix()}format=yuv420p[v]"
        )
        hwaccel_flags = self._hwaccel_flags()
        cmd = [
            "ffmpeg",
            "-y",
            *hwaccel_flags,
            "-i",
            current_video,
            "-stream_loop",
            "-1",
            "-i",
            pack_path,
            "-filter_complex",
            filter_str,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            str(audio_duration),
        ]
        cmd.extend(FFmpegHelper.get_overlay_nvenc_flags())
        cmd.extend([
            "-c:a",
            "copy",
            output_path,
        ])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "story_overlays",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ap dung overlay pack...",
            )

        use_gpu = self._gpu_overlay_enabled() and not style_filter
        ok = False
        if use_gpu:
            gpu_cmd = self._build_pack_overlay_gpu_cmd(
                current_video, output_path, audio_duration, pack_path
            )
            logger.info(
                f"[StoryPipeline:{self.story_id}] Applying overlay pack on GPU (overlay_cuda)."
            )
            ok = _run_overlay_ffmpeg(
                gpu_cmd,
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
            if not ok:
                self._raise_if_cancel_requested()
                logger.warning(
                    f"[StoryPipeline:{self.story_id}] GPU pack overlay failed; falling back to CPU overlay."
                )

        if not ok:
            ok = _run_overlay_ffmpeg(
                cmd,
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
            if not ok and hwaccel_flags:
                self._raise_if_cancel_requested()
                logger.warning(
                    f"[StoryPipeline:{self.story_id}] CUDA decode failed for pack overlay; retrying with CPU decode."
                )
                ok = _run_overlay_ffmpeg(
                    cmd[:2] + cmd[2 + len(hwaccel_flags):],
                    progress_callback=_progress,
                    progress_total_seconds=audio_duration,
                    cancel_callback=lambda: is_story_cancel_requested(self.story_id),
                )
        if ok and os.path.isfile(output_path):
            logger.info(f"[StoryPipeline:{self.story_id}] Applied precomposed overlay pack: {pack_path}")
            return output_path
        return None

    def _apply_tv_effect_only(
        self,
        current_video: str,
        audio_duration: float,
        style_filter: str,
    ) -> str | None:
        """Apply the selected TV effect style when no overlays are configured."""
        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"story_tv_effect_{self.story_id}.mp4")
        hwaccel_flags = self._hwaccel_flags()
        cmd = [
            "ffmpeg",
            "-y",
            *hwaccel_flags,
            "-i",
            current_video,
            "-vf",
            f"{style_filter},{self._ass_filter_suffix()}format=yuv420p",
            "-t",
            str(audio_duration),
        ]
        cmd.extend(FFmpegHelper.get_overlay_nvenc_flags())
        cmd.extend([
            "-c:a",
            "copy",
            output_path,
        ])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "story_overlays",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ap dung hieu ung TV...",
            )

        ok = _run_overlay_ffmpeg(
            cmd,
            progress_callback=_progress,
            progress_total_seconds=audio_duration,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if not ok and hwaccel_flags:
            self._raise_if_cancel_requested()
            logger.warning(
                f"[StoryPipeline:{self.story_id}] CUDA decode failed for TV effect pass; retrying with CPU decode."
            )
            ok = _run_overlay_ffmpeg(
                cmd[:2] + cmd[2 + len(hwaccel_flags):],
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
        if ok and os.path.isfile(output_path):
            return output_path

        self._raise_if_cancel_requested()
        logger.error(f"[StoryPipeline:{self.story_id}] Failed to apply TV effect style.")
        self._update_progress(
            "story_overlays",
            90,
            "Ap dung hieu ung TV that bai.",
            status="failed",
            error="TV effect pass failed.",
        )
        return None

    def _apply_subtitles_only(
        self,
        current_video: str,
        audio_duration: float,
    ) -> str | None:
        """Burn subtitles when no overlays/styles are configured (base video is stream-copied)."""
        temp = _temp_dir(self.story_id)
        output_path = os.path.join(temp, f"story_subtitles_{self.story_id}.mp4")
        hwaccel_flags = self._hwaccel_flags()
        cmd = [
            "ffmpeg",
            "-y",
            *hwaccel_flags,
            "-i",
            current_video,
            "-vf",
            f"{self._ass_filter_suffix()}format=yuv420p",
            "-t",
            str(audio_duration),
        ]
        cmd.extend(FFmpegHelper.get_overlay_nvenc_flags())
        cmd.extend([
            "-c:a",
            "copy",
            output_path,
        ])

        def _progress(payload: dict):
            ffmpeg_percent = payload.get("ffmpegPercent")
            if ffmpeg_percent is None:
                return
            self._update_progress(
                "story_overlays",
                90 + (float(ffmpeg_percent) * 0.08),
                "Dang ghi phu de vao video...",
            )

        ok = _run_overlay_ffmpeg(
            cmd,
            progress_callback=_progress,
            progress_total_seconds=audio_duration,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if not ok and hwaccel_flags:
            self._raise_if_cancel_requested()
            logger.warning(
                f"[StoryPipeline:{self.story_id}] CUDA decode failed for subtitle pass; retrying with CPU decode."
            )
            ok = _run_overlay_ffmpeg(
                cmd[:2] + cmd[2 + len(hwaccel_flags):],
                progress_callback=_progress,
                progress_total_seconds=audio_duration,
                cancel_callback=lambda: is_story_cancel_requested(self.story_id),
            )
        if ok and os.path.isfile(output_path):
            return output_path

        self._raise_if_cancel_requested()
        logger.error(f"[StoryPipeline:{self.story_id}] Failed to burn subtitles.")
        self._update_progress(
            "story_overlays",
            90,
            "Ghi phu de vao video that bai.",
            status="failed",
            error="Subtitle burn-in pass failed.",
        )
        return None

    def _prepend_intro(self, video_path: str, audio_duration: float) -> str:
        """Prepend the batch intro to the finished video.

        The intro was normalized to the pipeline's canonical spec on upload, so the
        fast path is a concat-demuxer *stream copy* (I/O only, no re-encode). If that
        yields a bad/short file (e.g. a codec-parameter mismatch the demuxer can't
        copy across), fall back to a concat *filter* re-encode which is robust to any
        source. On total failure the un-prefixed video is returned unchanged.
        """
        if not self.intro_video_path or not os.path.isfile(self.intro_video_path):
            return video_path

        temp = _temp_dir(self.story_id)
        intro_duration = FFmpegHelper.probe_duration(self.intro_video_path)
        expected_min = max(0.0, audio_duration + intro_duration - 1.0)

        # Fast path: concat demuxer, stream copy.
        concat_file = os.path.join(temp, "intro_concat.txt")
        with open(concat_file, "w", encoding="utf-8") as file_obj:
            for path in (self.intro_video_path, video_path):
                clean = os.path.abspath(path).replace("\\", "/").replace("'", "'\\''")
                file_obj.write(f"file '{clean}'\n")

        copy_out = os.path.join(temp, f"with_intro_{self.story_id}.mp4")
        copy_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c", "copy", "-movflags", "+faststart",
            copy_out,
        ]
        ok = FFmpegHelper.run_command(
            copy_cmd,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if ok and os.path.isfile(copy_out) and FFmpegHelper.probe_duration(copy_out) >= expected_min:
            return copy_out

        logger.warning(
            f"[StoryPipeline:{self.story_id}] Intro stream-copy concat failed or produced a "
            f"short file; falling back to re-encode."
        )

        # Fallback: concat filter, re-encode (robust to any intro params).
        reencode_out = os.path.join(temp, f"with_intro_reencode_{self.story_id}.mp4")
        reencode_cmd = [
            "ffmpeg", "-y",
            "-i", self.intro_video_path,
            "-i", video_path,
            "-filter_complex",
            "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
        ]
        reencode_cmd.extend(FFmpegHelper.get_nvenc_flags())
        reencode_cmd.extend([
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
            "-movflags", "+faststart",
            reencode_out,
        ])
        ok = FFmpegHelper.run_command(
            reencode_cmd,
            cancel_callback=lambda: is_story_cancel_requested(self.story_id),
        )
        if ok and os.path.isfile(reencode_out):
            return reencode_out

        logger.error(
            f"[StoryPipeline:{self.story_id}] Could not prepend intro; using video without intro."
        )
        return video_path

    def _finalize(self, current_video: str) -> str:
        """Remux the rendered video to final output and purge the per-story cache dir.

        The story dir (temp/, renders/, uploaded originals) is pure working cache;
        the only thing that must survive is progress.json for status polling, and
        that gets rewritten right after this call by the "completed" progress update,
        which recreates the dir via _story_dir()'s makedirs.

        This is also the single place +faststart is applied. Delivery used to be a raw
        shutil.copy2, so every intermediate had to carry faststart itself just in case
        it turned out to be the file that got copied out -- and a byte copy preserves
        moov placement, so that was the only way to ship a progressive file. Paying it
        here instead costs nothing extra (the copy already read and wrote the whole
        file), and it lets every stage inside the story dir drop faststart. Each one of
        those was a full extra read+write pass over a multi-GB file on the storage disk,
        which is the measured bottleneck of this pipeline.
        """
        safe_name = self.output_name.strip() if self.output_name else f"story_{self.story_id}"
        safe_name = Path(safe_name).stem
        if not safe_name:
            safe_name = f"story_{self.story_id}"

        output_dir = _output_dir(self.output_subdir)
        final_path = os.path.join(output_dir, f"{safe_name}.mp4")

        counter = 1
        while os.path.isfile(final_path):
            final_path = os.path.join(output_dir, f"{safe_name}_{counter}.mp4")
            counter += 1

        ok = FFmpegHelper.run_command([
            "ffmpeg", "-y", "-i", current_video,
            "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
            "-movflags", "+faststart", final_path,
        ])
        if not ok or not os.path.isfile(final_path):
            # Never fail a finished render over the moov position: ship it non-progressive.
            logger.warning(
                f"[StoryPipeline:{self.story_id}] faststart remux failed; "
                f"falling back to a raw copy (output will not be progressive)."
            )
            shutil.copy2(current_video, final_path)
        logger.info(f"[StoryPipeline:{self.story_id}] Final output: {final_path}")

        story_dir = _story_dir(self.story_id)
        try:
            shutil.rmtree(story_dir, ignore_errors=True)
        except OSError:
            pass

        return final_path


class StoryVideoCancelled(RuntimeError):
    """Raised when a Story Video cancellation marker is observed."""
