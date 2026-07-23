"""Build combined 10-min render report from single10 summary + par10 CSV/log."""
import csv
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")

METRIC_COLS = ["cpu_pct", "ram_pct", "gpu_util", "enc_util", "dec_util",
               "gpu_mem_mb", "power_w", "temp_c", "ffmpeg_cpu_pct"]


def hw_from_csv(csv_path, phase):
    data = {c: [] for c in METRIC_COLS}
    n = 0
    for r in csv.DictReader(open(csv_path, encoding="utf-8")):
        if r["phase"] != phase:
            continue
        n += 1
        for c in METRIC_COLS:
            try:
                data[c].append(float(r[c]))
            except (ValueError, KeyError):
                pass
    out = {"samples": n}
    for c in METRIC_COLS:
        v = data[c]
        out[c] = {"mean": round(sum(v) / len(v), 1), "max": round(max(v), 1)} if v else {"mean": None, "max": None}
    return out


def find_csv(phase):
    for f in sorted(glob.glob(os.path.join(RES, "metrics_*.csv"))):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            if r["phase"] == phase:
                return f
    return None


# ---- single10 from its summary JSON ----
single = None
for f in sorted(glob.glob(os.path.join(RES, "summary_*.json"))):
    d = json.load(open(f, encoding="utf-8"))
    for sc in d["scenarios"]:
        if sc["name"] == "single10":
            single = sc
if single is None:
    raise SystemExit("single10 summary not found")

# ---- par10 reconstructed ----
par_csv = find_csv("par10")
par = {
    "name": "par10",
    "n_jobs": 2,
    "video_secs_each": 600,
    "video_secs_total": 1200,
    "wall_s": 1053.6,
    "realtime_factor": round(1200 / 1053.6, 2),
    # stage times (s) averaged over the 2 jobs, from run_rest.log transitions
    "render_video_avg": 120,   # 115s & 125s
    "story_overlays_avg": 925,
    "hw": hw_from_csv(par_csv, "par10"),
}

single_sc = {
    "name": "single10",
    "n_jobs": 1,
    "video_secs_each": 600,
    "video_secs_total": 600,
    "wall_s": single["wall_s"],
    "realtime_factor": single["realtime_factor"],
    "render_video_avg": round(list(single["jobs"].values())[0]["stage_times"].get("render_video", 0)),
    "story_overlays_avg": round(list(single["jobs"].values())[0]["stage_times"].get("story_overlays", 0)),
    "hw": single["hw"],
}

LABELS = {"single10": "1 video · 10 phút", "par10": "2 video song song · 10 phút"}
scs = [single_sc, par]

L = []
L.append("# Báo cáo hiệu suất render — Story Video (audio 10 phút)")
L.append("")
L.append("**Thư viện:** THAI 11 - 15 · Ký Ức Vàng  |  **GPU:** NVIDIA GTX 1060 6GB  |  **Encoder:** h264_nvenc (NVENC)")
L.append("**Nguồn:** tests/vid 11 -kenh 1_full.mp3 (cắt 2 mẫu 10 phút: 00:00–10:00 và 10:00–20:00)")
L.append("")
L.append("## 1. Thời gian & thông lượng")
L.append("")
L.append("| Kịch bản | Số video | Wall-time (s) | RTF | render_video (s) | story_overlays (s) |")
L.append("|---|---|---|---|---|---|")
for s in scs:
    L.append(f"| {LABELS[s['name']]} | {s['n_jobs']} | {s['wall_s']} | {s['realtime_factor']}x "
             f"| {s['render_video_avg']} | {s['story_overlays_avg']} |")
L.append("")
L.append("- **RTF** = tổng giây video / wall-time. RTF>1 = nhanh hơn thời gian thực.")
L.append("- 2 video song song: **1053.6s** cho 1200s video, so với chạy tuần tự 2 lần ~1101s → chỉ nhanh hơn **~4%**.")
L.append("- Mỗi video khi chạy song song mất ~1053s (gần **gấp đôi** so với 551s khi chạy đơn) do tranh CPU.")
L.append("")
L.append("## 2. Chỉ số phần cứng (trung bình / đỉnh)")
L.append("")
L.append("| Kịch bản | GPU util % | NVENC % | GPU mem MB (đỉnh) | Power W (đỉnh) | Temp °C (đỉnh) | CPU hệ thống % | ffmpeg CPU % (đỉnh) | RAM % (đỉnh) |")
L.append("|---|---|---|---|---|---|---|---|---|")
for s in scs:
    hw = s["hw"]
    def mm(k):
        return f"{hw[k]['mean']}/{hw[k]['max']}"
    L.append(f"| {LABELS[s['name']]} | {mm('gpu_util')} | {mm('enc_util')} | {hw['gpu_mem_mb']['max']:.0f} "
             f"| {hw['power_w']['max']:.0f} | {hw['temp_c']['max']:.0f} | {mm('cpu_pct')} "
             f"| {hw['ffmpeg_cpu_pct']['max']:.0f} | {hw['ram_pct']['max']:.0f} |")
L.append("")
L.append("## 3. Nhận định")
L.append("")
L.append("- **story_overlays là nút thắt cổ chai (~88% thời gian).** Đây là bước chèn TV-noise + sóng âm + phụ đề, đang chạy **CPU** (`OVERLAY_USE_GPU_PIPELINE=false`).")
L.append("- **GPU/NVENC gần như nhàn rỗi** (util TB ~9%, NVENC đỉnh ~45%). Bước ghép clip (render_video, dùng NVENC) chỉ chiếm ~10% thời gian.")
L.append("- **CPU là tài nguyên giới hạn**: chạy 2 video song song không tăng thông lượng đáng kể vì cùng tranh CPU cho bước overlay.")
L.append("")
L.append("## 4. Khuyến nghị")
L.append("")
L.append("- Muốn tăng tốc thực sự phải **đưa bước overlay lên GPU** (bật `OVERLAY_USE_GPU_PIPELINE` với FFmpeg có libnpp/overlay_cuda) hoặc tối ưu chuỗi filter overlay.")
L.append("- Với cấu hình hiện tại, **render tuần tự từng video** cho throughput tương đương song song mà nhẹ CPU hơn; song song chỉ hữu ích nếu overlay chuyển sang GPU.")
L.append("")
L.append("## 5. File output để review")
L.append("")
L.append("Đặt trong `tests/benchmarks/render_output/` (1920×1080, 30fps, H.264, ~580MB/video):")
L.append("- `render_10min_single.mp4` — kịch bản 1 video")
L.append("- `render_10min_parallel_A.mp4`, `render_10min_parallel_B.mp4` — kịch bản 2 video song song")
L.append("")

md = "\n".join(L)
open(os.path.join(RES, "REPORT.md"), "w", encoding="utf-8").write(md)
json.dump({"scenarios": scs}, open(os.path.join(RES, "report_data.json"), "w", encoding="utf-8"),
          indent=2, ensure_ascii=False)
print(md)
