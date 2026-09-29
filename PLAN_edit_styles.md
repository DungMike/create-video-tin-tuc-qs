# Plan: tích hợp "kiểu dựng" (edit styles) vào Story Video

> File này là plan gốc (đã duyệt) + tiến độ thực hiện. Claude cập nhật mục **Tiến độ** và **Nhật ký** sau mỗi bước.
> Ký hiệu: ✅ xong · 🔄 đang làm · ⬜ chưa làm · ⚠️ có ghi chú

## Tổng quan tiến độ

| Đợt | Nội dung | Trạng thái |
|---|---|---|
| 1 | Hạ tầng + 10 bố cục + 4 bổ trợ + trang cấu hình + bộ chọn batch + file chương + font | ✅ Xong (2026-09-22) |
| 2 | magazine, split_accent, dossier, newsroom, album, doc_strip, biến thể film_frame, chapter_cards, quote_moments, upload ảnh | ✅ Xong (2026-09-22) |
| 3 | tv_zoom, two_layer (render theo timeline), Ken Burns trong bake thư viện | ✅ Xong (2026-09-22) |
| 4 | Hiệu ứng ánh sáng (bổ trợ): vệt quét sáng nhiều hướng, rò sáng phim, tia nắng xiên, đèn rọi trôi + mẫu tạo sẵn; hướng quét cho OSD camera an ninh; nhiều bản cùng kiểu bổ trợ xoay vòng qua video | ✅ Xong (2026-09-28) |

**Việc cần người dùng làm sau mỗi đợt:** restart backend Flask (`.\venv\Scripts\python -m src.web_app`) — Claude không tự restart được.

---

## Tiến độ chi tiết

### Đợt 1 — ✅ xong

**Hạ tầng backend**
- ✅ `src/utils/edit_styles/spec.py` — catalog kiểu, khai báo trường (number/int/color/bool/select/text/font/rect/point/list), `sanitize_params`, cờ `requiresDecor` / `shrinksFrame` / `needsChapters` / `needsOverlayPass`
- ✅ `store.py` — `index.json` trong `STORY_EDIT_STYLE_DIR`, tự tạo 1 bản mặc định cho mỗi kiểu, CRUD, nhân bản, `build_layout_rotation`, `resolve_modifiers`
- ✅ `graph.py` — `Ops(gpu)` sinh filter GPU (`overlay_cuda`/`scale_cuda`) hoặc CPU cho cùng một đồ thị; `T(ss)` cho đường chia đoạn
- ✅ `assets.py` — sinh PNG/MOV theo tham số, cache theo hash
- ✅ `chapters.py` — đọc `<tên audio>.chapters.txt`, tự chia chương theo SRT, kéo mốc về đầu dòng phụ đề
- ✅ `runtime.py` — `EditPlan`: input phụ, đoạn filter, vị trí sóng âm/CTA, lớp ASS, chỉnh phụ đề theo bố cục
- ✅ `preview.py` — render xem thử 10 giây bằng đúng bước overlay của batch
- ✅ Pipeline: hook vào cả đường GPU (kể cả chia đoạn) lẫn CPU dự phòng; lớp ASS chỉ có bố cục (không SRT) vẫn chạy; `_ass_event_spans` chỉ tính `Default`; `_rebase_ass_file` không chạy lại fade ở câu nối đoạn
- ✅ `SharedClipBag.draw_run` cho cảnh dài liền mạch
- ✅ Đo mốc cắt thật từ keyframe nền (sửa lệch ~3.4s cuối video 10 phút)
- ✅ Routes: CRUD `/api/story-video/edit-styles`, `/preview`; batch nhận `layoutIds`, `modifierIds`, `chapter_files[]`; render đơn nhận `layoutId`, `modifierIds`, file chương; quét thư mục local ghép `.chapters.txt`; payload cũ chỉ có `decorImageIds` = khung TV
- ✅ Batch runner lưu `layout_id/name`, `modifier_ids/names`, `chapters_path` vào progress, retry-failed giữ nguyên
- ✅ 5 font Thái OFL (Kanit, Trirong, Sriracha, Chakra Petch, Sarabun) trong `src/assets/fonts/`, tự chép vào thư mục font khi khởi động

**Bố cục (10):** ✅ plain · ✅ tv_frame · ✅ tv_glass · ✅ tv_drift · ✅ card · ✅ letterbox · ✅ film_frame (cuộn phim) · ✅ osd_camcorder · ✅ osd_cctv · ✅ drift

**Bổ trợ (4):** ✅ cut_accents · ✅ long_takes · ✅ voice_bars · ✅ cta_moments

**Frontend**
- ✅ Trang `/story-video/edit-styles` (`StoryEditStylesPage.tsx`): danh sách theo kiểu, bật/tắt, thêm bản, nhân bản, xoá, form tự sinh từ spec, kéo thả vùng/vị trí (`EditStyleFrameEditor.tsx`), xem thử
- ✅ `components/color-input.tsx` dùng chung
- ✅ `/story-video`: bộ chọn Bố cục (xoay vòng) + ảnh decor (chỉ hiện khi có bố cục khung TV) + Hiệu ứng bổ trợ; ghép `.chapters.txt`; thẻ tiến độ hiện Bố cục/Bổ trợ
- ✅ Link ở top nav + đầu trang render

**Kiểm tra đợt 1**
- ✅ pytest: `tests/test_edit_styles.py`, `tests/test_edit_style_graphs.py` (257 test backend qua; `test_google_drive_audio` lỗi từ trước, không liên quan)
- ✅ Render thật 10 phút qua `StoryVideoPipelineRunner` — tất cả ≥ 11x (chậm nhất 12.9x) → `tests/test-render/prod_styles/`
- ✅ Frontend build + lint sạch; đã xem UI thật

### Đợt 2 — ✅ xong

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| Trường `image` / `imageList` (upload ảnh): spec + store + route + form | ✅ | Ảnh mở bằng PIL rồi lưu PNG vào `story_edit_styles/images/`; file không còn bản ghi nào dùng thì tự xoá |
| Phụ đề nằm trong vùng hình (dời `\pos` + lề từng dòng) | ✅ | Đúng cả với preset chữ nhảy/karaoke |
| Khung "kind": mỗi kiểu một module `src/utils/edit_styles/kinds/` | ✅ | Kiểu đợt 1 giữ nguyên trong `runtime.py` |
| Ảnh tĩnh từng clip trích song song lúc dựng nền; PNG mỗi clip qua concat demuxer theo mốc cắt thật | ✅ | Không phải mã hoá MOV như lab |
| Filter graph dài đi qua file (`-/filter_complex`) | ✅ | Tránh giới hạn 32K ký tự dòng lệnh Windows |
| Sửa blur GPU (trước bị vỡ ô vuông) | ✅ | Gaussian thật trên bản thu nhỏ (`bilateral_cuda`), còn nhanh hơn cách cũ |
| Sửa giới hạn biểu thức FFmpeg | ✅ | Cộng dồn theo cây cân bằng: chuỗi phẳng chết ở ~110 mệnh đề, video 10 phút có ~200 điểm cắt |
| `magazine` Tạp chí chuyển động | ✅ | 10 phút: 15.4x |
| `split_accent` Chia màn hình điểm nhấn | ✅ | 10 phút: 15.5x |
| `dossier` Hồ sơ kể chuyện | ✅ | 10 phút: 15.9x; đổi được ảnh nền giấy + 4 ảnh trang trí |
| `newsroom` Bản tin chuyên đề | ✅ | 10 phút: 12.9x; chỉnh được vị trí/cao/dài/màu thẻ tiêu đề |
| `album` Album ký ức | ✅ | 10 phút: 15.0x |
| `doc_strip` Dải phim tư liệu | ✅ | 10 phút: 12.4x |
| `film_frame` thêm biến thể polaroid / sổ tay / tranh gỗ | ✅ | Có test dựng ảnh khung; chưa render riêng từng biến thể |
| `chapter_cards` Thẻ tiêu đề chương (bổ trợ) | ✅ | 10 phút: 15.4x; chạy được cả khi bố cục không có chương |
| `quote_moments` Câu nói điểm nhấn (bổ trợ) | ✅ | 10 phút: 12.9x |
| pytest đợt 2 (`tests/test_edit_styles_phase2.py`) | ✅ | 28 test |
| Frontend: form ảnh + build + lint | ✅ | |

### Đợt 3 — ✅ xong

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| Render theo mảnh: đoạn tĩnh GPU + đoạn chuyển CPU rồi ghép | ✅ code | Tách sẵn hàm dựng lệnh CPU; dùng chung phần ghép + mux với đường chia đoạn |
| `tv_zoom` TV mở ra toàn màn hình | ✅ | 10 phút: 11.3x (9 mảnh) |
| `two_layer` Hai lớp cùng nguồn | ✅ | 10 phút: 12.3x (9 mảnh) |
| Ken Burns là tuỳ chọn trong bake thư viện | ✅ | Bake thêm dưới 1 giây mỗi clip. | `motion` = tắt / trôi khung / phóng dần; lưu trong metadata thư viện nên bake tiếp tục và bake bù clip mới vẫn giữ |
| Form chọn Ken Burns ở hộp thoại bake | ✅ | Chọn tắt / trôi khung / phóng dần + mức phóng |
| pytest đợt 3 (`tests/test_edit_styles_phase3.py`) | ✅ | 5 test |
| Render thật 10 phút + đo tốc độ | ✅ | Cả 24 kiểu ≥ 11x; chậm nhất tv_zoom 11.3x |

### Bổ sung sau đợt 3

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| Không có file chương -> không hiện chữ chương | ✅ | Chương vẫn còn mốc thời gian để đổi cảnh/dip; chữ (tên chương, thẻ chương, thẻ chuyển chương, ghi chú hồ sơ, cột tạp chí) chỉ lấy từ file. Muốn quay lại kiểu tự đặt tên thì điền mẫu vào ô "Tiêu đề chương khi không có file chương". |
| Chuyển đổi bản ghi cũ | ✅ | `store._migrate_unlocked` xoá giá trị `Phần {n}` cũ một lần, ghi cờ `chapterTextFromFileOnly` vào index.json |
| Hướng dẫn cấu trúc file chương + file mẫu trong giao diện | ✅ | `components/chapter-file-help.tsx`, hiện ở trang render (cả single và batch) và trang Kiểu dựng; có nút tải file mẫu |

### Đợt 4 — ✅ xong (hiệu ứng ánh sáng)

Vệt quét sáng vốn chỉ có trong bố cục OSD camera an ninh; nay là **hiệu ứng bổ trợ** nên tick ở batch là chồng được lên mọi bố cục. Mọi hiệu ứng là 1 PNG dựng sẵn (cache theo tham số) + biểu thức x/y `eval=frame`, lúc không hiện thì `enable` tắt lớp đó. Code: `src/utils/edit_styles/kinds/light_fx.py`.

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| `light_sweep` Vệt quét sáng | ✅ | Hướng: trên→xuống, dưới→lên, trái→phải, phải→trái, chéo ↘/↙ (chỉnh độ nghiêng), qua lại dọc/ngang, ngẫu nhiên mỗi video. Kiểu vệt: mềm, dải đều (như CCTV), tia mảnh + quầng, hai vệt (ánh kính), đuôi sao chổi, cầu vồng. Nhịp: liên tục, mỗi N giây, mỗi N lần cắt, đầu đoạn SRT, đầu chương. 7 mẫu tạo sẵn |
| `light_leak` Rò sáng phim | ✅ | Quầng màu trôi vào từ mép rồi tan; 6 bảng màu + ngẫu nhiên; mép trái/phải/trên/luân phiên/ngẫu nhiên; 3 mẫu |
| `light_rays` Tia nắng xiên | ✅ | Chùm tia từ góc trên, lay chậm; 2 mẫu |
| `spotlight` Đèn rọi trôi | ✅ | Tối viền, vùng sáng trôi theo đường Lissajous; mẫu "Đèn pin trong đêm" |
| `osd_cctv`: trường `scanDirection` | ✅ | Mặc định "Trên → xuống" ra đúng biểu thức cũ |
| Mẫu tạo sẵn (`presets` trong spec) | ✅ | Chỉ tạo một lần khi kiểu xuất hiện lần đầu (`seededTypes`); xoá mẫu thì không tự sinh lại |
| Nhiều bản cùng kiểu bổ trợ | ✅ | `store.deal_modifier_rotation`: mỗi video vẫn tối đa 1 bản/kiểu; kiểu tick nhiều bản thì chia xoay vòng như bố cục. Kiểu tick 1 bản: y như cũ. `modifier_ids` lưu theo từng item nên retry giữ đúng bản đã bốc |
| pytest (`tests/test_edit_styles_light.py`) | ✅ | 78 test, gồm chạy FFmpeg thật trên chuỗi CPU cho mọi hướng/nhịp và cả 4 hiệu ứng chồng nhau |
| Đo tốc độ bước overlay thật (60 s, phụ đề + sóng âm/CTA, GPU chia đoạn) | ✅ | Từng hiệu ứng ×0.98–1.14 so với không hiệu ứng (nhiễu đo ~±5%); cả 4 chồng nhau ×1.24; OSD CCTV ×1.10–1.12 |

### Vấn đề đã biết / việc còn nợ

- ⚠️ (Phát hiện khi làm đợt 4, có từ trước) Đường GPU **không có phụ đề** (`_gpu_overlay_tail` nhánh `scale_cuda=format=yuv420p`) mà có `overlay_cuda` ra khung **1920×1088**, 8 dòng dưới cùng màu xanh lá. Có phụ đề (nhánh `hwdownload` + ASS) thì đúng 1080 — các bản render sản xuất đã kiểm đều 1080.
- ⚠️ Rò sáng / vệt quét "đầu chương" không hiện gì ở video ngắn hơn một chương tự chia (mặc định 2 phút) khi không có file chương.

- ⚠️ Chọn cùng lúc "Trôi khung" + "Cảnh dài": khung vẫn đổi hướng ở mỗi clip, kể cả giữa một cảnh dài.
- ⚠️ Tạp chí: câu trích tiếng Thái dài bị thu nhỏ nhiều vì cột hẹp và tiếng Thái ít dấu cách để ngắt dòng — nên tự xuống dòng bằng ký hiệu xuống dòng trong file chương.
- ⚠️ `two_layer`: đoạn chuyển (CPU) làm mờ nền bằng gblur, hơi khác đoạn tĩnh (GPU), nên độ mờ nhích nhẹ ở mối nối.
- ⚠️ `tests/` nằm trong `.gitignore` (quy ước sẵn của repo) nên file test mới chỉ có ở máy local.
- ⚠️ `tests/test-render/_lab/_prod/quick/` chứa các bản render thử 120s (~1 GB), xoá được.

---

## Nhật ký

- **2026-09-22** — Đợt 1 hoàn thành. Tốc độ bước overlay trên audio 10 phút: plain 14.5x, tv_frame 15.4x, tv_glass 15.6x, tv_drift 13.2x, card 15.3x, letterbox 18.4x, film_frame 14.0x, osd_camcorder 12.9x, osd_cctv 12.9x, drift 14.6x, cut_accents 15.4x, long_takes 15.3x, voice_bars 17.4x, cta_moments 15.6x.
- **2026-09-22** — Bắt đầu đợt 2.
- **2026-09-22** — Đợt 2 xong. Đo 10 phút: magazine 15.4x, split_accent 15.5x, dossier 15.9x, newsroom 12.9x, album 15.0x, doc_strip 12.4x, chapter_cards 15.4x, quote_moments 12.9x (card sau khi đổi blur: 15.0x).
- **2026-09-22** — Hai lỗi phát hiện khi render 10 phút và đã sửa: (1) blur GPU bị vỡ ô vuông; (2) FFmpeg không phân tích nổi biểu thức có hơn ~110 mệnh đề cộng — ảnh hưởng mọi thứ bám theo điểm cắt/đoạn, nay cộng theo cây cân bằng.
- **2026-09-22** — Đợt 3 xong: render theo mảnh (đoạn tĩnh GPU + đoạn chuyển CPU), `tv_zoom` 11.3x và `two_layer` 12.3x trên bản 10 phút; Ken Burns nung sẵn khi bake thư viện (thêm dưới 1 giây mỗi clip).
- **2026-09-22** — Cả 24 kiểu đã render thật 10 phút: `tests/test-render/prod_styles/` (video + `_previews/` + `results.csv`). Chậm nhất 11.3x, nhanh nhất 18.4x.
- **2026-09-28** — Đợt 4 xong: 4 hiệu ứng ánh sáng bổ trợ + 13 mẫu tạo sẵn, hướng quét cho OSD CCTV, xoay vòng nhiều bản cùng kiểu bổ trợ. Đo trên 60 s clip thư viện thật qua `_apply_story_overlays`: mỗi hiệu ứng tốn thêm 0–14%.

---

# Plan gốc (đã duyệt)

## Context

Trong đợt nghiên cứu, đã dựng thử 25 cách dựng trên audio 10 phút và đo tốc độ (xem `tests/test-render/README.md`). Hiện mọi video trong một batch có cùng bố cục: không khung, hoặc khung TV nếu bật công tắc decor. Chỉ sóng âm, CTA và kiểu phụ đề được xoay vòng.

Mục tiêu:
- Có một trang cấu hình riêng cho các kiểu dựng. Mỗi kiểu có bộ tham số riêng và công tắc bật/tắt (mặc định bật).
- Luồng batch ở `/story-video` hiện danh sách kiểu dựng và xoay vòng chúng qua các video, giống cách decor, CTA và sóng âm đang làm, để video trong cùng batch khác nhau.

**Đã chốt với người dùng:**
- **Loại theo tốc độ:** bỏ 10 (Ken Burns làm lúc render, 3.1x) và 19 (ambilight, 10.5x) vì dưới 11x. Còn 23 kiểu, tất cả ≥ 14.2x.
- **Hai nhóm:**
  - **Bố cục** (layout): mỗi video nhận đúng 1 kiểu, xoay vòng bằng `deal_rotation`.
  - **Hiệu ứng bổ trợ** (modifier): kiểu nào được tick thì áp cho mọi video trong batch, chồng lên bố cục.
- **Khung TV gộp vào danh sách bố cục.** Phần chọn ảnh decor giữ lại, làm kho ảnh cho các bố cục cần decor.
- **Tiêu đề chương và câu nhấn** lấy từ file `<tên audio>.chapters.txt` đi kèm. Thiếu file thì tự chia chương theo SRT.
- **Chia 3 đợt.** Đợt 1 viết chi tiết; đợt 2–3 dùng lại toàn bộ hạ tầng của đợt 1.

## Danh mục kiểu và tham số cấu hình

Kiểu trường trong spec:
- `number` / `int`: slider + ô số
- `color`: ô chọn màu + hex
- `bool`, `select`, `text`, `font` (chọn từ `/subtitle-fonts`)
- `rect`: kéo vùng trên khung 1920×1080
- `point`: kéo vị trí sóng âm/CTA
- `image`: upload PNG/JPG
- `list`: danh sách chuỗi hoặc mốc thời gian

Mọi bố cục đều có chung: `name`, `enabled` (mặc định true); vị trí sóng âm/CTA (`point`, để trống = tự động); vị trí phụ đề: `subMarginV`, `subMarginL`/`R`, `subFontSize`, `subForceColors` + `subTextColor` (cho nền sáng như giấy/polaroid).

Cột "Lab" là tỉ lệ thời gian render so với bố cục hiện tại, đo ở lượt 3.

### Bố cục (xoay vòng)

| Kiểu (lab) | Đợt | Tham số riêng | Lab |
|---|---|---|---|
| `plain` Không khung (01) | 1 | — | 1.00 |
| `tv_frame` Khung TV (02), cần decor | 1 | ảnh lấy từ kho decor theo nhóm đã chọn | 1.08 |
| `tv_glass` Khung TV + kính phản chiếu (20), cần decor | 1 | `glareStrength`, `glareAngle`, `glareWidth`, `topSheen` | 1.07 |
| `tv_drift` Khung TV + máy quay trôi (21), cần decor | 1 | `zoom`, `amplitude`, `periodX`/`periodY` | 1.14 |
| `card` Thẻ nổi nền mờ (03) | 1 | `rect`, `radius`, `borderWidth`, `borderColor`, `dim`, `blur`, `shadow`, `bob` | 1.16 |
| `letterbox` Letterbox điện ảnh (04) | 1 | `aspect`, `barColor`, `ruleColor`, tên chương (font/size/màu/mẫu), thanh tiến trình | 0.82 |
| `film_frame` Khung sinh bằng thuật toán (05) | 1 | `variant` (cuộn phim; đợt 2 thêm polaroid, sổ tay, tranh gỗ), `stripColor`, `sprocketMode`, `edgeText`, `rect`, `radius` | 1.09 |
| `osd_camcorder` OSD máy quay (07) | 1 | bật/tắt PLAY/REC/pin/bộ đếm/góc; font/size/màu; góc ngắm | 1.14 |
| `osd_cctv` OSD camera an ninh (08) | 1 | `cameraLabels`, `clockMode`, `scanBand`, font/size/màu, góc | 1.15 |
| `drift` Trôi khung GPU (09) | 1 | `zoom`, `amplitude`, `redirectEachClip` | 1.04 |
| `magazine` Tạp chí chuyển động (25) | 2 | `columnWidth`, `columnColor`, `columnImage`, `accentColor`; font/size/màu số chương, tiêu đề, câu trích; `columnSeconds`, `slideSeconds`, `alternateSides` | 1.12 |
| `split_accent` Chia màn hình điểm nhấn (26) | 2 | `ratio`, `panelSide`, `zoom`, `focus`, `dividerColor`/`Width`, `trigger`, `holdSeconds`, `slideSeconds` | 1.13 |
| `dossier` Hồ sơ kể chuyện (28) | 2 | nền `paperImage` hoặc tự sinh (`paperColor`, `paperGrain`); `decorImages` (băng keo, ghim, kẹp); `rect` ảnh + viền; con dấu (`stampLabels`, màu, font, góc); `fileNumberFormat`; ghi chú (`noteFont`, `inkColor`); `sheetSweep` | 0.97 |
| `newsroom` Bản tin chuyên đề (29) | 2 | lower third (cao, rộng, Y, màu, độ đậm); nhãn (chữ, màu, rộng, font); tiêu đề (font/size/màu/mẫu); hai ô (`twoBox`, `box1Rect`, `box2Rect`, `box2Zoom`, viền, nền); thẻ chuyển chương (`wipe`); thanh tiến trình | 1.08 |
| `album` Album ký ức (24) | 2 | `backgroundImage` + `bgBlur`/`bgDim`/`bgWarmth`; polaroid (`rect`, `frameColor`, viền); `pile` + `pileCount` + `pileMaxAngle`; `slideInOnParagraph`; phụ đề chú thích (`captionFont`, `inkColor`) | 1.13 |
| `doc_strip` Dải phim tư liệu (27) | 2 | `thumbCount`, `thumbSize`, `stripPosition`, viền, màu ô hiện tại, `showIndex`, nền, `rect` khung chính, `slideSeconds` | 1.21 |
| `tv_zoom` TV mở ra toàn màn hình (22), cần decor | 3 | `transitionSeconds`, `trigger`, trạng thái đầu/cuối, vị trí sóng âm ở từng trạng thái | 1.18 |
| `two_layer` Hai lớp cùng nguồn (23) | 3 | `rects` giữa/trái/phải, viền, nền mờ/tối, `sequence`, `fullOn`, `transitionSeconds` | 1.24 |

### Hiệu ứng bổ trợ (tick là áp cho cả batch)

| Kiểu (lab) | Đợt | Tham số | Lab |
|---|---|---|---|
| `cut_accents` Nhấn điểm cắt (12) | 1 | flash (màu, độ đậm, số frame, mỗi N lần cắt); dip (màu, độ dài, đổi đoạn SRT / đổi chương) | 1.04 |
| `long_takes` Cảnh dài liền mạch (13) | 1 | tỉ lệ cảnh 1/2/3 clip | 0.99 |
| `voice_bars` Sóng âm theo giọng (17) | 1 | số thanh, màu, rộng, cao, độ nhạy, độ hạ, kiểu, vị trí | 1.00 |
| `cta_moments` CTA theo mốc (18) | 1 | `moments` (giây hoặc %), `showSeconds`, `endSeconds`, `entry` | 0.96 |
| `chapter_cards` Thẻ tiêu đề chương (14) | 2 | `seconds`, `dim`, `labelFormat`, font/size/màu nhãn và tiêu đề, `zoomOut` | 1.11 |
| `quote_moments` Câu nói điểm nhấn (16) | 2 | `maxQuotes`, `minHoldSeconds`, `bgBlur`/`bgDim`, font/size/màu, `quoteMarkColor`, `hideCtaWave` | 1.18 |
| Ken Burns nướng sẵn (11) | 3 | Tuỳ chọn "chuyển động" trong bước bake thư viện (`zoom`, `direction`) | 0.99 |

**Ràng buộc:**
- **Bố cục thu khung hình** (card, film_frame, magazine, split_accent, dossier, newsroom, album, doc_strip, two_layer, tv_*) bị chặn với thư viện đã bake sẵn sóng âm/CTA. Riêng plain, letterbox, osd_*, drift thì không bị chặn.
- **Bố cục cần decor** (tv_frame, tv_glass, tv_drift, tv_zoom) chỉ vào vòng xoay khi đã chọn ít nhất 1 ảnh decor.

## Định dạng `<tên audio>.chapters.txt` (UTF-8, dòng `#` là chú thích)

```
00:00 | หมู่บ้านริมโขง | บทนำ | ความเงียบสงบ...บางครั้งก็เป็นเพียงฉากหน้า
01:01 | ป้าบัว หญิงทอเสื่อ | เบื้องหลัง
> 00:59 คดีของหญิงทอเสื่อ
> 08:17
```

- Dòng chương: `mốc | tiêu đề | nhãn (tuỳ chọn) | câu trích (tuỳ chọn)`.
- Dòng bắt đầu bằng `>` là câu nhấn. Bỏ trống chữ thì lấy câu phụ đề tại mốc đó.
- `\N` trong tiêu đề là chỗ xuống dòng.
- Mốc được kéo về đầu dòng phụ đề gần nhất.

## Thiết kế (tóm tắt)

- **Backend:** module `src/utils/edit_styles/` (spec, store, graph, assets, chapters, runtime, preview). Pipeline gọi `EditPlan` ở các điểm: chọn clip, dựng ASS, input phụ, đoạn filter (GPU + CPU), vị trí sóng âm/CTA.
- **Quy tắc chi phí:** hình vẽ ASS tách thành mảnh nhỏ (libass tốn theo diện tích); phần tử bật/tắt theo thời gian dùng PNG một frame + `eof_action=repeat`.
- **Đường chia đoạn:** biểu thức dùng `T(ss)` = t + đầu đoạn; input cần đồng bộ thời gian được `-ss` theo đoạn.
- **Đợt 3:** `render_timeline(pieces)` cho tv_zoom và two_layer: đoạn đứng yên chạy GPU, đoạn chuyển chạy CPU, rồi concat.
- **Routes / batch / frontend:** như đã làm ở đợt 1; đợt 2 thêm upload ảnh `POST /edit-styles/<id>/images/<key>`.

## Kiểm tra

- pytest cho spec/store/routes/rotation/đồ thị GPU+CPU/chương.
- Render thật bằng `tests/test-render/_lab/run.py prod-styles` trên `sample_10a` (10 phút), mọi kiểu ≥ 11x, xem ảnh xem nhanh.
- Frontend: `npm --prefix frontend run build` + xem UI.
- Backend Flask không tự restart: sau khi sửa `.py` cần người dùng restart.
