import { Loader2, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, getStoryLibraryBakeJob, pauseStoryLibraryBakeJob, resumeStoryLibraryBakeJob } from "@/lib/api";
import type { StoryLibrary, StoryLibraryBakeJob } from "@/types/api";

const STOPPED = new Set(["completed", "partial", "cancelled", "failed", "paused"]);

export interface StoryLibraryResumeBakeProps {
  /** The library being viewed; only paused/partial ones render anything. */
  library: StoryLibrary | null | undefined;
  onChanged?: () => void;
  disabled?: boolean;
}

/**
 * Resume entry point for a bake that was paused and whose dialog is long gone —
 * after closing the modal, or after the server restarted. The library record
 * itself carries the job id and progress, so this needs no in-memory state.
 */
export function StoryLibraryResumeBake({ library, onChanged, disabled = false }: StoryLibraryResumeBakeProps) {
  const [job, setJob] = useState<StoryLibraryBakeJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const jobId = library?.bakeJobId;
  const resumable = library?.bakeStatus === "paused" || library?.bakeStatus === "partial";

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  // Drop stale progress when switching libraries.
  useEffect(() => {
    setJob(null);
    setError(null);
    stopPolling();
  }, [library?.id, stopPolling]);

  const poll = useCallback(
    (id: string) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const next = await getStoryLibraryBakeJob(id);
          setJob(next);
          if (STOPPED.has(next.status)) {
            stopPolling();
            onChanged?.();
          }
        } catch {
          /* transient errors tolerated */
        }
      }, 2000);
    },
    [onChanged, stopPolling],
  );

  const handleResume = async () => {
    if (!jobId) return;
    setBusy(true);
    setError(null);
    try {
      await resumeStoryLibraryBakeJob(jobId);
      poll(jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không thể tiếp tục bake.");
    } finally {
      setBusy(false);
    }
  };

  const handlePause = async () => {
    if (!jobId) return;
    try {
      await pauseStoryLibraryBakeJob(jobId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không thể tạm dừng bake.");
    }
  };

  if (!library || !jobId || (!resumable && !job)) return null;

  const running = job ? !STOPPED.has(job.status) : false;
  const total = job?.total ?? library.bakeTotal ?? library.clipCount ?? 0;
  // Clamped: a progress bar must never claim more than the whole library, whatever
  // the server reports.
  const completed = Math.min(total, Math.max(0, job?.completed ?? library.bakeCompleted ?? 0));
  const percent = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/40 bg-primary/5 px-3 py-2">
      <span className="text-xs text-muted-foreground">
        Bake {running ? "đang chạy" : "đang tạm dừng"}: <span className="font-medium text-foreground">{completed}/{total}</span> clip ({percent}%)
        {job?.failed ? ` · ${job.failed} lỗi` : ""}
      </span>
      {running ? (
        <Button type="button" size="sm" variant="outline" onClick={() => void handlePause()}>
          Tạm dừng
        </Button>
      ) : (
        <Button type="button" size="sm" onClick={() => void handleResume()} disabled={busy || disabled}>
          {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Play className="mr-2 size-4" />}
          Tiếp tục bake ({Math.max(0, total - completed)} clip)
        </Button>
      )}
      {error ? <span className="text-xs text-destructive">{error}</span> : null}
    </div>
  );
}
