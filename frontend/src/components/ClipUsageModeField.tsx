import { useEffect, useState } from "react";

import { Label } from "@/components/ui/label";
import { ApiError, getClipUsageSummary } from "@/lib/api";
import type { ClipUsageMode, ClipUsageSummary } from "@/types/api";

// Chế độ chọn clip khi render. "reuse" (mặc định) = bốc ngẫu nhiên như cũ, không
// cần MongoDB. "once" = ưu tiên clip chưa dùng, hết thì clip dùng ít lần nhất;
// lượt dùng lưu ở MongoDB và chỉ tăng khi video render xong.

interface ClipUsageModeFieldProps {
  value: ClipUsageMode;
  onChange: (mode: ClipUsageMode) => void;
  libraryIds: string[];
  disabled?: boolean;
  /** Đổi giá trị này (vd trạng thái render) để tải lại số clip chưa dùng. */
  refreshKey?: string;
}

const formatCount = (value: number) => value.toLocaleString("vi-VN");

export function ClipUsageModeField({ value, onChange, libraryIds, disabled, refreshKey }: ClipUsageModeFieldProps) {
  const [summary, setSummary] = useState<ClipUsageSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const libraryKey = libraryIds.join(",");

  useEffect(() => {
    if (value !== "once" || !libraryKey) {
      setSummary(null);
      setSummaryError(null);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    getClipUsageSummary(libraryKey.split(","))
      .then((res) => {
        if (cancelled) return;
        setSummary(res);
        setSummaryError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setSummary(null);
        setSummaryError(err instanceof ApiError ? err.message : "Không đọc được lượt dùng clip.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [value, libraryKey, refreshKey]);

  const usedTiers = summary
    ? Object.entries(summary.byCount).map(([count, clips]) => `${count} lần: ${formatCount(clips)}`)
    : [];

  return (
    <div className="grid gap-2">
      <Label htmlFor="clip-usage-mode" className="text-sm">
        Chế độ dùng clip
      </Label>
      <select
        id="clip-usage-mode"
        value={value}
        onChange={(event) => onChange(event.target.value === "once" ? "once" : "reuse")}
        disabled={disabled}
        className="h-10 rounded-md border border-input bg-background px-3 text-sm"
      >
        <option value="reuse">Tái sử dụng (mặc định) — bốc ngẫu nhiên như cũ</option>
        <option value="once">Mỗi clip 1 lần — ưu tiên clip chưa dùng</option>
      </select>
      {value === "once" ? (
        <div className="grid gap-1 text-xs">
          <p className="text-muted-foreground">
            Mỗi video lấy clip chưa từng dùng trước; hết clip mới thì lấy clip dùng ít lần nhất. Lượt
            dùng chỉ được tính khi video render xong (cần MongoDB đang chạy).
          </p>
          {isLoading ? <p className="text-muted-foreground">Đang đếm clip chưa dùng...</p> : null}
          {summaryError ? <p className="text-amber-500">{summaryError}</p> : null}
          {summary && !isLoading ? (
            <p className={summary.unused === 0 ? "text-amber-500" : "text-muted-foreground"}>
              Còn <span className="font-medium">{formatCount(summary.unused)}</span>/
              {formatCount(summary.total)} clip chưa dùng
              {usedTiers.length ? ` · đã dùng ${usedTiers.join(", ")}` : ""}
              {summary.unused === 0 ? " — đã hết clip mới, video sẽ dùng lại clip ít lần nhất." : "."}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
