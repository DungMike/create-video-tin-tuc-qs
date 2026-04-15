# Auto Video Review Studio

Web app review video từ audio, ảnh và video nguồn. UI đã được migrate sang React + Vite + TypeScript + Tailwind CSS + `shadcn/ui`, trong khi backend Flask tiếp tục xử lý upload, cắt clip, thư viện tài nguyên và render video.

## Kiến trúc hiện tại

- `frontend/`: SPA React + Vite + TypeScript
- `src/web_app.py`: Flask JSON API + serve production build của frontend
- `src/processors/`: xử lý audio, image, video
- `src/composer/`: timeline + renderer
- `src/utils/file_manager.py`: storage, manifest, library asset metadata

## Route chính

- UI:
  - `/`
  - `/jobs/<job_id>/review`
  - `/jobs/<job_id>/resources`
  - `/jobs/<job_id>/result`
- API:
  - `POST /api/jobs`
  - `GET /api/jobs/<job_id>/review?page=<n>`
  - `GET /api/jobs/<job_id>/resources?tag=a&tag=b`
  - `POST /api/jobs/<job_id>/render`
  - `GET /api/jobs/<job_id>/result`
- Media:
  - `GET /media/<relative_path>`

## Yêu cầu môi trường

- Windows + PowerShell
- Python `3.14`
- Node.js `22+`
- `ffmpeg` và `ffprobe` có trong `PATH`

Kiểm tra nhanh:

```powershell
python --version
node --version
npm --version
ffmpeg -version
ffprobe -version
```

## Cài đặt

### Python

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Frontend / tooling

```powershell
npm install
npm --prefix frontend install
```

## Cấu hình `.env`

Các biến quan trọng:

```env
STORAGE_DIR=./storage
OUTPUT_DIR=./storage/output

WEB_HOST=127.0.0.1
WEB_PORT=5000

FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=5173
FRONTEND_DIST_DIR=./frontend/dist

WEB_SECRET_KEY=change-me
MAX_UPLOAD_SIZE_MB=4096
```

Ghi chú:

- `WEB_HOST`, `WEB_PORT`: port backend Flask
- `FRONTEND_HOST`, `FRONTEND_PORT`: port Vite dev server
- `FRONTEND_DIST_DIR`: thư mục build production mà Flask sẽ serve

## Chạy ở Dev Mode

Chạy cả backend và frontend cùng lúc từ root:

```powershell
npm run dev
```

Dev mode sẽ:

- chạy Flask API bằng `.\venv\Scripts\python -m src.web_app`
- chạy Vite dev server ở `FRONTEND_PORT`
- proxy `/api` và `/media` từ frontend sang backend

Mở trình duyệt tại:

```text
http://127.0.0.1:5173
```

Nếu bạn đổi `FRONTEND_HOST` hoặc `FRONTEND_PORT` trong `.env`, dùng đúng URL tương ứng.

## Build production frontend

```powershell
npm run build
```

Lệnh này tạo bundle tại `frontend/dist/`.

## Chạy Production Mode

Sau khi đã build frontend:

```powershell
.\venv\Scripts\Activate.ps1
.\venv\Scripts\waitress-serve.exe --listen=0.0.0.0:5000 wsgi:app
```

Flask sẽ:

- serve JSON API
- serve `frontend/dist/index.html`
- serve `frontend/dist/assets/*`
- serve media trong `storage/`

## Scripts hữu ích

```powershell
npm run dev
npm run build
npm run typecheck
npm run lint
python -m compileall src
```

## Storage

```text
storage/
  audio/<job_id>/
  raw_images/<job_id>/
  raw_videos/<job_id>/
  clips/img_clips/<job_id>/
  clips/vid_clips/<job_id>/
  jobs/<job_id>/manifest.json
  library/clips/
  library/index.json
  output/
```

## Lưu ý vận hành

- UI cũ dạng Flask templates đã bị loại khỏi runtime path.
- Frontend mới dùng Tailwind CSS + `shadcn/ui`; không còn phụ thuộc `style.css` cũ.
- Link YouTube vẫn được tải bằng `yt-dlp`.
- Render pipeline Python không đổi behavior chính, chỉ đổi lớp web interface.
