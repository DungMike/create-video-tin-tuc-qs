# 03. App dựng video nền: Story Video Studio

> Thư mục: `C:\Users\hiv\Documents\Cua Dung\create-video-tin-tuc-qs` (repo hiện tại, gọi tắt `SVS`).
> Vai trò trong pipeline: (1) **host orchestrator**; (2) dựng video **loại 2** từ audio + `.srt` + thư viện clip nền 5 s, kèm hiệu ứng TV, khung, sóng âm, CTA và phụ đề.
> `file:line` tính từ gốc repo. Các dòng route trỏ vào `src/routes/story_video_routes.py`, trừ khi ghi rõ file khác.

[← 02 TST Studio](02-app-tst-studio.md) · [Tổng quan](00-tong-quan-kien-truc.md) · [04 Thiết kế pipeline →](04-thiet-ke-pipeline.md)

---

## 1. Kiến trúc và khởi động

| Phần | Vị trí |
|---|---|
| Backend Flask | `src/web_app.py` (bootstrap, `/api/voices`, `/media`, SPA catch-all); blueprint `story_video_bp` ở `src/routes/story_video_routes.py` (khoảng 4900 dòng); `youtube_download_bp` ở `src/routes/youtube_download_routes.py`. Đăng ký blueprint: `src/web_app.py:21-24`. |
| Xử lý media | `src/utils/story_video_pipeline.py` (render), `story_video_batch.py` (hàng đợi batch), `story_subtitles.py` (SRT → ASS), `src/processors/crt_effect_processor.py` (hiệu ứng TV) |
| Cấu hình | `src/config.py` đọc `.env` |
| Frontend | React + Vite ở `frontend/`. Router: `frontend/src/router.tsx` (catch-all `*` → `/story-video` ở L27-30). Thanh menu: `components/top-nav.tsx:10-19`. Client: `lib/api.ts` (`requestJson<T>` L101-117). Kiểu dữ liệu: `types/api.ts`. |
| CSDL | File JSON dưới `STORAGE_DIR`. MongoDB tuỳ chọn (`docker-compose.yml`: `mongo:7` ở `127.0.0.1:27017`), chỉ cần cho chế độ clip `once`, lịch sử từ khoá và image clips. |

**Khởi động:**
- **Dev:** `npm run dev` ở gốc repo, chạy `.\venv\Scripts\python -m src.web_app` (Flask **5006**) và Vite (**5176**, proxy `/api` và `/media`).
- **Prod:** `npm run build` → `waitress-serve --listen=0.0.0.0:<port> wsgi:app`. Waitress bỏ qua `WEB_PORT`.
- **Luôn chạy từ gốc repo:** `logs/app.log`, `frontend/dist` và đường dẫn ASS tương đối đều phụ thuộc thư mục hiện hành.
- Khi khởi động, `resume_batch_queue()` chạy lại các batch còn dở. Hàm này chỉ được gọi ở `src/web_app.py:99-105` (`__main__`) và `wsgi.py`, không chạy lúc import.

**Phụ thuộc:**
- `ffmpeg`/`ffprobe` lấy từ PATH, không cấu hình được. Cần bản có `h264_nvenc`, `overlay_cuda`, `scale_cuda`, `hwupload_cuda` và libass.
- Python: Flask, waitress, requests, bs4, Pillow, python-dotenv, psutil, yt-dlp, pymongo, mongomock, pytest.

---

## 2. Danh sách endpoint

**Quy ước chung:**
- Lỗi có dạng `{"error":{"code","message"}}` (L60-61).
- **Không có xác thực, không có CORS.**
- Server nhận đường dẫn tuyệt đối bất kỳ, nên bắt buộc giữ `WEB_HOST=127.0.0.1`.

### 2.1 Ngoài blueprint chính

| Method | Path | Vị trí | Ghi chú |
|---|---|---|---|
| GET | `/api/voices` | `web_app.py:37` | `{voices, defaultVoiceId}` từ `STORAGE_DIR/voices/index.json` (giọng của TTS remote) |
| GET | `/media/<path>` | `web_app.py:42` | Ánh xạ: `output/…` → `OUTPUT_DIR`; `output_E/…` → `OUTPUT_DIR` trên ổ E; `story_raw/…` → `STORY_RAW_DIR`; còn lại → `STORAGE_DIR` |
| GET | `/`, `/<path>` | `web_app.py:72-73` | Phục vụ SPA (`frontend/dist`), trả 503 nếu chưa build |
| POST | `/api/youtube-download/jobs` | `youtube_download_routes.py:21` | `{format: mp3|mp4|both, urls[]}` |
| GET | `/api/youtube-download/jobs/<job_id>` | :47 | |
| POST | `/api/youtube-download/jobs/<job_id>/cancel` | :55 | |
| GET | `/api/youtube-download/jobs/<job_id>/items/<index>/<kind>` | :63 | Tải file |

### 2.2 Render lẻ

| Method | Path | Dòng | Ghi chú |
|---|---|---|---|
| POST | `/api/story-video/create` | 2847 | JSON, hoặc multipart `payload` + file `audio` / `subtitle` (`.srt`) / `chapters` → 202 `{storyId:"sv-xxxxxxxx"}`. **Không có key JSON cho đường dẫn srt**; srt phải gửi dạng upload. Chạy thread riêng, **không qua hàng đợi batch**. |
| GET | `/api/story-video/<story_id>/progress` | 2982 | `{storyId, status: pending|running|completed|failed|cancelled, stage, percent, message, outputName, result{videoPath}, error, startedAt, updatedAt}` |
| GET | `/api/story-video/<story_id>/result` | 2995 | `{videoPath}` hoặc 404 `video_not_ready` |
| POST | `/api/story-video/<story_id>/cancel` | 3021 | Ghi file đánh dấu `cancel.requested` |

### 2.3 Batch (pipeline dùng nhóm này)

| Method | Path | Dòng | Ghi chú |
|---|---|---|---|
| POST | `/api/story-video/batch/drive-audio-imports` | 3037 | `{folderUrl}` → 202 `{sessionId}`. Tải audio từ thư mục Google Drive công khai. |
| GET | `/api/story-video/batch/drive-audio-imports/<session_id>` | 3069 | Manifest |
| POST | `/api/story-video/batch/local-folder` | 3106 | `{path, recursive}` → ghép audio với `.srt` / `.chapters.txt` cùng tên |
| GET | `/api/story-video/output-drives` | 3187 | `{drives:[{drive, outputDir, freeBytes, totalBytes}], defaultDrive}` |
| **POST** | **`/api/story-video/batch/create`** | 3197 | Xem §4 → 202 `{batchId:"sb-xxxxxxxx", queuePosition}` |
| GET | `/api/story-video/batch/queue` | 3502 | `{activeBatchId, batches:[…]}`. Lịch sử giữ trong RAM, 20 batch gần nhất. |
| **GET** | **`/api/story-video/batch/<batch_id>/progress`** | 3531 | Xem §4 |
| POST | `/api/story-video/batch/<batch_id>/items/<story_id>/cancel` | 3586 | |
| POST | `/api/story-video/batch/<batch_id>/cancel` | 3599 | |
| POST | `/api/story-video/batch/<batch_id>/retry-failed` | 3612 | → 202 `{batchId:"<id>-retry", retryCount, queuePosition}`. ⚠️ Làm mất `tvEffectStyleId`. |

### 2.4 Thư viện clip

| Method | Path | Dòng |
|---|---|---|
| GET | `/api/story-video/library?libraryId&page&per_page&tags` | 257 |
| POST | `/api/story-video/library/download` `{links[], tags[], libraryId}` | 296 |
| POST | `/api/story-video/library/upload` (multipart `files[]`) | 362 |
| GET | `/api/story-video/library/download-progress/<session_id>` | 443 |
| GET | `/api/story-video/video-search/<provider>?q&page&per_page` (pixabay \| pexels) | 455 |
| POST | `/api/story-video/library/import-selected` | 497 |
| POST / GET | `/api/story-video/library/prefetch` | 612, 692 |
| GET / DELETE | `/api/story-video/library/prefetch/<session_id>` | 706, 853 |
| GET | `/api/story-video/library/prefetch/<session_id>/items/<item_id>/poster` | 716 |
| POST | `/api/story-video/library/prefetch/<session_id>/cancel` · `/discard-items` · `/commit` | 745, 755, 774 |
| POST / GET | `/api/story-video/library/harvest` | 884, 1022 |
| GET / DELETE | `/api/story-video/library/harvest/<job_id>` | 1029, 1289 |
| GET | `/api/story-video/library/harvest/<job_id>/items` | 1039 |
| POST | `/api/story-video/library/harvest/<job_id>/cancel` · `/resume` · `/items/delete` · `/commit` · `/commit/pause` | 1076, 1086, 1102, 1158, 1278 |
| GET | `/api/story-video/search-keywords` · `/search-keywords/lookup` | 940, 957 |
| DELETE | `/api/story-video/search-keywords/<provider>?keyword=` | 977 |
| GET | `/api/story-video/clip-usage/summary?libraryIds=` | 994 |
| DELETE | `/api/story-video/library/<clip_id>` | 1669 |
| POST | `/api/story-video/library/bulk-delete` | 1688 |
| PATCH | `/api/story-video/library/<clip_id>/tags` | 1724 |
| GET | `/api/story-video/library/stats` | 1754 |
| GET / POST | `/api/story-video/libraries` | 1814, 1823 |
| PATCH / DELETE | `/api/story-video/libraries/<library_id>` | 1834, 1848 |
| GET / POST | `/api/story-video/intros` | 1871, 1876 |
| PATCH / DELETE | `/api/story-video/intros/<intro_id>` | 1905, 1916 |
| POST | `/api/story-video/library/bake` `{sourceLibraryId, name, styleId|params, motion, motionZoom}` | 1969 |
| GET | `/api/story-video/library/bake/<job_id>` | 2057 |
| POST | `/api/story-video/library/bake/<job_id>/cancel` · `/pause` · `/resume` | 2067, 2079, 2101 |
| GET | `/api/story-video/library/normalize/scan` | 2142 |
| POST | `/api/story-video/library/normalize` `{libraryId | scope:"all", includeAll}` | 2156 |
| GET | `/api/story-video/library/normalize/<job_id>` | 2190 |
| POST | `/api/story-video/library/normalize/<job_id>/cancel` | 2200 |

### 2.5 Image clips (ảnh → clip Ken Burns, cần Mongo)

| Method | Path | Dòng |
|---|---|---|
| GET | `/api/story-video/image-clips/effects` | 1347 |
| POST / GET | `/api/story-video/image-clips/jobs` | 1363, 1426 |
| GET / DELETE | `/api/story-video/image-clips/jobs/<job_id>` | 1433, 1443 |
| GET | `/api/story-video/image-clips/jobs/<job_id>/items` | 1457 |
| POST | `/api/story-video/image-clips/jobs/<job_id>/cancel` · `/items/delete` · `/commit` · `/commit/cancel` | 1488, 1498, 1549, 1606 |
| PATCH | `/api/story-video/image-clips/jobs/<job_id>/items/<item_id>` | 1530 |
| GET | `/api/story-video/image-clips/cursors` | 1616 |
| POST | `/api/story-video/image-clips/cursors/<provider>/reset` | 1647 |

### 2.6 Hiệu ứng TV, phụ đề, overlay, khung

| Nhóm | Endpoint (dòng) |
|---|---|
| CRT / TV | GET `/crt-presets` (2216); POST `/crt-demo` (2249); GET `/tv-effects` (2342); POST `/tv-effects/select` `{styleId}` (2370; đổi style **toàn cục**); POST `/tv-effects/custom` (2381); POST `/tv-effects/preview` (2399) |
| Phụ đề | GET / POST `/subtitle-fonts` (2694, 2704); GET `/subtitle-presets` (2736); GET / POST `/subtitle-styles` (2762, 2767); POST `/subtitle-styles/restore-builtin` (2782); DELETE `/subtitle-styles/<style_id>` (2791); POST `/subtitle-preview` (2803) |
| Sóng âm | GET / POST `/waveform-overlays` (3721, 3739); PATCH / DELETE `/waveform-overlays/<overlay_id>` (3814, 3862) |
| CTA | GET / POST `/cta-overlays` (3876, 3893); PATCH / DELETE `/cta-overlays/<overlay_id>` (3915, 3961) |
| Edit styles | GET / POST `/edit-styles` (3976, 4015); PATCH / DELETE `/edit-styles/<style_id>` (4032, 4044); POST / DELETE `/edit-styles/<style_id>/images/<key>` (3986, 4002); POST `/edit-styles/<style_id>/preview` (4053) |
| Khung (decor) | GET / POST `/decor-images` (4082, 4102); GET `/decor-image-search/<provider>` (4126); POST `/decor-images/import` (4170); PATCH `/decor-images/<image_id>` (4194); PATCH `/decor-images/settings` (4256); PATCH `/decor-images/group` (4307); POST `/decor-images/reset-used` (4331); POST `/decor-images/purge-used` (4353); DELETE `/decor-images/<image_id>` (4377); POST `/decor-images/<image_id>/detect-frame` (4388); POST `/decor-images/<image_id>/frame-preview` (4415) |
| Nhiễu TV / sparkle | GET / POST `/tv-noise-overlays` (4486, 4496); POST `/tv-noise-overlays/import-youtube` (4519); GET `/tv-noise-overlays/jobs/<session_id>` (4779); PATCH / DELETE `/tv-noise-overlays/<overlay_id>` (4788, 4812); POST `/tv-noise-demo` (4823); GET `/sparkle-presets` (4687); POST `/sparkle-overlays` (4694) |
| Preview hiệu ứng | GET / POST `/effect-preview/sources` (4584, 4591); DELETE `/effect-preview/sources/<source_id>` (4610); POST `/effect-preview/render` (4619); GET `/effect-preview/jobs/<session_id>` (4678) |

(Tất cả có tiền tố `/api/story-video`. Tổng cộng 131 route trong blueprint chính; danh sách trên đã đối chiếu đủ với từng decorator.)

---

## 3. Audio, TTS và phụ đề

**Các cách đưa audio vào** (`story_video_pipeline.py _prepare_audio` L683-727):

| Cách | Áp dụng cho | Ghi chú |
|---|---|---|
| Upload | `audio` (lẻ) hoặc `audio_files[]` (batch) | |
| **Đường dẫn tuyệt đối trên máy** | `inputType:"audio_file"`, `inputValue:"D:/…/x.mp3"` | Kiểm tra bằng ffprobe rồi chép vào thư mục tạm |
| Thư mục Google Drive | chỉ batch, `drive_audio` token | Chỉ lấy audio, không lấy srt |
| Link Google Docs | `inputType:"script_url"` | Đi qua TTS sẵn có của app (xem dưới) |

**TTS sẵn có của app** (`src/utils/tts_audio.py create_audio_from_google_doc` L1084-1355):
- Xuất Google Docs ra text.
- Chia theo `TTS_MAX_CHARS`, gọi **API MiniMax từ xa**: `POST {TTS_API_BASE_URL}/api/minimax/createtask2` (header `T-API-KEY`), sau đó poll.
- Ghép thành `STORAGE_DIR/audio/generated/<name>.mp3`.
- **Không sinh SRT, không đọc `.docx` hay `.txt` trên máy.**
- Đây **không phải** app DGT ở [01](01-app-tts-dgt.md). Pipeline mới dùng DGT, vì DGT tạo được SRT và dùng được giọng clone.

**Phụ đề: chỉ dùng `.srt` có sẵn, không tự nhận dạng giọng** (không có Whisper). Không có `.srt` thì video không có phụ đề.

Luồng xử lý (`_prepare_subtitle_ass` L1194-1259):
1. `parse_srt` (`story_subtitles.py:190-232`)
2. `resegment` (L357-427): chia lại từng cue theo câu và số ký tự (`maxCharsPerLine` mặc định 42, `maxLines` mặc định 2). Thời gian chia theo tỉ lệ độ dài chữ, mỗi màn tối thiểu 0.8 s.
3. Cắt theo độ dài audio.
4. `build_ass` rồi burn bằng ffmpeg `ass=`.

→ **Cue dài cỡ đoạn văn của DGT dùng thẳng được.**

**Tuỳ chọn phụ đề** (`_subtitle_config_from_payload` L2609-2674):
- `subtitleFont`
- `subtitlePreset` (mặc định `clean`)
- `subtitleMaxCharsPerLine`, `subtitleMaxLines`
- `subtitleFontScale` (0.3-4)
- `subtitleTextColor`, `subtitleOutlineColor`, `subtitleBackColor` (`#RRGGBB`)
- `subtitleOutlineWidth` (0-20)
- `subtitleBackgroundEnabled`, `subtitleBackOpacity` (0-1)
- Chỉ ở batch: `subtitleStyleIds[]` (xoay vòng giữa các style đã lưu)

25 preset có sẵn: clean, fade_soft, karaoke_pop, emphasis_bold, yellow_pop, cyan_cool, pink_hot, green_mint, orange_warm, outline_bold, outline_gold, neon_cyan, neon_pink, box_dark, box_solid, tiktok_yellow, banner_red, pop_in, karaoke_box, word_bounce, word_bounce_box, rainbow_cycle, color_pulse, karaoke_zoom, karaoke_neon.

---

## 4. Công thức batch cho pipeline

> ⚠️ **`batch/create` đọc `request.form["payload"]` trước** (L3209-3213). Gửi body `application/json` thì nó đọc thành `"{}"`, parse thành công nên không rơi vào nhánh `get_json`, và trả `400 empty_items`. **Phải gửi multipart hoặc x-www-form-urlencoded có field `payload`.** Lỗi này sẽ được sửa ở P2.

```http
POST http://127.0.0.1:5006/api/story-video/batch/create
Content-Type: multipart/form-data

payload={
  "items": [
    {"inputType":"audio_file","inputValue":"C:/storage/pipeline/runs/pl-ab12cd34/items/it-01/tap-01.mp3",
     "subtitleFile":"C:/storage/pipeline/runs/pl-ab12cd34/items/it-01/tap-01.srt",
     "outputName":"tap-01"}
  ],
  "sharedConfig": {
    "libraryIds":["<library id>"], "outputDrive":"D",
    "outputSubdir":"pipeline-pl-ab12cd34",   // ⚠️ CHƯA được route đọc, sẽ thêm ở P2 (xem ghi chú dưới)
    "optimizeMode": false, "clipUsageMode":"reuse",
    "subtitlePreset":"clean", "subtitleFont":"Kanit",
    "tvEffectStyleId": null, "waveformOverlayIds": [], "ctaOverlayIds": [],
    "decorImageIds": [], "layoutIds": [], "modifierIds": [], "subtitleStyleIds": []
  }
}
→ 202 {"batchId":"sb-…","queuePosition":n}
```

> **`outputSubdir`:** `StoryVideoBatchRunner` đã có tham số `output_subdir` (`story_video_batch.py:427-435`), và `retry-failed` có dùng (L3702). Nhưng `batch/create` **chưa** truyền vào (L3471-3473), nên hiện video luôn ra `<ổ>/output/story-video/<batchId>/`. Ở P2 sẽ thêm `output_subdir=shared_config.get("outputSubdir")`, kèm kiểm tra tên an toàn: chỉ `[A-Za-z0-9_-]`, không cho `..`.

**Field của `items[]`:**
- `inputType`: `audio_file` | `script_url` | `drive_audio`
- `inputValue`: chỉ số file upload, tên file, **đường dẫn tuyệt đối** (L3380) hoặc token Drive
- `outputName`
- `subtitleFile`: chỉ số, tên, hoặc **đường dẫn `.srt` tuyệt đối, phải tồn tại** (L3408-3419)
- `chaptersFile`

**Field của `sharedConfig`:**
- `libraryIds[]`, `introId`, `optimizeMode`, `outputDrive`, `clipTags[]`, `tvEffectStyleId`, `skipTvEffect`, `clipUsageMode` (`reuse` | `once`)
- Các danh sách xoay vòng: `waveformOverlayIds[]`, `ctaOverlayIds[]`, `decorImageIds[]`, `layoutIds[]`, `modifierIds[]`, `subtitleStyleIds[]`. Xoay theo bộ bài đã xáo: cứ N video thì dùng hết N lựa chọn (L3303-3359).
- `voiceId` và các key phụ đề ở §3

**Lỗi thường gặp:**

| HTTP | `code` |
|---|---|
| 404 | `local_audio_not_found`, `local_subtitle_not_found`, `intro_not_found`, `library_not_found` |
| 400 | `invalid_output_drive`, `invalid_subtitle_format`, `*_unusable`, `empty_items` |
| 503 | `mongo_unavailable` (khi `clipUsageMode:"once"` mà Mongo tắt) |

**Theo dõi** bằng `GET /api/story-video/batch/<batchId>/progress`:

```json
{"batchId","status","totalItems","completedItems","failedItems","cancelledItems","currentIndex","queuePosition",
 "outputDir":"<thư mục tuyệt đối>",
 "items":[{"id","outputName","status","stage","percent","message","result":{"videoPath"},"error",
           "decorImageName","waveformName","ctaOverlayName","subtitleStyleName","layoutName","modifierNames"}]}
```

- Trạng thái được đổi cho UI: `running` → `processing`, `partial` → `completed` (L3490-3496). Dừng poll khi gặp `completed`, `failed` hoặc `cancelled`.
- **Đường dẫn file kết quả** = `outputDir` + basename của `items[i].result.videoPath`. Hoặc đổi `videoPath` (đường dẫn tương đối: `output/…`, `output_E/…`) bằng `storage_absolute_path` (`src/utils/file_manager.py:201-222`).
- **Không tự suy ra tên file:** trùng tên thì được thêm `_1`; phần sau dấu chấm cuối cùng trong `outputName` bị bỏ.

**Gọi trong cùng process** (pipeline sống trong app này): đọc thẳng `progress.json` qua `load_batch_progress`, hoặc gọi route qua `app.test_client()`. Xem [04 §8](04-thiet-ke-pipeline.md#8-loại-2-render-qua-svs).

---

## 5. Pipeline render (`story_video_pipeline.py run()` L538-681)

| Bước | Stage | % | Việc |
|---|---|---|---|
| 1 | `prepare_audio` | 5 | Chuẩn bị audio, ffprobe lấy thời lượng |
| 2 | (tuỳ chọn) | 10 | Kế hoạch edit-style nếu có chọn layout / modifier (L1087) |
| 3 | `select_clips` | 15 | Chọn clip (xem dưới) |
| 4 | `render_video` | 35-90 | Nối clip bằng stream copy, mux audio AAC 48 kHz stereo 192k, cắt bằng `-t` đúng độ dài audio (L1125-1192) |
| 5 | `story_overlays` | 90-98 | Một lượt overlay: filter TV (bỏ qua với thư viện đã bake hoặc khi `skipTvEffect`), lớp nhiễu TV, decor/layout, sóng âm, CTA, burn phụ đề. Có đường GPU (`overlay_cuda`), lỗi thì quay về CPU. |
| 6 | — | 97 | Gắn intro (chỉ batch) |
| 7 | `finalize` → `completed` | 98-100 | Remux `+faststart` ra thư mục xuất, xoá thư mục làm việc |

**Chọn clip:**

| Chế độ | Cách chọn |
|---|---|
| `reuse` (mặc định), render lẻ | Xáo trộn toàn bộ clip của các thư viện đã chọn |
| `reuse`, batch | Một "túi clip" xáo chung cho cả batch (`story_clip_bag.py`); clip chỉ lặp lại khi đã dùng hết |
| `once` | Ưu tiên clip chưa dùng, sau đó clip dùng ít nhất (đếm trong Mongo, `clip_usage.py`) |
| Modifier `long_takes` | 1-3 clip liên tiếp từ cùng một video gốc |

**Không chọn clip theo từng đoạn phụ đề.** `clipTags` chỉ lọc cho cả video (L729-756); clip harvest có tag dạng `keyword:<kw>`.

**Độ phân giải / FPS là cấu hình toàn cục** (`TARGET_RESOLUTION`, `TARGET_FPS`). Clip sai thông số bị loại (`clip_spec_validation.py`). Muốn xuất 9:16 thì phải đổi env và dùng thư viện khớp.

**Thư mục xuất** (`_finalize` L2582-2632, `_output_dir` L149-163):
- Render lẻ: `OUTPUT_DIR/story-video/`
- Batch: `<ổ>/output/story-video/<output_subdir hoặc batchId>/` (`batch_output_dir` trong `story_video_batch.py:76-86`). Hiện route `batch/create` luôn dùng `batchId`. **Ổ mặc định là `E`** (`OUTPUT_DEFAULT_DRIVE`).

---

## 6. Hàng đợi batch (`src/utils/story_video_batch.py`)

**Lớp chính:**
```python
StoryVideoBatchRunner(batch_id, story_configs, *, optimize_mode=False, output_subdir=None, output_drive="")  # L427-435
runner.enqueue() -> int   # L561, trả về vị trí trong hàng
```
Hàng đợi: `_enqueue_runner` (L123), `_queue_worker` (L140).

**Đồng thời:**
- **Mỗi lúc 1 batch.** Trong batch, chạy `min(STORY_BATCH_MAX_WORKERS, số item)` video song song. Mặc định 2 (`config.py:46`); `.env` hiện đặt 3.
- `OVERLAY_MAX_CONCURRENT` giới hạn số tiến trình ffmpeg overlay chạy cùng lúc.
- Render lẻ `/create` chạy ngoài hàng đợi này.

**Lưu trữ:**
- `STORAGE_DIR/story_video/batches/<id>/config.json` + `progress.json` + `cancel.requested`
- `STORAGE_DIR/story_video/<storyId>/progress.json`
- Kết quả nằm ở `progress["results"][] = {storyId, status, videoPath(tuyệt đối)|error}` (L667-675). `progress["stories"][]` có `outputName`.

**Trạng thái:**

| Đối tượng | Chuỗi trạng thái |
|---|---|
| Story | `pending` → `running` → `completed` / `failed` / `cancelled` |
| Batch | `pending` → `queued` → `running` / `cancelling` → `completed` / `partial` / `failed` / `cancelled` |

**Khi khởi động lại:** batch đang xếp hàng được đưa lại vào hàng. Batch đang chạy dở thì item chưa xong bị đánh `failed`, để `retry-failed` xử lý.

**Ghi JSON:** atomic, có retry trên Windows (`_save_json` / `_load_json`, `story_video_pipeline.py:59-96`).

---

## 7. Cấu hình (`src/config.py`, `.env`; chỉ ghi tên biến, không ghi giá trị bí mật)

| Nhóm | Biến | Giá trị hiện tại (không bí mật) |
|---|---|---|
| Web | `WEB_HOST`, `WEB_PORT`, `WEB_SECRET_KEY`, `MAX_UPLOAD_SIZE_MB`, `FRONTEND_HOST`, `FRONTEND_PORT` | `127.0.0.1`, `5006`, —, —, —, `5176` |
| Lưu trữ | `STORAGE_DIR`, `OUTPUT_DIR`, `OUTPUT_DRIVES` (mặc định `E,F`), `OUTPUT_DEFAULT_DRIVE` (mặc định `E`), `STORY_LIBRARY_DIR`, `STORY_RAW_DIR`, `FRONTEND_DIST_DIR` | `C:/storage`, `D:/output`, …, `E:/storage/story_library`, `D:/storage/story_raw_videos` |
| Render | `TARGET_RESOLUTION`, `TARGET_FPS`, `USE_GPU_NVENC`, `FFMPEG_PRESET`, `VIDEO_BITRATE`, `FFMPEG_COMMAND_TIMEOUT_SECONDS`, `STORY_BATCH_MAX_WORKERS`, `STORY_BAKE_MAX_WORKERS`, `OVERLAY_*`, `CLIP_EXPECTED_*`, `STORY_SKIP_CLIP_SPEC_CHECK` | `1920x1080`, `30`, `true`, …, `3` |
| Tối ưu | **`RENDER_SUSPEND_PROCESS_NAMES`**, `RENDER_BOOST_FFMPEG_PRIORITY` | Danh sách tiến trình bị **tạm dừng** khi `optimizeMode=true` |
| Mongo | `MONGODB_URI` (để trống = tắt), `MONGODB_DB`, `MONGODB_TIMEOUT_MS` | |
| TTS remote | `TTS_API_BASE_URL`, `TTS_API_KEY`, `TTS_PLATFORM`, `TTS_DEFAULT_VOICE_ID`, `TTS_MAX_CHARS`, `TTS_MAX_CONCURRENCY`, `TTS_POLL_INTERVAL_SECONDS`, `TTS_SINGLE_TASK_TIMEOUT_SECONDS`, `TTS_SPEED`, `TTS_VOLUME` | |
| Nguồn clip | `PIXABAY_API_KEY`, `PEXELS_API_KEY` … `PEXELS_API_KEY_32`, `PEXELS_KEY_COOLDOWN_SECONDS`, `PEXELS_POOL_MAX_WAIT_SECONDS` | |
| Thư viện | `STORY_CLIP_DURATION` (5), `STORY_LIBRARY_*`, `STORY_PREFETCH_*`, `STORY_DRIVE_AUDIO_*`, `STORY_IMAGE_CLIP_*` | |
| Phụ đề | `STORY_SUBTITLE_MAX_CHARS_PER_LINE`, `STORY_SUBTITLE_MAX_LINES`, `STORY_SUBTITLE_DEFAULT_FONT` | |
| Giao diện | `CRT_*`, `WAVEFORM_OVERLAY_*`, `STORY_CTA_OVERLAY_*`, `STORY_DECOR_*`, `STORY_TV_NOISE_*`, `STORY_OVERLAY_PRECOMPOSE_ENABLED`, `STORY_OVERLAY_PACK_DURATION_SECONDS` | |

Có một số key trong `.env` không được code đọc, ví dụ `MIN_*_CLIPS`, `VID_CLIP_*`, `ENABLE_OVERLAY`, `TTS_TASK_TIMEOUT_SECONDS` (code đọc `TTS_SINGLE_TASK_TIMEOUT_SECONDS`).

---

## 8. Bẫy khi tự động hoá

| # | Bẫy | Cách pipeline xử lý |
|---|---|---|
| 1 | `batch/create` bỏ qua body JSON (L3209-3213) | Gửi `payload` dạng form; sửa nhánh fallback ở P2 |
| 2 | Render lẻ không nhận đường dẫn srt và không qua hàng đợi | Chỉ dùng batch |
| 3 | Ổ xuất mặc định `E:`; route chưa nhận `outputSubdir` | Luôn truyền `outputDrive`; P2 bổ sung `outputSubdir=pipeline-<runId>` |
| 4 | Không có callback | Poll `progress.json` (cùng process) |
| 5 | Style TV, waveform mặc định, CTA đang bật, lớp nhiễu TV là **cấu hình toàn cục** | Ghi rõ trên UI pipeline; mỗi request chỉ chọn được `tvEffectStyleId` và các id xoay vòng |
| 6 | **Mỗi ảnh decor chỉ dùng một lần** | Health check cảnh báo khi hết decor; dùng `/decor-images/reset-used` |
| 7 | Chế độ `once` cần Mongo | Mongo tắt thì báo `waiting_service` thay vì lỗi |
| 8 | Không truyền `libraryIds` thì dùng thư viện mặc định (hiện là `26-30-kenh-thai-crt-arcade-f83df5`, đã bake nên bỏ qua lượt hiệu ứng TV) | Bắt buộc chọn thư viện trên form pipeline |
| 9 | `retry-failed` làm mất `tvEffectStyleId`; `crtSettings` bị bỏ qua | Pipeline tự submit lại thay vì dùng `retry-failed` |
| 10 | **`optimizeMode=true` tạm dừng các tiến trình trong `RENDER_SUSPEND_PROCESS_NAMES`** | Pipeline luôn gửi `optimizeMode=false`; health check cảnh báo nếu danh sách chứa tiến trình của DGT, TST, Chrome, claude, node hoặc python |
| 11 | Không có xác thực, nhận đường dẫn tuyệt đối bất kỳ | Giữ `WEB_HOST=127.0.0.1` |
