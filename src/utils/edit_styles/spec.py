"""Catalogue of edit styles: what each type is and which settings it takes.

Two groups:

- ``layout``: one per video, dealt out across a batch by ``deal_rotation``.
- ``modifier``: ticked ones apply to every video of the batch, on top of the layout.

Every field has a type the settings page knows how to draw (``number``, ``int``,
``color``, ``bool``, ``select``, ``text``, ``font``, ``rect``, ``point``, ``list``)
plus its default and bounds. ``sanitize_params`` clamps whatever the page sends
back against this spec, the same way ``sanitize_tv_effect_params`` does for the
TV effect sliders, so a stored record can always be rendered.

``labRatio`` is the render time measured against the current layout on the
10-minute sample (tests/test-render/README.md, pass 3).
"""

from __future__ import annotations

import copy
import re

W, H = 1920, 1080


def _f(key, label, ftype, default, group="", **extra):
    return {"key": key, "label": label, "type": ftype, "default": default, "group": group, **extra}


def num(key, label, default, lo, hi, step=0.01, group=""):
    return _f(key, label, "number", default, group, min=lo, max=hi, step=step)


def integer(key, label, default, lo, hi, step=1, group=""):
    return _f(key, label, "int", default, group, min=lo, max=hi, step=step)


def color(key, label, default, group=""):
    return _f(key, label, "color", default, group)


def boolean(key, label, default, group=""):
    return _f(key, label, "bool", default, group)


def select(key, label, default, options, group=""):
    return _f(key, label, "select", default, group, options=[{"value": v, "label": lbl} for v, lbl in options])


def text(key, label, default, group="", max_len=120):
    return _f(key, label, "text", default, group, maxLength=max_len)


def font(key, label, default, group=""):
    return _f(key, label, "font", default, group)


def rect(key, label, default, group="", aspect=None):
    return _f(key, label, "rect", default, group, aspect=aspect)


def point(key, label, default, group="", size=None):
    return _f(key, label, "point", default, group, size=size)


def str_list(key, label, default, group="", max_items=24):
    return _f(key, label, "list", default, group, maxItems=max_items)


def image(key, label, group=""):
    """An uploaded picture (path under STORY_EDIT_STYLE_DIR/images); None = the procedural default."""
    return _f(key, label, "image", None, group)


def image_list(key, label, group="", max_items=6):
    return _f(key, label, "imageList", [], group, maxItems=max_items)


# --------------------------------------------------------------------------- #
# Fields every layout carries: where waveform/CTA sit and how subtitles fit.
# A point left empty (None) keeps the position stored on the waveform/CTA record.
# --------------------------------------------------------------------------- #
def _placement_fields(sub_font_size=0, sub_margin_v=0, sub_force=False, sub_text="#FFFFFF",
                      sub_outline_w=-1):
    return [
        point("wavePlacement", "Vị trí sóng âm", None, "Vị trí", size=[420, 236]),
        point("ctaPlacement", "Vị trí CTA", None, "Vị trí", size=[360, 202]),
        integer("subFontSize", "Cỡ chữ phụ đề (0 = theo kiểu phụ đề)", sub_font_size, 0, 120, group="Phụ đề"),
        integer("subMarginV", "Lề dưới phụ đề (0 = mặc định)", sub_margin_v, 0, 600, group="Phụ đề"),
        integer("subMarginLR", "Lề trái/phải phụ đề (0 = mặc định)", 0, 0, 800, group="Phụ đề"),
        boolean("subForceColors", "Ép màu phụ đề theo bố cục", sub_force, "Phụ đề"),
        color("subTextColor", "Màu chữ phụ đề khi ép", sub_text, "Phụ đề"),
        color("subOutlineColor", "Màu viền phụ đề khi ép", "#000000", "Phụ đề"),
        integer("subOutlineWidth", "Độ dày viền phụ đề khi ép (-1 = theo kiểu phụ đề)", sub_outline_w, -1, 10,
                group="Phụ đề"),
    ]


def _chapter_fields():
    return [
        text("autoTitle", "Tiêu đề chương khi không có file chương (trống = không hiện chữ chương)", "",
             "Chương"),
        num("autoChapterMinutes", "Chia chương tự động mỗi (phút)", 2.0, 0.5, 15, 0.5, "Chương"),
    ]


def _corner_fields(group="Góc ngắm"):
    return [
        boolean("corners", "Hiện 4 góc ngắm", True, group),
        integer("cornerInset", "Cách mép (px)", 60, 10, 240, group=group),
        integer("cornerArm", "Độ dài cạnh (px)", 70, 20, 240, group=group),
        integer("cornerThickness", "Độ dày (px)", 4, 1, 16, group=group),
        num("cornerOpacity", "Độ đậm", 0.8, 0.1, 1.0, 0.05, group),
    ]


def _window_border_fields(group="Viền cửa sổ"):
    """Blurred room + ring + shadow around the TV screen hole, same look as
    "Hai lớp cùng nguồn".

    Drawn on top of the decor image, so the border replaces whatever the image
    itself was baked with and the blur adds to its own; 0 and no shadow leave
    the decor image as it is.
    """
    return [
        num("bgBlur", "Độ mờ ảnh nền khung (px, chỉ ảnh khung tự vẽ; 0 = theo ảnh decor)", 12, 0, 40, 1, "Nền"),
        integer("borderWidth", "Độ dày viền (0 = theo ảnh decor)", 6, 0, 20, group=group),
        color("borderColor", "Màu viền", "#F5F0E6", group),
        boolean("shadow", "Đổ bóng", True, group),
    ]


_SCAN_DIRECTIONS = [("down", "Trên → xuống"), ("up", "Dưới → lên"), ("right", "Trái → phải"),
                    ("left", "Phải → trái")]

# Light effects (phase 4): the sweep reuses the four straight directions and adds
# slanted ones, back-and-forth and a per-video draw.
SWEEP_DIRECTIONS = _SCAN_DIRECTIONS + [
    ("diag_right", "Chéo, trái → phải"), ("diag_left", "Chéo, phải → trái"),
    ("bounce_v", "Qua lại trên ↔ dưới"), ("bounce_h", "Qua lại trái ↔ phải"),
    ("random", "Ngẫu nhiên mỗi video (6 hướng một chiều)"),
]
SWEEP_PROFILES = [
    ("soft", "Mềm — mép mờ dần"), ("flat", "Dải đều — kiểu camera an ninh"),
    ("laser", "Tia mảnh + quầng sáng"), ("double", "Hai vệt — ánh kính"),
    ("trail", "Đuôi sao chổi (hướng qua lại dùng vệt mềm)"), ("prism", "Cầu vồng lăng kính (bỏ qua màu)"),
]
_EVENT_RHYTHMS = [("interval", "Mỗi N giây một lượt"), ("cuts", "Ở điểm cắt clip (mỗi N lần cắt)"),
                  ("paragraph", "Đầu mỗi đoạn SRT"), ("chapter", "Đầu mỗi chương")]
LEAK_PALETTES = [("amber", "Hổ phách ấm"), ("rose", "Hồng đào"), ("gold", "Vàng nắng"), ("fire", "Lửa cam đỏ"),
                 ("teal", "Xanh ngọc lạnh"), ("violet", "Tím mộng mơ"), ("random", "Ngẫu nhiên mỗi video")]


def _rhythm_fields(default, every, every_cuts, loop=False, group="Nhịp"):
    options = ([("loop", "Liên tục — hết lượt này tới lượt khác")] if loop else []) + _EVENT_RHYTHMS
    return [
        select("rhythm", "Khi nào xuất hiện", default, options, group),
        num("everySeconds", "N giây (khi chọn mỗi N giây)", every, 2, 180, 0.5, group),
        integer("everyNCuts", "N lần cắt (khi chọn điểm cắt)", every_cuts, 1, 20, group=group),
    ]


def _sweep_preset(name, **params):
    return {"name": name, "params": params}


TYPES: list[dict] = [
    # ------------------------------------------------------------- layouts
    {
        "id": "plain", "group": "layout", "phase": 1, "labRatio": 1.00,
        "name": "Không khung", "description": "Clip phủ kín khung hình, như bố cục hiện tại.",
        "fields": _placement_fields(),
    },
    {
        "id": "tv_frame", "group": "layout", "phase": 1, "labRatio": 1.08, "requiresDecor": True,
        "shrinksFrame": True,
        "name": "Khung TV (decor)", "description": "Video chạy trong màn hình của ảnh decor đã chọn.",
        "fields": _window_border_fields() + _placement_fields(),
    },
    {
        "id": "tv_glass", "group": "layout", "phase": 1, "labRatio": 1.07, "requiresDecor": True,
        "shrinksFrame": True,
        "name": "Khung TV + kính phản chiếu",
        "description": "Như khung TV, thêm vệt sáng chéo mờ trên mặt kính màn hình.",
        "fields": [
            integer("glareStrength", "Độ sáng vệt chính", 26, 0, 60, group="Kính"),
            integer("glareSecondary", "Độ sáng vệt phụ", 14, 0, 60, group="Kính"),
            num("glareSlope", "Độ nghiêng vệt", 0.55, 0.1, 1.5, 0.05, "Kính"),
            num("glareWidth", "Bề rộng vệt", 0.10, 0.03, 0.30, 0.01, "Kính"),
            integer("topSheen", "Ánh sáng mép trên", 10, 0, 40, group="Kính"),
        ] + _window_border_fields() + _placement_fields(),
    },
    {
        "id": "tv_drift", "group": "layout", "phase": 1, "labRatio": 1.14, "requiresDecor": True,
        "shrinksFrame": True,
        "name": "Khung TV + máy quay trôi",
        "description": "Cả căn phòng phóng nhẹ và trôi rất chậm, ảnh decor không còn đứng im.",
        "fields": [
            num("zoom", "Mức phóng", 1.05, 1.02, 1.15, 0.01, "Chuyển động"),
            num("amplitude", "Biên độ trôi (tỉ lệ phần dư)", 0.9, 0.1, 1.0, 0.05, "Chuyển động"),
            integer("periodX", "Chu kỳ ngang (giây)", 41, 10, 180, group="Chuyển động"),
            integer("periodY", "Chu kỳ dọc (giây)", 53, 10, 180, group="Chuyển động"),
        ] + _window_border_fields() + _placement_fields(),
    },
    {
        "id": "card", "group": "layout", "phase": 1, "labRatio": 1.16, "shrinksFrame": True,
        "name": "Thẻ nổi nền mờ",
        "description": "Clip thu nhỏ thành thẻ bo góc, nền là chính clip làm mờ và tối.",
        "fields": [
            rect("rect", "Vị trí thẻ", {"x": 240, "y": 50, "w": 1440, "h": 810}, "Thẻ", aspect=16 / 9),
            integer("radius", "Bo góc", 16, 0, 48, group="Thẻ"),
            integer("borderWidth", "Độ dày viền", 8, 0, 16, group="Thẻ"),
            color("borderColor", "Màu viền", "#F5F0E6", "Thẻ"),
            boolean("shadow", "Đổ bóng", True, "Thẻ"),
            integer("shadowStrength", "Độ đậm bóng", 200, 0, 255, group="Thẻ"),
            num("dim", "Độ tối nền", 0.38, 0.0, 0.8, 0.02, "Nền"),
            select("blur", "Độ mờ nền", "medium", [("light", "Nhẹ"), ("medium", "Vừa"), ("strong", "Mạnh")], "Nền"),
            boolean("bob", "Thẻ nhấp nhô", True, "Chuyển động"),
            integer("bobAmplitude", "Biên độ nhấp nhô (px)", 10, 0, 40, group="Chuyển động"),
            integer("bobPeriod", "Chu kỳ nhấp nhô (giây)", 8, 3, 40, group="Chuyển động"),
        ] + _placement_fields(sub_margin_v=40),
    },
    {
        "id": "letterbox", "group": "layout", "phase": 1, "labRatio": 0.82, "needsChapters": True,
        "name": "Letterbox điện ảnh",
        "description": "Hai dải đen trên dưới; tên chương ở dải trên; phụ đề và thanh tiến trình ở dải dưới.",
        "fields": [
            select("aspect", "Tỉ lệ khung", "2.39", [("2.39", "2.39:1 (dải 138px)"), ("2.2", "2.2:1 (dải 104px)"),
                                                  ("2.0", "2.0:1 (dải 60px)")], "Dải"),
            color("barColor", "Màu dải", "#08080A", "Dải"),
            color("ruleColor", "Màu đường kẻ", "#C8AA6E", "Dải"),
            integer("ruleWidth", "Độ dày đường kẻ", 2, 0, 6, group="Dải"),
            boolean("titleEnabled", "Hiện tên chương ở dải trên", True, "Tên chương"),
            text("titleFormat", "Mẫu tên chương ({n}, {total}, {title}, {label})", "{title}", "Tên chương"),
            font("titleFont", "Font tên chương", "Trirong", "Tên chương"),
            integer("titleSize", "Cỡ chữ tên chương", 34, 16, 72, group="Tên chương"),
            color("titleColor", "Màu tên chương", "#C8AA6E", "Tên chương"),
            integer("titleSpacing", "Giãn chữ", 5, 0, 16, group="Tên chương"),
            boolean("progressEnabled", "Thanh tiến trình", True, "Thanh tiến trình"),
            color("progressColor", "Màu thanh tiến trình", "#C8AA6E", "Thanh tiến trình"),
            integer("progressHeight", "Độ dày thanh tiến trình", 4, 2, 12, group="Thanh tiến trình"),
        ] + _chapter_fields() + _placement_fields(sub_font_size=44),
    },
    {
        "id": "film_frame", "group": "layout", "phase": 1, "labRatio": 1.09, "shrinksFrame": True,
        "name": "Khung cuộn phim",
        "description": "Khung sinh bằng thuật toán: cuộn phim, polaroid trên bàn, trang sổ tay hoặc tranh khung gỗ.",
        "fields": [
            select("variant", "Kiểu khung", "film", [("film", "Cuộn phim"), ("polaroid", "Polaroid trên bàn"),
                                                   ("notebook", "Trang sổ tay"), ("wood", "Tranh khung gỗ")],
                   "Khung"),
            rect("rect", "Vùng hình", {"x": 150, "y": 130, "w": 1620, "h": 820}, "Khung"),
            integer("radius", "Bo góc vùng hình (cuộn phim)", 18, 0, 48, group="Khung"),
            color("stripColor", "Màu dải phim / mặt bàn", "#12100E", "Khung"),
            color("frameColor", "Màu thẻ polaroid / giấy sổ / nền tranh", "#F4F1EA", "Khung"),
            color("woodColor", "Màu gỗ (tranh khung gỗ)", "#6B4226", "Khung"),
            color("lineColor", "Màu dòng kẻ (sổ tay)", "#9DB4D0", "Khung"),
            select("sprocketMode", "Lỗ răng cưa", "open", [("open", "Xuyên thấy hình"), ("solid", "Đặc")], "Khung"),
            text("edgeText", "Chữ in mép trên / chú thích polaroid", "KODAK 5219   ▸ 24", "Chữ mép"),
            text("edgeTextBottom", "Chữ in mép dưới (cuộn phim)", "▸ 25A", "Chữ mép"),
            color("edgeTextColor", "Màu chữ mép", "#E6A03C", "Chữ mép"),
        ] + _placement_fields(),
    },
    {
        "id": "osd_camcorder", "group": "layout", "phase": 1, "labRatio": 1.14,
        "name": "OSD máy quay gia đình",
        "description": "PLAY, REC nhấp nháy, pin, bộ đếm băng và 4 góc ngắm, vẽ bằng lớp phụ đề.",
        "fields": [
            boolean("showPlay", "Hiện chữ PLAY", True, "Thành phần"),
            text("playText", "Chữ PLAY", "PLAY ▶", "Thành phần", 40),
            boolean("showRec", "Hiện REC nhấp nháy", True, "Thành phần"),
            color("recColor", "Màu chấm REC", "#FF2020", "Thành phần"),
            num("blinkPeriod", "Chu kỳ nhấp nháy (giây)", 1.0, 0.5, 3.0, 0.1, "Thành phần"),
            boolean("showBattery", "Hiện pin (SP ▮▮▮)", True, "Thành phần"),
            boolean("showCounter", "Hiện bộ đếm băng", True, "Thành phần"),
            font("font", "Font", "Chakra Petch", "Chữ"),
            integer("size", "Cỡ chữ", 44, 20, 80, group="Chữ"),
            color("color", "Màu chữ", "#FFFFFF", "Chữ"),
        ] + _corner_fields() + _placement_fields(),
    },
    {
        "id": "osd_cctv", "group": "layout", "phase": 1, "labRatio": 1.15,
        "name": "OSD camera an ninh",
        "description": "Tên camera, REC, đồng hồ chạy, 4 góc và vệt quét sáng trượt dọc.",
        "fields": [
            str_list("cameraLabels", "Tên camera (mỗi video bốc 1)",
                     [f"CAM {i:02d}" for i in range(1, 9)], "Thành phần"),
            select("clockMode", "Đồng hồ", "counter", [("counter", "Bộ đếm từ 00:00:00"),
                                                         ("clock", "Giờ 24h từ mốc bắt đầu")], "Thành phần"),
            text("startTime", "Mốc bắt đầu (HH:MM:SS)", "21:47:12", "Thành phần", 8),
            color("recColor", "Màu chấm REC", "#FF2020", "Thành phần"),
            boolean("scanBand", "Vệt quét sáng", True, "Vệt quét"),
            select("scanDirection", "Hướng quét", "down", _SCAN_DIRECTIONS, "Vệt quét"),
            num("scanOpacity", "Độ sáng vệt quét", 0.07, 0.02, 0.3, 0.01, "Vệt quét"),
            integer("scanSpeed", "Tốc độ vệt quét (px/giây)", 140, 20, 400, group="Vệt quét"),
            font("font", "Font", "Chakra Petch", "Chữ"),
            integer("size", "Cỡ chữ", 44, 20, 80, group="Chữ"),
            color("color", "Màu chữ", "#FFFFFF", "Chữ"),
        ] + _corner_fields() + _placement_fields(),
    },
    {
        "id": "drift", "group": "layout", "phase": 1, "labRatio": 1.04,
        "name": "Trôi khung",
        "description": "Khung hình phóng nhẹ và trượt chậm, đổi hướng mỗi clip — stock footage trông như được quay.",
        "fields": [
            num("zoom", "Mức phóng", 1.10, 1.04, 1.20, 0.01, "Chuyển động"),
            num("amplitude", "Biên độ trượt (tỉ lệ phần dư)", 0.9, 0.1, 1.0, 0.05, "Chuyển động"),
            boolean("redirectEachClip", "Đổi hướng mỗi clip", True, "Chuyển động"),
        ] + _placement_fields(),
    },
    # ------------------------------------------------------------- layouts, phase 2
    {
        "id": "magazine", "group": "layout", "phase": 2, "labRatio": 1.12, "shrinksFrame": True,
        "needsChapters": True,
        "name": "Tạp chí chuyển động",
        "description": "Đầu mỗi chương, hình dịch sang một bên nhường chỗ cho cột chữ: số chương, tiêu đề, "
                       "câu trích. Cột đổi bên theo chương.",
        "fields": [
            integer("columnWidth", "Bề rộng cột (px)", 640, 400, 800, 16, "Cột"),
            color("columnColor", "Màu cột", "#121418", "Cột"),
            image("columnImage", "Ảnh nền cột (tuỳ chọn, phủ kín cột)", "Cột"),
            boolean("columnGrain", "Hạt nhiễu nhẹ trên cột", True, "Cột"),
            color("accentColor", "Màu nhấn (gạch, số chương)", "#C9A45C", "Cột"),
            select("firstSide", "Cột chương đầu nằm", "right", [("right", "Bên phải"), ("left", "Bên trái")], "Cột"),
            boolean("alternateSides", "Đổi bên mỗi chương", True, "Cột"),
            integer("columnSeconds", "Cột hiện bao lâu mỗi chương (giây)", 35, 8, 180, group="Thời gian"),
            num("slideSeconds", "Thời gian trượt (giây)", 0.7, 0.3, 2.0, 0.1, "Thời gian"),
            text("numFormat", "Mẫu số chương ({n}, {total}, {label})", "บทที่ {n}", "Chữ"),
            font("numFont", "Font số chương", "Kanit", "Chữ"),
            integer("numSize", "Cỡ số chương", 30, 16, 60, group="Chữ"),
            font("titleFont", "Font tiêu đề", "Trirong", "Chữ"),
            integer("titleSize", "Cỡ tiêu đề", 62, 30, 100, group="Chữ"),
            color("titleColor", "Màu tiêu đề", "#FFFFFF", "Chữ"),
            boolean("showQuote", "Hiện câu trích (cột 4 file chương, trống = câu phụ đề đầu chương)", True, "Chữ"),
            font("quoteFont", "Font câu trích", "Sarabun", "Chữ"),
            integer("quoteSize", "Cỡ câu trích", 36, 20, 60, group="Chữ"),
            color("quoteColor", "Màu câu trích", "#EDE6D6", "Chữ"),
        ] + _chapter_fields() + _placement_fields(),
    },
    {
        "id": "split_accent", "group": "layout", "phase": 2, "labRatio": 1.13, "shrinksFrame": True,
        "needsChapters": True,
        "name": "Chia màn hình điểm nhấn",
        "description": "Ở các điểm nhấn, một ô cận cảnh của chính clip trượt vào một bên (65/35) rồi rút đi.",
        "fields": [
            select("ratio", "Tỉ lệ chia", "65", [("60", "60 / 40"), ("65", "65 / 35"), ("70", "70 / 30")], "Chia"),
            select("panelSide", "Ô cận cảnh nằm", "right", [("right", "Bên phải"), ("left", "Bên trái")], "Chia"),
            num("zoom", "Độ phóng ô cận cảnh", 1.6, 1.2, 2.2, 0.1, "Chia"),
            select("focus", "Vùng phóng", "center", [("center", "Giữa hình"), ("left", "1/3 trái"),
                                                      ("right", "1/3 phải")], "Chia"),
            color("dividerColor", "Màu đường chia", "#F0A93B", "Chia"),
            integer("dividerWidth", "Độ dày đường chia", 4, 0, 16, group="Chia"),
            select("trigger", "Mở ô cận cảnh khi", "chapter",
                   [("chapter", "Đầu mỗi chương"), ("paragraph", "Đầu đoạn SRT"), ("interval", "Mỗi N giây"),
                    ("quote", "Câu nhấn trong file chương")], "Nhịp"),
            integer("everySeconds", "N giây (khi chọn mỗi N giây)", 60, 15, 600, group="Nhịp"),
            integer("minGapSeconds", "Khoảng tối thiểu giữa hai lần (giây)", 45, 10, 600, group="Nhịp"),
            num("holdSeconds", "Giữ ô cận cảnh (giây)", 7.0, 3.0, 30.0, 0.5, "Nhịp"),
            num("slideSeconds", "Thời gian trượt (giây)", 0.5, 0.2, 1.5, 0.1, "Nhịp"),
        ] + _chapter_fields() + _placement_fields(),
    },
    {
        "id": "dossier", "group": "layout", "phase": 2, "labRatio": 0.97, "shrinksFrame": True,
        "needsChapters": True,
        "name": "Hồ sơ kể chuyện",
        "description": "Video là tấm ảnh dán trên mặt giấy hồ sơ, có con dấu, số hồ sơ và ghi chú viết tay; "
                       "đổi chương thì một tờ giấy lướt qua.",
        "fields": [
            image("paperImage", "Ảnh nền giấy (tuỳ chọn; trống = giấy tự sinh)", "Nền"),
            color("paperColor", "Màu giấy tự sinh", "#D8C7A1", "Nền"),
            integer("paperGrain", "Độ sần giấy", 7, 0, 20, group="Nền"),
            rect("rect", "Vị trí ảnh (video)", {"x": 120, "y": 150, "w": 1152, "h": 648}, "Ảnh", aspect=16 / 9),
            color("photoBorderColor", "Màu viền ảnh", "#FAF8F0", "Ảnh"),
            integer("photoBorderWidth", "Độ dày viền ảnh", 14, 0, 40, group="Ảnh"),
            boolean("photoShadow", "Bóng đổ dưới ảnh", True, "Ảnh"),
            image_list("decorImages", "Ảnh trang trí ở góc ảnh (băng keo, ghim, kẹp — PNG nền trong)",
                       "Trang trí", max_items=4),
            boolean("autoTape", "Băng keo tự sinh khi không có ảnh trang trí", True, "Trang trí"),
            integer("decorSize", "Cỡ ảnh trang trí (px)", 180, 60, 400, group="Trang trí"),
            boolean("stampEnabled", "Con dấu", True, "Con dấu"),
            str_list("stampLabels", "Chữ con dấu khi chương không có nhãn (lần lượt theo chương)", ["หลักฐาน"],
                     "Con dấu", max_items=12),
            color("stampColor", "Màu con dấu", "#B02A24", "Con dấu"),
            font("stampFont", "Font con dấu", "Kanit", "Con dấu"),
            integer("stampSize", "Cỡ chữ con dấu", 46, 20, 90, group="Con dấu"),
            integer("stampAngle", "Góc nghiêng con dấu (độ)", 4, -25, 25, group="Con dấu"),
            boolean("notesEnabled", "Ghi chú bên cạnh ảnh", True, "Ghi chú"),
            boolean("ruledLines", "Kẻ dòng vùng ghi chú", True, "Ghi chú"),
            text("fileNumberFormat", "Mẫu số hồ sơ ({n}, {total})", "แฟ้มที่ {n}/{total}", "Ghi chú"),
            font("fileFont", "Font số hồ sơ", "Chakra Petch", "Ghi chú"),
            integer("fileSize", "Cỡ số hồ sơ", 30, 16, 60, group="Ghi chú"),
            font("noteFont", "Font ghi chú", "Sriracha", "Ghi chú"),
            integer("noteSize", "Cỡ ghi chú", 40, 20, 70, group="Ghi chú"),
            color("inkColor", "Màu mực", "#1F4A70", "Ghi chú"),
            boolean("noteQuote", "Ghi câu trích dưới tiêu đề", True, "Ghi chú"),
            boolean("sheetSweep", "Tờ giấy lướt qua khi đổi chương", True, "Chuyển chương"),
            num("sheetSeconds", "Thời gian lướt (giây)", 1.3, 0.6, 3.0, 0.1, "Chuyển chương"),
            font("subFont", "Font phụ đề", "Sarabun", "Phụ đề"),
        ] + _chapter_fields() + _placement_fields(sub_font_size=40, sub_force=True, sub_text="#1F2A40",
                                                  sub_outline_w=0),
    },
    {
        "id": "newsroom", "group": "layout", "phase": 2, "labRatio": 1.08, "shrinksFrame": True,
        "needsChapters": True,
        "name": "Bản tin chuyên đề",
        "description": "Thanh tiêu đề kiểu bản tin (nhãn + tên chương), hai ô hình ở đầu chương, "
                       "thẻ chuyển chương và thanh tiến trình.",
        "fields": [
            integer("ltY", "Vị trí dọc thanh tiêu đề (px)", 930, 560, 1030, group="Thanh tiêu đề"),
            integer("ltHeight", "Chiều cao thanh", 80, 50, 160, group="Thanh tiêu đề"),
            integer("ltWidth", "Chiều dài thanh", 1920, 600, 1920, 8, "Thanh tiêu đề"),
            color("ltColor", "Màu thanh", "#0E1D3A", "Thanh tiêu đề"),
            num("ltOpacity", "Độ đậm thanh", 0.92, 0.3, 1.0, 0.02, "Thanh tiêu đề"),
            color("ruleColor", "Màu gạch dưới thanh", "#C8102E", "Thanh tiêu đề"),
            integer("ruleHeight", "Độ dày gạch dưới", 6, 0, 16, group="Thanh tiêu đề"),
            text("tagText", "Chữ nhãn", "สารคดีอาชญากรรม", "Nhãn", 40),
            integer("tagWidth", "Bề rộng nhãn", 300, 120, 600, 4, "Nhãn"),
            color("tagColor", "Màu nền nhãn", "#C8102E", "Nhãn"),
            color("tagTextColor", "Màu chữ nhãn", "#FFFFFF", "Nhãn"),
            font("tagFont", "Font nhãn", "Kanit", "Nhãn"),
            integer("tagSize", "Cỡ chữ nhãn", 30, 16, 60, group="Nhãn"),
            text("titleFormat", "Mẫu tiêu đề ({n}, {total}, {title}, {label})", "บทที่ {n}  |  {title}", "Tiêu đề"),
            font("titleFont", "Font tiêu đề", "Kanit", "Tiêu đề"),
            integer("titleSize", "Cỡ tiêu đề", 38, 18, 70, group="Tiêu đề"),
            color("titleColor", "Màu tiêu đề", "#FFFFFF", "Tiêu đề"),
            boolean("twoBox", "Hai ô hình ở đầu chương", True, "Hai ô"),
            integer("twoBoxSeconds", "Giữ hai ô (giây)", 10, 3, 40, group="Hai ô"),
            rect("box1Rect", "Ô lớn", {"x": 60, "y": 170, "w": 1040, "h": 584}, "Hai ô"),
            rect("box2Rect", "Ô cận cảnh", {"x": 1140, "y": 170, "w": 720, "h": 584}, "Hai ô"),
            num("box2Zoom", "Độ phóng ô cận cảnh", 1.5, 1.1, 2.5, 0.1, "Hai ô"),
            color("boxBorderColor", "Màu viền ô", "#FFFFFF", "Hai ô"),
            color("bgColor", "Màu nền sau hai ô", "#0E1D3A", "Hai ô"),
            text("box2Label", "Nhãn ô cận cảnh", "ภาพขยาย", "Hai ô", 30),
            boolean("wipe", "Thẻ chuyển chương", True, "Chuyển chương"),
            color("wipeColor", "Màu thẻ chuyển chương", "#0E1D3A", "Chuyển chương"),
            num("wipeSeconds", "Thời gian thẻ (giây)", 1.4, 0.8, 3.0, 0.1, "Chuyển chương"),
            text("wipeLabelFormat", "Mẫu chữ trên thẻ", "บทที่ {n}", "Chuyển chương"),
            boolean("progressEnabled", "Thanh tiến trình", True, "Thanh tiến trình"),
            color("progressColor", "Màu thanh tiến trình", "#FFFFFF", "Thanh tiến trình"),
        ] + _chapter_fields() + _placement_fields(),
    },
    {
        "id": "album", "group": "layout", "phase": 2, "labRatio": 1.13, "shrinksFrame": True,
        "name": "Album ký ức",
        "description": "Video là tấm polaroid trên nền ảnh mờ ấm; đầu mỗi clip dừng hình một nhịp, bên cạnh "
                       "là chồng ảnh của các clip trước.",
        "fields": [
            image("backgroundImage", "Ảnh nền (tuỳ chọn; trống = khung hình của clip đầu tiên)", "Nền"),
            integer("bgBlur", "Độ mờ nền", 9, 0, 30, group="Nền"),
            num("bgBrightness", "Độ sáng nền", 0.55, 0.2, 1.0, 0.05, "Nền"),
            num("bgWarmth", "Độ ấm nền", 0.6, 0.0, 1.0, 0.05, "Nền"),
            boolean("bgVignette", "Tối viền", True, "Nền"),
            rect("rect", "Vị trí ảnh (video)", {"x": 170, "y": 96, "w": 1280, "h": 720}, "Polaroid", aspect=16 / 9),
            color("frameColor", "Màu thẻ polaroid", "#F6F3EC", "Polaroid"),
            integer("borderSides", "Viền trái/phải/trên", 28, 8, 80, group="Polaroid"),
            integer("borderBottom", "Viền dưới (chỗ chú thích)", 132, 40, 220, group="Polaroid"),
            num("stillSeconds", "Dừng hình đầu mỗi clip (giây, 0 = tắt)", 0.5, 0.0, 1.5, 0.1, "Chuyển động"),
            boolean("slideInOnParagraph", "Ảnh mới trượt vào khi đổi đoạn", True, "Chuyển động"),
            boolean("pile", "Chồng ảnh các clip trước", True, "Chồng ảnh"),
            integer("pileCount", "Số ảnh trong chồng", 2, 1, 3, group="Chồng ảnh"),
            integer("pileMaxAngle", "Góc nghiêng tối đa (độ)", 9, 0, 20, group="Chồng ảnh"),
            font("subFont", "Font phụ đề (chú thích)", "Sriracha", "Phụ đề"),
        ] + _placement_fields(sub_font_size=40, sub_force=True, sub_text="#2A2622", sub_outline_w=0),
    },
    {
        "id": "doc_strip", "group": "layout", "phase": 2, "labRatio": 1.21, "shrinksFrame": True,
        "name": "Dải phim tư liệu",
        "description": "Khung hình chính ở giữa, phía trên là dải ảnh nhỏ các clip trước/sau; "
                       "mỗi lần cắt dải trượt một nấc.",
        "fields": [
            select("thumbSize", "Cỡ ảnh nhỏ", "256", [("192", "Nhỏ (192×108)"), ("256", "Vừa (256×144)"),
                                                     ("320", "Lớn (320×180)")], "Dải phim"),
            select("stripPosition", "Dải phim nằm", "top", [("top", "Phía trên"), ("bottom", "Phía dưới")],
                   "Dải phim"),
            color("thumbBorderColor", "Màu viền ảnh nhỏ", "#E8DCC0", "Dải phim"),
            color("currentColor", "Màu viền ảnh hiện tại", "#F0A93B", "Dải phim"),
            boolean("showIndex", "Hiện số thứ tự", True, "Dải phim"),
            num("dimOthers", "Độ tối các ảnh khác", 0.38, 0.0, 0.8, 0.02, "Dải phim"),
            num("slideSeconds", "Thời gian trượt (giây)", 0.4, 0.2, 1.2, 0.1, "Dải phim"),
            rect("rect", "Khung hình chính", {"x": 288, "y": 196, "w": 1344, "h": 756}, "Khung chính",
                 aspect=16 / 9),
            color("bgColor", "Màu nền", "#0D0C0B", "Khung chính"),
            boolean("bgGrain", "Hạt nhiễu nền", True, "Khung chính"),
        ] + _placement_fields(sub_font_size=44, sub_margin_v=14),
    },
    # ------------------------------------------------------------- layouts, phase 3
    {
        "id": "tv_zoom", "group": "layout", "phase": 3, "labRatio": 1.18, "requiresDecor": True,
        "shrinksFrame": True, "needsChapters": True,
        "name": "TV mở ra toàn màn hình",
        "description": "Video chạy trong màn hình của ảnh decor, tới đầu chương thì cả căn phòng phóng dần "
                       "cho tới khi hình đầy khung, rồi lùi về ở chương sau.",
        "fields": [
            num("transitionSeconds", "Thời gian phóng/lùi (giây)", 1.2, 0.6, 3.0, 0.1, "Chuyển cảnh"),
            select("trigger", "Đổi trạng thái khi", "chapter",
                   [("chapter", "Đầu mỗi chương"), ("interval", "Mỗi N phút")], "Chuyển cảnh"),
            num("everyMinutes", "N phút (khi chọn mỗi N phút)", 3.0, 0.5, 20.0, 0.5, "Chuyển cảnh"),
            select("startState", "Bắt đầu ở", "room", [("room", "Trong khung TV"), ("full", "Toàn màn hình")],
                   "Chuyển cảnh"),
        ] + _window_border_fields() + _chapter_fields() + _placement_fields(),
    },
    {
        "id": "two_layer", "group": "layout", "phase": 3, "labRatio": 1.24, "shrinksFrame": True,
        "needsChapters": True,
        "name": "Hai lớp cùng nguồn",
        "description": "Cửa sổ hình sắc nét nằm trên chính nó đã làm mờ và tối; cửa sổ đổi chỗ theo chương "
                       "và thỉnh thoảng mở ra toàn khung.",
        "fields": [
            rect("centerRect", "Cửa sổ giữa", {"x": 240, "y": 60, "w": 1440, "h": 810}, "Cửa sổ", aspect=16 / 9),
            rect("sideRect", "Cửa sổ lệch", {"x": 80, "y": 120, "w": 1280, "h": 720}, "Cửa sổ", aspect=16 / 9),
            select("sequence", "Thứ tự trạng thái", "center_side_full",
                   [("center_side", "Giữa ↔ lệch"), ("center_side_full", "Giữa → lệch → toàn khung"),
                    ("center_full", "Giữa ↔ toàn khung")], "Cửa sổ"),
            integer("borderWidth", "Độ dày viền", 6, 0, 20, group="Cửa sổ"),
            color("borderColor", "Màu viền", "#F5F0E6", "Cửa sổ"),
            boolean("shadow", "Đổ bóng", True, "Cửa sổ"),
            select("bgBlur", "Độ mờ nền", "strong", [("light", "Nhẹ"), ("medium", "Vừa"), ("strong", "Mạnh")], "Nền"),
            num("bgDim", "Độ tối nền", 0.35, 0.0, 0.8, 0.05, "Nền"),
            num("transitionSeconds", "Thời gian chuyển (giây)", 0.8, 0.4, 2.0, 0.1, "Chuyển cảnh"),
        ] + _chapter_fields() + _placement_fields(),
    },
    # ------------------------------------------------------------- modifiers
    {
        "id": "cut_accents", "group": "modifier", "phase": 1, "labRatio": 1.04,
        "name": "Nhấn điểm cắt",
        "description": "Flash ngắn ở điểm cắt và dip đen khi chuyển đoạn trong SRT.",
        "fields": [
            boolean("flash", "Flash ở điểm cắt", True, "Flash"),
            color("flashColor", "Màu flash", "#FFFFFF", "Flash"),
            num("flashOpacity", "Độ đậm flash", 0.55, 0.1, 1.0, 0.05, "Flash"),
            integer("flashFrames", "Độ dài flash (frame)", 2, 1, 6, group="Flash"),
            integer("flashEveryNCuts", "Flash mỗi N lần cắt", 4, 1, 12, group="Flash"),
            boolean("dip", "Dip khi chuyển đoạn", True, "Dip"),
            color("dipColor", "Màu dip", "#000000", "Dip"),
            num("dipSeconds", "Độ dài dip (giây)", 0.5, 0.2, 1.5, 0.1, "Dip"),
            select("dipOn", "Dip ở", "paragraph", [("paragraph", "Mỗi đoạn SRT"), ("chapter", "Mỗi chương")], "Dip"),
        ],
    },
    {
        "id": "long_takes", "group": "modifier", "phase": 1, "labRatio": 0.99,
        "name": "Cảnh dài liền mạch",
        "description": "Ghép 2–3 clip liền nhau của cùng nguồn thành cảnh 6–9s, nhịp cắt không còn đều 3s.",
        "fields": [
            integer("weight1", "Tỉ lệ cảnh 1 clip", 40, 0, 100, group="Nhịp"),
            integer("weight2", "Tỉ lệ cảnh 2 clip", 35, 0, 100, group="Nhịp"),
            integer("weight3", "Tỉ lệ cảnh 3 clip", 25, 0, 100, group="Nhịp"),
        ],
    },
    {
        "id": "voice_bars", "group": "modifier", "phase": 1, "labRatio": 1.00, "needsOverlayPass": True,
        "name": "Sóng âm theo giọng",
        "description": "Thay sóng âm lặp bằng thanh sóng vẽ từ chính giọng đọc, lặng xuống khi ngắt câu.",
        "fields": [
            integer("bars", "Số thanh", 28, 12, 48, group="Hình"),
            select("style", "Kiểu", "mirrored", [("mirrored", "Đối xứng giữa"), ("bottom", "Mọc từ đáy")], "Hình"),
            color("color", "Màu", "#F2E6C8", "Hình"),
            num("barFill", "Độ rộng thanh (tỉ lệ ô)", 0.6, 0.2, 0.9, 0.05, "Hình"),
            integer("width", "Chiều rộng (px)", 420, 200, 960, 2, "Hình"),
            integer("height", "Chiều cao (px)", 160, 60, 320, 2, "Hình"),
            num("sensitivity", "Độ nhạy", 1.0, 0.3, 3.0, 0.1, "Phản ứng"),
            num("release", "Độ hạ chậm", 0.82, 0.5, 0.95, 0.01, "Phản ứng"),
            point("placement", "Vị trí (trống = theo sóng âm đang chọn)", None, "Vị trí", size=[420, 160]),
        ],
    },
    {
        "id": "cta_moments", "group": "modifier", "phase": 1, "labRatio": 0.96, "needsOverlayPass": True,
        "name": "CTA theo mốc",
        "description": "CTA chỉ xuất hiện ở vài mốc và trượt vào/ra, thay vì lặp suốt video.",
        "fields": [
            str_list("moments", "Mốc xuất hiện (giây hoặc %)", ["20", "25%", "50%", "75%"], "Mốc"),
            integer("showSeconds", "Hiện mỗi lần (giây)", 14, 4, 60, group="Mốc"),
            integer("endSeconds", "Hiện ở cuối video (giây, 0 = tắt)", 30, 0, 120, group="Mốc"),
            select("entry", "Kiểu xuất hiện", "slide_left",
                   [("slide_left", "Trượt từ trái"), ("slide_right", "Trượt từ phải"), ("cut", "Hiện ngay")], "Mốc"),
        ],
    },
    # ------------------------------------------------------------- modifiers, phase 4: light
    # Listed before chapter cards and quotes so those still draw on top of the light.
    # ``presets`` are extra records seeded next to the default one the first time
    # the type appears: ready-made looks the user can tick, tweak or delete.
    {
        "id": "light_sweep", "group": "modifier", "phase": 4,
        "name": "Vệt quét sáng",
        "description": "Dải sáng lướt qua khung hình, chồng được lên mọi bố cục: chọn hướng (dọc, ngang, chéo, "
                       "qua lại), kiểu vệt và nhịp xuất hiện.",
        "fields": [
            select("direction", "Hướng quét", "down", SWEEP_DIRECTIONS, "Hướng"),
            integer("angle", "Độ nghiêng vệt chéo (độ)", 20, 5, 45, group="Hướng"),
            select("profile", "Kiểu vệt", "soft", SWEEP_PROFILES, "Vệt"),
            color("color", "Màu vệt", "#FFFFFF", "Vệt"),
            num("opacity", "Độ sáng (giữa vệt)", 0.2, 0.02, 0.6, 0.01, "Vệt"),
            integer("width", "Bề rộng vệt (px)", 240, 20, 600, 2, "Vệt"),
            integer("speed", "Tốc độ (px/giây)", 160, 40, 2400, 10, "Nhịp"),
        ] + _rhythm_fields("loop", 8, 3, loop=True),
        "presets": [
            _sweep_preset("Ánh kim chéo", direction="diag_right", angle=22, profile="double", opacity=0.22,
                          width=150, speed=1100, rhythm="interval", everySeconds=7),
            _sweep_preset("Tia laser quét lên xuống", direction="bounce_v", profile="laser", color="#9FE8FF",
                          opacity=0.2, width=120, speed=240, rhythm="loop"),
            _sweep_preset("Cầu vồng lăng kính", direction="diag_left", angle=18, profile="prism", opacity=0.16,
                          width=280, speed=800, rhythm="interval", everySeconds=10),
            _sweep_preset("Sao chổi theo nhịp cắt", direction="right", profile="trail", opacity=0.2, width=280,
                          speed=1300, rhythm="cuts", everyNCuts=4),
            _sweep_preset("Ánh vàng chuyển đoạn", direction="diag_right", angle=26, profile="soft",
                          color="#FFD89A", opacity=0.22, width=340, speed=1500, rhythm="paragraph"),
            _sweep_preset("Bóng tối lướt qua", direction="up", profile="soft", color="#000000", opacity=0.4,
                          width=420, speed=120, rhythm="loop"),
            _sweep_preset("Quét ngẫu nhiên mỗi video", direction="random", profile="soft", opacity=0.16,
                          width=240, speed=700, rhythm="interval", everySeconds=9),
        ],
    },
    {
        "id": "light_leak", "group": "modifier", "phase": 4,
        "name": "Rò sáng phim",
        "description": "Quầng màu như phim bị lọt sáng trôi vào từ mép khung rồi tan đi, theo nhịp đã chọn.",
        "fields": [
            select("palette", "Bảng màu", "amber", LEAK_PALETTES, "Màu"),
            num("intensity", "Độ đậm", 0.42, 0.1, 0.8, 0.02, "Màu"),
            integer("size", "Cỡ quầng sáng (px)", 1100, 500, 1600, 20, "Hình"),
            select("side", "Trôi vào từ", "alternate",
                   [("left", "Mép trái"), ("right", "Mép phải"), ("top", "Mép trên"),
                    ("alternate", "Luân phiên trái / phải"), ("random", "Ngẫu nhiên mỗi lần")], "Hình"),
            integer("reach", "Lấn vào khung (px)", 240, 0, 800, 10, "Hình"),
            num("seconds", "Mỗi lần kéo dài (giây)", 4.5, 1.5, 12, 0.5, "Nhịp"),
        ] + _rhythm_fields("interval", 14, 6),
        "presets": [
            {"name": "Rò sáng hồng khi chuyển đoạn",
             "params": {"palette": "rose", "rhythm": "paragraph", "seconds": 3.5, "side": "random"}},
            {"name": "Lửa cam đầu chương",
             "params": {"palette": "fire", "rhythm": "chapter", "seconds": 5, "intensity": 0.44, "size": 1300}},
            {"name": "Xanh ngọc lạnh",
             "params": {"palette": "teal", "intensity": 0.3, "everySeconds": 18, "side": "top"}},
        ],
    },
    {
        "id": "light_rays", "group": "modifier", "phase": 4,
        "name": "Tia nắng xiên",
        "description": "Chùm tia sáng chiếu xiên từ phía trên, lay rất chậm như nắng lọt qua tán cây hay khung cửa.",
        "fields": [
            select("corner", "Nguồn sáng", "top_left",
                   [("top_left", "Góc trên trái"), ("top_right", "Góc trên phải"), ("top", "Chính giữa phía trên"),
                    ("random", "Ngẫu nhiên mỗi video")], "Tia"),
            color("color", "Màu tia", "#FFF1D0", "Tia"),
            num("intensity", "Độ sáng", 0.3, 0.04, 0.6, 0.01, "Tia"),
            integer("rays", "Số tia", 11, 4, 24, group="Tia"),
            integer("spread", "Độ xòe (độ)", 60, 20, 120, group="Tia"),
            num("length", "Độ dài tia (tỉ lệ khung)", 1.0, 0.4, 1.6, 0.05, "Tia"),
            integer("sway", "Biên độ lay (px)", 80, 0, 240, 2, "Chuyển động"),
            integer("period", "Chu kỳ lay (giây)", 16, 4, 60, group="Chuyển động"),
        ],
        "presets": [
            {"name": "Nắng vàng góc phải",
             "params": {"corner": "top_right", "color": "#FFD58A", "intensity": 0.34, "rays": 8, "spread": 50}},
            {"name": "Ánh trăng lạnh",
             "params": {"corner": "top", "color": "#BFD8FF", "intensity": 0.24, "rays": 14, "spread": 80,
                        "period": 24}},
        ],
    },
    {
        "id": "spotlight", "group": "modifier", "phase": 4,
        "name": "Đèn rọi trôi",
        "description": "Khung tối dần ra mép, chỉ một vùng sáng trôi chậm quanh hình — như đèn pin rọi trong đêm.",
        "fields": [
            num("darkness", "Độ tối quanh vùng sáng", 0.6, 0.1, 0.9, 0.02, "Vùng tối"),
            color("color", "Màu vùng tối", "#000000", "Vùng tối"),
            select("shape", "Hình vùng sáng", "ellipse", [("ellipse", "Bầu dục theo khung"), ("circle", "Tròn")],
                   "Vùng sáng"),
            num("radius", "Cỡ vùng sáng (tỉ lệ chiều cao khung)", 0.62, 0.25, 1.0, 0.01, "Vùng sáng"),
            num("softness", "Độ mềm mép", 0.6, 0.1, 1.0, 0.05, "Vùng sáng"),
            integer("drift", "Biên độ trôi (px, 0 = đứng yên)", 200, 0, 420, 2, "Chuyển động"),
            integer("periodX", "Chu kỳ ngang (giây)", 37, 8, 120, group="Chuyển động"),
            integer("periodY", "Chu kỳ dọc (giây)", 23, 8, 120, group="Chuyển động"),
        ],
        "presets": [
            {"name": "Đèn pin trong đêm",
             "params": {"darkness": 0.78, "color": "#03060F", "shape": "circle", "radius": 0.5, "softness": 0.45,
                        "drift": 320, "periodX": 19, "periodY": 13}},
        ],
    },
    # ------------------------------------------------------------- modifiers, phase 2
    {
        "id": "chapter_cards", "group": "modifier", "phase": 2, "labRatio": 1.11, "needsChapters": True,
        "name": "Thẻ tiêu đề chương",
        "description": "Đầu mỗi chương, hình tối xuống vài giây và hiện số chương + tên chương lớn ở giữa.",
        "fields": [
            num("seconds", "Thẻ hiện bao lâu (giây)", 3.6, 2.0, 8.0, 0.2, "Thẻ"),
            num("dim", "Độ tối nền", 0.7, 0.2, 0.95, 0.05, "Thẻ"),
            boolean("skipFirst", "Bỏ thẻ ở chương đầu (0:00)", False, "Thẻ"),
            text("labelFormat", "Mẫu nhãn ({n}, {total}, {label})", "บทที่ {n}", "Chữ"),
            font("labelFont", "Font nhãn", "Kanit", "Chữ"),
            integer("labelSize", "Cỡ nhãn", 38, 16, 80, group="Chữ"),
            color("labelColor", "Màu nhãn", "#C8AA6E", "Chữ"),
            integer("labelSpacing", "Giãn chữ nhãn", 12, 0, 30, group="Chữ"),
            font("titleFont", "Font tên chương", "Trirong", "Chữ"),
            integer("titleSize", "Cỡ tên chương", 104, 40, 160, group="Chữ"),
            color("titleColor", "Màu tên chương", "#FFFFFF", "Chữ"),
            boolean("zoomOut", "Chữ thu nhỏ dần", True, "Chữ"),
        ] + _chapter_fields(),
    },
    {
        "id": "quote_moments", "group": "modifier", "phase": 2, "labRatio": 1.18,
        "name": "Câu nói điểm nhấn",
        "description": "Vài câu quan trọng hiện to giữa màn hình trên nền mờ, thay cho phụ đề của câu đó.",
        "fields": [
            select("source", "Lấy câu nhấn từ", "auto",
                   [("auto", "File chương (dòng >), thiếu thì tự chọn câu có ! ? …"),
                    ("file", "Chỉ file chương (dòng >)")], "Nguồn"),
            integer("maxQuotes", "Số câu tối đa", 5, 1, 20, group="Nguồn"),
            integer("minGapSeconds", "Cách nhau tối thiểu (giây)", 60, 10, 600, group="Nguồn"),
            num("minHoldSeconds", "Giữ tối thiểu (giây)", 5.0, 2.0, 12.0, 0.5, "Nguồn"),
            select("bgBlur", "Độ mờ nền", "medium", [("light", "Nhẹ"), ("medium", "Vừa"), ("strong", "Mạnh")], "Nền"),
            num("bgDim", "Độ tối nền", 0.35, 0.0, 0.8, 0.05, "Nền"),
            font("font", "Font câu nhấn", "Trirong", "Chữ"),
            integer("size", "Cỡ chữ", 72, 36, 120, group="Chữ"),
            color("color", "Màu chữ", "#FFFFFF", "Chữ"),
            color("quoteMarkColor", "Màu dấu ngoặc kép", "#C8AA6E", "Chữ"),
            boolean("hideCtaWave", "Ẩn sóng âm/CTA khi hiện câu nhấn", True, "Chữ"),
        ],
    },
]

TYPE_BY_ID = {t["id"]: t for t in TYPES}
_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")
# Uploaded pictures live in STORY_EDIT_STYLE_DIR/images; params keep the relative path.
_IMAGE_REF = re.compile(r"^images/[A-Za-z0-9_.-]{1,120}$")


def clean_image_ref(value) -> str | None:
    value = str(value or "").strip().replace("\\", "/")
    return value if _IMAGE_REF.match(value) and ".." not in value else None


def get_type(type_id: str) -> dict | None:
    return TYPE_BY_ID.get(str(type_id or ""))


def default_params(type_id: str) -> dict:
    spec = get_type(type_id) or {"fields": []}
    return {f["key"]: copy.deepcopy(f["default"]) for f in spec["fields"]}


def _clamp_num(value, field, is_int):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return field["default"]
    if v != v:  # NaN
        return field["default"]
    v = min(field["max"], max(field["min"], v))
    return int(round(v)) if is_int else round(v, 4)


def _clean_rect(value, field):
    base = field["default"] or {"x": 0, "y": 0, "w": W, "h": H}
    if not isinstance(value, dict):
        return dict(base)
    try:
        x, y, w, h = (int(round(float(value.get(k, base[k])))) for k in ("x", "y", "w", "h"))
    except (TypeError, ValueError):
        return dict(base)
    w = max(64, min(W, w))
    h = max(64, min(H, h))
    aspect = field.get("aspect")
    if aspect:
        h = max(64, min(H, int(round(w / aspect))))
        w = min(w, int(round(h * aspect)))
    # yuv420p wants even sizes and offsets
    w -= w % 2
    h -= h % 2
    x = max(0, min(W - w, x))
    y = max(0, min(H - h, y))
    return {"x": x - x % 2, "y": y - y % 2, "w": w, "h": h}


def _clean_point(value):
    if value is None or not isinstance(value, dict):
        return None
    try:
        x = int(round(float(value.get("x"))))
        y = int(round(float(value.get("y"))))
    except (TypeError, ValueError):
        return None
    return {"x": max(0, min(W, x)), "y": max(0, min(H, y))}


def sanitize_params(type_id: str, params: dict | None) -> dict:
    """Clamp a params dict against the type's spec; unknown keys are dropped."""
    spec = get_type(type_id)
    if not spec:
        raise ValueError(f"Kieu dung khong ton tai: {type_id}")
    incoming = params if isinstance(params, dict) else {}
    out = {}
    for field in spec["fields"]:
        key, ftype = field["key"], field["type"]
        raw = incoming.get(key, field["default"])
        if ftype in ("number", "int"):
            out[key] = _clamp_num(raw, field, ftype == "int")
        elif ftype == "bool":
            out[key] = bool(raw) if isinstance(raw, (bool, int)) else str(raw).lower() in ("1", "true", "yes", "on")
        elif ftype == "color":
            out[key] = ("#" + str(raw).strip().lstrip("#").upper()) if isinstance(raw, str) and _HEX.match(raw.strip()) \
                else field["default"]
        elif ftype == "select":
            allowed = {o["value"] for o in field["options"]}
            out[key] = raw if raw in allowed else field["default"]
        elif ftype in ("text", "font"):
            # One line, but inner spacing is kept: "KODAK 5219   ▸ 24" is spaced on purpose.
            value = " ".join(str(raw if raw is not None else "").splitlines()).strip()
            out[key] = value[: field.get("maxLength", 120)] or field["default"]
        elif ftype == "rect":
            out[key] = _clean_rect(raw, field)
        elif ftype == "point":
            out[key] = _clean_point(raw)
        elif ftype == "image":
            out[key] = clean_image_ref(raw)
        elif ftype == "imageList":
            items = raw if isinstance(raw, list) else []
            refs = [ref for ref in (clean_image_ref(i) for i in items) if ref]
            out[key] = list(dict.fromkeys(refs))[: field.get("maxItems", 6)]
        elif ftype == "list":
            items = raw if isinstance(raw, list) else field["default"]
            cleaned = [" ".join(str(i).split())[:60] for i in items if str(i).strip()]
            out[key] = cleaned[: field.get("maxItems", 24)] or list(field["default"])
    return out


def image_fields(type_id: str) -> list[dict]:
    spec = get_type(type_id) or {"fields": []}
    return [f for f in spec["fields"] if f["type"] in ("image", "imageList")]


def public_types() -> list[dict]:
    """The catalogue as the settings page consumes it."""
    return [
        {
            "id": t["id"], "group": t["group"], "phase": t["phase"], "name": t["name"],
            "description": t["description"], "labRatio": t.get("labRatio"),
            "requiresDecor": bool(t.get("requiresDecor")), "shrinksFrame": bool(t.get("shrinksFrame")),
            "needsChapters": bool(t.get("needsChapters")),
            # Works on the waveform/CTA overlay: a fully baked library skips that pass.
            "needsOverlayPass": bool(t.get("needsOverlayPass")), "fields": t["fields"],
        }
        for t in TYPES
    ]
