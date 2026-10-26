import { ChevronDown, ChevronRight, ExternalLink, Eye, Loader2, Pause, Play, RotateCcw, Square, X } from "lucide-react";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  cancelPipelineItem,
  cancelPipelineRun,
  getPipelineEvents,
  getPipelineRun,
  listPipelineRuns,
  pausePipelineRun,
  resumePipelineRun,
  retryPipelineItem,
  retryPipelineRunItems,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PipelineEvent, PipelineItem, PipelineRun, PipelineRunSummary } from "@/types/api";

import {
  ITEM_STATUS_CLASS,
  ITEM_STATUS_LABEL,
  RUN_FINISHED,
  RUN_STATUS_LABEL,
  STAGE_LABEL,
  formatTime,
  stagesOf,
} from "./pipelineLabels";
import { PipelineItemDetail } from "./PipelineItemDetail";

const RUN_POLL_MS = 3000;
const LIST_POLL_MS = 8000;
const ACTIVE_ITEM = new Set(["pending", "running", "waiting_service"]);
const RETRYABLE_ITEM = new Set(["failed", "needs_attention", "cancelled"]);

export function PipelineRunList({
  selectedRunId,
  onSelect,
  refreshKey,
}: {
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  refreshKey: number;
}) {
  const [runs, setRuns] = useState<PipelineRunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      listPipelineRuns()
        .then((res) => {
          if (cancelled) return;
          setRuns(res.runs);
          setError(null);
          // Chưa chọn lượt nào (hoặc lượt đã nhớ không còn): mở lượt mới nhất.
          if (res.runs.length && !res.runs.some((run) => run.runId === selectedRunId)) onSelect(res.runs[0].runId);
        })
        .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : "Lỗi tải danh sách"));
    void load();
    const timer = window.setInterval(() => void load(), LIST_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshKey, selectedRunId, onSelect]);

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!runs.length) return <p className="text-sm text-muted-foreground">Chưa có lượt chạy nào.</p>;

  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {runs.map((run) => {
        const total = run.counts.total ?? 0;
        const review = run.counts.awaiting_review ?? 0;
        const attention = (run.counts.needs_attention ?? 0) + (run.counts.failed ?? 0);
        return (
          <button
            key={run.runId}
            type="button"
            onClick={() => onSelect(run.runId)}
            className={cn(
              "min-w-[14rem] rounded-xl border px-3 py-2 text-left transition-colors",
              run.runId === selectedRunId ? "border-primary bg-primary/5" : "border-border hover:bg-accent",
            )}
          >
            <div className="truncate text-sm font-medium">{run.name}</div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span>{RUN_STATUS_LABEL[run.status] ?? run.status}</span>
              <span>·</span>
              <span>
                {run.counts.done ?? 0}/{total} xong
              </span>
              {review ? <span className="font-medium text-violet-700 dark:text-violet-300">· {review} chờ duyệt</span> : null}
              {attention ? <span className="font-medium text-orange-700 dark:text-orange-300">· {attention} cần xử lý</span> : null}
            </div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">{formatTime(run.createdAt)}</div>
          </button>
        );
      })}
    </div>
  );
}

function StageDots({ item }: { item: PipelineItem }) {
  const stages = stagesOf(item);
  const current = stages.indexOf(item.stage);
  return (
    <div className="flex gap-0.5" title={stages.map((s) => STAGE_LABEL[s] ?? s).join(" → ")}>
      {stages.slice(0, -1).map((stage, index) => (
        <span
          key={stage}
          className={cn(
            "h-1.5 w-3 rounded-full",
            index < current || item.status === "done"
              ? "bg-emerald-500"
              : index === current
                ? item.status === "failed" || item.status === "needs_attention"
                  ? "bg-red-500"
                  : "bg-sky-500"
                : "bg-muted",
          )}
        />
      ))}
    </div>
  );
}

function RetryFromSelect({ item, busy, onRetry }: { item: PipelineItem; busy: boolean; onRetry: (stage: string) => void }) {
  const stages = stagesOf(item);
  const current = item.status === "done" ? stages.length - 1 : stages.indexOf(item.stage);
  const choices = stages.slice(0, Math.max(0, current + (item.status === "done" ? 0 : 1))).filter((s) => !s.startsWith("GATE_"));
  if (!choices.length) return null;
  return (
    <select
      className="h-8 rounded-md border border-input bg-background px-2 text-xs"
      value=""
      disabled={busy}
      title="Chạy lại từ một bước trước đó"
      onChange={(event) => {
        const stage = event.target.value;
        if (stage && window.confirm(`Chạy lại "${item.title}" từ bước ${STAGE_LABEL[stage] ?? stage}?`)) onRetry(stage);
      }}
    >
      <option value="">Chạy lại từ…</option>
      {choices.map((stage) => (
        <option key={stage} value={stage}>
          {STAGE_LABEL[stage] ?? stage}
        </option>
      ))}
    </select>
  );
}

function ItemRow({
  run,
  item,
  busy,
  expanded,
  onToggle,
  onRetry,
  onCancel,
  onReview,
}: {
  run: PipelineRun;
  item: PipelineItem;
  busy: boolean;
  expanded: boolean;
  onToggle: () => void;
  onRetry: (stage?: string) => void;
  onCancel: () => void;
  onReview?: () => void;
}) {
  const percent = item.progress?.percent ?? 0;
  const message = item.status === "failed" || item.status === "needs_attention" ? item.error?.message ?? item.progress?.message : item.progress?.message;
  return (
    <Fragment>
      <tr className={cn("border-t border-border/60 align-top", expanded && "bg-muted/30")}>
        <td className="px-3 py-2 text-muted-foreground">{item.index}</td>
        <td className="px-3 py-2">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            className="flex items-center gap-1 text-left font-medium hover:text-primary"
            title={expanded ? "Ẩn chi tiết" : "Xem chi tiết các bước, số ảnh, job đang chạy"}
          >
            {expanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
            {item.title}
          </button>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
              {item.type === 1 ? "Loại 1 · Ảnh" : "Loại 2 · Clip nền"}
            </Badge>
            {item.type === 1 && item.tst?.projectId ? (
              <Badge variant="outline" className="px-1.5 py-0 text-[10px]" title="Chủ đề TST (bộ skill viết prompt ảnh)">
                Chủ đề {item.tst.projectId}
              </Badge>
            ) : null}
            {item.input === "media" ? (
              <Badge variant="outline" className="px-1.5 py-0 text-[10px]" title={item.source.srtName ?? undefined}>
                Có sẵn audio + srt
              </Badge>
            ) : null}
            {item.media?.durationSeconds ? <span>{(item.media.durationSeconds / 60).toFixed(1)} phút</span> : null}
            {item.text ? <span>{item.text.chars.toLocaleString("vi-VN")} ký tự</span> : null}
          </div>
          {item.warnings?.length ? (
            <div className="mt-1 text-xs text-amber-700 dark:text-amber-300">⚠ {item.warnings.join("; ")}</div>
          ) : null}
        </td>
        <td className="px-3 py-2">
          <div className="text-sm">{STAGE_LABEL[item.stage] ?? item.stage}</div>
          <div className="mt-1">
            <StageDots item={item} />
          </div>
        </td>
        <td className="px-3 py-2">
          <span className={cn("inline-flex rounded-md border px-2 py-0.5 text-xs font-medium", ITEM_STATUS_CLASS[item.status])}>
            {ITEM_STATUS_LABEL[item.status] ?? item.status}
          </span>
          {item.waitingReason ? <div className="mt-1 text-[11px] text-muted-foreground">{item.waitingReason}</div> : null}
        </td>
        <td className="min-w-[14rem] px-3 py-2">
          {item.status === "running" && percent > 0 ? (
            <div className="mb-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-sky-500 transition-all" style={{ width: `${percent}%` }} />
            </div>
          ) : null}
          <div className={cn("text-xs", item.status === "failed" ? "text-destructive" : "text-muted-foreground")}>{message}</div>
        </td>
        <td className="px-3 py-2 text-xs">
          <div className="flex flex-col gap-0.5">
            {item.media?.audioUrl ? (
              <a className="text-primary hover:underline" href={item.media.audioUrl} target="_blank" rel="noreferrer">
                Audio
              </a>
            ) : null}
            {item.media?.srtUrl ? (
              <a className="text-primary hover:underline" href={item.media.srtUrl} target="_blank" rel="noreferrer">
                SRT
              </a>
            ) : null}
            {item.media?.videoUrl ? (
              <a className="font-medium text-primary hover:underline" href={item.media.videoUrl} target="_blank" rel="noreferrer">
                Video
              </a>
            ) : item.files.video ? (
              <span className="break-all text-muted-foreground" title={item.files.video}>
                {item.files.video}
              </span>
            ) : null}
            {item.tst?.url ? (
              <a className="inline-flex items-center gap-1 text-primary hover:underline" href={item.tst.url} target="_blank" rel="noreferrer">
                TST <ExternalLink className="h-3 w-3" />
              </a>
            ) : null}
          </div>
        </td>
        <td className="px-3 py-2">
          <div className="flex justify-end gap-1">
            {item.status === "awaiting_review" && onReview ? (
              <Button size="sm" onClick={onReview} disabled={busy}>
                <Eye className="h-4 w-4" />
                Duyệt
              </Button>
            ) : null}
            {RETRYABLE_ITEM.has(item.status) || item.status === "done" ? (
              <RetryFromSelect item={item} busy={busy} onRetry={(stage) => onRetry(stage)} />
            ) : null}
            {RETRYABLE_ITEM.has(item.status) ? (
              <Button size="sm" variant="outline" onClick={() => onRetry()} disabled={busy} title="Chạy lại bước này">
                <RotateCcw className="h-4 w-4" />
                Thử lại
              </Button>
            ) : null}
            {ACTIVE_ITEM.has(item.status) || item.status === "awaiting_review" ? (
              <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy} title="Huỷ item này">
                <X className="h-4 w-4" />
              </Button>
            ) : null}
          </div>
        </td>
      </tr>
      {expanded ? (
        <tr className="border-t border-border/40">
          <td colSpan={7} className="p-0">
            <PipelineItemDetail run={run} item={item} />
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

export function PipelineRunDetail({
  runId,
  onReview,
  onChanged,
}: {
  runId: string;
  onReview?: (run: PipelineRun, item: PipelineItem) => void;
  onChanged?: () => void;
}) {
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [showEvents, setShowEvents] = useState(false);
  const [openItemId, setOpenItemId] = useState<string | null>(null);
  const eventSeq = useRef(0);

  const load = useCallback(async () => {
    try {
      const next = await getPipelineRun(runId);
      setRun(next);
      setError(null);
      const res = await getPipelineEvents(runId, eventSeq.current);
      if (res.events.length) {
        eventSeq.current = res.next;
        setEvents((current) => [...current, ...res.events].slice(-300));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không tải được run.");
    }
  }, [runId]);

  useEffect(() => {
    eventSeq.current = 0;
    setEvents([]);
    setRun(null);
    void load();
  }, [load]);

  const finished = run ? RUN_FINISHED.has(run.status) : false;
  const needsHuman = run ? run.items.filter((i) => i.status === "awaiting_review" || i.status === "needs_attention").length : 0;
  useEffect(() => {
    const previous = document.title;
    document.title = needsHuman ? `(${needsHuman}) Cần xử lý · Tạo video tự động` : "Tạo video tự động";
    return () => {
      document.title = previous;
    };
  }, [needsHuman]);
  useEffect(() => {
    if (finished) return;
    const timer = window.setInterval(() => void load(), RUN_POLL_MS);
    return () => window.clearInterval(timer);
  }, [finished, load]);

  const act = async (fn: () => Promise<PipelineRun>) => {
    setBusy(true);
    try {
      setRun(await fn());
      setError(null);
      onChanged?.();
      void load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Thao tác lỗi.");
    } finally {
      setBusy(false);
    }
  };

  if (!run) {
    return error ? <p className="text-sm text-destructive">{error}</p> : <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />;
  }

  const counts = run.items.reduce<Record<string, number>>((acc, item) => {
    acc[item.status] = (acc[item.status] ?? 0) + 1;
    return acc;
  }, {});
  const retryable = (counts.needs_attention ?? 0) + (counts.failed ?? 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{run.name}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">{run.runId}</span>
            <span>·</span>
            <span>{RUN_STATUS_LABEL[run.status]}</span>
            <span>·</span>
            <span>
              {counts.done ?? 0}/{run.items.length} xong
            </span>
            {counts.awaiting_review ? <span className="text-violet-700 dark:text-violet-300">· {counts.awaiting_review} chờ duyệt</span> : null}
            {counts.needs_attention ? <span className="text-orange-700 dark:text-orange-300">· {counts.needs_attention} cần xử lý</span> : null}
            {counts.failed ? <span className="text-red-700 dark:text-red-300">· {counts.failed} lỗi</span> : null}
            {counts.waiting_service ? <span className="text-amber-700 dark:text-amber-300">· {counts.waiting_service} chờ dịch vụ</span> : null}
            <span>· tạo {formatTime(run.createdAt)}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {retryable ? (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              title="Chạy lại mọi mục cần xử lý / lỗi từ bước đang dừng (mục đã huỷ giữ nguyên)"
              onClick={() => {
                if (window.confirm(`Thử lại ${retryable} mục cần xử lý / lỗi từ bước đang dừng?`)) {
                  void act(() => retryPipelineRunItems(run.runId));
                }
              }}
            >
              <RotateCcw className="h-4 w-4" />
              Thử lại {retryable} mục
            </Button>
          ) : null}
          {!finished ? (
            <>
              {run.status === "paused" ? (
                <Button size="sm" variant="outline" onClick={() => void act(() => resumePipelineRun(run.runId))} disabled={busy}>
                  <Play className="h-4 w-4" />
                  Tiếp tục
                </Button>
              ) : (
                <Button size="sm" variant="outline" onClick={() => void act(() => pausePipelineRun(run.runId))} disabled={busy}>
                  <Pause className="h-4 w-4" />
                  Tạm dừng
                </Button>
              )}
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                onClick={() => {
                  if (window.confirm("Huỷ cả lượt chạy? Các job đang chạy ở app TTS/TST/render sẽ được yêu cầu dừng.")) {
                    void act(() => cancelPipelineRun(run.runId));
                  }
                }}
              >
                <Square className="h-4 w-4" />
                Huỷ
              </Button>
            </>
          ) : null}
        </div>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="overflow-x-auto rounded-2xl border border-border/70">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
            <tr>
              <th className="px-3 py-2">#</th>
              <th className="px-3 py-2">Kịch bản</th>
              <th className="px-3 py-2">Bước</th>
              <th className="px-3 py-2">Trạng thái</th>
              <th className="px-3 py-2">Tiến độ</th>
              <th className="px-3 py-2">File</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {run.items.map((item) => (
              <ItemRow
                key={item.itemId}
                run={run}
                item={item}
                busy={busy}
                expanded={openItemId === item.itemId}
                onToggle={() => setOpenItemId((current) => (current === item.itemId ? null : item.itemId))}
                onRetry={(stage) => void act(() => retryPipelineItem(run.runId, item.itemId, stage))}
                onCancel={() => {
                  if (window.confirm(`Huỷ "${item.title}"?`)) void act(() => cancelPipelineItem(run.runId, item.itemId));
                }}
                onReview={onReview ? () => onReview(run, item) : undefined}
              />
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <Button variant="ghost" size="sm" onClick={() => setShowEvents((value) => !value)}>
          {showEvents ? "Ẩn" : "Xem"} nhật ký ({events.length})
        </Button>
        {showEvents ? (
          <div className="mt-2 max-h-72 overflow-y-auto rounded-xl border border-border/70 bg-muted/30 p-3 font-mono text-xs">
            {events
              .slice()
              .reverse()
              .map((event) => (
                <div key={event.seq} className="flex gap-2 py-0.5">
                  <span className="shrink-0 text-muted-foreground">{formatTime(event.at)}</span>
                  <span className="shrink-0">{event.itemId ?? "run"}</span>
                  <span className="shrink-0 text-muted-foreground">{STAGE_LABEL[event.stage] ?? event.stage}</span>
                  <span className="shrink-0">{event.status}</span>
                  <span className="text-muted-foreground">{event.message}</span>
                </div>
              ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
