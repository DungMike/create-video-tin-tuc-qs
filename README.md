# Auto Video Review Studio

Hệ thống tự động hóa sản xuất video từ Audio, Hình ảnh, và Video clip nguồn. 
Hệ thống sử dụng **React + Vite** cho Frontend (Web UI) và **Flask + FFmpeg** (Python) cho Backend xử lý media chuyên sâu.

Mục tiêu của dự án là biến 1 file audio/bản ghi âm hoặc tài liệu text kết hợp với các hình ảnh/video thô thành một video hoàn chỉnh (chuẩn YouTube/Tiktok) với các hiệu ứng chuyển động, overlay PiP (Picture-in-Picture), watermark, và âm thanh nền một cách hoàn toàn tự động hoặc bán tự động qua giao diện Review.

---

## 1. Tổng quan Kiến trúc (Architecture)

Hệ thống được thiết kế theo mô hình **Client-Server SPA (Single Page Application)**:

- **Frontend (`frontend/`)**: 
  - Framework: React 18, Vite, TypeScript.
  - Styling: Tailwind CSS, `shadcn/ui`.
  - Routing: React Router DOM (client-side routing).
- **Backend (`src/`)**:
  - Web Server: Flask (Python 3.10+). Cung cấp RESTful JSON API và serve file tĩnh (media) cũng như file build của frontend.
  - Media Engine: FFmpeg (gọi qua Python `subprocess`), hỗ trợ tăng tốc phần cứng (NVENC) nếu có GPU NVIDIA.
- **Storage (`storage/`)**:
  - Kho lưu trữ file thô, file tạm, cache, thư viện hiệu ứng, thư viện decor, và các video thành phẩm. Đóng vai trò như một Database nội dung (File-based DB).

---

## 2. Luồng hoạt động của hệ thống (System Workflow)

Video đi qua 4 giai đoạn chính để từ nguyên liệu thô thành sản phẩm hoàn chỉnh:

### Giai đoạn 1: Nguồn dữ liệu (Ingestion / Upload)
- Qua trang `UploadPage`, người dùng tải lên File Audio chính (hoặc chọn từ Thư viện Audio sinh ra từ trang Docs-to-Audio).
- Người dùng nhập các keywords để hệ thống tự crawl ảnh (qua Google) và video youtube, hoặc tự upload ảnh/video local.
- Hệ thống tạo 1 `job_id` duy nhất (VD: `8f3d1a2b`), tạo thư mục làm việc và lưu trạng thái vào `manifest.json`.

### Giai đoạn 2: Xử lý tiền kỳ (Processing)
- `AudioSplitter`: Phân tích file audio, tính thời lượng.
- `ImageProcessor`: Trích xuất ảnh, chuẩn hóa kích thước, chống phân mảnh (convert sang định dạng an toàn cho FFmpeg).
- Tự động sinh **Motion Clips** cho ảnh chưa có cảnh (Áp dụng Pan & Zoom / Ken Burns từ **Effects Library**).

### Giai đoạn 3: Duyệt và cấu hình (Review)
- Frontend (trang `ReviewPage`) trình bày danh sách các đoạn clip/ảnh theo timeline.
- Người dùng có thể kéo thả, sắp xếp vị trí tài nguyên ảnh/video trên thanh timeline để khớp nối với Voice.
- **Tính năng mới**: Người dùng chọn **Decor Video** (Video góc khung hình / PiP overlay) từ dropdown.

### Giai đoạn 4: Composer & Rendering
- Bấm nút Render trên UI gọi tới `POST /api/jobs/<job_id>/render` kèm cấu hình (tags, decorVideoId,...).
- `TimelineBuilder` sắp xếp clip thành mảng tuần tự.
- **Các Mode Render**:
  - **`image_audio_only` mode**: Nếu chỉ có ảnh và audio, hệ thống dùng thuật toán render Nhanh (Fast Mode). Lặp lại lượng ảnh (loop) cho đến khi phủ hết chiều dài Audio.
  - **Mixed mode**: Dùng xfade chuyển cảnh giữa ảnh-ảnh hoặc video-video phức tạp. Chunk rendering hoạt động để tránh lệnh FFmpeg quá tải.
- Áp dụng các filter cuối cùng: **Watermark text** (Nguồn tổng hợp) + **PiP Decor Overlay**.
- Đóng gói MP4 tại `storage/output/`.

---

## 3. Các Module Core Backend (`src/`)

### 3.1. API Gateway (`web_app.py` / `main.py`)
- Định nghĩa toàn bộ Endpoints REST (Job creation, Upload, Decor Library CRUD, Effects API).
- Quản lý quá trình serve file tĩnh (streaming video preview cho UI).
- Nhận lệnh render, chạy background thread và trả tiến độ (Progress API).

### 3.2. Tiền xử lý (`processors/`)
- `audio_processor.py`: Lấy info audio (thời lượng, bitrate).
- `image_processor.py`: Resize ảnh thông minh, cache motion (giảm thời gian render lại những ảnh không tuỳ chỉnh hiệu ứng).
- `youtube_downloader.py` (yt-dlp wrapper): Tải clip theo link từ YouTube.

### 3.3. Trình tạo Video (`composer/`)
- `timeline.py`: Tính toán duration. Map từng frame ảnh/video khớp với thời gian chạy. 
- `renderer.py`: Trái tim FFmpeg của dự án.
  - Xử lý chia video làm nhiều *chunks* nhỏ, render song song hoặc tuần tự rồi `concat` lại (để vượt qua giới hạn memory/command length của OS).
  - Tích hợp hàm sinh Filter Complex (`_build_overlay_filter`) cho Picture-in-Picture và Text Drawing.
  - Hỗ trợ GPU H.264/HEVC (`hevc_nvenc`, `h264_nvenc`).

### 3.4. Utilities (`utils/`)
- `decor_videos.py`: Định nghĩa CRUD cho thư viện video trang trí (`decor_videos/index.json`).
- `config.py`: Parser cấu hình từ `.env`.
- `ffmpeg_helper.py`: Wrapper xử lý các flags chung của FFmpeg.

---

## 4. Các Tính Năng Mở Rộng Đặc Trọng Tâm

* **Trình sinh Audio từ Văn bản (Docs-to-Audio)**: Lấy text từ Google Docs, gọi API (VD: Minimax) sinh file MP3 chất lượng cao ghép vào workflow tự động.
* **Thư viện Decor Video (PiP Overlays)**: Người dùng upload video (MC, Logo động) tại `/decor-library`. Video này tự động thu nhỏ 25% (scale config), đặt ở góc màn, lặp vô hạn (stream_loop -1) và bị tắt tiếng (-an) để không đè lên Voice chính.
* **Watermark Text Cứng**: Chữ "Nguồn: Tổng hợp" (chỉnh trong cấu hình) được đóng đinh vào góc cấp độ pixel, có viền đen để phản quang rõ trên mọi nền sáng tối.
* **Hệ thống Library Effects**: Các template Pan-Zoom được sinh sẵn bằng lệnh FFmpeg logic để hệ thống có thể bốc ngẫu nhiên tạo độ "sống động" cho ảnh tĩnh tự động.

---

## 5. Cấu trúc thư mục (Directory Tree)

```text
CRAWL VIDEO - AUDIO - QS/
├── .env                  # FIle cấu hình lõi
├── frontend/             # Root React Project
│   ├── src/
│   │   ├── components/   # React Components (UI, Layout)
│   │   ├── lib/          # API services client, Utils (fetchers, cn)
│   │   ├── pages/        # Router Views (Upload, Review, DecorLibrary, v.v)
│   │   ├── types/        # Giao tiếp kiểu chữ TypeScript (Interfaces)
│   ├── vite.config.ts    # Config Vite (proxy api sang port 5000)
├── logs/                 # Chứa app.log theo dõi quá trình chạy
├── src/                  # Root Backend Python
│   ├── composer/         # Rendering Logic (FFmpeg)
│   ├── crawlers/         # System crawl web resources
│   ├── processors/       # Media processors
│   ├── utils/            # Helper logic (decor_videos.py, config.py) 
│   ├── tools/            # Công cụ CLI (gen effects library)
│   └── web_app.py        # Flask App Entry
├── storage/              # CƠ SỞ DỮ LIỆU FILE
│   ├── audio/            
│   ├── clips/            # File đã được cut/crop trước render
│   ├── decor_videos/     # File MP4 & index.json cho PiP Overlay
│   ├── jobs/             # Chứa Manifest mỗi phiên làm việc
│   ├── output/           # Dành cho Video đã render xong
│   └── temp/             # Filter texts, caches xử lý ảnh
```

---

## 6. Giải thích `.env` (Cấu hình)

File `.env` cực kỳ quan trọng để điều chỉnh hành vi Render. Các thông số đặc biệt:

```env
# ----- CHỤP/RENDER -----
RENDER_CHUNK_SEGMENT_LIMIT=40        # Quá bao nhiêu scene (cảnh) thì sẽ bị cắt thành phần nhỏ để render rời rồi ghép lại (tránh crash RAM).
IMAGE_ONLY_FAST_CHUNK_CONCAT=true    # Bật tăng tốc ghép file khi chỉ dùng Audio và Ảnh tĩnh.
FFMPEG_COMMAND_TIMEOUT_SECONDS=0     # 0 là không giới hạn.

# ----- OVERLAY (DECOR + WATERMARK) -----
DECOR_VIDEOS_DIR=./storage/decor_videos
OVERLAY_VIDEO_POSITION=top_right     # Góc chứa Decor (top_left, bottom_right, v.v.)
OVERLAY_VIDEO_SCALE=0.25             # Decor to bằng 25% chiều rộng Video
OVERLAY_VIDEO_MARGIN=10              # Khoảng cách so với mép viền (pixel)

SOURCE_TEXT=Nguồn: Tổng hợp          # Watermark Text
SOURCE_TEXT_FONT_SIZE=22             # Size chữ. (Lưu ý tăng size thì có tính tăng cả SOURCE_TEXT_MARGIN)
SOURCE_TEXT_FONT=C:/Windows/Fonts/arial.ttf # Bắt buộc có trên máy tính Windows Windows
SOURCE_TEXT_POSITION=bottom_left
SOURCE_TEXT_MARGIN=20
```

---

## 7. Yêu cầu Cài đặt & Dev Mode

### Phụ thuộc (Dependencies)
- **Hệ điều hành**: Thiết kế tối ưu trên Windows (PowerShell / Command Prompt).
- **Phần mềm lõi**: 
  - `python 3.10+`
  - `node 22+`
  - `ffmpeg` + `ffprobe` (Bắt buộc phải add biến môi trường `PATH`).

### Cài đặt môi trường
1. Active Virtual Environment Backend:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. Cài đặt npm dependencies:
   ```powershell
   npm install
   npm --prefix frontend install
   ```

### Chạy Dự án lúc Dev (Development Mode)
```powershell
npm run dev
```
Lệnh này đồng thời khởi động Flask (`http://localhost:5000`) và Frontend Vite (`http://localhost:5173`). Bạn truy cập qua cổng `5173`. Giao tiếp API được cấu hình proxy tự động ở Vite sang `:5000`.

### Build & Chạy Production
Khi chạy ở Producton, Vite được Build ra web tĩnh, Flask sẽ gom lại chạy chung trên port 5000.
```powershell
# B1: Build frontend
cd frontend
npm run build
cd ..

# B2: Khởi động Waitress Server
.\venv\Scripts\Activate.ps1
.\venv\Scripts\waitress-serve.exe --listen=0.0.0.0:5000 wsgi:app
```
Truy cập: `http://localhost:5000`

---

## 8. Sửa Lỗi Thường Gặp (Troubleshooting)

1. **Lỗi `Invalid argument` khi Render FFmpeg (liên quan font/đường dẫn)**
   - Hệ thống tự động copy file Font vào thư mục temp để chạy render nhằm cởi bỏ các vấn đề "escaping dấu hai chấm" trên Windows.
   - Nếu bị lỗi, hãy check lại đường dẫn biến `SOURCE_TEXT_FONT` trong file `.env` xem có đúng font ttf không tồn tại không. (VD: C:/Windows/Fonts/arial.ttf).
2. **Crash RAM lúc Render video dài > 30p**
   - Hãy điều chỉnh giảm thông số biến môi trường: `IMAGE_MOTION_WORKERS=1` và `RENDER_CHUNK_SEGMENT_LIMIT=20`.
3. **Ảnh không sinh được Motion Clip**
   - Thường do ảnh WebP hoặc PNG chứa kênh Alpha (Trong suốt). Hệ thống sẽ tự normalize convert về `JPEG RGB` tại giai đoạn tiền xử lý. Nếu vẫn kẹt, hãy đọc `logs/app.log`.
4. **Thay đổi cỡ chữ (Font size) ở đâu?**
   - Vào tận `.env`, chỉnh chỉ số `SOURCE_TEXT_FONT_SIZE` rồi **Re-run (Khởi động lại Server)**. Các thông số `.env` chỉ apply ngay lúc server start.
5. **Đổi vị trí Decor Video (PiP góc)**
   - Biến `OVERLAY_VIDEO_POSITION` nhận các giá trị: `top_left`, `top_right`, `bottom_left`, `bottom_right`. Chỉnh trong `.env` và restart server. Tương tự cho vị trí Text (`SOURCE_TEXT_POSITION`).
