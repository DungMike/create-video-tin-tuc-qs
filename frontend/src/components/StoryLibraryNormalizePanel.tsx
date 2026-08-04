import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Wand2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  cancelStoryLibraryNormalizeJob,
  getStoryLibraryNormalizeJob,
  scanStoryLibraryNormalize,
  startStoryLibraryNormalize,
} from "@/lib/api";
import type { StoryLibraryNormalizeJob, StoryLibraryNormalizeScan } from "@/types/api";

const TERMINAL = new Set(["completed", "partial", "cancelled", "failed"]);

export interface StoryLibraryNormalizePanelProps {
  /** Refresh the clip list after clips were re-encoded in place. */
  onNormalized?: () => void;
}

/**
 * Finds and repairs library clips whose encoded format drifted from the one the
 * render accepts (resolution / pix_fmt / color range / color space). Off-spec
 * clips aren't broken files — they are silently dropped from every render's
 * selection pool, which is why the panel leads with the count.
 */
export function StoryLibraryNormalizePanel({ onNormalized }: StoryLibraryNormalizePanelProps) {
  const [scan, setScan] = useState<StoryLibraryNormalizeScan | null>(null);
  const [isScanning, setIsScanning] = useState(true);
  const [job, setJob] = useState<StoryLibraryNormalizeJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const runScan = useCallback(async () => {
    setIsScanning(true);
    setError(null);
    try {
      setScan(await scanStoryLibraryNormalize());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the quet thu vien clip.");
    } finally {
      setIsScanning(false);
    }
  }, []);

  useEffect(() => {
    void runScan();
  }, [runScan]);

  const pollJob = useCallback(
    (jobId: string) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const next = await getStoryLibraryNormalizeJob(jobId);
          setJob(next);
          if (TERMINAL.has(next.status)) {
            stopPolling();
            setBusy(false);
            onNormalized?.();
            void runScan();
          }
        } catch {
          /* transient errors are tolerated; keep polling */
        }
      }, 2000);
    },
    [onNormalized, runScan, stopPolling],
  );

  const handleStart = async (libraryId?: string) => {
    setBusy(true);
    setError(null);
    setJob(null);
    try {
      const res = await startStoryLibraryNormalize(libraryId ? { libraryId } : { scope: "all" });
      if (res.total === 0) {
        setBusy(false);
        await runScan();
        return;
      }
      pollJob(res.jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the bat dau chuan hoa.");
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!job) return;
    try {
      await cancelStoryLibraryNormalizeJob(job.jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the huy tac vu chuan hoa.");
    }
  };

  const mismatched = scan?.mismatchedClips ?? 0;
  const running = Boolean(job && !TERMINAL.has(job.status));

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Wand2 className="size-4 text-primary" />
            Chuan hoa dinh dang clip
          </h3>
          <p className="max-w-3xl text-xs text-muted-foreground">
            Clip co dinh dang lech chuan (do nguon Pixabay/Pexels tag mau khac nhau) bi loai khoi moi lan render — day
            la dong canh bao "Excluded ... mismatched resolution/pix_fmt/color tags" trong log. Nut duoi se encode lai
            nhung clip do ve dung mot chuan
            {scan ? <span className="font-medium text-foreground"> {scan.expected}</span> : null}. Do dai clip giu
            nguyen. Nen chay khi khong co batch nao dang render.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => void runScan()} disabled={isScanning || running}>
            {isScanning ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RefreshCw className="mr-2 size-4" />}
            Quet lai
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => void handleStart()}
            disabled={busy || running || isScanning || mismatched === 0}
          >
            {busy || running ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Wand2 className="mr-2 size-4" />}
            Chuan hoa tat ca ({mismatched})
          </Button>
        </div>
      </div>

      {error ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      {job ? (
        <div className="space-y-2 rounded-lg border border-border/70 bg-background/70 p-3">
          <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>{job.message}</span>
            <div className="flex items-center gap-2">
              <span>{job.percent}%</span>
              {running ? (
                <Button type="button" variant="ghost" size="sm" className="h-6 px-2" onClick={() => void handleCancel()}>
                  <X className="mr-1 size-3" />
                  Huy
                </Button>
              ) : null}
            </div>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${job.percent}%` }} />
          </div>
          <div className="text-xs text-muted-foreground">
            {job.completed}/{job.total} clip da xu ly{job.failed ? ` · ${job.failed} loi` : ""}
          </div>
        </div>
      ) : null}

      {isScanning && !scan ? (
        <div className="flex items-center gap-3 py-4 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          <span>Dang quet dinh dang clip...</span>
        </div>
      ) : scan ? (
        <div className="grid gap-2">
          {scan.libraries.map((library) => (
            <div
              key={library.libraryId}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/70 bg-background/70 px-3 py-2"
            >
              <div className="min-w-0 space-y-1">
                <div className="flex items-center gap-2">
                  {library.mismatchedClips === 0 ? (
                    <CheckCircle2 className="size-4 shrink-0 text-emerald-500" />
                  ) : (
                    <AlertTriangle className="size-4 shrink-0 text-amber-500" />
                  )}
                  <span className="truncate text-sm font-medium text-foreground">{library.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {library.mismatchedClips}/{library.totalClips} clip lech chuan
                  </span>
                </div>
                {library.combos.length ? (
                  <div className="flex flex-wrap gap-1">
                    {library.combos.map((combo) => (
                      <Badge key={combo.spec} variant="secondary" className="rounded-full px-2 py-0.5 text-[10px]">
                        {combo.spec} × {combo.count}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void handleStart(library.libraryId)}
                disabled={busy || running || library.mismatchedClips === 0}
              >
                Chuan hoa thu vien nay
              </Button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
