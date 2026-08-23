import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { TopNav } from "@/components/top-nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  cancelYoutubeDownload,
  deleteYoutubeDownloadFile,
  getYoutubeDownloadConfig,
  getYoutubeDownloadJob,
  listYoutubeDownloadFiles,
  startYoutubeDownload,
} from "@/lib/api";
import type { YoutubeDownloadFile, YoutubeDownloadItem, YoutubeDownloadJob } from "@/types/api";

const STORAGE_KEY = "youtube-download-prefs";

type Prefs = {
  outputDir: string;
  downloadVideo: boolean;
  extractAudio: boolean;
};

function loadPrefs(): Partial<Prefs> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Partial<Prefs>) : {};
  } catch {
    return {};
  }
}

function savePrefs(prefs: Prefs) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // localStorage disabled — preferences just won't persist.
  }
}

function formatSize(bytes: number): string {
  if (!bytes) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

const ITEM_STATUS_LABELS: Record<YoutubeDownloadItem["status"], string> = {
  pending: "Đang chờ",
  downloading: "Đang tải",
  extracting: "Đang tách MP3",
  completed: "Xong",
  failed: "Lỗi",
  cancelled: "Đã huỷ",
};

function itemBadgeVariant(status: YoutubeDownloadItem["status"]): "default" | "secondary" | "destructive" | "outline" {
  if (status === "completed") return "default";
  if (status === "failed") return "destructive";
  if (status === "downloading" || status === "extracting") return "secondary";
  return "outline";
}

export function YoutubeDownloadPage() {
  const [linksText, setLinksText] = useState("");
  const [outputDir, setOutputDir] = useState("");
  const [downloadVideo, setDownloadVideo] = useState(true);
  const [extractAudio, setExtractAudio] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [job, setJob] = useState<YoutubeDownloadJob | null>(null);
  const [files, setFiles] = useState<YoutubeDownloadFile[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isLoadingFiles, setIsLoadingFiles] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const isRunning = isSubmitting || job?.status === "downloading";

  const linkCount = useMemo(
    () => new Set(linksText.split("\n").map((line) => line.trim()).filter(Boolean)).size,
    [linksText],
  );

  const refreshFiles = useCallback((dir: string) => {
    setIsLoadingFiles(true);
    listYoutubeDownloadFiles(dir)
      .then((res) => setFiles(res.files))
      .catch((err) => {
        setFiles([]);
        setErrorMessage(err instanceof ApiError ? err.message : "Không đọc được thư mục lưu.");
      })
      .finally(() => setIsLoadingFiles(false));
  }, []);

  useEffect(() => {
    const prefs = loadPrefs();
    if (typeof prefs.downloadVideo === "boolean") setDownloadVideo(prefs.downloadVideo);
    if (typeof prefs.extractAudio === "boolean") setExtractAudio(prefs.extractAudio);

    if (prefs.outputDir) {
      setOutputDir(prefs.outputDir);
      refreshFiles(prefs.outputDir);
      setIsLoading(false);
      return;
    }

    getYoutubeDownloadConfig()
      .then((res) => {
        setOutputDir(res.defaultOutputDir);
        refreshFiles(res.defaultOutputDir);
      })
      .catch((err) => {
        setErrorMessage(err instanceof ApiError ? err.message : "Không tải được cấu hình thư mục.");
      })
      .finally(() => setIsLoading(false));
  }, [refreshFiles]);

  // Poll the running job until it settles, then refresh the folder listing.
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;

    const poll = () => {
      getYoutubeDownloadJob(sessionId)
        .then((data) => {
          if (cancelled) return;
          setJob(data);
          if (data.status === "completed" || data.status === "failed" || data.status === "cancelled") {
            setIsSubmitting(false);
            setIsCancelling(false);
            setSessionId(null);
            refreshFiles(data.outputDir);
          }
        })
        .catch(() => undefined);
    };

    poll();
    const interval = window.setInterval(poll, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [sessionId, refreshFiles]);

  const handleStart = async () => {
    const links = Array.from(
      new Set(linksText.split("\n").map((line) => line.trim()).filter(Boolean)),
    );
    setErrorMessage(null);
    setSuccessMessage(null);

    if (!links.length) {
      setErrorMessage("Nhập ít nhất 1 link YouTube (mỗi dòng 1 link).");
      return;
    }
    if (!downloadVideo && !extractAudio) {
      setErrorMessage("Chọn ít nhất một định dạng: MP4 hoặc MP3.");
      return;
    }

    savePrefs({ outputDir, downloadVideo, extractAudio });
    setIsSubmitting(true);
    setJob(null);
    try {
      const res = await startYoutubeDownload({ links, outputDir, downloadVideo, extractAudio });
      setSessionId(res.sessionId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Không bắt đầu được phiên tải.");
      setIsSubmitting(false);
    }
  };

  const handleCancel = async () => {
    if (!sessionId) return;
    setIsCancelling(true);
    try {
      const data = await cancelYoutubeDownload(sessionId);
      setJob(data);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Không huỷ được phiên tải.");
      setIsCancelling(false);
    }
  };

  const handleDeleteFile = async (name: string) => {
    setErrorMessage(null);
    setSuccessMessage(null);
    try {
      await deleteYoutubeDownloadFile(outputDir, name);
      setSuccessMessage(`Đã xoá ${name}.`);
      refreshFiles(outputDir);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Xoá file thất bại.");
    }
  };

  const handleCopyPath = async (name: string) => {
    const separator = outputDir.includes("\\") ? "\\" : "/";
    const fullPath = `${outputDir.replace(/[\\/]+$/, "")}${separator}${name}`;
    try {
      await navigator.clipboard.writeText(fullPath);
      setSuccessMessage(`Đã copy đường dẫn: ${fullPath}`);
    } catch {
      setSuccessMessage(fullPath);
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Đang tải trang YouTube Downloader..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="YouTube Downloader"
        title="Tải video YouTube về máy"
        description="Dán nhiều link (mỗi dòng 1 link), chọn MP4 và/hoặc MP3. Các link được tải lần lượt và lưu vào thư mục chỉ định trên máy chủ."
        stats={[
          { label: "Link trong ô nhập", value: linkCount },
          { label: "Định dạng", value: [downloadVideo ? "MP4" : null, extractAudio ? "MP3" : null].filter(Boolean).join(" + ") || "-" },
          { label: "File trong thư mục", value: files.length },
          { label: "Trạng thái", value: isRunning ? "Đang tải" : "Sẵn sàng" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Có lỗi xảy ra" message={errorMessage} variant="destructive" /> : null}
      {successMessage ? <StatusAlert title="Thành công" message={successMessage} /> : null}

      <PageSection>
        <div className="grid gap-5">
          <div className="grid gap-2">
            <Label htmlFor="links">Link YouTube (mỗi dòng 1 link)</Label>
            <Textarea
              id="links"
              rows={6}
              value={linksText}
              onChange={(event) => setLinksText(event.target.value)}
              placeholder={"https://www.youtube.com/watch?v=...\nhttps://youtu.be/..."}
              disabled={isRunning}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="outputDir">Thư mục lưu trên máy chủ</Label>
            <Input
              id="outputDir"
              value={outputDir}
              onChange={(event) => setOutputDir(event.target.value)}
              onBlur={(event) => refreshFiles(event.target.value)}
              placeholder="D:\Downloads\YouTube"
              disabled={isRunning}
            />
            <p className="text-xs text-muted-foreground">
              Đường dẫn tuyệt đối. Thư mục sẽ được tạo nếu chưa tồn tại.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-6">
            <div className="flex items-center gap-2">
              <Checkbox
                id="downloadVideo"
                checked={downloadVideo}
                onCheckedChange={(checked) => setDownloadVideo(checked === true)}
                disabled={isRunning}
              />
              <Label htmlFor="downloadVideo" className="cursor-pointer">Tải MP4 (video)</Label>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="extractAudio"
                checked={extractAudio}
                onCheckedChange={(checked) => setExtractAudio(checked === true)}
                disabled={isRunning}
              />
              <Label htmlFor="extractAudio" className="cursor-pointer">Tự động tách MP3 (audio)</Label>
            </div>
            <p className="text-xs text-muted-foreground">
              {downloadVideo && extractAudio
                ? "Tải MP4 rồi tách MP3 — giữ cả 2 file."
                : extractAudio
                  ? "Chỉ tải audio rồi convert sang MP3 (không tốn băng thông video)."
                  : "Chỉ tải file MP4."}
            </p>
          </div>

          <div className="flex justify-end gap-3">
            {isRunning && sessionId ? (
              <Button variant="destructive" onClick={handleCancel} disabled={isCancelling}>
                {isCancelling ? "Đang huỷ..." : "Huỷ"}
              </Button>
            ) : null}
            <Button size="lg" onClick={handleStart} disabled={isRunning}>
              {isRunning ? "Đang tải..." : "Bắt đầu tải"}
            </Button>
          </div>
        </div>
      </PageSection>

      {job ? (
        <PageSection>
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-foreground">Tiến độ</h2>
                <p className="text-sm text-muted-foreground">{job.message}</p>
              </div>
              <Badge variant={job.status === "failed" ? "destructive" : job.status === "completed" ? "default" : "secondary"}>
                {job.current}/{job.total} · {job.status}
              </Badge>
            </div>

            <div className="grid gap-3">
              {job.items.map((item, index) => (
                <div key={item.id} className="rounded-2xl border border-border/70 bg-background/70 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <div className="truncate text-sm font-medium text-foreground">
                        {index + 1}. {item.title || item.url}
                      </div>
                      {item.title ? <div className="truncate text-xs text-muted-foreground">{item.url}</div> : null}
                    </div>
                    <Badge variant={itemBadgeVariant(item.status)}>{ITEM_STATUS_LABELS[item.status]}</Badge>
                  </div>

                  {item.status === "downloading" || item.status === "extracting" ? (
                    <div className="mt-3 space-y-1">
                      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                        <div className="h-full bg-primary transition-all" style={{ width: `${item.percent}%` }} />
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {item.percent.toFixed(1)}%
                        {item.speed ? ` · ${item.speed}` : ""}
                        {item.eta ? ` · còn ${item.eta}` : ""}
                      </div>
                    </div>
                  ) : null}

                  {item.videoFile || item.audioFile ? (
                    <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                      {item.videoFile ? <div className="truncate">MP4: {item.videoFile}</div> : null}
                      {item.audioFile ? <div className="truncate">MP3: {item.audioFile}</div> : null}
                      {item.sizeBytes ? <div>Dung lượng: {formatSize(item.sizeBytes)}</div> : null}
                    </div>
                  ) : null}

                  {item.error ? <p className="mt-3 text-xs text-destructive">{item.error}</p> : null}
                </div>
              ))}
            </div>
          </div>
        </PageSection>
      ) : null}

      <PageSection>
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-foreground">File đã tải</h2>
              <p className="text-sm text-muted-foreground break-all">{outputDir}</p>
            </div>
            <Button variant="outline" onClick={() => refreshFiles(outputDir)} disabled={isLoadingFiles}>
              {isLoadingFiles ? "Đang tải..." : "Làm mới"}
            </Button>
          </div>

          {files.length ? (
            <div className="grid gap-2">
              {files.map((file) => (
                <div
                  key={file.name}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border/70 bg-background/70 px-4 py-3"
                >
                  <div className="min-w-0 space-y-1">
                    <div className="truncate text-sm font-medium text-foreground">{file.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {file.kind.toUpperCase()} · {formatSize(file.sizeBytes)} · {formatDate(file.modifiedAt)}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => handleCopyPath(file.name)}>
                      Copy đường dẫn
                    </Button>
                    <Button variant="destructive" size="sm" onClick={() => handleDeleteFile(file.name)}>
                      Xoá
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyCard
              title="Chưa có file nào"
              description="Thư mục này chưa có file MP4/MP3 nào. Tải một link để bắt đầu."
            />
          )}
        </div>
      </PageSection>
    </AppShell>
  );
}
