import { Check, Clapperboard, ExternalLink, ImagePlus, Loader2, Square, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { EmptyCard } from "@/components/empty-card";
import { PaginationBar } from "@/components/pagination-bar";
import { StatusAlert } from "@/components/status-alert";
import { StoryImageSearchCursorsPanel } from "@/components/StoryImageSearchCursorsPanel";
import { StoryLibrarySelect } from "@/components/StoryLibrarySelect";
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
  cancelStoryImageClipCommit,
  cancelStoryImageClipJob,
  commitStoryImageClipJob,
  deleteStoryImageClipItems,
  deleteStoryImageClipJob,
  getStoryDownloadProgress,
  getStoryImageClipItems,
  getStoryImageClipJob,
  getStoryLibraries,
  listStoryImageClipEffects,
  listStoryImageClipJobs,
  setStoryImageClipItemEffect,
  startStoryImageClipJob,
} from "@/lib/api";
import { readActiveLibraryId, subscribeActiveLibrary } from "@/lib/storyLibrarySelection";
import type {
  DownloadProgress,
  StoryImageClipDeleteRequest,
  StoryImageClipEffect,
  StoryImageClipItem,
  StoryImageClipJob,
  StoryImageClipStatus,
  StoryLibrary,
  StoryVideoProvider,
} from "@/types/api";

const ITEMS_PAGE_SIZE = 24;
const DEFAULT_PREVIEW_SECONDS = 4;
const PROVIDERS: { value: StoryVideoProvider; label: string }[] = [
  { value: "pexels", label: "Pexels" },
  { value: "pixabay", label: "Pixabay" },
];
const ZOOM_OPTIONS = [
  { value: 1.12, label: "Nhẹ (1.12x)" },
  { value: 1.2, label: "Vừa (1.2x)" },
  { value: 1.3, label: "Mạnh (1.3x)" },
];
const RUNNING_STATUSES: StoryImageClipStatus[] = ["running", "cancelling"];
const STATUS_LABEL: Record<StoryImageClipStatus, string> = {
  running: "Đang tìm",
  cancelling: "Đang huỷ",
  completed: "Tìm xong",
  failed: "Lỗi",
  cancelled: "Đã huỷ",
  stopped_disk: "Dừng — đầy đĩa",
};
const STOPPED_BY: Record<string, string> = {
  limit: "đủ số ảnh của lượt",
  quota: "hết quota",
  error: "lỗi tìm kiếm",
  no_full_access: "key Pixabay thiếu full access",
  page_limit: "chạm 50 trang/lượt",
  disk: "đầy đĩa",
};

function formatBytes(bytes: number) {
  if (!bytes) return "0 MB";
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(0)} MB`;
}

function splitList(value: string, pattern: RegExp) {
  return value
    .split(pattern)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

/** CSS transform mô phỏng đầu/cuối một hiệu ứng Ken Burns trên thumbnail (khớp công thức zoompan ở backend). */
function effectTransform(effect: StoryImageClipEffect, zoom: number, atEnd: boolean) {
  const scale = (atEnd ? effect.endZoomed : effect.startZoomed) ? zoom : 1;
  const [fx, fy] = atEnd ? effect.to : effect.from;
  // Phần lề mỗi bên sau khi phóng, tính theo % kích thước khung trước khi scale.
  const margin = ((scale - 1) / (2 * scale)) * 100;
  const dx = ((0.5 - fx) * 2 * margin).toFixed(2);
  const dy = ((0.5 - fy) * 2 * margin).toFixed(2);
  return `scale(${scale}) translate(${dx}%, ${dy}%)`;
}

function EffectThumbnail({
  src,
  effect,
  zoom,
  seconds,
  alt,
}: {
  src?: string;
  effect?: StoryImageClipEffect;
  zoom: number;
  seconds: number;
  alt: string;
}) {
  const [playing, setPlaying] = useState(false);
  return (
    <div
      className="relative aspect-video w-full min-w-0 overflow-hidden rounded-lg bg-black"
      onMouseEnter={() => setPlaying(true)}
      onMouseLeave={() => setPlaying(false)}
      title="Rê chuột để xem thử chuyển động"
    >
      {src ? (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          className="absolute inset-0 block h-full w-full object-cover"
          style={
            effect
              ? {
                  transform: effectTransform(effect, zoom, playing),
                  transition: playing ? `transform ${seconds}s ease-in-out` : "transform 0.4s ease-out",
                }
              : undefined
          }
        />
      ) : null}
    </div>
  );
}

export interface StoryImageClipPanelProps {
  /** Đổi giá trị khi danh sách thư viện có thể đã đổi (tạo/xoá ở phần Thư viện clip). */
  refreshKey?: number;
  onCommitted?: () => void;
}

/**
 * Thư viện clip từ ảnh: tìm ảnh Pexels/Pixabay theo từ khóa → tải về vùng tạm →
 * duyệt, xoá ảnh xấu → mỗi ảnh còn lại thành 1 clip Ken Burns trong thư viện đích.
 *
 * Mỗi (nguồn, từ khóa) có con trỏ phân trang trong MongoDB: lượt sau đi tiếp từ
 * trang lượt trước dừng, với đúng `per_page` đã dùng, và ảnh đã lấy / đã loại
 * không bao giờ bị tải lại.
 */
export function StoryImageClipPanel({ refreshKey = 0, onCommitted }: StoryImageClipPanelProps) {
  // KHÔNG dùng useActiveStoryLibrary: mỗi instance hook tự "sửa" id active không
  // có trong danh sách của nó, nên một instance thứ hai (danh sách cũ) sẽ ghi đè
  // lựa chọn của Thư viện clip ngay khi bên đó vừa tạo thư viện mới. Ở đây chỉ
  // ĐỌC lựa chọn đã lưu làm mặc định, và tự giữ thư viện đích riêng.
  const [libraries, setLibraries] = useState<StoryLibrary[]>([]);
  const [libraryId, setLibraryId] = useState("");

  const [effects, setEffects] = useState<StoryImageClipEffect[]>([]);
  const [enabledEffects, setEnabledEffects] = useState<string[]>([]);
  const [zoom, setZoom] = useState(1.2);
  const [durationRange, setDurationRange] = useState<[number, number] | null>(null);
  const [keywordsText, setKeywordsText] = useState("");
  const [providers, setProviders] = useState<StoryVideoProvider[]>(["pexels"]);
  const [maxPerKeyword, setMaxPerKeyword] = useState("100");
  const [tagsText, setTagsText] = useState("");
  const [rescanExhausted, setRescanExhausted] = useState(false);
  const [isStarting, setIsStarting] = useState(false);

  const [job, setJob] = useState<StoryImageClipJob | null>(null);
  const [items, setItems] = useState<StoryImageClipItem[]>([]);
  const [itemsPage, setItemsPage] = useState(1);
  const [itemsTotalPages, setItemsTotalPages] = useState(1);
  const [keptTotal, setKeptTotal] = useState(0);
  const [failedTotal, setFailedTotal] = useState(0);
  const [keywordFilter, setKeywordFilter] = useState("");
  const [availableKeywords, setAvailableKeywords] = useState<string[]>([]);
  const [isLoadingItems, setIsLoadingItems] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [cursorsToken, setCursorsToken] = useState(0);

  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<StoryImageClipDeleteRequest | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const [commitSessionId, setCommitSessionId] = useState<string | null>(null);
  const [commitProgress, setCommitProgress] = useState<DownloadProgress | null>(null);
  const [isCommitting, setIsCommitting] = useState(false);

  const [mongoMessage, setMongoMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Poll phụ thuộc jobId/isRunning, không phụ thuộc cả object job (xem StoryBulkHarvestPanel).
  const jobId = job?.jobId ?? null;
  const isRunning = Boolean(job && RUNNING_STATUSES.includes(job.status) && !job.stale);
  const jobIdRef = useRef(jobId);
  jobIdRef.current = jobId;
  const onCommittedRef = useRef(onCommitted);
  onCommittedRef.current = onCommitted;

  const effectById = new Map(effects.map((effect) => [effect.id, effect]));
  const jobZoom = job?.zoom ?? zoom;

  const loadLibraries = useCallback(async () => {
    try {
      const response = await getStoryLibraries();
      setLibraries(response.libraries);
      setLibraryId((current) => {
        const known = (id: string | null) => Boolean(id && response.libraries.some((lib) => lib.id === id));
        if (known(current)) return current;
        const persisted = readActiveLibraryId();
        return persisted && known(persisted) ? persisted : response.defaultLibraryId;
      });
    } catch {
      // Danh sách thư viện lỗi thì nút tạo clip bị khoá (libraryId rỗng).
    }
  }, []);

  // Nạp lại khi phần "Thư viện clip" tạo/đổi/xoá thư viện (pub/sub localStorage) hoặc page báo refresh.
  useEffect(() => {
    void loadLibraries();
  }, [loadLibraries, refreshKey]);
  useEffect(() => subscribeActiveLibrary(() => void loadLibraries()), [loadLibraries]);

  useEffect(() => {
    listStoryImageClipEffects()
      .then((response) => {
        setEffects(response.effects);
        setEnabledEffects(response.effects.map((effect) => effect.id));
        setZoom(response.defaultZoom);
        setDurationRange([response.durationMin, response.durationMax]);
      })
      .catch(() => undefined);
  }, []);

  // Khôi phục đợt gần nhất: ảnh đã tải vẫn nằm trên đĩa, quay lại đúng bước duyệt.
  useEffect(() => {
    let cancelled = false;
    listStoryImageClipJobs()
      .then((response) => {
        if (cancelled) return;
        const latest = response.jobs.find(
          (entry) => (entry.keptItems ?? 0) > 0 || (RUNNING_STATUSES.includes(entry.status) && !entry.stale) || entry.commitLive,
        );
        if (!latest) return;
        setJob(latest);
        if (latest.libraryId) setLibraryId(latest.libraryId);
        if (latest.commitLive && latest.commit?.sessionId) {
          setIsCommitting(true);
          setCommitSessionId(latest.commit.sessionId);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const loadItems = useCallback(async (id: string, page: number, keyword: string) => {
    setIsLoadingItems(true);
    try {
      const response = await getStoryImageClipItems(id, page, ITEMS_PAGE_SIZE, keyword || undefined);
      setItems(response.items);
      setItemsTotalPages(response.totalPages);
      setKeptTotal(response.keptTotal);
      setFailedTotal(response.failedTotal);
      setAvailableKeywords(response.keywords);
      if (page > response.totalPages) setItemsPage(response.totalPages);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Không tải được danh sách ảnh.");
    } finally {
      setIsLoadingItems(false);
    }
  }, []);

  // Poll tiến độ tìm ảnh.
  useEffect(() => {
    if (!jobId || !isRunning) return;
    const poll = () => {
      getStoryImageClipJob(jobId)
        .then((progress) => {
          setJob(progress);
          if (!RUNNING_STATUSES.includes(progress.status) || progress.stale) {
            setItemsPage(1);
            setCursorsToken((current) => current + 1);
          }
        })
        .catch(() => undefined);
    };
    poll();
    const timer = setInterval(poll, 2000);
    return () => clearInterval(timer);
  }, [jobId, isRunning]);

  useEffect(() => {
    if (!jobId || isRunning) return;
    void loadItems(jobId, itemsPage, keywordFilter);
  }, [jobId, isRunning, itemsPage, keywordFilter, reloadToken, loadItems]);

  // Poll tiến độ tạo clip — dùng chung endpoint download-progress với luồng import video.
  useEffect(() => {
    if (!commitSessionId) return;
    let timer: ReturnType<typeof setInterval> | null = null;
    const stop = () => {
      if (timer) clearInterval(timer);
      timer = null;
      setIsCommitting(false);
      setCommitSessionId(null);
    };
    const afterCommit = async () => {
      setCursorsToken((current) => current + 1);
      const id = jobIdRef.current;
      if (!id) return;
      try {
        const [latestJob, response] = await Promise.all([
          getStoryImageClipJob(id),
          getStoryImageClipItems(id, 1, ITEMS_PAGE_SIZE),
        ]);
        if (response.keptTotal === 0) {
          setJob(null);
          setItems([]);
          setKeptTotal(0);
          setSelectedIds([]);
        } else {
          setJob(latestJob);
          setItemsPage(1);
          setReloadToken((current) => current + 1);
        }
      } catch {
        setReloadToken((current) => current + 1);
      }
    };
    const poll = () => {
      getStoryDownloadProgress(commitSessionId)
        .then((progress) => {
          setCommitProgress(progress);
          if (progress.status !== "completed" && progress.status !== "failed") return;
          stop();
          if (progress.status === "completed") {
            setSuccessMessage(progress.message);
            if (progress.addedClips) onCommittedRef.current?.();
          } else {
            setErrorMessage(progress.message);
          }
          void afterCommit();
        })
        .catch((err) => {
          if (err instanceof ApiError && err.status === 404) {
            stop();
            setCommitProgress(null);
            setErrorMessage(
              "Mất phiên tạo clip (backend vừa khởi động lại?). Ảnh chưa tạo clip vẫn còn — bấm Tạo clip để làm tiếp.",
            );
            void afterCommit();
          }
        });
    };
    poll();
    timer = setInterval(poll, 2000);
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [commitSessionId]);

  const reportError = (err: unknown, fallback: string) => {
    const message = err instanceof ApiError ? err.message : fallback;
    if (err instanceof ApiError && err.status === 503) setMongoMessage(message);
    setErrorMessage(message);
  };

  const handleStart = async () => {
    const keywords = splitList(keywordsText, /[\n,]/);
    if (!keywords.length) return setErrorMessage("Nhập ít nhất 1 từ khóa (mỗi dòng một từ khóa).");
    if (!providers.length) return setErrorMessage("Chọn ít nhất 1 nguồn.");
    if (effects.length && !enabledEffects.length) return setErrorMessage("Bật ít nhất 1 hiệu ứng.");
    if (!libraryId) return setErrorMessage("Chọn thư viện đích.");

    setErrorMessage(null);
    setSuccessMessage(null);
    setIsStarting(true);
    try {
      const started = await startStoryImageClipJob({
        libraryId,
        keywords,
        providers,
        tags: splitList(tagsText, /,/),
        maxPerKeyword: Number(maxPerKeyword) || 0,
        effects: enabledEffects,
        zoom,
        ...(rescanExhausted ? { rescanExhausted: true } : {}),
      });
      setMongoMessage(null);
      setJob(started);
      setItems([]);
      setKeptTotal(0);
      setSelectedIds([]);
      setItemsPage(1);
      setKeywordFilter("");
      setRescanExhausted(false);
    } catch (err) {
      reportError(err, "Không bắt đầu được đợt tìm ảnh.");
    } finally {
      setIsStarting(false);
    }
  };

  const handleCancel = async () => {
    if (!job) return;
    try {
      setJob(await cancelStoryImageClipJob(job.jobId));
    } catch (err) {
      reportError(err, "Không huỷ được.");
    }
  };

  const handleDelete = async () => {
    if (!job || !deleteTarget) return;
    setIsDeleting(true);
    setErrorMessage(null);
    try {
      const response = await deleteStoryImageClipItems(job.jobId, deleteTarget);
      setSelectedIds([]);
      setDeleteTarget(null);
      if (deleteTarget.scope === "keyword") setKeywordFilter("");
      setSuccessMessage(`Đã loại ${response.deletedCount} ảnh (sẽ không bị tải lại). Còn ${response.remainingCount}.`);
      setReloadToken((current) => current + 1);
    } catch (err) {
      reportError(err, "Xoá ảnh thất bại.");
    } finally {
      setIsDeleting(false);
    }
  };

  const handleEffectChange = async (item: StoryImageClipItem, effect: string) => {
    if (!job) return;
    try {
      const response = await setStoryImageClipItemEffect(job.jobId, item.itemId, effect);
      setItems((current) =>
        current.map((entry) => (entry.itemId === item.itemId ? { ...entry, effect: response.item.effect } : entry)),
      );
    } catch (err) {
      reportError(err, "Không đổi được hiệu ứng.");
    }
  };

  const handleCommit = async () => {
    if (!job || !libraryId) return;
    setErrorMessage(null);
    setSuccessMessage(null);
    setIsCommitting(true);
    setCommitProgress(null);
    try {
      const response = await commitStoryImageClipJob(job.jobId, libraryId);
      setCommitSessionId(response.sessionId);
    } catch (err) {
      reportError(err, "Không tạo được clip.");
      setIsCommitting(false);
    }
  };

  const handleCancelCommit = async () => {
    if (!job) return;
    try {
      await cancelStoryImageClipCommit(job.jobId);
    } catch (err) {
      reportError(err, "Không dừng được.");
    }
  };

  const handleDiscardJob = async () => {
    if (!job) return;
    try {
      await deleteStoryImageClipJob(job.jobId);
      setJob(null);
      setItems([]);
      setKeptTotal(0);
      setSelectedIds([]);
      setSuccessMessage("Đã bỏ đợt này. Ảnh chưa duyệt có thể được tìm thấy lại nếu reset từ khóa.");
    } catch (err) {
      reportError(err, "Không xoá được đợt này.");
    }
  };

  const toggleProvider = (provider: StoryVideoProvider) =>
    setProviders((current) =>
      current.includes(provider) ? current.filter((value) => value !== provider) : [...current, provider],
    );
  const toggleEffect = (effectId: string) =>
    setEnabledEffects((current) =>
      current.includes(effectId) ? current.filter((value) => value !== effectId) : [...current, effectId],
    );
  const toggleItem = (itemId: string) =>
    setSelectedIds((current) =>
      current.includes(itemId) ? current.filter((value) => value !== itemId) : [...current, itemId],
    );

  const pageIds = items.map((item) => item.itemId);
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.includes(id));
  const commitPercent = commitProgress
    ? Math.round((commitProgress.current / Math.max(commitProgress.total, 1)) * 100)
    : 0;
  const busy = isCommitting || isDeleting;
  // Đợt cũ còn ảnh chưa tạo clip: ảnh đó đã "staged" nên sẽ không bao giờ được tải
  // lại — phải tạo clip hoặc bỏ đợt trước, không để nó bị lãng quên.
  const pendingReview = Boolean(job && !isRunning && keptTotal > 0);
  const formLocked = isRunning || busy || isStarting;

  return (
    <div className="grid min-w-0 gap-4">
      {mongoMessage ? (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm text-muted-foreground">
          <span className="font-medium text-amber-500">Cần MongoDB:</span> {mongoMessage}
        </div>
      ) : null}
      {errorMessage && errorMessage !== mongoMessage ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {errorMessage}
        </div>
      ) : null}
      {successMessage ? <StatusAlert title="Thành công" message={successMessage} /> : null}

      {/* --- Bước 1: tìm ảnh --- */}
      <Card className="min-w-0 overflow-hidden border-border/70 bg-card/90">
        <CardContent className="grid min-w-0 gap-4 p-4">
          <div className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,180px)]">
            <div className="grid min-w-0 content-end gap-2" title="Thư viện đích: clip tạo từ ảnh được thêm vào đây">
              <StoryLibrarySelect
                libraries={libraries}
                value={libraryId}
                onChange={setLibraryId}
                disabled={formLocked}
              />
            </div>
            <div className="grid min-w-0 gap-2">
              <Label className="text-xs">Nguồn ảnh</Label>
              <div className="flex h-9 items-center gap-4">
                {PROVIDERS.map((provider) => (
                  <label key={provider.value} className="flex cursor-pointer items-center gap-2 text-sm">
                    <Checkbox
                      checked={providers.includes(provider.value)}
                      onCheckedChange={() => toggleProvider(provider.value)}
                      disabled={formLocked}
                    />
                    {provider.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="grid min-w-0 gap-2">
              <Label className="text-xs">Tối đa ảnh / từ khóa / lượt</Label>
              <Input
                type="number"
                min={0}
                value={maxPerKeyword}
                onChange={(event) => setMaxPerKeyword(event.target.value)}
                placeholder="0 = tới khi hết"
                disabled={formLocked}
              />
            </div>
          </div>

          <div className="grid min-w-0 gap-2">
            <Label className="text-xs">Từ khóa (mỗi dòng một từ khóa, chạy lần lượt)</Label>
            <Textarea
              rows={3}
              value={keywordsText}
              onChange={(event) => setKeywordsText(event.target.value)}
              placeholder={"city night\nmountain lake\nold temple"}
              disabled={formLocked}
            />
            <p className="text-xs text-muted-foreground">
              Mỗi từ khóa đi tiếp từ trang lần trước dừng (xem bảng bên dưới) với đúng per_page đã dùng — từ khóa
              nhiều kết quả được quét dần qua nhiều lượt. Chỉ nhận ảnh ngang, cắt 16:9 vẫn rộng ≥1920px. Pixabay cần
              key có full API access.
              {durationRange
                ? ` Mỗi ảnh thành 1 clip dài ngẫu nhiên ${durationRange[0]}–${durationRange[1]} giây (STORY_IMAGE_CLIP_DURATION_MIN/MAX), không phụ thuộc độ dài cắt video.`
                : ""}
            </p>
          </div>

          <div className="grid min-w-0 gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label className="text-xs">
                Hiệu ứng ({enabledEffects.length}/{effects.length}) — chia đều cho các ảnh, đổi được từng ảnh lúc duyệt
              </Label>
              <div className="flex gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => setEnabledEffects(effects.map((effect) => effect.id))}
                  disabled={formLocked}
                >
                  Chọn hết
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => setEnabledEffects([])}
                  disabled={formLocked}
                >
                  Bỏ hết
                </Button>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {effects.map((effect) => (
                <label
                  key={effect.id}
                  className="flex cursor-pointer items-center gap-2 rounded-md border border-border/70 px-2 py-1 text-xs"
                >
                  <Checkbox
                    checked={enabledEffects.includes(effect.id)}
                    onCheckedChange={() => toggleEffect(effect.id)}
                    disabled={formLocked}
                  />
                  {effect.label}
                </label>
              ))}
            </div>
          </div>

          <div className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,180px)_minmax(0,1fr)]">
            <div className="grid min-w-0 gap-2">
              <Label className="text-xs">Mức zoom</Label>
              <select
                value={zoom}
                onChange={(event) => setZoom(Number(event.target.value))}
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                disabled={formLocked}
              >
                {ZOOM_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid min-w-0 gap-2">
              <Label className="text-xs">Tags khi lưu vào thư viện</Label>
              <Input
                value={tagsText}
                onChange={(event) => setTagsText(event.target.value)}
                placeholder="city, night"
                disabled={formLocked}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <label
              className="flex cursor-pointer items-center gap-2 text-sm"
              title="Từ khóa đã quét hết kết quả thì mặc định bị bỏ qua. Tick để quét lại từ trang 1 (ảnh đã lấy vẫn không bị tải trùng)."
            >
              <Checkbox
                checked={rescanExhausted}
                onCheckedChange={(checked) => setRescanExhausted(Boolean(checked))}
                disabled={formLocked}
              />
              Quét lại từ đầu các từ khóa đã hết
            </label>
            <div className="flex flex-wrap items-center gap-3">
              {pendingReview ? (
                <span className="text-xs text-amber-500">Tạo clip hoặc bỏ đợt hiện tại trước khi tìm tiếp.</span>
              ) : null}
              <Button type="button" onClick={handleStart} disabled={formLocked || pendingReview}>
                {isStarting || isRunning ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : (
                  <ImagePlus className="mr-2 size-4" />
                )}
                Tìm &amp; tải ảnh
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* --- Bước 2: tiến độ tìm ảnh --- */}
      {job ? (
        <Card className="min-w-0 overflow-hidden border-border/70 bg-card/90">
          <CardContent className="grid min-w-0 gap-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={isRunning ? "default" : "secondary"} className="rounded-full">
                  {STATUS_LABEL[job.status] ?? job.status}
                </Badge>
                <span className="text-sm text-muted-foreground">
                  Từ khóa {Math.min(job.keywordIndex + 1, job.keywordTotal)}/{job.keywordTotal}
                  {job.currentKeyword ? ` — "${job.currentKeyword}"` : ""}
                  {isRunning && job.currentProvider ? ` (${job.currentProvider}` : ""}
                  {isRunning && job.currentProvider && job.currentPage
                    ? `, trang ${job.currentPage}${job.currentMaxPage ? `/${job.currentMaxPage}` : ""})`
                    : isRunning && job.currentProvider
                      ? ")"
                      : ""}
                </span>
              </div>
              {isRunning ? (
                <Button type="button" variant="outline" size="sm" onClick={handleCancel} disabled={job.status === "cancelling"}>
                  <X className="mr-2 size-4" />
                  Huỷ
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
                  Bỏ đợt này
                </Button>
              )}
            </div>

            <p className="text-sm text-muted-foreground">{job.message}</p>

            {job.stale ? (
              <p className="text-xs text-amber-500">
                Đợt này bị dừng giữa chừng (web app khởi động lại). Ảnh đã tải vẫn duyệt và tạo clip được; chạy lại
                từ khóa để tìm tiếp — hệ thống đi tiếp từ trang đã lưu.
              </p>
            ) : null}
            {Object.entries({ ...job.notices, ...job.quotaStopped }).map(([provider, message]) => (
              <p key={provider} className="text-xs text-amber-500">
                [{provider}] {message}
              </p>
            ))}
            {job.providerErrors.map((entry, index) => (
              <p key={`${entry.provider}-${index}`} className="text-xs text-amber-500">
                [{entry.provider}] "{entry.keyword}": {entry.message}
              </p>
            ))}
            {job.exhaustedPairs.length ? (
              <p className="text-xs text-muted-foreground">
                Bỏ qua {job.exhaustedPairs.length} từ khóa đã quét hết:{" "}
                {job.exhaustedPairs.map((pair) => `"${pair.keyword}" (${pair.provider})`).join(", ")}
              </p>
            ) : null}

            <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
              <span>
                Đã tải: <span className="font-semibold text-foreground">{job.downloaded}</span> ảnh
              </span>
              <span>Dung lượng: {formatBytes(job.bytesDownloaded)}</span>
              <span>Request tìm kiếm: {job.searchRequests}</span>
              <span>Loại (nhỏ/sai tỉ lệ): {job.rejected}</span>
              <span>Trùng (đã có): {job.duplicates}</span>
              <span>Lỗi tải: {job.failed}</span>
            </div>

            {job.keywordStats.length ? (
              <div className="grid gap-1 rounded-md border border-border/60 p-2 text-xs text-muted-foreground">
                {job.keywordStats.map((stat, index) => (
                  <div key={`${stat.provider}-${stat.keyword}-${index}`} className="flex flex-wrap gap-x-3">
                    <span className="font-medium text-foreground">"{stat.keyword}"</span>
                    <span className="capitalize">{stat.provider}</span>
                    <span>
                      trang {stat.startPage}
                      {stat.endPage && stat.endPage !== stat.startPage ? `→${stat.endPage}` : ""}
                      {stat.maxPage ? `/${stat.maxPage}` : ""} · {stat.perPage}/trang
                    </span>
                    <span>
                      lấy {stat.accepted} · loại {stat.rejected} · trùng {stat.duplicates}
                    </span>
                    <span>
                      {stat.exhausted ? "đã hết kết quả" : stat.stoppedBy ? `dừng: ${STOPPED_BY[stat.stoppedBy] ?? stat.stoppedBy}` : ""}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {/* --- Bước 3: duyệt ảnh + tạo clip --- */}
      {job && !isRunning ? (
        <Card className="min-w-0 overflow-hidden border-border/70 bg-card/90">
          <CardContent className="grid min-w-0 gap-4 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="rounded-full">
                  Còn {keptTotal} ảnh
                </Badge>
                {failedTotal ? (
                  <Badge variant="destructive" className="rounded-full">
                    {failedTotal} ảnh tạo clip lỗi — thử lại được
                  </Badge>
                ) : null}
                {selectedIds.length ? (
                  <Badge variant="default" className="rounded-full">
                    Đã chọn {selectedIds.length}
                  </Badge>
                ) : null}
                {availableKeywords.length > 1 ? (
                  <div className="flex flex-wrap gap-1">
                    {["", ...availableKeywords].map((keyword) => (
                      <Badge
                        key={keyword || "__all"}
                        variant={keywordFilter === keyword ? "default" : "secondary"}
                        className="cursor-pointer rounded-full px-2 py-0.5"
                        onClick={() => {
                          setKeywordFilter(keyword);
                          setItemsPage(1);
                        }}
                      >
                        {keyword || "Tất cả"}
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
                  {allPageSelected ? "Bỏ chọn trang này" : "Chọn trang này"}
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
                  Loại đã chọn ({selectedIds.length})
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
                    Loại hết "{keywordFilter}"
                  </Button>
                ) : null}
              </div>
            </div>

            {isLoadingItems ? (
              <div className="flex items-center gap-3 py-8 text-sm text-muted-foreground">
                <div className="size-4 animate-spin rounded-full border-2 border-primary/25 border-t-primary" />
                <span>Đang tải danh sách...</span>
              </div>
            ) : items.length === 0 ? (
              <EmptyCard
                title="Không còn ảnh nào"
                description="Ảnh của đợt này đã bị loại hết hoặc đã thành clip trong thư viện."
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
                        {/* minmax(0,1fr): tiêu đề truncate / select dài không được nở cột ra ngoài thẻ. */}
                        <CardContent className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2 p-3">
                          <EffectThumbnail
                            src={item.thumbPath ?? item.previewPath}
                            effect={effectById.get(item.effect)}
                            zoom={jobZoom}
                            seconds={item.duration ?? DEFAULT_PREVIEW_SECONDS}
                            alt={item.title}
                          />
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="truncate text-sm font-semibold text-foreground" title={item.title}>
                                {item.title || item.photoId}
                              </div>
                              <div className="text-xs text-muted-foreground">
                                {item.duration ? (
                                  <span className="font-medium text-foreground">{item.duration.toFixed(1)}s · </span>
                                ) : null}
                                {item.width}x{item.height} · trang {item.page}
                                {item.author ? ` · ${item.author}` : ""}
                              </div>
                            </div>
                            <div className="flex shrink-0 items-center">
                              {item.previewPath ? (
                                <a
                                  href={item.previewPath}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="rounded-md p-1 text-muted-foreground hover:text-foreground"
                                  title="Xem ảnh gốc đã tải"
                                >
                                  <ImagePlus className="size-4" />
                                </a>
                              ) : null}
                              {item.pageUrl ? (
                                <a
                                  href={item.pageUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="rounded-md p-1 text-muted-foreground hover:text-foreground"
                                  title="Mở trang gốc"
                                >
                                  <ExternalLink className="size-4" />
                                </a>
                              ) : null}
                            </div>
                          </div>
                          <select
                            value={item.effect}
                            onChange={(event) => void handleEffectChange(item, event.target.value)}
                            className="h-8 w-full min-w-0 rounded-md border border-input bg-background px-2 text-xs"
                            disabled={busy}
                            title="Hiệu ứng Ken Burns của clip này"
                          >
                            {effects.map((effect) => (
                              <option key={effect.id} value={effect.id}>
                                {effect.label}
                              </option>
                            ))}
                          </select>
                          <div className="flex flex-wrap items-center gap-1">
                            <Badge variant="secondary" className="rounded-full px-2 py-0.5 text-[10px]">
                              {item.provider}
                            </Badge>
                            <Badge variant="secondary" className="rounded-full px-2 py-0.5 text-[10px]">
                              {item.keyword}
                            </Badge>
                            {item.lastError ? (
                              <Badge variant="destructive" className="rounded-full px-2 py-0.5 text-[10px]">
                                lỗi {item.lastError} ×{item.failedAttempts ?? 1}
                              </Badge>
                            ) : null}
                          </div>
                          <div className="flex items-center gap-2">
                            <Checkbox
                              id={`image-clip-${item.itemId}`}
                              checked={selected}
                              onCheckedChange={() => toggleItem(item.itemId)}
                              disabled={busy}
                            />
                            <label
                              htmlFor={`image-clip-${item.itemId}`}
                              className="cursor-pointer text-xs text-muted-foreground"
                            >
                              Chọn để loại
                            </label>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              className="ml-auto h-7 px-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
                              onClick={() => setDeleteTarget({ scope: "ids", itemIds: [item.itemId] })}
                              disabled={busy}
                              title="Loại ảnh này"
                            >
                              <Trash2 className="size-4" />
                            </Button>
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

            {commitProgress && isCommitting ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>{commitProgress.message}</span>
                  <span className="flex items-center gap-2">
                    {commitPercent}%
                    <Button type="button" variant="ghost" size="sm" className="h-7 px-2" onClick={handleCancelCommit}>
                      <Square className="mr-1 size-3" />
                      Dừng
                    </Button>
                  </span>
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
                Mỗi ảnh còn lại thành 1 clip Ken Burns dài đúng số giây ghi trên thẻ; khi render, mọi clip (ảnh và
                video) phát đủ độ dài của chính nó. Ảnh bị loại sẽ không bao giờ được tải lại.
              </span>
              <Button type="button" onClick={handleCommit} disabled={busy || !keptTotal || !libraryId}>
                {isCommitting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Clapperboard className="mr-2 size-4" />}
                Tạo clip &amp; thêm vào thư viện ({keptTotal})
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {!job ? (
        <EmptyCard
          title="Chưa có đợt tìm ảnh nào"
          description="Nhập từ khóa và bấm Tìm & tải ảnh — ảnh được tải về máy trước, bạn duyệt xong mới tạo clip."
        />
      ) : null}

      <div className="grid min-w-0 gap-2">
        <h3 className="text-sm font-semibold text-foreground">Metadata phân trang theo từ khóa</h3>
        <StoryImageSearchCursorsPanel refreshKey={cursorsToken} disabled={isRunning} />
      </div>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !isDeleting) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Loại ảnh đã tải?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.scope === "ids"
                ? `Xoá ${deleteTarget.itemIds.length} ảnh khỏi ổ cứng và đánh dấu "đã loại" — các lần tìm sau sẽ bỏ qua chúng.`
                : deleteTarget?.scope === "keyword"
                  ? `Loại toàn bộ ảnh của từ khóa "${deleteTarget.keyword}". Các lần tìm sau sẽ bỏ qua chúng.`
                  : "Loại toàn bộ ảnh của đợt này."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>Huỷ</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={isDeleting}
              onClick={(event) => {
                event.preventDefault();
                void handleDelete();
              }}
            >
              {isDeleting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Trash2 className="mr-2 size-4" />}
              Loại
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
