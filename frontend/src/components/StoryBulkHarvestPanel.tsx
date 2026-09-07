import { Check, Download, ExternalLink, Loader2, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { EmptyCard } from "@/components/empty-card";
import { PaginationBar } from "@/components/pagination-bar";
import { StatusAlert } from "@/components/status-alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  cancelStoryHarvest,
  commitStoryHarvest,
  deleteStoryHarvestItems,
  deleteStoryHarvestJob,
  getStoryDownloadProgress,
  getStoryHarvestItems,
  getStoryHarvestJob,
  listStoryHarvestJobs,
  startStoryHarvest,
} from "@/lib/api";
import type {
  DownloadProgress,
  StoryHarvestDeleteRequest,
  StoryHarvestItem,
  StoryHarvestJob,
  StoryVideoProvider,
} from "@/types/api";

const ITEMS_PAGE_SIZE = 24;
const PROVIDERS: { value: StoryVideoProvider; label: string }[] = [
  { value: "pixabay", label: "Pixabay" },
  { value: "pexels", label: "Pexels" },
];

const RUNNING_STATUSES: StoryHarvestJob["status"][] = ["running", "cancelling"];

function formatBytes(bytes: number) {
  if (!bytes) return "0 MB";
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(0)} MB`;
}

function parseKeywords(value: string) {
  return value
    .split(/[\n,]/)
    .map((keyword) => keyword.trim())
    .filter(Boolean);
}

function parseTags(value: string) {
  return value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

export interface StoryBulkHarvestPanelProps {
  libraryId: string;
  disabled?: boolean;
  onCommitted?: () => void;
}

/**
 * Tải hàng loạt: search từng từ khoá → tải hết video về máy → preview/xoá từ ổ
 * cứng → chỉ video giữ lại mới cắt clip và vào thư viện.
 *
 * Khác luồng search/import ở các tab bên cạnh: ở đó mỗi kết quả mở một stream
 * tới CDN provider ngay khi hiện lưới. Ở đây preview đọc từ /media/... nên bước
 * chọn lọc không tốn thêm một request nào tới Pixabay/Pexels.
 */
export function StoryBulkHarvestPanel({ libraryId, disabled = false, onCommitted }: StoryBulkHarvestPanelProps) {
  const [keywordsText, setKeywordsText] = useState("");
  const [providers, setProviders] = useState<StoryVideoProvider[]>(["pixabay"]);
  const [landscapeOnly, setLandscapeOnly] = useState(true);
  const [maxPerKeyword, setMaxPerKeyword] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [isStarting, setIsStarting] = useState(false);

  const [job, setJob] = useState<StoryHarvestJob | null>(null);
  const [items, setItems] = useState<StoryHarvestItem[]>([]);
  const [itemsPage, setItemsPage] = useState(1);
  const [itemsTotalPages, setItemsTotalPages] = useState(1);
  const [keptTotal, setKeptTotal] = useState(0);
  const [keywordFilter, setKeywordFilter] = useState("");
  const [availableKeywords, setAvailableKeywords] = useState<string[]>([]);
  const [isLoadingItems, setIsLoadingItems] = useState(false);
  /** Bump để bắt effect nạp lại danh sách mà không cần gọi loadItems thủ công. */
  const [reloadToken, setReloadToken] = useState(0);

  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<StoryHarvestDeleteRequest | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const [commitSessionId, setCommitSessionId] = useState<string | null>(null);
  const [commitProgress, setCommitProgress] = useState<DownloadProgress | null>(null);
  const [isCommitting, setIsCommitting] = useState(false);

  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const jobPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const commitPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Các effect poll ở dưới phải phụ thuộc vào jobId/isRunning chứ KHÔNG phải cả
  // object `job`: mỗi lần poll lại setJob một object mới, nên nếu để `job` trong
  // deps thì effect tự huỷ và chạy lại ngay, biến interval 2s thành vòng lặp
  // gọi API liên tục.
  const jobId = job?.jobId ?? null;
  const isRunning = Boolean(job && RUNNING_STATUSES.includes(job.status));

  // Callback của parent là arrow inline nên đổi identity mỗi render — giữ trong
  // ref để không kéo theo việc dựng lại interval poll commit.
  const onCommittedRef = useRef(onCommitted);
  onCommittedRef.current = onCommitted;

  const loadItems = useCallback(async (jobId: string, page: number, keyword: string) => {
    setIsLoadingItems(true);
    try {
      const response = await getStoryHarvestItems(jobId, page, ITEMS_PAGE_SIZE, keyword || undefined);
      setItems(response.items);
      setItemsTotalPages(response.totalPages);
      setKeptTotal(response.keptTotal);
      setAvailableKeywords(response.keywords);
      if (page > response.totalPages) setItemsPage(response.totalPages);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong tai duoc danh sach video da tai.");
    } finally {
      setIsLoadingItems(false);
    }
  }, []);

  // Khôi phục job gần nhất khi mở lại trang — video đã tải vẫn nằm trên đĩa nên
  // người dùng quay lại đúng bước chọn lọc thay vì phải tải lại từ đầu.
  useEffect(() => {
    let cancelled = false;
    listStoryHarvestJobs()
      .then((response) => {
        if (cancelled) return;
        const latest = response.jobs.find((entry) => (entry.keptItems ?? 0) > 0 || RUNNING_STATUSES.includes(entry.status));
        if (latest) setJob(latest);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // Poll tiến độ harvest.
  useEffect(() => {
    if (!jobId || !isRunning) return;

    const poll = () => {
      getStoryHarvestJob(jobId)
        .then((progress) => {
          setJob(progress);
          if (!RUNNING_STATUSES.includes(progress.status)) {
            // Danh sách để effect bên dưới nạp một lần khi isRunning tắt.
            setItemsPage(1);
          }
        })
        .catch(() => undefined);
    };
    poll();
    jobPollRef.current = setInterval(poll, 2000);
    return () => {
      if (jobPollRef.current) {
        clearInterval(jobPollRef.current);
        jobPollRef.current = null;
      }
    };
  }, [jobId, isRunning]);

  // Nạp danh sách khi job đã dừng (bao gồm cả job khôi phục lúc mở trang).
  useEffect(() => {
    if (!jobId || isRunning) return;
    void loadItems(jobId, itemsPage, keywordFilter);
  }, [jobId, isRunning, itemsPage, keywordFilter, reloadToken, loadItems]);

  // Poll tiến độ commit — dùng chung session/endpoint với luồng import cũ.
  useEffect(() => {
    if (!commitSessionId) return;

    const poll = () => {
      getStoryDownloadProgress(commitSessionId)
        .then((progress) => {
          setCommitProgress(progress);
          if (progress.status === "completed" || progress.status === "failed") {
            if (commitPollRef.current) {
              clearInterval(commitPollRef.current);
              commitPollRef.current = null;
            }
            setIsCommitting(false);
            setCommitSessionId(null);
            if (progress.status === "completed") {
              setSuccessMessage(`Da them ${progress.addedClips} clip vao thu vien.`);
              setJob(null);
              setItems([]);
              setKeptTotal(0);
              setSelectedIds([]);
              onCommittedRef.current?.();
            } else {
              setErrorMessage(progress.message);
            }
          }
        })
        .catch(() => undefined);
    };
    poll();
    commitPollRef.current = setInterval(poll, 2000);
    return () => {
      if (commitPollRef.current) {
        clearInterval(commitPollRef.current);
        commitPollRef.current = null;
      }
    };
  }, [commitSessionId]);

  const handleStart = async () => {
    const keywords = parseKeywords(keywordsText);
    if (!keywords.length) {
      setErrorMessage("Nhap it nhat 1 tu khoa (moi dong mot tu khoa).");
      return;
    }
    if (!providers.length) {
      setErrorMessage("Chon it nhat 1 provider.");
      return;
    }

    setErrorMessage(null);
    setSuccessMessage(null);
    setIsStarting(true);
    try {
      const started = await startStoryHarvest({
        libraryId,
        keywords,
        providers,
        tags: parseTags(tagsText),
        landscapeOnly,
        maxPerKeyword: Number(maxPerKeyword) || 0,
      });
      setJob(started);
      setItems([]);
      setSelectedIds([]);
      setItemsPage(1);
      setKeywordFilter("");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong bat dau duoc job tai hang loat.");
    } finally {
      setIsStarting(false);
    }
  };

  const handleCancel = async () => {
    if (!job) return;
    try {
      setJob(await cancelStoryHarvest(job.jobId));
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong huy duoc job.");
    }
  };

  const handleDelete = async () => {
    if (!job || !deleteTarget) return;
    setIsDeleting(true);
    setErrorMessage(null);
    try {
      const response = await deleteStoryHarvestItems(job.jobId, deleteTarget);
      // Xoá sạch một từ khoá thì bộ lọc đó không còn item nào — quay về "Tất cả"
      // thay vì để người dùng nhìn lưới rỗng.
      setSelectedIds([]);
      setDeleteTarget(null);
      if (deleteTarget.scope === "keyword") setKeywordFilter("");
      setSuccessMessage(`Da xoa ${response.deletedCount} video. Con lai ${response.remainingCount}.`);
      setReloadToken((current) => current + 1);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Xoa video that bai.");
    } finally {
      setIsDeleting(false);
    }
  };

  const handleCommit = async () => {
    if (!job) return;
    setErrorMessage(null);
    setSuccessMessage(null);
    setIsCommitting(true);
    setCommitProgress(null);
    try {
      const response = await commitStoryHarvest(job.jobId, libraryId);
      setCommitSessionId(response.sessionId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong nhap duoc vao thu vien.");
      setIsCommitting(false);
    }
  };

  const handleDiscardJob = async () => {
    if (!job) return;
    try {
      await deleteStoryHarvestJob(job.jobId);
      setJob(null);
      setItems([]);
      setKeptTotal(0);
      setSelectedIds([]);
      setSuccessMessage("Da xoa toan bo video da tai cua dot nay.");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong xoa duoc job.");
    }
  };

  const toggleProvider = (provider: StoryVideoProvider) => {
    setProviders((current) =>
      current.includes(provider) ? current.filter((value) => value !== provider) : [...current, provider],
    );
  };

  const toggleItem = (itemId: string) => {
    setSelectedIds((current) =>
      current.includes(itemId) ? current.filter((value) => value !== itemId) : [...current, itemId],
    );
  };

  const pageIds = items.map((item) => item.itemId);
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.includes(id));
  const commitPercent = commitProgress
    ? Math.round((commitProgress.current / Math.max(commitProgress.total, 1)) * 100)
    : 0;
  const busy = disabled || isCommitting || isDeleting;

  return (
    <div className="grid min-w-0 gap-4">
      {errorMessage ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {errorMessage}
        </div>
      ) : null}
      {successMessage ? <StatusAlert title="Thanh cong" message={successMessage} /> : null}

      {/* --- Bước 1: nhập từ khoá --- */}
      <Card className="min-w-0 overflow-hidden border-border/70 bg-card/90">
        <CardContent className="grid min-w-0 gap-4 p-4">
          <div className="grid min-w-0 gap-2">
            <Label className="text-xs">Tu khoa (moi dong mot tu khoa, chay lan luot)</Label>
            <Textarea
              rows={4}
              value={keywordsText}
              onChange={(event) => setKeywordsText(event.target.value)}
              placeholder={"rain forest\nnight city\nocean waves"}
              disabled={isRunning || busy}
            />
            <p className="text-xs text-muted-foreground">
              Tai file khong ton API request — chi buoc search ton quota. Voi Pixabay, mot tu khoa toi da
              500 ket qua (3 request); Pexels co the nhieu hon.
            </p>
          </div>

          <div className="grid min-w-0 gap-3 md:grid-cols-3">
            <div className="grid min-w-0 gap-2">
              <Label className="text-xs">Nguon</Label>
              <div className="flex h-9 items-center gap-4">
                {PROVIDERS.map((provider) => (
                  <label key={provider.value} className="flex cursor-pointer items-center gap-2 text-sm">
                    <Checkbox
                      checked={providers.includes(provider.value)}
                      onCheckedChange={() => toggleProvider(provider.value)}
                      disabled={isRunning || busy}
                    />
                    {provider.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="grid min-w-0 gap-2">
              <Label className="text-xs">Toi da moi tu khoa (de trong = tai het)</Label>
              <Input
                type="number"
                min={0}
                value={maxPerKeyword}
                onChange={(event) => setMaxPerKeyword(event.target.value)}
                placeholder="tai het"
                disabled={isRunning || busy}
              />
            </div>
            <div className="grid min-w-0 gap-2">
              <Label className="text-xs">Tags khi luu vao thu vien</Label>
              <Input
                value={tagsText}
                onChange={(event) => setTagsText(event.target.value)}
                placeholder="nature, city"
                disabled={isRunning || busy}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <Checkbox
                checked={landscapeOnly}
                onCheckedChange={(checked) => setLandscapeOnly(Boolean(checked))}
                disabled={isRunning || busy}
              />
              Chi tai video landscape 16:9
            </label>
            <Button type="button" onClick={handleStart} disabled={isRunning || isStarting || busy}>
              {isStarting || isRunning ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <Download className="mr-2 size-4" />
              )}
              Bat dau tai hang loat
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* --- Bước 2: tiến độ tải --- */}
      {job ? (
        <Card className="min-w-0 overflow-hidden border-border/70 bg-card/90">
          <CardContent className="grid min-w-0 gap-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={isRunning ? "default" : "secondary"} className="rounded-full">
                  {job.status}
                </Badge>
                <span className="text-sm text-muted-foreground">
                  Tu khoa {Math.min(job.keywordIndex + 1, job.keywordTotal)}/{job.keywordTotal}
                  {job.currentKeyword ? ` — "${job.currentKeyword}"` : ""}
                  {isRunning && job.currentProvider ? ` (${job.currentProvider})` : ""}
                </span>
              </div>
              {isRunning ? (
                <Button type="button" variant="outline" size="sm" onClick={handleCancel} disabled={job.status === "cancelling"}>
                  <X className="mr-2 size-4" />
                  Huy
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive"
                  onClick={handleDiscardJob}
                  disabled={busy}
                >
                  <Trash2 className="mr-2 size-4" />
                  Bo dot nay
                </Button>
              )}
            </div>

            <p className="text-sm text-muted-foreground">{job.message}</p>

            <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
              <span>Da tai: <span className="font-semibold text-foreground">{job.downloaded}</span> video</span>
              <span>Dung luong: {formatBytes(job.bytesDownloaded)}</span>
              <span>Request search da dung: {job.searchRequests}</span>
              <span>Bo qua: {job.skipped}</span>
              <span>Loi: {job.failed}</span>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* --- Bước 3: chọn lọc --- */}
      {job && !isRunning ? (
        <Card className="min-w-0 overflow-hidden border-border/70 bg-card/90">
          <CardContent className="grid min-w-0 gap-4 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="rounded-full">
                  Con {keptTotal} video
                </Badge>
                {selectedIds.length ? (
                  <Badge variant="default" className="rounded-full">
                    Da chon {selectedIds.length}
                  </Badge>
                ) : null}
                {availableKeywords.length > 1 ? (
                  <div className="flex flex-wrap gap-1">
                    <Badge
                      variant={keywordFilter === "" ? "default" : "secondary"}
                      className="cursor-pointer rounded-full px-2 py-0.5"
                      onClick={() => {
                        setKeywordFilter("");
                        setItemsPage(1);
                      }}
                    >
                      Tat ca
                    </Badge>
                    {availableKeywords.map((keyword) => (
                      <Badge
                        key={keyword}
                        variant={keywordFilter === keyword ? "default" : "secondary"}
                        className="cursor-pointer rounded-full px-2 py-0.5"
                        onClick={() => {
                          setKeywordFilter(keyword);
                          setItemsPage(1);
                        }}
                      >
                        {keyword}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setSelectedIds((current) =>
                      allPageSelected
                        ? current.filter((id) => !pageIds.includes(id))
                        : Array.from(new Set([...current, ...pageIds])),
                    )
                  }
                  disabled={busy || !items.length}
                >
                  {allPageSelected ? <X className="mr-2 size-4" /> : <Check className="mr-2 size-4" />}
                  {allPageSelected ? "Bo chon trang nay" : "Chon trang nay"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive"
                  onClick={() => setDeleteTarget({ scope: "ids", itemIds: selectedIds })}
                  disabled={busy || !selectedIds.length}
                >
                  <Trash2 className="mr-2 size-4" />
                  Xoa da chon ({selectedIds.length})
                </Button>
                {keywordFilter ? (
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    onClick={() => setDeleteTarget({ scope: "keyword", keyword: keywordFilter })}
                    disabled={busy}
                  >
                    <Trash2 className="mr-2 size-4" />
                    Xoa het "{keywordFilter}"
                  </Button>
                ) : null}
              </div>
            </div>

            {isLoadingItems ? (
              <div className="flex items-center gap-3 py-8 text-sm text-muted-foreground">
                <div className="size-4 animate-spin rounded-full border-2 border-primary/25 border-t-primary" />
                <span>Dang tai danh sach...</span>
              </div>
            ) : items.length === 0 ? (
              <EmptyCard
                title="Khong con video nao"
                description="Tat ca video cua dot nay da bi xoa hoac da duoc nhap vao thu vien."
              />
            ) : (
              <>
                <section className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  {items.map((item) => {
                    const selected = selectedIds.includes(item.itemId);
                    return (
                      <Card
                        key={item.itemId}
                        className={`min-w-0 overflow-hidden bg-card/90 ${selected ? "border-primary" : "border-border/70"}`}
                      >
                        <CardContent className="grid min-w-0 gap-3 p-3">
                          <div className="relative aspect-video w-full min-w-0 overflow-hidden rounded-lg bg-black">
                            {/* Phát từ ổ cứng qua /media/... — không gọi tới Pixabay/Pexels. */}
                            <video
                              controls
                              preload="metadata"
                              src={item.previewPath}
                              className="absolute inset-0 block h-full w-full max-w-full object-cover"
                            />
                          </div>
                          <div className="grid min-w-0 gap-2">
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <div className="truncate text-sm font-semibold text-foreground">
                                  {item.title || item.videoId}
                                </div>
                                <div className="text-xs text-muted-foreground">
                                  {item.duration}s | {item.width}x{item.height} | {formatBytes(item.bytes)}
                                </div>
                              </div>
                              {item.pageUrl ? (
                                <a
                                  href={item.pageUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="shrink-0 rounded-md p-1 text-muted-foreground hover:text-foreground"
                                  title="Mo trang goc"
                                >
                                  <ExternalLink className="size-4" />
                                </a>
                              ) : null}
                            </div>
                            <div className="flex flex-wrap items-center gap-1">
                              <Badge variant="secondary" className="rounded-full px-2 py-0.5 text-[10px]">
                                {item.provider}
                              </Badge>
                              <Badge variant="secondary" className="rounded-full px-2 py-0.5 text-[10px]">
                                {item.keyword}
                              </Badge>
                            </div>
                            <div className="flex items-center gap-2">
                              <Checkbox
                                id={`harvest-${item.itemId}`}
                                checked={selected}
                                onCheckedChange={() => toggleItem(item.itemId)}
                                disabled={busy}
                              />
                              <label htmlFor={`harvest-${item.itemId}`} className="cursor-pointer text-xs text-muted-foreground">
                                Chon de xoa
                              </label>
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                className="ml-auto h-7 px-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
                                onClick={() => setDeleteTarget({ scope: "ids", itemIds: [item.itemId] })}
                                disabled={busy}
                                title="Xoa video nay"
                              >
                                <Trash2 className="size-4" />
                              </Button>
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </section>

                <PaginationBar
                  page={itemsPage}
                  totalPages={itemsTotalPages}
                  onPrevious={() => setItemsPage((current) => Math.max(1, current - 1))}
                  onNext={() => setItemsPage((current) => Math.min(itemsTotalPages, current + 1))}
                />
              </>
            )}

            {commitProgress ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>{commitProgress.message}</span>
                  <span>{commitPercent}%</span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-all duration-300"
                    style={{ width: `${commitPercent}%` }}
                  />
                </div>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border/70 pt-4">
              <span className="mr-auto text-xs text-muted-foreground">
                Chi video con lai moi duoc cat clip va bake theo hieu ung thu vien.
              </span>
              <Button type="button" onClick={handleCommit} disabled={busy || !keptTotal}>
                {isCommitting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Check className="mr-2 size-4" />}
                Cat clip &amp; dua vao thu vien ({keptTotal})
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {!job ? (
        <EmptyCard
          title="Chua co dot tai nao"
          description="Nhap tu khoa o tren va bam Bat dau — he thong se tai het video ve may truoc, sau do ban moi preview va xoa video thua."
        />
      ) : null}

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !isDeleting) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Xoa video da tai?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.scope === "ids"
                ? `Thao tac se xoa ${deleteTarget.itemIds.length} video khoi o cung. Khong the hoan tac (tai lai se ton them request search).`
                : deleteTarget?.scope === "keyword"
                  ? `Thao tac se xoa toan bo video cua tu khoa "${deleteTarget.keyword}" khoi o cung. Khong the hoan tac.`
                  : "Thao tac se xoa toan bo video cua dot nay khoi o cung. Khong the hoan tac."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Huy</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={isDeleting}
              onClick={(event) => {
                event.preventDefault();
                void handleDelete();
              }}
            >
              {isDeleting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Trash2 className="mr-2 size-4" />}
              Xoa
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
