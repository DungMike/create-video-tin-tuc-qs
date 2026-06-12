# Design Doc (Bổ sung): Subtitle Text-Effects, Font Options, Preview Video & Max-Chars-per-Frame cho /story-video

> Phạm vi: phần bổ sung này mở rộng plan subtitle đã có (re-segment strategy A, burn-in ASS, single + batch). Mọi tuyên bố về năng lực dưới đây đều dựa trên **bằng chứng render thật trên máy đích** (ffmpeg 8.1 Gyan full build, libass 0.17.4-21, font provider `directwrite (with GDI)`). Các hạng mục KHÔNG verify được sẽ được flag rõ ràng. Tài liệu này là **kế hoạch**, chưa phải lúc viết code.

Điểm móc nối quan trọng trong codebase hiện tại (đã đọc thực tế):
- `src/utils/story_video_pipeline.py` → `_apply_story_overlays()` đã re-encode bằng `h264_nvenc` (qua `FFmpegHelper.get_nvenc_flags()`) và đã build `filter_complex`. `style_filter` (TV effect) đã được prepend `[0:v]{style_filter}[styled]`. Đây chính là pass ta sẽ **append `ass=`** vào cuối `filter_complex`.
- `src/routes/story_video_routes.py` → các route `tv-effects/preview`, `tv-noise-demo`, `crt-demo` đã có sẵn khuôn mẫu "render mẫu ngắn rồi trả relativePath cho `/media/`".
- `src/processors/crt_effect_processor.py` → `generate_tv_effect_style_preview()` là **template chuẩn** cho preview: 720p, clamp 3-5s, `-stream_loop -1`, NVENC, fallback CUDA→CPU, timeout 120s, cache theo tên file `{style_id}.mp4`.
- `Config`: `TARGET_RESOLUTION="1920x1080"`, `TARGET_FPS=30`, `USE_GPU_NVENC=true`. → 1080p là khung mặc định để tính max-chars.
- Lưu ý: module `story_subtitles.py` (từ plan trước) **chưa tồn tại** trong repo; nó là module sẽ được tạo. Phần này mô tả các bộ phận sẽ thêm vào module đó.

---

## 1. Cơ chế hiệu ứng subtitle trong ffmpeg/libass

**SRT không có styling/animation.** SRT chỉ chứa index + timestamp + text thuần; nó không có khái niệm font, màu, vị trí, hay animation. Muốn có hiệu ứng, **bắt buộc convert sang ASS (Advanced SubStation Alpha)**. ASS có hai tầng style:

1. **Per-Style defaults** — khai báo trong section `[V4+ Styles]` (một dòng `Style:` định nghĩa Fontname, Fontsize, PrimaryColour, Outline, Shadow, Alignment, MarginV...). Mỗi `Dialogue:` tham chiếu một Style mặc định.
2. **Inline override tags** — đặt `{\tag...}` ngay trong phần text của dòng `Dialogue:`, ví dụ `Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,{\fad(300,300)}그날 밤, 진실.`. Tag override style cho riêng dòng/đoạn đó. Đây là cơ chế tạo gần như mọi hiệu ứng ở Mục 2.

**libass render ASS, filter video burn-in.** ffmpeg có hai filter:
- `subtitles=` — filter tổng quát, nhận SRT/VTT/ASS, có thêm `charenc`, `stream_index`, `force_style`, `wrap_unicode`; nội bộ tự convert sang ASS rồi giao cho libass.
- `ass=` — chỉ nhận ASS (không auto-convert SRT/VTT), nhưng **lộ thêm option `shaping`** (simple=FriBidi / complex=HarfBuzz cho ligature, combining marks, RTL/Indic) mà `subtitles=` không có.

Cả hai đều **burn pixel cố định vào frame → bắt buộc re-encode**. Vì pass `story_overlays` của ta **vốn đã re-encode** bằng nvenc, append filter này **không thêm pass mới**, chỉ thêm một bước cuối trong `filter_complex` (chi phí gần như chỉ là libass rasterize).

**Option có trên build này (đã verify từ evidence):**
- `subtitles=`: `filename/f`, `original_size`, `fontsdir`, `alpha`, `charenc`, `stream_index/si`, `force_style`, `wrap_unicode`.
- `ass=`: `filename/f`, `original_size`, `fontsdir`, `alpha`, `shaping` (-1 auto / 0 simple / 1 complex).
- Năng lực libass runtime: API 0x1704000, FriBidi 1.0.16, HarfBuzz 13.1.1, font provider `directwrite (with GDI)`.

**CẢNH BÁO ĐƯỜNG DẪN (load-bearing, đã verify):** filter parser của ffmpeg coi `:` là dấu ngăn option. Đường dẫn tuyệt đối có drive-colon (`e:\...`) **sẽ làm vỡ parse**. Evidence xác nhận cả `subtitles=tests/test-transcript/_probe/x.ass` và `ass=tests/test-transcript/_probe/x.ass` (đường dẫn **tương đối, không drive-colon**) đều load + render OK (exit 0) khi cwd = project root. → **Quy ước thiết kế: luôn truyền đường dẫn .ass dưới dạng RELATIVE so với cwd của tiến trình ffmpeg.**

**Vì sao chọn ASS chứ không drawtext.** Evidence ghi rõ `drawtext` là filter DUY NHẤT re-evaluate biểu thức **mỗi frame**, nên chỉ drawtext mới làm được: (1) typewriter reveal theo hàm liên tục của `t`; (2) marquee `x=w-t*speed` (đã verify `x='w-t*120'`); (3) text data-driven (`textfile=...reload=N`, timecode, `%{pts}`); (4) styling computed theo biểu thức (`fontcolor_expr`, `alpha='if(lt(t,1),t,1)'`). ASS/libass chỉ render timeline cố định (animation giới hạn trong `\t(...)`, `\move`, karaoke `\k` theo timestamp tác giả). **Ta cố ý dùng ASS** vì: (a) đủ cho toàn bộ hiệu ứng subtitle ở Mục 2; (b) text đã biết trước từ transcript (không cần data-driven); (c) ASS dùng DirectWrite, **không dính lỗi fontconfig** mà drawtext gặp ("Cannot load default config file" — drawtext by-name lookup cảnh báo, libass không ảnh hưởng). Hai hiệu ứng drawtext-only (typewriter liên tục theo `t`, và crawl mở vô hạn) sẽ được flag là **ngoài phạm vi** (xem Mục 6).

---

## 2. Catalog hiệu ứng (đã verify)

Tất cả hiệu ứng dưới đây đều `parseOk=true` **và** `visuallyConfirmed=true` trên máy đích (render lavfi 640x360, libass resolve "Malgun Gothic" → MalgunGothic, Hangul + Latin/VN render thật, không tofu). **Không có hiệu ứng nào FAIL verification** trong danh sách đã test.

### Entrance / Exit
| Hiệu ứng | ASS tag | Status | Khi nào dùng (story-video) |
|---|---|---|---|
| Fade in/out đơn giản | `\fad(in_ms,out_ms)` | parse OK + visual OK | **Mặc định** cho mọi caption: fade 300-500ms làm chuyển dòng mượt, không giật. |
| Complex fade 7-arg | `\fade(a1,a2,a3,t1,t2,t3,t4)` | parse OK + visual OK | Title/quote card điện ảnh: fade-in → giữ rõ → fade-out theo thời điểm chỉ định. |

### Emphasis / Karaoke highlight (style "trend" mạng xã hội)
| Hiệu ứng | ASS tag | Status | Khi nào dùng |
|---|---|---|---|
| Karaoke fill sweep (mượt) | `\kf` / `\K` (cs) | parse OK + visual OK | **Word-by-word fill** trôi trái→phải bám narration — đúng style social trend. Cần PrimaryColour ≠ SecondaryColour (vd trắng→vàng). |
| Karaoke step (hard) | `\k` (fill) / `\ko` (outline) | parse OK + visual OK | Highlight từng từ "bật sáng" tức thì theo nhịp; `\ko` chỉ wipe viền. |

### Motion
| Hiệu ứng | ASS tag | Status | Khi nào dùng |
|---|---|---|---|
| Slide / move | `\move(x1,y1,x2,y2[,t1,t2])` | parse OK + visual OK | Trượt caption vào từ ngoài khung; chuyển scene/chapter. |
| Pop / scale-up | `\fscx70\fscy70` + `\t(0,250,\fscx100\fscy100)` | parse OK + visual OK | Nhấn punchline/reveal: bật từ nhỏ→full size. |
| Rotation / tilt | `\frz<deg>` | parse OK + visual OK | Tạo cảm giác căng thẳng/lệch trục cho chapter title. |

### Style (tĩnh)
| Hiệu ứng | ASS tag | Status | Khi nào dùng |
|---|---|---|---|
| Outline + shadow màu | `\bord \shad \3c \4c` | parse OK + visual OK | **Giữ legibility trên footage rối/sáng** — viền đen dày + shadow màu nhấn mạnh. |
| Blur / soften edge | `\blur \be` | parse OK + visual OK | Caption dreamy/flashback; làm mượt viền răng cưa. |
| Letter spacing | `\fsp<px>` | parse OK + visual OK | Title-card "thoáng", nhấn một từ kịch tính. |
| Animated colour | `\t(0,dur,\1c&H..&)` | parse OK + visual OK | Đổi hue dần trong suốt dòng (vd ấm→đỏ căng) báo hiệu cảm xúc. |

### Positioning
| Hiệu ứng | ASS tag | Status | Khi nào dùng |
|---|---|---|---|
| Position + alignment | `\pos(x,y)` + `\an<1-9>` | parse OK + visual OK | Đặt caption tránh mặt người nói / lower-third; `\an8` top-center, `\an2` bottom-center. |

### Reveal
| Hiệu ứng | ASS tag | Status | Khi nào dùng |
|---|---|---|---|
| Clip wipe | `\clip(...)` + `\t` animate hình chữ nhật | parse OK + visual OK | Quét lộ chữ trái→phải cho dòng narration hồi hộp. |
| Combined pop+fade+move | `\fad` + `\move` + `\t(\fscx\fscy)` (stack) | parse OK + visual OK | Entrance mềm, hút mắt: slide vào + pop + fade cho dòng mở scene. |

### Preset mặc định đề xuất (compose từ tag đã verify)
- **"Clean"** — `Style` Default + `\fad(250,250)`. Đơn giản, an toàn nhất; mặc định toàn cục.
- **"Fade soft"** — `\fade(255,0,255,0,400,...)` (fade-in / hold / fade-out) cho cảm giác điện ảnh.
- **"Karaoke pop"** — per-word `\kf` (trắng→vàng) + `\fad(150,150)` toàn dòng (+ optional pop nhẹ `\fscx85\fscy85`→100). Đây là preset "trend".
- **"Emphasis bold"** — `\bord3\shad2\3c&H000000&\4c&H202020&` cho footage rối; tùy chọn `\fsp2`.

Mỗi preset = một dict tag-builder trong module subtitle (giống cách `TV_EFFECT_STYLES` là list dict). Preset chỉ kết hợp các tag đã `visuallyConfirmed`.

---

## 3. Quản lý & lựa chọn font (đã verify)

**Kết luận thực nghiệm cốt lõi:** trên máy đích, libass **resolve font BY FAMILY NAME** qua provider `directwrite (with GDI)`. Test với `ass=` filter **không truyền `fontsdir`** (font_*_a.png) đã resolve đúng và render đủ Hangul + Vietnamese + Latin cho cả 6 family verify (Malgun Gothic, Arial, Segoe UI, Tahoma, Verdana, Calibri). → **`fontsdir` KHÔNG bắt buộc cho font hệ thống.** Đây quyết định cách triển khai: chỉ cần set `Style Fontname` = tên family đã cài, bỏ qua `fontsdir`.

**Auto per-glyph fallback hoạt động:** một Style Fontname Latin (vd Arial) tự fallback sang MalgunGothic cho glyph Hangul; ngược lại Malgun Gothic fallback Arial cho VN diacritic `Đ` (U+0110). Nghĩa là **một dòng mixed KR+VI render được dù chọn family nào**.

**Font đã verify (visuallyConfirmed):**
- **Korean-capable:** `Malgun Gothic` (`malgun.ttf` regular / `malgunbd.ttf` bold / `malgunsl.ttf` semilight) — **font Hangul duy nhất cài thật** trên máy. Render Hangul native; thiếu VN diacritic precomposed (vd `ế` U+1EBF, `Đ` U+0110) → DirectWrite tự fallback Arial. **Không có** Batang/Gulim/Nanum/Noto KR/Source Han.
- **Latin/Vietnamese-capable (full VN diacritic native):** `Arial`, `Segoe UI`, `Tahoma`, `Verdana`, `Calibri`, và (chưa render-test trực tiếp nhưng có file) `Times New Roman`, `Georgia`, `Cambria`.

**Thiết kế tính năng font (cho phép user "tìm, update, chọn"):**

1. **Font registry / scan.** Module subtitle có `scan_fonts()` quét 2 nguồn:
   - System: `C:/Windows/Fonts` (đã có sẵn 119 file; `fontInfo.fontsDir`).
   - Project user-fonts: thêm `Config.STORY_FONTS_DIR = STORAGE_DIR/story_fonts` cho font user tự thả vào.
   Mỗi entry registry: `{ family, files{regular,bold,italic,...}, supportsKorean: bool, supportsVietnamese: bool, source: "system"|"user" }`. Cờ supports* xác định bằng cách probe coverage (đọc cmap qua fonttools nếu có, hoặc whitelist family đã verify + heuristic "có file Hangul→KR"). `fc-list` **không có** trên máy → KHÔNG dựa fontconfig; dùng danh sách verify + scan file là chính.
   - Cache registry vào `STORY_FONTS_DIR/fonts_index.json`, invalidation theo mtime thư mục (giống `STORY_TV_NOISE`/library index pattern).

2. **Font chảy vào ASS như thế nào.** Family user chọn → ghi vào dòng `Style:` field **Fontname** trong file .ass sinh ra (per-Style default). Vì family-name resolution hoạt động, **không truyền `fontsdir`** trong filter `ass=`/`subtitles=`. Pass `story_overlays` re-encode không đổi.
   - **Chỉ khi** font là user-font không cài system-wide (DirectWrite không thấy): fallback truyền `fontsdir`. **Cảnh báo escaping (verify):** form `fontsdir=C\:/Windows/Fonts` (1 backslash) **FAIL** ("No option name near /Windows/Fonts"). Form **chạy được**: double-backslash `fontsdir=C\\:/Windows/Fonts` HOẶC single-quote `fontsdir='C\:/Windows/Fonts'`. Và **đừng spawn qua Git-Bash/MSYS** (mangle path) — pipeline spawn qua Python nên an toàn. Vì ffmpeg spawn trực tiếp bằng subprocess (không qua shell), chỉ cần double-backslash drive-colon là đủ. Để tránh hẳn rắc rối: **ưu tiên copy user-font vào một thư mục project rồi truyền `fontsdir` đến thư mục đó (no drive-colon nếu để relative), hoặc cài family-name resolution là chính.**

3. **Thêm font mới (drop → scan → dropdown).** User upload `.ttf/.otf` qua route mới `POST /api/story-video/subtitle-fonts` → lưu vào `STORY_FONTS_DIR` → gọi `scan_fonts()` re-index → family mới xuất hiện trong `GET /api/story-video/subtitle-fonts`. Frontend render dropdown từ list này (kèm badge "KR"/"VN" coverage).

4. **Khuyến nghị Korean-default.** Nội dung Korean-primary → default `Fontname = "Malgun Gothic"` (Hangul native, Latin/VN auto-fill Arial). Nội dung Latin/VN-primary → default `Arial` hoặc `Segoe UI` (VN diacritic native, Hangul auto-fill Malgun). Lưu ý: auto-fallback có thể trộn metric/weight giữa dòng — **không có font cài sẵn nào phủ ĐỦ Hangul + ĐỦ VN diacritic** (Malgun thiếu VN composed). Nếu cần một face đồng nhất tuyệt đối cho cả hai script, phải để user thêm font (vd Noto Sans CJK + một face VN).

5. **Re-encode không bị ảnh hưởng bởi font.** libx264/nvenc encode độc lập với việc libass rasterize font nào; chọn font chỉ đổi pixel vào frame, không đổi codec path.

---

## 4. Cấu hình số ký tự tối đa / khung hình

Cấu hình này điều khiển bước **re-segment strategy A** (split câu + chia thời lượng theo tỉ lệ) đã chốt.

**Config (đề xuất, mặc định cho 1080p):**
```
maxCharsPerLine = 42      # ký tự/dòng (≈ phù hợp 1920px @ Fontsize ~54)
maxLines        = 2       # số dòng hiển thị đồng thời
→ maxCharsPerFrame = maxCharsPerLine * maxLines = 84
```
Cho phép override per-request (single) và per-batch (sharedConfig). Lưu vào `Config.STORY_SUBTITLE_MAX_CHARS_PER_LINE` / `_MAX_LINES` với default trên.

**Cách splitter đóng gói (packing):**
1. Bắt đầu từ cue gốc của strategy A (1 câu + [start,end] proportional timing).
2. Nếu `len(text) ≤ maxCharsPerFrame` → giữ nguyên 1 frame; line-wrap nội bộ thành ≤ `maxLines` dòng bằng **manual `\N`** (xem dưới).
3. Nếu vượt → greedy pack **word-by-word** (token theo space cho Latin/VN) vào block sao cho mỗi block ≤ `maxCharsPerFrame`, tách thành nhiều cue (frame) liên tiếp.
4. Trong mỗi block, chèn `\N` để bẻ dòng tại ranh giới word gần nhất sao cho mỗi dòng ≤ `maxCharsPerLine`.

**Line-wrapping — `\q` vs `\N`:** ASS có `WrapStyle`/`\q` (0=smart, 1=end-of-line, 2=no-wrap, 3=smart-bottom-wide) để libass tự wrap, NHƯNG kết quả phụ thuộc font metric runtime → khó kiểm soát chính xác. **Ưu tiên manual `\N`** do splitter tự tính (đếm ký tự), đặt `WrapStyle: 2` (no auto-wrap) để libass không tự bẻ thêm. Điều này cho kết quả deterministic, khớp với cap đã cấu hình. (Evidence không test trực tiếp `\q`/`\N`, nhưng `\N` là tag ASS chuẩn thuộc cùng cơ chế override đã verify; flag nhẹ ở Mục 6.)

**Chia lại timing khi 1 cue → nhiều frame:** giữ nguyên triết lý proportional của strategy A — thời lượng cue gốc chia cho các block con **theo tỉ lệ độ dài** (số ký tự, vì narration đọc đều xấp xỉ theo ký tự):
```
block_dur = cue_dur * (len(block) / sum(len(all_blocks)))
```
Cộng dồn start/end để các frame nối liền, không hở/không chồng. Áp min-duration sàn (vd ≥ 0.8s/frame) để frame ngắn không nhấp nháy; nếu tổng min vượt cue_dur thì giảm số block (nới `maxCharsPerFrame` mềm cho cue đó).

**Edge cases:**
- **Một từ dài hơn cap** (URL, từ ghép): không tách giữa từ ở Latin → cho phép từ đó vượt `maxCharsPerLine` (overflow 1 dòng) thay vì cắt sai; optional `\fscx` thu nhỏ ngang để vừa khung.
- **CJK/Korean không có space:** **đo & wrap theo SỐ KÝ TỰ (grapheme), KHÔNG theo space.** Splitter phải có nhánh: nếu text chứa Hangul/CJK → tokenize theo ký tự (hoặc cụm eojeol nếu phát hiện space tiếng Hàn), bẻ `\N` theo đếm ký tự. Đây là điểm **quan trọng** — packing theo space sẽ fail hoàn toàn với Hàn. (libass shaping Hangul đã verify render đúng; vấn đề thuần là logic đếm/bẻ phía Python.)
- **Mixed KR+VI 1 dòng:** đếm theo ký tự cho cả hai để cap ổn định (1 Hangul ≈ rộng gấp ~2 Latin → có thể dùng "visual width" = Hangul tính 2, Latin tính 1 nếu muốn cap chính xác hơn; default đơn giản: đếm grapheme = 1).

---

## 5. Cơ chế video preview

**Mục tiêu:** render một mẫu NGẮN với font + effect + style + max-chars người dùng chọn, chi phí thấp, trả mp4 nhỏ cho UI để "soi" trước khi commit full render. **Tái dùng nguyên template `generate_tv_effect_style_preview()`** đã có.

**Đặt ở đâu:**
- Route mới trong `story_video_routes.py`: `POST /api/story-video/subtitle-preview` (body: `{ font, effectId/preset, styleParams, maxCharsPerLine, maxLines, sampleClipId? }`), trả `{ previewPath }` (relative cho `/media/`) — y hệt cách `generate_tv_effect_style_preview` route trả.
- Helper render nhỏ trong module subtitle (vd `render_subtitle_preview(...)`), mirror crt processor.

**Cách render (low cost, CPU OK):**
1. Nền: lấy `_find_sample_clip()` (đã có trong routes) — một clip library, hoặc fallback **solid bg** (`lavfi color`) nếu thư viện rỗng. Dùng `-stream_loop -1 -i sample -t 4` như preview hiện tại.
2. Subtitle mẫu: 3-4 dòng cứng có dấu KR + VI (vd "그날 밤, 진실이 깨어났다." / "Đêm đó sự thật thức tỉnh." / "The truth.") để user thấy ngay font/effect/wrap trên cả hai script. Sinh file `.ass` tạm với đúng Style (font đã chọn) + tag effect/preset + áp `maxCharsPerLine/maxLines` (chạy splitter Mục 4 trên text mẫu để thấy wrap thật).
3. Filter: `vf = "scale=1280:-2,...,ass=<relative_ass_path>,format=yuv420p"` (720p như TV preview). **Đường dẫn .ass phải RELATIVE** (Mục 1) — sinh file .ass dưới một thư mục con của project rồi truyền path relative so với cwd. Bỏ `fontsdir` (family-name resolution).
4. Encode: clamp duration 3-5s, NVENC + fallback CUDA→CPU + timeout 120s — **copy nguyên** logic `_build_cmd`/retry của crt processor. Preview hoàn toàn chạy CPU được nếu cần (libass rasterize rẻ; evidence: 30-frame null render ~24-63x speed).

**Cache:** đặt tên file output theo hash `sha1(font|effectId|styleParamsJSON|maxCharsPerLine|maxLines|sampleClipId)` → `STORY_SUBTITLE_PREVIEW_DIR/{hash}.mp4`. Nếu file tồn tại → trả ngay, **không render lại** (giống cache `{style_id}.mp4` của TV preview nhưng key giàu hơn vì tổ hợp lớn). Dọn file cũ theo mtime (LRU) khi thư mục vượt ngưỡng.

**Không phá batch đang chạy:** preview phải **tiny + ngắn** (720p, ≤5s, 1 clip loop). Để tránh GPU contention với batch (batch dùng nvenc full-res):
- Mặc định preview encode bằng **libx264 CPU** (`get_nvenc_flags()` đã tự rơi về libx264 khi `USE_GPU_NVENC=false`; với preview ta có thể ép CPU bất kể config) → 0 tranh chấp NVENC session với batch.
- Hoặc nếu dùng NVENC, render với timeout ngắn + chấp nhận fallback CPU; KHÔNG dùng `-hwaccel cuda` decode khi batch đang nặng. Giữ preview ở mức "best-effort, low priority".

---

## 6. Tích hợp vào pipeline & rủi ro

**Điểm móc nối (mỗi mảnh vào đâu):**

1. **`story_subtitles.py` (module từ plan trước — sẽ tạo, nay mở rộng):**
   - `scan_fonts()` + font registry + cache index (Mục 3).
   - `build_ass(cues, style_params, effect_preset, font, maxCharsPerLine, maxLines)` → sinh file `.ass`: section `[V4+ Styles]` (Fontname = font đã chọn) + `[Events]` (Dialogue với inline `{\tag}` của preset, `\N` wrap từ splitter).
   - `resegment_strategy_a(...)` mở rộng để nhận `maxCharsPerLine/maxLines` (Mục 4), nhánh CJK đếm theo ký tự.
   - `render_subtitle_preview(...)` (Mục 5).
   - **ASS escaping helper:** escape `\`, `{`, `}` trong text gốc trước khi nhúng vào Dialogue để text người dùng không vô tình thành tag.

2. **`story_video_pipeline.py` → `_apply_story_overlays()` (và các nhánh `_apply_precomposed_story_overlay`, `_apply_tv_effect_only`):** append `ass=<relative_path_to_ass>` vào **cuối** `filter_complex`, ngay trước/sau bước `format=yuv420p` cuối (sau overlay, để chữ nằm trên TV-noise/waveform). Tất cả nhánh re-encode đã có → chỉ thêm filter, không thêm pass. **Phải đảm bảo cwd của tiến trình ffmpeg sao cho path .ass là relative không drive-colon** (sinh .ass vào `temp` dir và truyền path relative tính từ cwd; nếu cwd không kiểm soát được, cân nhắc `os.path.relpath` hoặc dùng `fontsdir`-style escaping cho `f=` — nhưng evidence chỉ verify path relative, nên ưu tiên relative).

3. **`story_video_routes.py`:** plumb các param mới qua `config_dict` (single `/create`) và `sharedConfig` (batch `/batch/create`): `subtitleEnabled`, `font`, `subtitleEffect/preset`, `subtitleStyleParams`, `maxCharsPerLine`, `maxLines`. Route mới: `GET/POST/DELETE /api/story-video/subtitle-fonts`, `POST /api/story-video/subtitle-preview`.

4. **Frontend:** thêm controls (font dropdown từ registry + badge KR/VN, effect/preset picker, max-chars sliders) + nút Preview gọi `/subtitle-preview` rồi phát mp4 trả về (giống flow TV-effect preview hiện có).

**Rủi ro & ẩn số còn lại (từ evidence):**

- **Path drive-colon (load-bearing):** đường dẫn .ass tuyệt đối `e:\...` **chắc chắn vỡ parse**. Mọi pass burn-in PHẢI dùng relative path. → Cần test integration với cwd thực của tiến trình app (chưa verify trong context production cwd; evidence chỉ verify cwd = project root).
- **`fontsdir` escaping:** form 1-backslash FAIL; chỉ double-backslash (`C\\:/...`) hoặc single-quote-value chạy. Vì ta dùng family-name resolution (không cần fontsdir), rủi ro này **né được** trừ khi dùng user-font chưa cài system. **KHÔNG** chạy lệnh fontsdir qua Git-Bash/MSYS (mangle path) — pipeline spawn qua Python nên OK.
- **Không có font phủ đủ KR+VI trong một face:** Malgun thiếu VN composed diacritic; mọi dòng mixed dựa vào auto-fallback (có thể lệch metric/weight giữa dòng). Nếu khách yêu cầu một face đồng nhất, **bắt buộc user thêm font** (chưa có sẵn).
- **CJK wrapping (logic Python, chưa code):** packing/wrap **không được dựa space** cho Hàn; phải đếm grapheme. Render Hangul đã verify OK, nhưng logic đếm/bẻ là phần dễ sai nhất — cần unit test riêng cho text Hàn thuần, VN thuần, và mixed.
- **`\N` vs `\q` chưa verify trực tiếp:** evidence không render thử bẻ dòng manual `\N` hay `WrapStyle`. Là tag/feature ASS chuẩn cùng cơ chế đã verify, rủi ro thấp nhưng **nên probe nhanh** một dòng nhiều `\N` trước khi chốt manual-wrap.
- **drawtext-only effects ngoài phạm vi:** typewriter reveal liên tục theo `t` và crawl/marquee mở vô hạn **không làm được bằng ASS** (evidence). Nếu sau này cần, phải dùng drawtext (kèm rủi ro fontconfig "Cannot load default config file" → bắt buộc `fontfile=` đường dẫn tuyệt đối thay vì by-name). Hiện **không đưa vào** plan này.
- **Preview GPU contention:** nếu preview dùng NVENC trùng lúc batch chạy, có thể tranh session encoder; mitigations ở Mục 5 (ép libx264 CPU cho preview) là khuyến nghị mặc định.
- **Thứ tự filter:** đặt `ass=` sau overlay TV-noise/waveform để chữ luôn nằm trên cùng; cần verify không bị `format=yuv420p` trung gian làm mất alpha của text (libass burn trực tiếp lên frame YUV nên thực tế an toàn, nhưng xác nhận khi tích hợp).

**File liên quan (absolute):**
- `E:\CRAWL VIDEO - AUDIO - QS\src\utils\story_video_pipeline.py` (append `ass=` tại `_apply_story_overlays`/`_apply_precomposed_story_overlay`/`_apply_tv_effect_only`)
- `E:\CRAWL VIDEO - AUDIO - QS\src\routes\story_video_routes.py` (plumb params + route font/preview)
- `E:\CRAWL VIDEO - AUDIO - QS\src\processors\crt_effect_processor.py` (template preview `generate_tv_effect_style_preview`, dòng 325-375)
- `E:\CRAWL VIDEO - AUDIO - QS\src\utils\ffmpeg_helper.py` (`get_nvenc_flags`, `run_command`, `probe_duration`)
- `E:\CRAWL VIDEO - AUDIO - QS\src\config.py` (thêm `STORY_FONTS_DIR`, `STORY_SUBTITLE_PREVIEW_DIR`, `STORY_SUBTITLE_MAX_CHARS_PER_LINE`, `STORY_SUBTITLE_MAX_LINES`; hiện có `TARGET_RESOLUTION=1920x1080`, `TARGET_FPS=30`)
- `story_subtitles.py` (module mới từ plan trước — chưa tồn tại trong repo)