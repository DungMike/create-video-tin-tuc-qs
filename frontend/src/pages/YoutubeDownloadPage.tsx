import { Download, Loader2, Plus, Square, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { StatusAlert } from "@/components/status-alert";
import { TopNav } from "@/components/top-nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  cancelYoutubeDownloadJob,
  createYoutubeDownloadJob,
  getYoutubeDownloadJob,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  YoutubeDownloadFormat,
  YoutubeDownloadItem,
  YoutubeDownloadItemStatus,
  YoutubeDownloadJob,
} from "@/types/api";

const JOB_STORAGE_KEY = "youtubeDownload.lastJobId";
const POLL_INTERVAL_MS = 1000;

const FORMAT_OPTIONS: { value: YoutubeDownloadFormat; label: string; hint: string }[] = [
  { value: "mp3", label: "MP3", hint: "Chỉ tách audio" },
  { value: "mp4", label: "MP4", hint: "Video + tiếng" },
  { value: "both", label: "Cả hai", hint: "MP4 + MP3 (tải 1 lần)" },
];

const ITEM_STATUS_LABEL: Record<YoutubeDownloadItemStatus, string> = {
  pending: "Chờ",
  downloading: "Đang tải",
  converting: "Đang xử lý",
  done: "Xong",
  failed: "Lỗi",
  cancelled: "Đã huỷ",
};

// Backend tra phase khong dau; doi sang tieng Viet co dau cho de doc.
const PHASE_LABEL: Record<string, string> = {
  "Dang lay thong tin video...": "Đang lấy thông tin video...",
  "Dang tai video...": "Đang tải video...",
  "Dang tai audio...": "Đang tải audio...",
  "Dang ghep video + audio...": "Đang ghép video + audio...",
  "Dang tach MP3...": "Đang tách MP3...",
  "Dang tach MP3 tu MP4...": "Đang tách MP3 từ MP4...",
  "Thu lai bang client khac...": "Thử lại bằng client khác...",
};

type UrlRow = { id: number; value: string };

let nextRowId = 1;
const newRow = (value = ""): UrlRow => ({ id: nextRowId++, value });

function splitUrls(text: string) {
  return text
    .split(/\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function formatBytes(bytes: number) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function formatEta(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}p ${Math.round(seconds % 60)}s` : `${Math.round(seconds)}s`;
}

function isActive(job: YoutubeDownloadJob | null) {
  return job?.status === "queued" || job?.status === "running";
}

function readStoredJobId() {
  try {
    return localStorage.getItem(JOB_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeJobId(jobId: string | null) {
  try {
    if (jobId) localStorage.setItem(JOB_STORAGE_KEY, jobId);
    else localStorage.removeItem(JOB_STORAGE_KEY);
  } catch {
    // localStorage bi chan: chi mat kha nang tiep tuc theo doi sau khi tai lai trang.
  }
}

export function YoutubeDownloadPage() {
  const [rows, setRows] = useState<UrlRow[]>(() => [newRow()]);
  const [format, setFormat] = useState<YoutubeDownloadFormat>("both");
  const [job, setJob] = useState<YoutubeDownloadJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRefs = useRef(new Map<number, HTMLInputElement>());
  const focusRowId = useRef<number | null>(null);

  const running = isActive(job);

  // Tai lai trang giua chung: tiep tuc theo doi job gan nhat (neu server con giu).
  useEffect(() => {
    const storedJobId = readStoredJobId();
    if (!storedJobId) return;
    getYoutubeDownloadJob(storedJobId)
      .then(setJob)
      .catch(() => storeJobId(null));
  }, []);

  useEffect(() => {
    if (!job || !isActive(job)) return;
    const timer = window.setTimeout(() => {
      getYoutubeDownloadJob(job.jobId)
        .then(setJob)
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : "Không đọc được tiến độ.");
          setJob(null);
          storeJobId(null);
        });
    }, POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [job]);

  useEffect(() => {
    if (focusRowId.current === null) return;
    inputRefs.current.get(focusRowId.current)?.focus();
    focusRowId.current = null;
  }, [rows]);

  const updateRow = (id: number, value: string) =>
    setRows((current) => current.map((row) => (row.id === id ? { ...row, value } : row)));

  const removeRow = (id: number) =>
    setRows((current) => (current.length > 1 ? current.filter((row) => row.id !== id) : [newRow()]));

  const addRowAfter = useCallback((id: number | null, values: string[] = [""]) => {
    const created = values.map((value) => newRow(value));
    focusRowId.current = created[created.length - 1].id;
    setRows((current) => {
      const at = id === null ? current.length : current.findIndex((row) => row.id === id) + 1;
      return [...current.slice(0, at), ...created, ...current.slice(at)];
    });
  }, []);

  // Dan nhieu link cung luc (moi dong / cach nhau khoang trang) -> tach thanh nhieu o.
  const handlePaste = (row: UrlRow, event: ClipboardEvent<HTMLInputElement>) => {
    const urls = splitUrls(event.clipboardData.getData("text"));
    if (urls.length < 2) return;
    event.preventDefault();
    const [first, ...rest] = urls;
    updateRow(row.id, row.value.trim() ? row.value : first);
    addRowAfter(row.id, row.value.trim() ? urls : rest);
  };

  const handleKeyDown = (row: UrlRow, event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      addRowAfter(row.id);
    }
  };

  const urls = Array.from(new Set(rows.map((row) => row.value.trim()).filter(Boolean)));

  const handleSubmit = async () => {
    if (!urls.length || running) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createYoutubeDownloadJob(urls, format);
      setJob(created);
      storeJobId(created.jobId);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : "Không tạo được job tải.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async () => {
    if (!job) return;
    try {
      setJob(await cancelYoutubeDownloadJob(job.jobId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không huỷ được.");
    }
  };

  const doneCount = job?.items.filter((item) => item.status === "done").length ?? 0;

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Công cụ"
        title="Tải YouTube"
        description="Nhập một hoặc nhiều link YouTube, chọn MP3 / MP4 hoặc cả hai. Các link được tải lần lượt; file lưu trên máy chủ và có nút tải về cho từng file."
      />

      <PageSection>
        <div className="space-y-5">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label>Link video ({urls.length})</Label>
              <span className="text-xs text-muted-foreground">
                Enter để thêm ô mới · dán nhiều link một lúc sẽ tự tách thành nhiều ô
              </span>
            </div>
            <div className="space-y-2">
              {rows.map((row, index) => (
                <div key={row.id} className="flex items-center gap-2">
                  <span className="w-6 shrink-0 text-right text-sm tabular-nums text-muted-foreground">{index + 1}</span>
                  <Input
                    ref={(element) => {
                      if (element) inputRefs.current.set(row.id, element);
                      else inputRefs.current.delete(row.id);
                    }}
                    value={row.value}
                    placeholder="https://www.youtube.com/watch?v=..."
                    inputMode="url"
                    disabled={running}
                    onChange={(event) => updateRow(row.id, event.target.value)}
                    onPaste={(event) => handlePaste(row, event)}
                    onKeyDown={(event) => handleKeyDown(row, event)}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="Xoá link"
                    disabled={running}
                    onClick={() => removeRow(row.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
            <Button type="button" variant="outline" size="sm" disabled={running} onClick={() => addRowAfter(null)}>
              <Plus className="h-4 w-4" />
              Thêm link
            </Button>
          </div>

          <div className="space-y-2">
            <Label>Định dạng</Label>
            <div className="grid gap-2 sm:grid-cols-3">
              {FORMAT_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  disabled={running}
                  onClick={() => setFormat(option.value)}
                  className={cn(
                    "rounded-xl border px-4 py-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                    format === option.value
                      ? "border-primary bg-primary/10 ring-1 ring-primary"
                      : "border-border/70 bg-background/70 hover:bg-muted",
                  )}
                >
                  <div className="font-semibold">{option.label}</div>
                  <div className="text-xs text-muted-foreground">{option.hint}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" disabled={!urls.length || running || submitting} onClick={handleSubmit}>
              {submitting || running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              {running ? "Đang tải..." : urls.length ? `Tải ${urls.length} link` : "Tải"}
            </Button>
            {running ? (
              <Button type="button" variant="outline" onClick={handleCancel}>
                <Square className="h-4 w-4" />
                Huỷ
              </Button>
            ) : null}
          </div>

          {error ? <StatusAlert title="Lỗi" message={error} variant="destructive" /> : null}
        </div>
      </PageSection>

      {job ? (
        <PageSection>
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-lg font-semibold">Kết quả</h2>
                <p className="text-sm text-muted-foreground">
                  {doneCount}/{job.items.length} xong · định dạng{" "}
                  {FORMAT_OPTIONS.find((option) => option.value === job.format)?.label}
                </p>
              </div>
              <JobStatusBadge job={job} />
            </div>
            <p className="break-all text-xs text-muted-foreground">Thư mục lưu: {job.outputDir}</p>
            <div className="space-y-3">
              {job.items.map((item) => (
                <DownloadItemRow key={item.index} item={item} />
              ))}
            </div>
          </div>
        </PageSection>
      ) : null}
    </AppShell>
  );
}

function JobStatusBadge({ job }: { job: YoutubeDownloadJob }) {
  const label = {
    queued: "Đang xếp hàng",
    running: "Đang chạy",
    completed: "Hoàn tất",
    failed: "Thất bại",
    cancelled: "Đã huỷ",
  }[job.status];
  return <Badge variant={job.status === "failed" ? "destructive" : job.status === "completed" ? "default" : "secondary"}>{label}</Badge>;
}

function DownloadItemRow({ item }: { item: YoutubeDownloadItem }) {
  const busy = item.status === "downloading" || item.status === "converting";
  return (
    <div className="space-y-2 rounded-2xl border border-border/70 bg-background/70 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{item.title || item.url}</div>
          {item.title ? <div className="truncate text-xs text-muted-foreground">{item.url}</div> : null}
        </div>
        <Badge
          variant={item.status === "failed" ? "destructive" : item.status === "done" ? "default" : "outline"}
          className="shrink-0"
        >
          {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
          {ITEM_STATUS_LABEL[item.status]}
        </Badge>
      </div>

      {busy ? (
        <div className="space-y-1">
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className={cn("h-full rounded-full bg-primary transition-all", item.status === "converting" && "animate-pulse")}
              style={{ width: `${item.status === "converting" ? 100 : item.percent}%` }}
            />
          </div>
          <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
            <span>{PHASE_LABEL[item.phase] ?? item.phase}</span>
            {item.status === "downloading" ? (
              <span className="tabular-nums">
                {item.percent.toFixed(1)}%
                {item.speed ? ` · ${formatBytes(item.speed)}/s` : ""}
                {item.eta != null ? ` · còn ${formatEta(item.eta)}` : ""}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {item.error ? <p className="break-words text-sm text-destructive">{item.error}</p> : null}

      {item.files.length ? (
        <div className="flex flex-wrap gap-2">
          {item.files.map((file) => (
            <Button key={file.kind} asChild variant="secondary" size="sm">
              <a href={file.url} download={file.name}>
                <Download className="h-4 w-4" />
                {file.kind.toUpperCase()} · {formatBytes(file.size)}
              </a>
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
