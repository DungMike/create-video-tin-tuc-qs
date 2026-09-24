import { Check, ChevronLeft, ChevronRight, Download, ExternalLink, Loader2, Search, X } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyCard } from "@/components/empty-card";
import { StatusAlert } from "@/components/status-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, searchDecorProviderImages } from "@/lib/api";
import type { StoryProviderImage, StoryProviderImageSearchResponse, StoryVideoProvider } from "@/types/api";

const PROVIDERS: StoryVideoProvider[] = ["pexels", "pixabay"];
const PROVIDER_LABEL: Record<StoryVideoProvider, string> = { pexels: "Pexels", pixabay: "Pixabay" };

const imageKey = (item: Pick<StoryProviderImage, "provider" | "id">) => `${item.provider}:${item.id}`;

// Nhieu tu khoa cung luc: moi tu khoa la mot request, gop ket qua lai. Chay
// song song vua phai de khong dot quota Pexels (200 req/h/key) qua nhanh.
const KEYWORD_CONCURRENCY = 3;

/** Tach danh sach tu khoa theo dau phay hoac xuong dong, bo trung (khong phan biet hoa thuong). */
function parseKeywords(value: string) {
  const seen = new Set<string>();
  return value
    .split(/[,\n]/)
    .map((keyword) => keyword.trim())
    .filter((keyword) => {
      const key = keyword.toLowerCase();
      if (!keyword || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

/** Ket qua cua cung mot trang tren moi tu khoa, da gop va bo trung. */
type MergedResult = {
  page: number;
  items: StoryProviderImage[];
  keywordCount: number;
  rawCount: number;
  filteredOut: number;
  duplicates: number;
  hasMore: boolean;
  notice?: string;
};

type ProviderState = {
  query: string;
  response: MergedResult | null;
};

const EMPTY_STATE: Record<StoryVideoProvider, ProviderState> = {
  pexels: { query: "", response: null },
  pixabay: { query: "", response: null },
};

/**
 * Tìm ảnh decor trên Pexels/Pixabay theo từ khoá. Backend đã lọc sẵn: chỉ ảnh
 * >= 1920x1080 và tỷ lệ ngang ~16:9 (±3%). Chọn xong bấm import: parent tải
 * từng ảnh về như một lượt upload và trả lại các key đã import thành công.
 */
export function StoryDecorImageSearchPanel({
  group,
  isImporting,
  progress,
  onImport,
}: {
  group: string;
  isImporting: boolean;
  progress: { done: number; total: number } | null;
  onImport: (items: StoryProviderImage[]) => Promise<string[]>;
}) {
  const [provider, setProvider] = useState<StoryVideoProvider>("pexels");
  const [states, setStates] = useState(EMPTY_STATE);
  const [searching, setSearching] = useState<StoryVideoProvider | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Record<string, StoryProviderImage>>({});
  const [imported, setImported] = useState<Set<string>>(new Set());

  const selectedList = useMemo(() => Object.values(selected), [selected]);

  const runSearch = async (target: StoryVideoProvider, page: number) => {
    const keywords = parseKeywords(states[target].query);
    if (!keywords.length) {
      setError("Nhập từ khoá để tìm ảnh.");
      return;
    }
    setError(null);
    setSearching(target);
    try {
      // Giu thu tu tu khoa khi gop, du request nao xong truoc.
      const results: (StoryProviderImageSearchResponse | null)[] = new Array(keywords.length).fill(null);
      const errors: string[] = [];
      let next = 0;
      const worker = async () => {
        while (next < keywords.length) {
          const index = next++;
          try {
            results[index] = await searchDecorProviderImages(target, keywords[index], page);
          } catch (err) {
            errors.push(`"${keywords[index]}": ${err instanceof ApiError ? err.message : "lỗi không xác định"}`);
          }
        }
      };
      await Promise.all(Array.from({ length: Math.min(KEYWORD_CONCURRENCY, keywords.length) }, worker));

      const ok = results.filter((result): result is StoryProviderImageSearchResponse => result !== null);
      const seen = new Set<string>();
      const items: StoryProviderImage[] = [];
      let duplicates = 0;
      for (const result of ok) {
        for (const item of result.items) {
          const key = imageKey(item);
          if (seen.has(key)) duplicates += 1;
          else {
            seen.add(key);
            items.push(item);
          }
        }
      }
      const merged: MergedResult = {
        page,
        items,
        keywordCount: keywords.length,
        rawCount: ok.reduce((sum, result) => sum + result.rawCount, 0),
        filteredOut: ok.reduce((sum, result) => sum + result.filteredOut, 0),
        duplicates,
        hasMore: ok.some((result) => result.hasMore),
        notice: ok.find((result) => result.notice)?.notice,
      };
      setStates((current) => ({ ...current, [target]: { ...current[target], response: merged } }));
      setImported((current) => new Set([...current, ...ok.flatMap((result) => result.importedKeys)]));
      if (errors.length) {
        setError(`Không tìm được ${errors.length}/${keywords.length} từ khoá trên ${PROVIDER_LABEL[target]}: ${errors.join("; ")}`);
      }
    } finally {
      setSearching(null);
    }
  };

  const toggle = (item: StoryProviderImage) => {
    const key = imageKey(item);
    setSelected((current) => {
      const next = { ...current };
      if (next[key]) delete next[key];
      else next[key] = item;
      return next;
    });
  };

  const togglePage = (items: StoryProviderImage[], select: boolean) => {
    setSelected((current) => {
      const next = { ...current };
      for (const item of items) {
        if (select) next[imageKey(item)] = item;
        else delete next[imageKey(item)];
      }
      return next;
    });
  };

  const handleImport = async () => {
    if (!selectedList.length) return;
    const done = await onImport(selectedList);
    if (!done.length) return;
    setImported((current) => new Set([...current, ...done]));
    setSelected((current) => {
      const next = { ...current };
      for (const key of done) delete next[key];
      return next;
    });
  };

  return (
    <div className="grid min-w-0 gap-3 rounded-lg border border-border/70 bg-background/60 p-4">
      <div>
        <div className="text-sm font-semibold text-foreground">Tìm ảnh decor trên Pexels / Pixabay</div>
        <p className="text-xs text-muted-foreground">
          Chỉ hiện ảnh tối thiểu 1920x1080, tỷ lệ ngang 16:9 (±3%). Ảnh import sẽ vào nhóm{" "}
          <strong>{group || "Chưa phân nhóm"}</strong> và mở ngay trong hàng đợi duyệt để kéo khung.
        </p>
      </div>

      {error ? <StatusAlert title="Tìm ảnh decor" message={error} variant="destructive" /> : null}

      <Tabs value={provider} onValueChange={(value) => setProvider(value as StoryVideoProvider)}>
        <TabsList className="w-fit">
          {PROVIDERS.map((p) => (
            <TabsTrigger key={p} value={p}>
              {PROVIDER_LABEL[p]}
            </TabsTrigger>
          ))}
        </TabsList>

        {PROVIDERS.map((p) => {
          const { query, response } = states[p];
          const keywordCount = parseKeywords(query).length;
          // Anh da co trong thu vien decor (ke ca vua import xong) bi bo khoi ket qua.
          const items = (response?.items ?? []).filter((item) => !imported.has(imageKey(item)));
          const skippedImported = (response?.items.length ?? 0) - items.length;
          const allPageSelected = items.length > 0 && items.every((item) => selected[imageKey(item)]);
          const isSearching = searching === p;
          return (
            <TabsContent key={p} value={p} className="mt-3 grid min-w-0 gap-3">
              <div className="grid min-w-0 gap-2 md:grid-cols-[minmax(0,1fr)_auto]">
                <div className="grid min-w-0 gap-1">
                  <Label className="text-xs">
                    Từ khoá — cách nhau bằng dấu phẩy hoặc xuống dòng
                    {keywordCount > 1 ? ` (${keywordCount} từ khoá)` : ""}
                  </Label>
                  <Textarea
                    value={query}
                    rows={3}
                    onChange={(event) => {
                      const value = event.target.value;
                      setStates((current) => ({ ...current, [p]: { ...current[p], query: value } }));
                    }}
                    onKeyDown={(event) => {
                      // Enter xuong dong; Ctrl/Cmd+Enter de tim.
                      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                        event.preventDefault();
                        void runSearch(p, 1);
                      }
                    }}
                    placeholder={`VD:\nliving room tv, old television\ncinema screen (${PROVIDER_LABEL[p]})`}
                  />
                  <span className="text-[11px] text-muted-foreground">Ctrl+Enter để tìm.</span>
                </div>
                <Button type="button" className="self-end" onClick={() => void runSearch(p, 1)} disabled={isSearching}>
                  {isSearching ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Search className="mr-2 size-4" />}
                  Tìm
                </Button>
              </div>

              {response?.notice ? <StatusAlert title={PROVIDER_LABEL[p]} message={response.notice} /> : null}

              {response ? (
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs text-muted-foreground">
                    Trang {response.page}
                    {response.keywordCount > 1 ? ` (${response.keywordCount} từ khoá)` : ""}: {items.length} ảnh mới
                    {response.filteredOut ? ` · ẩn ${response.filteredOut}/${response.rawCount} ảnh không đạt chuẩn` : ""}
                    {skippedImported ? ` · bỏ ${skippedImported} ảnh đã có` : ""}
                    {response.duplicates ? ` · bỏ ${response.duplicates} ảnh trùng giữa các từ khoá` : ""}
                  </span>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={isSearching || response.page <= 1}
                      onClick={() => void runSearch(p, response.page - 1)}
                    >
                      <ChevronLeft className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={isSearching || !response.hasMore}
                      onClick={() => void runSearch(p, response.page + 1)}
                    >
                      <ChevronRight className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant={allPageSelected ? "secondary" : "outline"}
                      size="sm"
                      disabled={isSearching || !items.length}
                      onClick={() => togglePage(items, !allPageSelected)}
                    >
                      {allPageSelected ? <X className="mr-2 size-4" /> : <Check className="mr-2 size-4" />}
                      {allPageSelected ? "Bỏ chọn trang" : "Chọn cả trang"}
                    </Button>
                  </div>
                </div>
              ) : null}

              {items.length ? (
                <div className="grid max-h-[560px] min-w-0 grid-cols-2 gap-3 overflow-y-auto pr-1 md:grid-cols-3 xl:grid-cols-4">
                  {items.map((item) => {
                    const key = imageKey(item);
                    const isSelected = Boolean(selected[key]);
                    return (
                      <div
                        key={key}
                        className={`grid min-w-0 gap-1 rounded-md border p-1.5 ${
                          isSelected ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
                        }`}
                      >
                        <button
                          type="button"
                          className="relative aspect-video w-full overflow-hidden rounded bg-black"
                          onClick={() => toggle(item)}
                          title={item.title}
                        >
                          <img src={item.thumbnailUrl} alt={item.title} loading="lazy" className="h-full w-full object-cover" />
                          {isSelected ? (
                            <span className="absolute right-1 top-1 rounded-full bg-primary p-0.5 text-primary-foreground">
                              <Check className="size-3.5" />
                            </span>
                          ) : null}
                        </button>
                        <div className="flex items-center justify-between gap-1 text-[11px] text-muted-foreground">
                          <span className="truncate">
                            {item.width}x{item.height}
                            {item.author ? ` · ${item.author}` : ""}
                          </span>
                          {item.pageUrl ? (
                            <a
                              href={item.pageUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="shrink-0 hover:text-foreground"
                              title={`Mở trên ${PROVIDER_LABEL[p]}`}
                            >
                              <ExternalLink className="size-3.5" />
                            </a>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : response ? (
                <EmptyCard
                  title="Không có ảnh mới ở trang này"
                  description="Thử từ khoá khác hoặc sang trang sau — ảnh dưới Full HD, không đúng 16:9 hoặc đã có trong thư viện đều bị ẩn."
                />
              ) : null}
            </TabsContent>
          );
        })}
      </Tabs>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border/70 pt-3">
        <Badge variant="secondary" className="h-9 rounded-full px-3">
          Đã chọn {selectedList.length} ảnh
        </Badge>
        {selectedList.length ? (
          <Button type="button" variant="outline" onClick={() => setSelected({})} disabled={isImporting}>
            Bỏ chọn
          </Button>
        ) : null}
        <Button type="button" onClick={() => void handleImport()} disabled={isImporting || !selectedList.length}>
          {isImporting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Download className="mr-2 size-4" />}
          {isImporting && progress
            ? `Đang import ${progress.done}/${progress.total}...`
            : "Thêm vào ảnh decor"}
        </Button>
      </div>
    </div>
  );
}
