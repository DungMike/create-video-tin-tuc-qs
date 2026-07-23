# Báo cáo hiệu suất render — Story Video (audio 10 phút)

**Thư viện:** THAI 11 - 15 · Ký Ức Vàng  |  **GPU:** NVIDIA GTX 1060 6GB  |  **Encoder:** h264_nvenc (NVENC)
**Nguồn:** tests/vid 11 -kenh 1_full.mp3 (cắt 2 mẫu 10 phút: 00:00–10:00 và 10:00–20:00)

## 1. Thời gian & thông lượng

| Kịch bản | Số video | Wall-time (s) | RTF | render_video (s) | story_overlays (s) |
|---|---|---|---|---|---|
| 1 video · 10 phút | 1 | 550.8 | 1.09x | 59 | 487 |
| 2 video song song · 10 phút | 2 | 1053.6 | 1.14x | 120 | 925 |

- **RTF** = tổng giây video / wall-time. RTF>1 = nhanh hơn thời gian thực.
- 2 video song song: **1053.6s** cho 1200s video, so với chạy tuần tự 2 lần ~1101s → chỉ nhanh hơn **~4%**.
- Mỗi video khi chạy song song mất ~1053s (gần **gấp đôi** so với 551s khi chạy đơn) do tranh CPU.

## 2. Chỉ số phần cứng (trung bình / đỉnh)

| Kịch bản | GPU util % | NVENC % | GPU mem MB (đỉnh) | Power W (đỉnh) | Temp °C (đỉnh) | CPU hệ thống % | ffmpeg CPU % (đỉnh) | RAM % (đỉnh) |
|---|---|---|---|---|---|---|---|---|
| 1 video · 10 phút | 9.1/42.0 | 13.8/45.0 | 3634 | 34 | 71 | 51.2/100.0 | 175 | 64 |
| 2 video song song · 10 phút | 11.6/62.0 | 13.3/43.0 | 4667 | 47 | 78 | 99.2/100.0 | 287 | 74 |

## 3. Nhận định

- **story_overlays là nút thắt cổ chai (~88% thời gian).** Đây là bước chèn TV-noise + sóng âm + phụ đề, đang chạy **CPU** (`OVERLAY_USE_GPU_PIPELINE=false`).
- **GPU/NVENC gần như nhàn rỗi** (util TB ~9%, NVENC đỉnh ~45%). Bước ghép clip (render_video, dùng NVENC) chỉ chiếm ~10% thời gian.
- **CPU là tài nguyên giới hạn**: chạy 2 video song song không tăng thông lượng đáng kể vì cùng tranh CPU cho bước overlay.

## 4. Khuyến nghị

- Muốn tăng tốc thực sự phải **đưa bước overlay lên GPU** (bật `OVERLAY_USE_GPU_PIPELINE` với FFmpeg có libnpp/overlay_cuda) hoặc tối ưu chuỗi filter overlay.
- Với cấu hình hiện tại, **render tuần tự từng video** cho throughput tương đương song song mà nhẹ CPU hơn; song song chỉ hữu ích nếu overlay chuyển sang GPU.

## 5. File output để review

Đặt trong `tests/benchmarks/render_output/` (1920×1080, 30fps, H.264, ~580MB/video):
- `render_10min_single.mp4` — kịch bản 1 video
- `render_10min_parallel_A.mp4`, `render_10min_parallel_B.mp4` — kịch bản 2 video song song
