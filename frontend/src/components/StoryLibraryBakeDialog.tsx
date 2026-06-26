import { Loader2, Sparkles } from "lucide-react";
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
  getCtaOverlays,
  getStoryLibraryBakeJob,
  getTVEffectStyles,
  getWaveformOverlays,
} from "@/lib/api";
import type {
  CtaOverlay,
  StoryLibrary,
  StoryLibraryBakeJob,
  TVEffectParams,
  TVEffectStyle,
  WaveformOverlay,
} from "@/types/api";

const CUSTOM_OPTION = "__custom__";
const TERMINAL = new Set(["completed", "partial", "cancelled", "failed"]);

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
  const [waveforms, setWaveforms] = useState<WaveformOverlay[]>([]);
  const [ctas, setCtas] = useState<CtaOverlay[]>([]);
  const [waveformId, setWaveformId] = useState("");
  const [ctaId, setCtaId] = useState("");
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

  // Load styles + waveform + CTA options when the dialog opens.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const [styleRes, waveRes, ctaRes] = await Promise.all([
          getTVEffectStyles(),
          getWaveformOverlays(),
          getCtaOverlays(),
        ]);
        if (cancelled) return;
        const usable = styleRes.styles.filter((s) => s.id !== "none");
        setStyles(usable);
        setCustomParams(styleRes.customParams ?? null);
        setStyleId((prev) => prev || usable[0]?.id || "");
        const waves = waveRes.overlays ?? [];
        const ctaList = (ctaRes.overlays ?? []).filter((c) => c.enabled !== false);
        setWaveforms(waves);
        setCtas(ctaList);
        setWaveformId((prev) => prev || waves.find((w) => w.isDefault)?.id || waves[0]?.id || "");
        setCtaId((prev) => prev || ctaList.find((c) => c.isDefault)?.id || ctaList[0]?.id || "");
      } catch {
        if (!cancelled) setError("Không tải được hiệu ứng / sóng âm / CTA.");
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
            if (next.status === "completed" || next.status === "partial") onBaked?.();
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
    if (!waveformId || !ctaId) {
      setError("Hãy chọn sóng âm và CTA để soạn tài nguyên.");
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
        mode: "full" as const,
        waveformId,
        ctaId,
        unitSeconds: 10,
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

  const resetAndClose = () => {
    stopPolling();
    setJob(null);
    setError(null);
    setOpen(false);
  };

  const jobRunning = job ? !TERMINAL.has(job.status) : false;

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
              Nung hiệu ứng TV + sóng âm + CTA vào clip của "{source?.name}" (ghép thành unit 10s) lưu thành
              thư viện mới. Khi render chọn thư viện này chỉ còn ghi phụ đề — nhanh nhất.
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
              <div className="grid grid-cols-2 gap-2">
                <div className="grid gap-1.5">
                  <Label htmlFor="bake-wave" className="text-xs">
                    Sóng âm
                  </Label>
                  <select
                    id="bake-wave"
                    value={waveformId}
                    disabled={busy}
                    onChange={(e) => setWaveformId(e.target.value)}
                    className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  >
                    {waveforms.length === 0 ? <option value="">(chưa có sóng âm)</option> : null}
                    {waveforms.map((w) => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                        {w.isDefault ? " (mặc định)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="bake-cta" className="text-xs">
                    CTA
                  </Label>
                  <select
                    id="bake-cta"
                    value={ctaId}
                    disabled={busy}
                    onChange={(e) => setCtaId(e.target.value)}
                    className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  >
                    {ctas.length === 0 ? <option value="">(chưa có CTA)</option> : null}
                    {ctas.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                        {c.isDefault ? " (mặc định)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
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
                {source?.clipCount ?? 0} clip → ~{Math.ceil((source?.clipCount ?? 0) / 2)} unit 10s. Sóng âm/CTA sẽ
                bị nung cố định (đổi sau phải bake lại). Tốn thời gian tương đương một lần render — nên chạy khi
                không có batch nào đang chạy.
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
              {job.status === "failed" ? (
                <p className="text-sm text-destructive">{job.error || "Bake thất bại."}</p>
              ) : null}
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
                  disabled={busy || !styleId || !waveformId || !ctaId}
                  onClick={() => void handleStart()}
                >
                  {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Sparkles className="mr-2 size-4" />}
                  Bắt đầu bake
                </Button>
              </>
            ) : jobRunning ? (
              <Button type="button" variant="outline" onClick={() => void handleCancel()}>
                Hủy bake
              </Button>
            ) : (
              <Button type="button" onClick={resetAndClose}>
                Đóng
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
