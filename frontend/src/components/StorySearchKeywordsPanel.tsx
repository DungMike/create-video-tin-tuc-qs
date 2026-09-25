import { Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, deleteStorySearchKeyword, listStorySearchKeywords } from "@/lib/api";
import type { StorySearchKeywordRecord, StoryVideoProvider } from "@/types/api";

type ProviderFilter = "all" | StoryVideoProvider;

const STATUS_LABEL: Record<StorySearchKeywordRecord["status"], string> = {
  completed: "Quet het",
  limited: "Co gioi han",
  manual: "Tim tay",
  partial: "Quet do",
};

const MAX_ROWS = 500;

/**
 * Tu khoa da tim tren Pixabay/Pexels (MongoDB). Harvest/prefetch chi bo qua tu khoa
 * da dung khi tick "Bo qua tu khoa da dung"; xoa mot dong o day de tim lai duoc.
 */
export function StorySearchKeywordsPanel() {
  const [items, setItems] = useState<StorySearchKeywordRecord[]>([]);
  const [provider, setProvider] = useState<ProviderFilter>("all");
  const [filter, setFilter] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await listStorySearchKeywords(provider === "all" ? undefined : provider);
      setItems(res.items);
      setError(null);
    } catch (err) {
      setItems([]);
      setError(err instanceof ApiError ? err.message : "Khong tai duoc danh sach tu khoa.");
    } finally {
      setIsLoading(false);
    }
  }, [provider]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return needle ? items.filter((item) => item.normalized.includes(needle)) : items;
  }, [items, filter]);

  const handleDelete = async (item: StorySearchKeywordRecord) => {
    const key = `${item.provider}:${item.normalized}`;
    setDeletingKey(key);
    try {
      await deleteStorySearchKeyword(item.provider, item.keyword);
      setItems((current) => current.filter((row) => `${row.provider}:${row.normalized}` !== key));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong xoa duoc tu khoa.");
    } finally {
      setDeletingKey(null);
    }
  };

  const usedCount = items.filter((item) => item.used).length;

  return (
    <div className="grid min-w-0 gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={provider}
          onChange={(event) => setProvider(event.target.value as ProviderFilter)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="all">Ca hai provider</option>
          <option value="pixabay">Pixabay</option>
          <option value="pexels">Pexels</option>
        </select>
        <Input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Loc tu khoa..."
          className="h-9 w-56"
        />
        <Button type="button" variant="outline" size="sm" onClick={() => void load()} disabled={isLoading}>
          {isLoading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RefreshCw className="mr-2 size-4" />}
          Tai lai
        </Button>
        {!error && !isLoading ? (
          <span className="text-xs text-muted-foreground">
            {items.length} tu khoa, {usedCount} da dung (bi bo qua khi tick "Bo qua tu khoa da dung")
          </span>
        ) : null}
      </div>

      {error ? <p className="text-sm text-amber-500">{error}</p> : null}

      {!error && !isLoading && !filtered.length ? (
        <p className="text-sm text-muted-foreground">Chua co tu khoa nao.</p>
      ) : null}

      {filtered.length ? (
        <div className="max-h-96 min-w-0 overflow-auto rounded-md border border-border/70">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-card text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Tu khoa</th>
                <th className="px-3 py-2 font-medium">Provider</th>
                <th className="px-3 py-2 font-medium">Trang thai</th>
                <th className="px-3 py-2 font-medium">Video</th>
                <th className="px-3 py-2 font-medium">Lan cuoi</th>
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
                    <td className="px-3 py-1.5">
                      <Badge variant={item.used ? "default" : "secondary"} className="rounded-full">
                        {STATUS_LABEL[item.status] ?? item.status}
                      </Badge>
                      {item.backfilled ? <span className="ml-2 text-xs text-muted-foreground">(nhap tu du lieu cu)</span> : null}
                    </td>
                    <td className="px-3 py-1.5 tabular-nums">{item.videosDownloaded || "-"}</td>
                    <td className="px-3 py-1.5 text-xs text-muted-foreground">
                      {item.lastSearchedAt ? new Date(item.lastSearchedAt).toLocaleString("vi-VN") : "-"}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        title="Xoa de tim/tai lai tu khoa nay"
                        onClick={() => void handleDelete(item)}
                        disabled={deletingKey === key}
                      >
                        {deletingKey === key ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {filtered.length > MAX_ROWS ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              Dang hien {MAX_ROWS}/{filtered.length} dong — loc them de thu hep.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
