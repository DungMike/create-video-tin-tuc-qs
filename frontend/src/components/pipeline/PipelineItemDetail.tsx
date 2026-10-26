import { CheckCircle2, Circle, CircleDot, ExternalLink, Loader2, RefreshCw, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { getPipelineItemDetail } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PipelineItem, PipelineItemDetail as Detail, PipelineRun, PipelineTstDetail } from "@/types/api";

import { STAGE_LABEL, formatTime, stagesOf } from "./pipelineLabels";

const DETAIL_POLL_MS = 5000;
const ACTIVE = new Set(["pending", "running", "waiting_service"]);

const JOB_LABEL: Record<string, string> = {
  split: "Chia timeline",
  ai: "AI viết bible + prompt",
  build: "Build prompts.json",
  characters: "Tạo ảnh nhân vật",
  scenes: "Tạo ảnh cảnh",
  regen: "Tạo lại ảnh",
  video: "Dựng video",
  export: "Xuất timeline",
};

type StepState = "done" | "active" | "pending" | "problem";

interface Step {
  key: string;
  label: string;
  state: StepState;
  info?: string;
  progress?: { done: number; total: number };
}

function StepIcon({ state }: { state: StepState }) {
  if (state === "done") return <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />;
  if (state === "problem") return <XCircle className="h-4 w-4 shrink-0 text-red-600" />;
  if (state === "active") return <CircleDot className="h-4 w-4 shrink-0 text-sky-600" />;
  return <Circle className="h-4 w-4 shrink-0 text-muted-foreground/50" />;
}

/** Trạng thái một bước theo vị trí của item trong luồng; bước hiện tại đỏ khi item lỗi/cần xử lý. */
function stageState(item: PipelineItem, stage: string): StepState {
  const stages = stagesOf(item);
  if (item.status === "done") return "done";
  const index = stages.indexOf(stage);
  const current = stages.indexOf(item.stage);
  if (index < current) return "done";
  if (index > current) return "pending";
  return item.status === "failed" || item.status === "needs_attention" ? "problem" : "active";
}

function countInfo(counts?: { done?: number; total?: number }) {
  return counts && counts.total ? { done: counts.done ?? 0, total: counts.total } : undefined;
}

function type1Steps(item: PipelineItem, run: PipelineRun, tst: PipelineTstDetail | null): Step[] {
  const flow = tst?.flow ?? item.tst?.flow ?? {};
  const live = flow as NonNullable<PipelineTstDetail["flow"]>;
  const approvals = item.tst?.approvals ?? {};
  const aiState = stageState(item, "AI");
  const bibleDone = Boolean(live.bible?.exists);
  const prompts = countInfo(flow.prompts);
  const promptsDone = Boolean(prompts && prompts.done >= prompts.total);
  const steps: Step[] = [
    { key: "story", label: "Chia timeline", state: stageState(item, "TST_STORY"), info: flow.segments ? `${flow.segments} đoạn ảnh` : undefined },
    {
      key: "bible",
      label: "AI: bible nhân vật",
      state: bibleDone ? "done" : aiState,
      info: live.bible?.characters ? `${live.bible.characters} nhân vật` : undefined,
    },
    {
      key: "prompts",
      label: "AI: prompt ảnh cảnh",
      state: promptsDone ? "done" : bibleDone ? aiState : aiState === "done" ? "done" : "pending",
      progress: prompts,
    },
    { key: "build", label: "Build prompts.json", state: live.built ? "done" : stageState(item, "BUILD") },
    {
      key: "gate",
      label: "Duyệt prompt",
      state: approvals.prompts ? "done" : stageState(item, "GATE_PROMPTS"),
      info: approvals.prompts
        ? `đã duyệt ${formatTime(approvals.prompts.at)}`
        : flow.characters?.total || flow.images?.total
          ? `sẽ tạo ${flow.characters?.total ?? 0} ảnh nhân vật + ${flow.images?.total ?? 0} ảnh cảnh`
          : undefined,
    },
    { key: "characters", label: "Ảnh nhân vật", state: stageState(item, "CHARACTERS"), progress: countInfo(flow.characters) },
  ];
  if (run.config.type1?.gates?.characters) {
    steps.push({
      key: "gate-characters",
      label: "Duyệt ảnh nhân vật",
      state: approvals.characters ? "done" : stageState(item, "GATE_CHARACTERS"),
    });
  }
  steps.push(
    {
      key: "scenes",
      label: "Ảnh cảnh",
      state: stageState(item, "SCENES"),
      progress: countInfo(flow.images),
      info: tst?.scenes?.errors.length ? `${tst.scenes.errors.length} ảnh lỗi` : undefined,
    },
    {
      key: "video",
      label: "Dựng video",
      state: stageState(item, "VIDEO"),
      info: tst?.video?.renders ? `${tst.video.renders} bản render` : undefined,
    },
    { key: "collect", label: "Gom video vào thư mục xuất", state: stageState(item, "COLLECT") },
  );
  return steps;
}

function genericSteps(item: PipelineItem): Step[] {
  return stagesOf(item)
    .slice(0, -1)
    .map((stage) => ({ key: stage, label: STAGE_LABEL[stage] ?? stage, state: stageState(item, stage) }));
}

function StepList({ steps, item }: { steps: Step[]; item: PipelineItem }) {
  return (
    <ol className="space-y-2">
      {steps.map((step) => {
        const percent = step.progress ? Math.min(100, (100 * step.progress.done) / step.progress.total) : 0;
        return (
          <li key={step.key} className="space-y-1">
            <div className="flex items-center gap-2 text-sm">
              <StepIcon state={step.state} />
              <span className={cn(step.state === "pending" && "text-muted-foreground", step.state === "active" && "font-medium")}>
                {step.label}
              </span>
              {step.progress ? (
                <span className="text-xs tabular-nums text-muted-foreground">
                  {step.progress.done}/{step.progress.total}
                </span>
              ) : null}
              {step.info ? <span className="text-xs text-muted-foreground">· {step.info}</span> : null}
            </div>
            {step.progress && step.state !== "pending" ? (
              <div className="ml-6 h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn("h-full transition-all", step.state === "done" ? "bg-emerald-500" : step.state === "problem" ? "bg-red-500" : "bg-sky-500")}
                  style={{ width: `${percent}%` }}
                />
              </div>
            ) : null}
            {step.state === "problem" && item.error?.message ? (
              <p className="ml-6 whitespace-pre-wrap text-xs text-destructive">{item.error.message}</p>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function JobLog({ tst, fallback }: { tst: PipelineTstDetail | null; fallback?: string[] }) {
  const job = tst?.job;
  const lines = job?.lines?.length ? job.lines : fallback ?? [];
  const box = useRef<HTMLPreElement>(null);
  const lastLine = lines[lines.length - 1];
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines.length, lastLine]);
  if (!job && !lines.length) return <p className="text-sm text-muted-foreground">Chưa có job nào trong TST Studio.</p>;
  return (
    <div className="space-y-1.5">
      {job ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {job.status === "running" ? <Loader2 className="h-4 w-4 animate-spin text-sky-600" /> : null}
          <span className="font-medium">{JOB_LABEL[job.step] ?? job.step}</span>
          <span className="text-xs text-muted-foreground">
            {job.status === "running" ? "đang chạy" : job.status === "done" ? "xong" : job.status === "error" ? `lỗi (mã ${job.exitCode ?? "?"})` : "đã dừng"}
            {job.startedAt ? ` · bắt đầu ${formatTime(job.startedAt)}` : ""}
          </span>
        </div>
      ) : null}
      <pre
        ref={box}
        className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/70 bg-muted/40 p-2 font-mono text-[11px] leading-relaxed"
      >
        {lines.join("\n")}
      </pre>
    </div>
  );
}

function Characters({ tst }: { tst: PipelineTstDetail }) {
  if (!tst.characters?.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold uppercase text-muted-foreground">Nhân vật ({tst.characters.length})</h4>
      <div className="flex flex-wrap gap-3">
        {tst.characters.map((character) => (
          <div key={character.id} className="flex w-44 items-start gap-2">
            {character.image ? (
              <a href={character.image} target="_blank" rel="noreferrer" className="shrink-0">
                <img src={character.image} alt={character.name ?? character.id} loading="lazy" className="h-20 w-[45px] rounded object-cover" />
              </a>
            ) : (
              <div className="flex h-20 w-[45px] shrink-0 items-center justify-center rounded bg-muted text-xs text-muted-foreground">
                {(character.name ?? character.id).slice(0, 2)}
              </div>
            )}
            <div className="min-w-0 text-xs">
              <div className="font-medium">{character.name ?? character.id}</div>
              {character.role ? <div className="line-clamp-3 text-muted-foreground">{character.role}</div> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RecentScenes({ tst }: { tst: PipelineTstDetail }) {
  const recent = tst.scenes?.recent ?? [];
  if (!recent.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold uppercase text-muted-foreground">
        Ảnh cảnh mới nhất ({tst.scenes?.withImage}/{tst.scenes?.total})
      </h4>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        {recent.map((scene) =>
          scene.image ? (
            <a key={scene.id} href={scene.image} target="_blank" rel="noreferrer" className="group relative block">
              <img src={scene.image} alt={`Cảnh ${scene.id}`} loading="lazy" className="aspect-video w-full rounded object-cover" />
              <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[10px] text-white">#{scene.id}</span>
            </a>
          ) : null,
        )}
      </div>
    </div>
  );
}

export function PipelineItemDetail({ run, item }: { run: PipelineRun; item: PipelineItem }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = ACTIVE.has(item.status);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDetail(await getPipelineItemDetail(run.runId, item.itemId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không tải được chi tiết.");
    } finally {
      setLoading(false);
    }
  }, [run.runId, item.itemId]);

  useEffect(() => {
    void load();
  }, [load, item.stage, item.status]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void load(), DETAIL_POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, load]);

  const tst = detail?.tst ?? null;
  const type1 = item.type === 1;
  const tstUrl = tst?.url ?? item.tst?.url;
  const steps = type1 ? type1Steps(item, run, tst) : genericSteps(item);

  return (
    <div className="space-y-4 bg-muted/20 px-4 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">
          {type1 && item.tst?.storyId ? (
            <>
              Truyện TST <span className="font-mono">{item.tst.storyId}</span>
              <span className="text-muted-foreground"> · chủ đề {item.tst.projectId}</span>
            </>
          ) : (
            <span className="text-muted-foreground">{item.source.originalName}</span>
          )}
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => void load()} disabled={loading} title="Tải lại">
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
          {tstUrl ? (
            <Button size="sm" variant="outline" asChild>
              <a href={tstUrl} target="_blank" rel="noreferrer">
                Mở trong TST Studio <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </Button>
          ) : null}
        </div>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {detail?.tstError ? <p className="text-sm text-amber-700 dark:text-amber-300">{detail.tstError}</p> : null}
      {tst?.missing ? <p className="text-sm text-destructive">TST Studio không còn truyện này.</p> : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(16rem,22rem)_1fr]">
        <div className="space-y-2">
          <h4 className="text-xs font-semibold uppercase text-muted-foreground">Các bước</h4>
          <StepList steps={steps} item={item} />
          {item.waitingReason ? <p className="text-xs text-amber-700 dark:text-amber-300">Đang chờ: {item.progress?.message}</p> : null}
        </div>
        <div className="min-w-0 space-y-2">
          <h4 className="text-xs font-semibold uppercase text-muted-foreground">{type1 ? "Job trong TST Studio" : "Nhật ký"}</h4>
          {type1 ? (
            <JobLog tst={tst} fallback={item.tst?.logTail} />
          ) : (
            <EventList events={detail?.events ?? []} />
          )}
        </div>
      </div>

      {tst ? <Characters tst={tst} /> : null}
      {tst ? <RecentScenes tst={tst} /> : null}

      {type1 && detail?.events.length ? (
        <details>
          <summary className="cursor-pointer text-xs font-semibold uppercase text-muted-foreground">Nhật ký item ({detail.events.length})</summary>
          <div className="mt-2">
            <EventList events={detail.events} />
          </div>
        </details>
      ) : null}
    </div>
  );
}

function EventList({ events }: { events: Detail["events"] }) {
  if (!events.length) return <p className="text-sm text-muted-foreground">Chưa có sự kiện.</p>;
  return (
    <div className="max-h-60 overflow-y-auto rounded-lg border border-border/70 bg-muted/30 p-2 font-mono text-[11px]">
      {events
        .slice()
        .reverse()
        .map((event) => (
          <div key={event.seq} className="flex gap-2 py-0.5">
            <span className="shrink-0 text-muted-foreground">{formatTime(event.at)}</span>
            <span className="shrink-0 text-muted-foreground">{STAGE_LABEL[event.stage] ?? event.stage}</span>
            <span className="shrink-0">{event.status}</span>
            <span className="whitespace-pre-wrap break-words">{event.message}</span>
          </div>
        ))}
    </div>
  );
}
