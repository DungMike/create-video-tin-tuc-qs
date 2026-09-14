# Story Video Studio

Hệ thống tự động ghép **1 file audio (hoặc link Google Docs) + thư viện clip 5 giây** thành video
hoàn chỉnh chuẩn YouTube/TikTok, kèm hiệu ứng TV (CRT), khung TV (decor), sóng âm, CTA và phụ đề.

**React + Vite** cho Frontend, **Flask + FFmpeg** (Python) cho Backend xử lý media.

---

## 1. Phạm vi hệ thống

Toàn bộ hệ thống chỉ còn **hai trang**:

| Trang | Vai trò |
|---|---|
| `/story-video` | Tạo video: chọn audio/script, thư viện clip, intro, khung TV, sóng âm/CTA, phụ đề. Chạy 1 video lẻ hoặc batch. |
| `/story-video/settings` | Cấu hình tài nguyên: thư viện clip (harvest/prefetch/normalize/bake), khung TV, hiệu ứng TV, overlay sóng âm & CTA. |

Mọi URL khác đều redirect về `/story-video`.

API: tất cả nằm dưới `/api/story-video/*` (blueprint `story_video_bp`), cộng thêm
`GET /api/voices` (danh sách giọng đọc TTS) và `GET /media/<path>` (serve file preview).

---

## 2. Luồng hoạt động

### Giai đoạn 1: Chuẩn bị thư viện clip (trang settings)

Thư viện là tập hợp clip 5 giây đã chuẩn hoá, dùng làm nguyên liệu cho mọi video.

- **Harvest** (`story_bulk_harvest.py`): tải hàng loạt video theo từ khoá từ Pexels/Pixabay, cắt thành clip.
- **Prefetch** (`story_video_prefetch.py`): nhánh thay thế — tải hết kết quả tìm kiếm về trước,
  người dùng duyệt/loại trên file local rồi mới cắt.
- **Normalize** (`story_library_normalize.py`): đưa clip về đúng một định dạng canon
  (resolution / fps / pix_fmt / color) để render không phải re-encode lẻ tẻ.
- **Bake** (`story_library_bake.py`): nướng sẵn hiệu ứng TV vào từng clip. Thư viện đã bake thì
  bước hiệu ứng lúc render được bỏ qua.

### Giai đoạn 2: Render (trang chính)

1. Nhận audio (upload / folder local / Google Drive) hoặc link Google Docs → TTS ra audio.
2. `story_video_pipeline.py`: bốc ngẫu nhiên clip từ thư viện cho đủ độ dài audio.
3. Áp hiệu ứng TV, khung TV (decor), overlay sóng âm + CTA, burn phụ đề.
4. Mux audio, xuất MP4 vào `storage/output/`.

Batch (`story_video_batch.py`) xếp hàng nhiều video, chạy song song có giới hạn
(`STORY_BATCH_MAX_WORKERS`), có progress polling, cancel và retry-failed.

---

## 3. Cấu trúc Backend (`src/`)

```text
src/
├── config.py                    # Parser cấu hình từ .env
├── web_app.py                   # Flask bootstrap + /api/voices + /media + SPA catch-all
├── routes/
│   └── story_video_routes.py    # Toàn bộ API /api/story-video/*
├── processors/
│   ├── audio_utils.py           # Đọc thông tin audio (duration, validate)
│   └── crt_effect_processor.py  # Sinh filter hiệu ứng TV/CRT
└── utils/
    ├── story_video_pipeline.py     # Runner 1 video
    ├── story_video_batch.py        # Runner batch
    ├── story_library*.py           # Thư viện clip: CRUD, bake, normalize
    ├── story_bulk_harvest.py       # Tải hàng loạt theo từ khoá
    ├── story_video_prefetch.py     # Nhánh tải-hết-rồi-duyệt
    ├── story_decor_images.py       # Khung TV (chroma key vùng màn hình)
    ├── waveform_overlays.py        # Overlay sóng âm
    ├── story_cta_overlay.py        # Overlay CTA (like/subscribe)
    ├── story_subtitles.py          # Sinh & burn phụ đề
    ├── video_source_downloader.py  # Tải nguồn từ Pexels/Pixabay/YouTube
    ├── ffmpeg_helper.py            # Wrapper FFmpeg dùng chung
    └── render_priority.py          # Optimize mode: tạm dừng app cạnh tranh, ưu tiên CPU
```

## 4. Cấu trúc Frontend (`frontend/src/`)

```text
frontend/src/
├── router.tsx                   # 2 route + catch-all redirect
├── pages/
│   ├── StoryVideoPage.tsx           # Trang tạo video
│   └── StoryVideoSettingsPage.tsx   # Trang cấu hình
├── components/
│   ├── StoryLibrary*.tsx            # Quản lý thư viện, bake, prefetch, normalize
│   ├── StoryBulkHarvestPanel.tsx
│   ├── StoryDecorFrameEditor.tsx    # Editor khung TV (chroma key)
│   ├── StoryOverlayPlacementEditor.tsx
│   └── ui/                          # shadcn/ui
├── lib/api.ts                   # Client gọi API
└── types/api.ts                 # Interface TypeScript khớp payload backend
```

## 5. Storage (`storage/`)

File-based DB. Các thư mục còn dùng:

```text
storage/
├── story_library/          # Thư viện clip 5s + index.json + libraries.json
├── story_raw_videos/       # Nguồn thô đã tải, trước khi cắt
├── story_video/            # Progress/manifest mỗi phiên render
├── story_decor_images/     # Khung TV
├── story_overlay_packs/    # Overlay đã ghép sẵn
├── story_cta_overlays/     # Video CTA
├── story_tv_noise_overlays/
├── story_effect_previews/  # Clip mẫu xem trước hiệu ứng
├── story_subtitle_previews/
├── story_fonts/            # Font phụ đề đã upload
├── waveform_overlays/
├── crt_effect/
├── voices/                 # Bản ghi giọng đọc TTS
├── audio/                  # Audio sinh từ Google Docs
├── output/                 # Video thành phẩm
└── temp/
```

> `storage/jobs/`, `clips/`, `channels/`, `decor_videos/`, `effects_library/` là dữ liệu của các
> luồng đã gỡ bỏ — không còn code nào đọc tới, có thể xoá tay khi chắc chắn không cần.

---

## 6. Cấu hình `.env`

```env
# ----- Đường dẫn -----
STORAGE_DIR=./storage                      # Đổi để chuyển cả kho storage sang ổ khác
STORY_RAW_DIR=./storage/story_raw_videos   # Tách riêng được (nguồn thô rất nặng)
OUTPUT_DIR=./storage/output

# ----- Render -----
TARGET_RESOLUTION=1920x1080
TARGET_FPS=30
USE_GPU_NVENC=true               # Cần GPU NVIDIA + FFmpeg build có nvenc
FFMPEG_PRESET=p2
VIDEO_BITRATE=8M
STORY_BATCH_MAX_WORKERS=2        # Số video render song song trong 1 batch
STORY_BAKE_MAX_WORKERS=2         # Số clip bake song song

# ----- Overlay pass -----
OVERLAY_USE_GPU_PIPELINE=true    # overlay_cuda; tự fallback về CPU nếu build không hỗ trợ
OVERLAY_PARALLEL_SEGMENTS=3      # Chia pass phụ đề thành N đoạn song song (libass đơn luồng)

# ----- Nguồn video -----
PEXELS_API_KEY=...               # Key đầu tiên dùng tên trần; thêm PEXELS_API_KEY_2, _3... để mở rộng pool
PIXABAY_API_KEY=...

# ----- Web -----
WEB_PORT=5000
FRONTEND_PORT=5176
```

Toàn bộ key nằm trong [src/config.py](src/config.py). `.env` chỉ đọc lúc server khởi động —
sửa xong phải restart.

---

## 7. Cài đặt & Chạy

### Yêu cầu

- Windows (thiết kế tối ưu cho PowerShell), `python 3.10+`, `node 22+`
- `ffmpeg` + `ffprobe` trong `PATH`

### Cài đặt

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

npm install
npm --prefix frontend install
```

### Dev

```powershell
npm run dev
```

Chạy đồng thời Flask (`WEB_PORT`, mặc định 5000) và Vite (`FRONTEND_PORT`). Truy cập qua cổng
frontend; Vite proxy sẵn `/api` và `/media` sang backend.

### Production

```powershell
npm run build
.\venv\Scripts\Activate.ps1
.\venv\Scripts\waitress-serve.exe --listen=0.0.0.0:5000 wsgi:app
```

Flask serve luôn `frontend/dist`. Truy cập `http://localhost:5000`.

### Kiểm thử

```powershell
.\venv\Scripts\python -m pytest tests/ -q    # backend
npm run typecheck                            # frontend
```

---

## 8. Sửa lỗi thường gặp

1. **Render lỗi font / `Invalid argument`**
   Font phụ đề được copy vào thư mục temp trước khi render để né vấn đề escaping dấu hai chấm
   trên Windows. Nếu vẫn lỗi, kiểm tra font đã upload trong `storage/story_fonts/`.

2. **Render chậm / nghẽn CPU**
   Bật *optimize mode* ở trang chính: batch sẽ tạm dừng các app cạnh tranh
   (`RENDER_SUSPEND_PROCESS_NAMES`) và nâng ưu tiên tiến trình ffmpeg.
   Giảm `STORY_BATCH_MAX_WORKERS` nếu máy ít nhân.

3. **Không chọn được khung TV (decor)**
   Thư viện đã bake toàn phần (`fullyBaked`) có sẵn sóng âm/CTA trong clip, backend từ chối kết
   hợp với decor. Dùng thư viện chưa bake, hoặc bỏ decor.

4. **Clip bị loại lúc render**
   Clip không khớp định dạng canon (`CLIP_EXPECTED_*`) sẽ bị loại. Chạy **Normalize** ở trang
   settings để chuẩn hoá lại thư viện.

5. **Hết quota Pexels**
   Quota tính theo từng key (200 req/giờ, 20.000/tháng). Thêm `PEXELS_API_KEY_2`,
   `PEXELS_API_KEY_3`... — pool tự xoay vòng và cho key hết quota nghỉ.

6. **Xem log**
   `logs/app.log`.
