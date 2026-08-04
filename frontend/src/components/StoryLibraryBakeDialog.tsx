import { Loader2, Pause, Play, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  bakeStoryLibrary,
  cancelStoryLibraryBakeJob,
  getStoryLibraryBakeJob,
  getTVEffectStyles,
  pauseStoryLibraryBakeJob,
  resumeStoryLibraryBakeJob,
} from "@/lib/api";
import type {
  StoryLibrary,
  StoryLibraryBakeJob,
  TVEffectParams,
  TVEffectStyle,
} from "@/types/api";

const CUSTOM_OPTION = "__custom__";
// States where the job is no longer progressing, so polling stops. "paused" is
// one of them even though the job can still be resumed.
const TERMINAL = new Set(["completed", "partial", "cancelled", "failed", "paused"]);

export interface StoryLibraryBakeDialogProps {
  /** The library to bake from (usually the active one). */
  source: StoryLibrary | null | undefined;
  /** Called after a bake completes so the parent can refresh the library list. */
  onBaked?: () => void;
  disabled?: boolean;
}

export function StoryLibraryBakeDialog({ source, onBaked, disabled = false }: StoryLibraryBakeDialogProps) {
  const [open, setOpen] = useState(false);
  const [styles, setStyles] = useState<TVEffectStyle[]>([]);
  const [customParams, setCustomParams] = useState<TVEffectParams | null>(null);
  const [styleId, setStyleId] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<StoryLibraryBakeJob | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const styledSource = Boolean(source?.styled);
  const emptySource = (source?.clipCount ?? 0) <= 0;
  const triggerDisabled = disabled || !source || styledSource || emptySource;

  const selectedStyleLabel =
    styleId === CUSTOM_OPTION ? "Custom" : styles.find((s) => s.id === styleId)?.name ?? "";

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  // Load TV effect styles when the dialog opens. Waveform + CTA are NOT baked
  // here anymore — they are added at render time — so only styles are needed.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const styleRes = await getTVEffectStyles();
        if (cancelled) return;
        const usable = styleRes.styles.filter((s) => s.id !== "none");
        setStyles(usable);
        setCustomParams(styleRes.customParams ?? null);
        setStyleId((prev) => prev || usable[0]?.id || "");
      } catch {
        if (!cancelled) setError("Không tải được hiệu ứng.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  // Prefill the target name from source + chosen style.
  useEffect(() => {
    if (!open || !source || job) return;
    setName(selectedStyleLabel ? `${source.name} · ${selectedStyleLabel}` : source.name);
  }, [open, source, selectedStyleLabel, job]);

  const pollJob = useCallback(
    (jobId: string) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const next = await getStoryLibraryBakeJob(jobId);
          setJob(next);
          if (TERMINAL.has(next.status)) {
            stopPolling();
            // A paused job has already written a usable library, so the parent
            // must refresh its list just as it would for a finished one.
            if (next.status === "completed" || next.status === "partial" || next.status === "paused") {
              onBaked?.();
            }
          }
        } catch {
          /* keep polling; transient errors are tolerated */
        }
      }, 1500);
    },
    [onBaked, stopPolling],
  );

  const handleStart = async () => {
    if (!source) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Tên thư viện đích không được để trống.");
      return;
    }
    if (!styleId) {
      setError("Hãy chọn một hiệu ứng để bake.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const styleField =
        styleId === CUSTOM_OPTION ? { params: customParams ?? undefined } : { styleId };
      const payload = {
        sourceLibraryId: source.id,
        name: trimmed,
        mode: "style" as const,
        ...styleField,
      };
      const res = await bakeStoryLibrary(payload);
      setJob({
        jobId: res.jobId,
        status: "pending",
        sourceLibraryId: source.id,
        targetLibraryId: res.targetLibraryId,
        targetName: trimmed,
        styleId: styleId === CUSTOM_OPTION ? "custom" : styleId,
        styleLabel: selectedStyleLabel,
        total: source.clipCount,
        completed: 0,
        failed: 0,
        percent: 0,
        message: "Đang khởi tạo...",
        startedAt: "",
        updatedAt: "",
      });
      pollJob(res.jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không thể bắt đầu bake.");
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!job) return;
    try {
      await cancelStoryLibraryBakeJob(job.jobId);
    } catch {
      /* ignore — poll will reflect the final state */
    }
  };

  const handlePause = async () => {
    if (!job) return;
    setError(null);
    try {
      await pauseStoryLibraryBakeJob(job.jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không thể tạm dừng bake.");
    }
  };

  const handleResume = async () => {
    if (!job) return;
    setError(null);
    setBusy(true);
    try {
      await resumeStoryLibraryBakeJob(job.jobId);
      pollJob(job.jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không thể tiếp tục bake.");
    } finally {
      setBusy(false);
    }
  };

  const resetAndClose = () => {
    stopPolling();
    setJob(null);
    setError(null);
    setOpen(false);
  };

  const jobRunning = job ? !TERMINAL.has(job.status) : false;
  const jobPaused = job?.status === "paused";
  const jobResumable = jobPaused || job?.status === "partial" || job?.status === "failed";

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={triggerDisabled}
        title={
          styledSource
            ? "Thư viện này đã được style"
            : emptySource
              ? "Thư viện chưa có clip"
              : "Bake hiệu ứng vào toàn bộ clip → tạo thư viện mới"
        }
        onClick={() => {
          setError(null);
          setJob(null);
          setOpen(true);
        }}
      >
        <Sparkles className="mr-2 size-4" />
        Bake hiệu ứng
      </Button>

      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (busy || jobRunning) return;
          if (!next) resetAndClose();
          else setOpen(true);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Bake hiệu ứng → thư viện mới</DialogTitle>
            <DialogDescription>
              Nung hiệu ứng TV vào từng clip 5s của "{source?.name}" và lưu thành thư viện mới. Khi render
              chọn thư viện này sẽ bỏ qua bước style — sóng âm và CTA vẫn được thêm ở bước render.
            </DialogDescription>
          </DialogHeader>

          {!job ? (
            <div className="grid gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="bake-style" className="text-xs">
                  Hiệu ứng
                </Label>
                <select
                  id="bake-style"
                  value={styleId}
                  disabled={busy}
                  onChange={(e) => setStyleId(e.target.value)}
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                >
                  {styles.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                  {customParams ? <option value={CUSTOM_OPTION}>Custom (cấu hình đã lưu)</option> : null}
                </select>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="bake-name" className="text-xs">
                  Tên thư viện đích
                </Label>
                <Input
                  id="bake-name"
                  value={name}
                  disabled={busy}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Tên thư viện đã style"
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {source?.clipCount ?? 0} clip 5s sẽ được nung hiệu ứng và giữ nguyên số lượng. Sóng âm và CTA
                không nung ở đây — chúng được thêm khi render. Tốn thời gian tương đương một lần render — nên chạy
                khi không có batch nào đang chạy.
              </p>
              {error ? <p className="text-sm text-destructive">{error}</p> : null}
            </div>
          ) : (
            <div className="grid gap-3">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium">{job.targetName}</span>
                <span className="text-muted-foreground">
                  {job.completed}/{job.total}
                  {job.failed ? ` · ${job.failed} lỗi` : ""}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded bg-muted">
                <div
                  className="h-2 rounded bg-primary transition-all"
                  style={{ width: `${Math.min(100, Math.max(0, job.percent))}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground">{job.message}</p>
              {jobPaused ? (
                <p className="rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-xs text-muted-foreground">
                  Thư viện <span className="font-medium text-foreground">"{job.targetName}"</span> đã dùng được ngay
                  với {job.completed} clip đã bake. Bấm <span className="font-medium text-foreground">Tiếp tục</span> bất
                  cứ lúc nào để bake nốt {job.total - job.completed} clip còn lại — kể cả sau khi khởi động lại app.
                </p>
              ) : null}
              {job.status === "failed" ? (
                <p className="text-sm text-destructive">{job.error || "Bake thất bại."}</p>
              ) : null}
              {error ? <p className="text-sm text-destructive">{error}</p> : null}
            </div>
          )}

          <DialogFooter>
            {!job ? (
              <>
                <Button type="button" variant="outline" disabled={busy} onClick={resetAndClose}>
                  Hủy
                </Button>
                <Button
                  type="button"
                  disabled={busy || !styleId}
                  onClick={() => void handleStart()}
                >
                  {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Sparkles className="mr-2 size-4" />}
                  Bắt đầu bake
                </Button>
              </>
            ) : jobRunning ? (
              <>
                <Button type="button" variant="outline" onClick={() => void handleCancel()}>
                  Hủy bake
                </Button>
                <Button type="button" onClick={() => void handlePause()} disabled={job.status === "pausing"}>
                  {job.status === "pausing" ? (
                    <Loader2 className="mr-2 size-4 animate-spin" />
                  ) : (
                    <Pause className="mr-2 size-4" />
                  )}
                  {job.status === "pausing" ? "Đang dừng..." : "Tạm dừng"}
                </Button>
              </>
            ) : (
              <>
                <Button type="button" variant="outline" onClick={resetAndClose}>
                  Đóng
                </Button>
                {jobResumable ? (
                  <Button type="button" disabled={busy} onClick={() => void handleResume()}>
                    {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Play className="mr-2 size-4" />}
                    Tiếp tục ({job.total - job.completed} clip)
                  </Button>
                ) : null}
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
