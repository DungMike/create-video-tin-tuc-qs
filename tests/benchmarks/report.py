"""Merge benchmark summary JSONs into one comparison report (markdown + JSON)."""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")

LABELS = {
    "single10": "1 video · 10 phút",
    "par10": "2 video song song · 10 phút",
    "single30": "1 video · 30 phút",
    "par30": "2 video song song · 30 phút",
}
ORDER = ["single10", "par10", "single30", "par30"]


def load_scenarios(files):
    out = {}
    for fp in files:
        d = json.load(open(fp, encoding="utf-8"))
        for sc in d["scenarios"]:
            out[sc["name"]] = sc
    return out


def stage_avg(sc, stage):
    vals = [j["stage_times"].get(stage, 0) for j in sc["jobs"].values()]
    return sum(vals) / len(vals) if vals else 0


def main():
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(RES, "summary_*.json")))
    sc = load_scenarios(files)
    names = [n for n in ORDER if n in sc]

    lines = []
    lines.append("# Báo cáo hiệu suất render — Story Video")
    lines.append("")
    lines.append("**Thư viện:** THAI 11 - 15 · Ký Ức Vàng · **GPU:** GTX 1060 6GB · **Encoder:** h264_nvenc")
    lines.append("")
    lines.append("## Tổng quan thời gian & thông lượng")
    lines.append("")
    lines.append("| Kịch bản | Số video | Video/​video (s) | Wall (s) | RTF | render_video (s) | story_overlays (s) |")
    lines.append("|---|---|---|---|---|---|---|")
    for n in names:
        s = sc[n]
        lines.append(
            f"| {LABELS[n]} | {s['n_jobs']} | {s['video_secs_each']} | "
            f"{s['wall_s']} | {s['realtime_factor']}x | "
            f"{stage_avg(s,'render_video'):.0f} | {stage_avg(s,'story_overlays'):.0f} |"
        )
    lines.append("")
    lines.append("> RTF = tổng giây video tạo ra / wall-time. RTF > 1 nghĩa là nhanh hơn thời gian thực.")
    lines.append("")
    lines.append("## Chỉ số phần cứng (trung bình / đỉnh trong suốt kịch bản)")
    lines.append("")
    lines.append("| Kịch bản | GPU util % | NVENC % | GPU mem (MB) | Power (W) | Temp °C | CPU sys % | ffmpeg CPU % | RAM % |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for n in names:
        hw = sc[n]["hw"]
        def mm(k):
            v = hw.get(k, {})
            return f"{v.get('mean')}/{v.get('max')}"
        lines.append(
            f"| {LABELS[n]} | {mm('gpu_util')} | {mm('enc_util')} | "
            f"{hw['gpu_mem_mb']['max']:.0f} | {hw['power_w']['max']:.0f} | "
            f"{hw['temp_c']['max']:.0f} | {mm('cpu_pct')} | {hw['ffmpeg_cpu_pct']['max']:.0f} | "
            f"{hw['ram_pct']['max']:.0f} |"
        )
    lines.append("")

    md = "\n".join(lines)
    out_md = os.path.join(RES, "REPORT.md")
    open(out_md, "w", encoding="utf-8").write(md)
    print(md)
    print(f"\n-> {out_md}")


if __name__ == "__main__":
    main()
