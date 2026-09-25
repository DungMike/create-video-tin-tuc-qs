import { Loader2, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, listStoryImageSearchCursors, resetStoryImageSearchCursor } from "@/lib/api";
import type { StoryImageSearchCursor, StoryImageSearchCursorsResponse, StoryVideoProvider } from "@/types/api";

type ProviderFilter = "all" | StoryVideoProvider;

const EXHAUSTED_REASON: Record<string, string> = {
  last_page: "đã tới trang cuối",
  empty_page: "trang rỗng",
  no_results: "không có kết quả",
  no_next_page: "hết trang",
  out_of_range: "vượt giới hạn truy cập (Pixabay tối đa 500)",
};

const MAX_ROWS = 500;

export interface StoryImageSearchCursorsPanelProps {
  /** Đổi giá trị để nạp lại (vd khi job tìm ảnh vừa xong). */
  refreshKey?: number;
  disabled?: boolean;
}

/**
 * Metadata phân trang theo từng (nguồn, từ khóa) — MongoDB `image_search_cursors`.
 * Lần tìm sau của từ khóa bắt đầu ở "Trang kế" với đúng `per_page` đã chốt, nên
 * từ khóa nhiều kết quả được quét dần qua nhiều lượt thay vì luôn lặp lại trang 1.
 */
export function StoryImageSearchCursorsPanel({ refreshKey = 0, disabled = false }: StoryImageSearchCursorsPanelProps) {
  const [data, setData] = useState<StoryImageSearchCursorsResponse | null>(null);
  const [provider, setProvider] = useState<ProviderFilter>("all");
  const [filter, setFilter] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [resettingKey, setResettingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      setData(await listStoryImageSearchCursors(provider === "all" ? undefined : provider));
      setError(null);
    } catch (err) {
      setData(null);
      setError(err instanceof ApiError ? err.message : "Không tải được metadata phân trang.");
    } finally {
      setIsLoading(false);
    }
  }, [provider]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return needle ? items.filter((item) => item.normalized.includes(needle)) : items;
  }, [items, filter]);

  const handleReset = async (item: StoryImageSearchCursor) => {
    const key = `${item.provider}:${item.normalized}`;
    setResettingKey(key);
    try {
      await resetStoryImageSearchCursor(item.provider, item.keyword);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không reset được từ khóa.");
    } finally {
      setResettingKey(null);
    }
  };

  const exhaustedCount = items.filter((item) => item.exhausted).length;
  const counts = data?.photoCounts;

  return (
    <div className="grid min-w-0 gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={provider}
          onChange={(event) => setProvider(event.target.value as ProviderFilter)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="all">Cả hai nguồn</option>
          <option value="pixabay">Pixabay</option>
          <option value="pexels">Pexels</option>
        </select>
        <Input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Lọc từ khóa..."
          className="h-9 w-56"
        />
        <Button type="button" variant="outline" size="sm" onClick={() => void load()} disabled={isLoading}>
          {isLoading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RefreshCw className="mr-2 size-4" />}
          Tải lại
        </Button>
        {!error && !isLoading ? (
          <span className="text-xs text-muted-foreground">
            {items.length} từ khóa, {exhaustedCount} đã quét hết
            {counts
              ? ` · ảnh: ${counts.committed} đã thành clip, ${counts.staged} chờ duyệt, ${counts.rejected} bị loại`
              : ""}
          </span>
        ) : null}
      </div>

      {error ? <p className="text-sm text-amber-500">{error}</p> : null}

      {!error && !isLoading && !filtered.length ? (
        <p className="text-sm text-muted-foreground">Chưa tìm ảnh cho từ khóa nào.</p>
      ) : null}

      {filtered.length ? (
        <div className="max-h-96 min-w-0 overflow-auto rounded-md border border-border/70">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-card text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Từ khóa</th>
                <th className="px-3 py-2 font-medium">Nguồn</th>
                <th className="px-3 py-2 font-medium" title="Lần tìm sau bắt đầu từ trang này">Trang kế</th>
                <th className="px-3 py-2 font-medium" title="Chốt từ lần tìm đầu, các lần sau dùng lại">per_page</th>
                <th className="px-3 py-2 font-medium">Tổng kết quả</th>
                <th className="px-3 py-2 font-medium" title="Trang đã tới / trang tối đa truy cập được">Đã quét</th>
                <th className="px-3 py-2 font-medium" title="Ảnh đã tải về / ảnh bị loại (nhỏ, sai tỉ lệ)">Ảnh lấy / loại</th>
                <th className="px-3 py-2 font-medium">Trạng thái</th>
                <th className="px-3 py-2 font-medium">Lần cuối</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, MAX_ROWS).map((item) => {
                const key = `${item.provider}:${item.normalized}`;
                return (
                  <tr key={key} className="border-t border-border/50">
                    <td className="px-3 py-1.5">{item.keyword}</td>
                    <td className="px-3 py-1.5 capitalize">{item.provider}</td>
                    <td className="px-3 py-1.5 tabular-nums">{item.exhausted ? "-" : item.nextPage}</td>
                    <td className="px-3 py-1.5 tabular-nums">{item.perPage ?? "-"}</td>
                    <td className="px-3 py-1.5 tabular-nums">{item.totalResults?.toLocaleString("vi-VN") ?? "-"}</td>
                    <td className="px-3 py-1.5 tabular-nums">
                      {item.lastPageFetched ?? 0}/{item.maxPage ?? "?"}
                      <span className="ml-1 text-xs text-muted-foreground">({item.requests} request)</span>
                    </td>
                    <td className="px-3 py-1.5 tabular-nums">
                      {item.photosAccepted} / {item.photosRejected}
                    </td>
                    <td className="px-3 py-1.5">
                      {!item.signatureCurrent ? (
                        <Badge variant="secondary" className="rounded-full" title="Bộ lọc tìm kiếm đã đổi">
                          Tìm lại từ trang 1
                        </Badge>
                      ) : item.exhausted ? (
                        <Badge variant="secondary" className="rounded-full">
                          Hết{item.exhaustedReason ? ` — ${EXHAUSTED_REASON[item.exhaustedReason] ?? item.exhaustedReason}` : ""}
                        </Badge>
                      ) : (
                        <Badge variant="default" className="rounded-full">Còn</Badge>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-xs text-muted-foreground">
                      {item.lastSearchedAt ? new Date(item.lastSearchedAt).toLocaleString("vi-VN") : "-"}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        title="Quét lại từ trang 1 (ảnh đã lấy vẫn không bị tải trùng)"
                        onClick={() => void handleReset(item)}
                        disabled={disabled || resettingKey === key || (item.nextPage === 1 && !item.exhausted)}
                      >
                        {resettingKey === key ? <Loader2 className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {filtered.length > MAX_ROWS ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              Đang hiện {MAX_ROWS}/{filtered.length} dòng — lọc thêm để thu hẹp.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
