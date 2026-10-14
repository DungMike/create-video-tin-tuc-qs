import { Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { getPipelineHealth } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PipelineHealth } from "@/types/api";

const POLL_MS = 15000;

type Tone = "ok" | "warn" | "bad" | "idle";

const DOT_CLASS: Record<Tone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  bad: "bg-red-500",
  idle: "bg-muted-foreground/40",
};

function ServiceChip({ tone, label, detail }: { tone: Tone; label: string; detail?: string }) {
  return (
    <div className="flex min-w-0 items-start gap-2 rounded-xl border border-border/70 bg-background/70 px-3 py-2" title={detail}>
      <span className={cn("mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full", DOT_CLASS[tone])} />
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {detail ? <div className="truncate text-xs text-muted-foreground">{detail}</div> : null}
      </div>
    </div>
  );
}

export function PipelineHealthPanel({ onChange }: { onChange?: (health: PipelineHealth | null) => void }) {
  const [health, setHealth] = useState<PipelineHealth | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await getPipelineHealth();
      setHealth(next);
      setError(null);
      onChange?.(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không đọc được trạng thái dịch vụ.");
      onChange?.(null);
    } finally {
      setLoading(false);
    }
  }, [onChange]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const s = health?.services;
  const dgtTone: Tone = !s ? "idle" : !s.dgt.ok ? "bad" : s.dgt.ready || s.dgt.reason === "desktop_busy" ? "ok" : "warn";
  const dgtDetail = !s
    ? ""
    : !s.dgt.ok
      ? "API :8787 không chạy (scripts\\Start-Api.ps1)"
      : s.dgt.ready
        ? `Sẵn sàng · v${s.dgt.version ?? "?"}`
        : s.dgt.reason === "desktop_busy"
          ? "Đang tạo giọng (bận)"
          : `Chưa sẵn sàng: ${s.dgt.reason ?? "?"}`;
  const tstTone: Tone = !s ? "idle" : !s.tstStudio.ok ? "bad" : !s.tst.online ? "warn" : s.tst.loggedIn ? "ok" : "warn";
  const tstDetail = !s
    ? ""
    : !s.tstStudio.ok
      ? "TST Studio :5180 không chạy"
      : !s.tst.online
        ? "App TST chưa mở (chỉ cần cho loại 1)"
        : s.tst.loggedIn
          ? "App TST đã đăng nhập"
          : "App TST chưa đăng nhập license";
  const claudeTone: Tone = !s ? "idle" : s.claude.found ? "ok" : s.claude.found === false ? "bad" : "idle";
  const claudeDetail = !s
    ? ""
    : s.claude.found
      ? `Claude CLI ${s.claude.version ?? ""}`.trim()
      : s.claude.error ?? "Không tìm thấy claude (chỉ cần cho loại 1)";
  const queueDetail = !s ? "" : s.svQueue.activeBatchId ? `Đang render · ${s.svQueue.queued} batch chờ` : `Rảnh · ${s.svQueue.queued} batch chờ`;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Dịch vụ</h2>
        <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Làm mới
        </Button>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <ServiceChip tone={dgtTone} label="App TTS (DGT)" detail={dgtDetail} />
        <ServiceChip tone={tstTone} label="TST Studio" detail={tstDetail} />
        <ServiceChip tone={claudeTone} label="AI (Claude CLI)" detail={claudeDetail} />
        <ServiceChip tone={s ? "ok" : "idle"} label="Hàng đợi render" detail={queueDetail} />
      </div>
      {health?.warnings.length ? (
        <ul className="space-y-1 text-sm text-amber-700 dark:text-amber-300">
          {health.warnings.map((warning) => (
            <li key={warning}>⚠ {warning}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
