import { Loader2, Play, RefreshCw, Save, Square, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

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
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  cancelAutoSourceRun,
  getAutoSource,
  getAutoSourceKeywordPlan,
  getAutoSourceReport,
  getStoryLibraries,
  runAutoSourceCleanup,
  saveAutoSourceSettings,
  startAutoSourceRun,
} from "@/lib/api";
import type {
  AutoSourceKeywordPlan,
  AutoSourceOverview,
  AutoSourceReport,
  AutoSourceRun,
  AutoSourceSettings,
  AutoSourceStockSummary,
  StoryLibrary,
  StoryVideoProvider,
} from "@/types/api";

const PROVIDERS: StoryVideoProvider[] = ["pexels", "pixabay"];

const RUN_STATUS: Record<AutoSourceRun["status"], string> = {
  running: "Đang chạy",
  completed: "Xong",
  failed: "Lỗi",
  cancelled: "Đã hủy",
  interrupted: "Bị ngắt",
};

const STEP_LABEL: Record<string, string> = {
  report: "Kho",
  cleanup: "Dọn clip",
  jobs: "Dọn job",
  harvest: "Tải nguồn",
  commit: "Cắt clip",
  error: "Lỗi",
};

const KEYWORD_SOURCE: Record<string, string> = { cursor: "đang dở", file: "file", bank: "toàn cầu" };

const NUMBER_FIELDS: { key: keyof AutoSourceSettings; label: string; hint: string; min: number; max: number }[] = [
  { key: "runHour", label: "Giờ chạy hằng ngày", hint: "0-23, giờ máy", min: 0, max: 23 },
  { key: "keywordsPerRun", label: "Từ khóa mỗi lượt", hint: "", min: 1, max: 200 },
  { key: "maxVideosPerRun", label: "Tối đa video gốc mỗi lượt", hint: "0 = không giới hạn", min: 0, max: 20000 },
  { key: "minStockHours", label: "Chỉ tải khi kho đích dưới (giờ)", hint: "0 = lượt nào cũng tải", min: 0, max: 100000 },
  { key: "minFreeGb", label: "Ổ đĩa trống tối thiểu (GB)", hint: "", min: 0, max: 10000 },
  { key: "maxClipUses", label: "Mỗi clip dùng tối đa (video)", hint: "0 = không giới hạn", min: 0, max: 1000 },
  { key: "keepJobDays", label: "Giữ job tải tự động (ngày)", hint: "", min: 0, max: 365 },
];

const fmt = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? "-" : value.toLocaleString("vi-VN", { maximumFractionDigits: digits });

const fmtDate = (value: string | null | undefined) => (value ? new Date(value).toLocaleString("vi-VN") : "-");

/** "1" + "2" + ... gộp: dùng 1 lần, 2 lần, >= 3 lần. */
function usedBuckets(counts: Record<string, number>) {
  let one = 0;
  let two = 0;
  let more = 0;
  for (const [key, value] of Object.entries(counts)) {
    const times = Number(key);
    if (times === 1) one += value;
    else if (times === 2) two += value;
    else if (times >= 3) more += value;
  }
  return { one, two, more };
}

function errorText(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : fallback;
}

/**
 * Tự động tìm nguồn video cho thư viện clip + kiểm tra kho hằng ngày.
 * Backend: src/utils/story_auto_source.py (tắt mặc định, cần MongoDB).
 */
export function StoryAutoSourcePanel() {
  const [overview, setOverview] = useState<AutoSourceOverview | null>(null);
  const [draft, setDraft] = useState<AutoSourceSettings | null>(null);
  const [libraries, setLibraries] = useState<StoryLibrary[]>([]);
  const [report, setReport] = useState<AutoSourceReport | null>(null);
  const [plan, setPlan] = useState<AutoSourceKeywordPlan | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);

  const loadOverview = useCallback(async () => {
    try {
      const data = await getAutoSource();
      setOverview(data);
      setDraft((current) => current ?? data.settings);
      setError(null);
    } catch (err) {
      setError(errorText(err, "Không tải được trạng thái tự động tìm nguồn."));
    }
  }, []);

  const loadReport = useCallback(async (refresh: boolean) => {
    setReportLoading(true);
    try {
      const [nextReport, nextPlan] = await Promise.all([getAutoSourceReport(refresh), getAutoSourceKeywordPlan(30)]);
      setReport(nextReport);
      setPlan(nextPlan);
      setReportError(null);
    } catch (err) {
      setReportError(errorText(err, "Không lập được báo cáo kho (cần MongoDB)."));
    } finally {
      setReportLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOverview();
    void loadReport(false);
    getStoryLibraries()
      .then((response) => setLibraries(response.libraries))
      .catch(() => setLibraries([]));
  }, [loadOverview, loadReport]);

  const running = overview?.running ?? false;
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void loadOverview(), 3000);
    return () => window.clearInterval(timer);
  }, [running, loadOverview]);

  // Lượt vừa chạy xong: số kho đã đổi.
  const [wasRunning, setWasRunning] = useState(false);
  useEffect(() => {
    if (wasRunning && !running) void loadReport(true);
    setWasRunning(running);
  }, [running, wasRunning, loadReport]);

  const update = <K extends keyof AutoSourceSettings>(key: K, value: AutoSourceSettings[K]) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));

  const dirty = useMemo(
    () => Boolean(draft && overview && JSON.stringify(draft) !== JSON.stringify(overview.settings)),
    [draft, overview],
  );

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const result = await saveAutoSourceSettings(draft);
      setDraft(result.settings);
      setOverview((current) => (current ? { ...current, settings: result.settings, nextRunAt: result.nextRunAt } : current));
      setNotice("Đã lưu cài đặt.");
      setError(null);
      void loadReport(false);
    } catch (err) {
      setError(errorText(err, "Không lưu được cài đặt."));
    } finally {
      setSaving(false);
    }
  };

  const handleStart = async () => {
    setStarting(true);
    try {
      await startAutoSourceRun();
      setNotice("Đã bắt đầu một lượt.");
      await loadOverview();
    } catch (err) {
      setError(errorText(err, "Không bắt đầu được lượt chạy."));
    } finally {
      setStarting(false);
    }
  };

  const handleCancel = async () => {
    try {
      await cancelAutoSourceRun();
      await loadOverview();
    } catch (err) {
      setError(errorText(err, "Không hủy được lượt chạy."));
    }
  };

  const handleCleanup = async () => {
    setCleaning(true);
    try {
      const result = await runAutoSourceCleanup();
      setNotice(`Đã xóa ${fmt(result.deleted)} clip dùng ≥ ${result.maxUses} lần (${fmt(result.hours, 1)} giờ).`);
      setCleanupOpen(false);
      await loadReport(true);
    } catch (err) {
      setError(errorText(err, "Không dọn được clip."));
      setCleanupOpen(false);
    } finally {
      setCleaning(false);
    }
  };

  const cap = report?.maxClipUses ?? 0;
  const currentRun = overview?.currentRun ?? null;

  return (
    <div className="grid min-w-0 gap-6">
      {error ? <p className="text-sm text-amber-500">{error}</p> : null}
      {notice ? <p className="text-sm text-emerald-500">{notice}</p> : null}

      {/* 1. Kho clip */}
      <div className="grid min-w-0 gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground">Kho clip theo thư viện (cũ → mới)</h3>
          <Button type="button" variant="outline" size="sm" onClick={() => void loadReport(true)} disabled={reportLoading}>
            {reportLoading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RefreshCw className="mr-2 size-4" />}
            Tải lại
          </Button>
          {report ? (
            <span className="text-xs text-muted-foreground">
              Lập lúc {fmtDate(report.generatedAt)} ({report.elapsedSeconds}s)
            </span>
          ) : null}
        </div>
        {reportError ? <p className="text-sm text-amber-500">{reportError}</p> : null}
        {report ? <StockSummary report={report} /> : null}
        {report ? (
          <div className="max-h-[36rem] min-w-0 overflow-auto rounded-md border border-border/70">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-card text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">Thư viện</th>
                  <th className="px-3 py-2 font-medium">Tạo lúc</th>
                  <th className="px-3 py-2 text-right font-medium">Clip</th>
                  <th className="px-3 py-2 text-right font-medium" title="Số video Pexels/Pixabay gốc">Video gốc</th>
                  <th className="px-3 py-2 text-right font-medium">Chưa dùng</th>
                  <th className="px-3 py-2 text-right font-medium">Dùng 1 / 2 / ≥3</th>
                  <th className="px-3 py-2 text-right font-medium">Giờ chưa dùng</th>
                  <th className="px-3 py-2 text-right font-medium" title="Chế độ mỗi clip 1 lần, theo độ dài video trung bình">
                    ≈ Video còn
                  </th>
                  {cap > 0 ? (
                    <th className="px-3 py-2 text-right font-medium" title={`Giờ phim còn dùng được trước khi mỗi clip chạm ${cap} lần`}>
                      Giờ còn (≤{cap} lần)
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {report.libraries.map((row) => {
                  const used = usedBuckets(row.useCounts);
                  const low = row.clips > 0 && row.unused / row.clips < 0.05;
                  return (
                    <tr key={row.id} className="border-t border-border/50">
                      <td className="px-3 py-1.5">
                        {row.name}
                        {row.styled ? <span className="ml-1 text-xs text-muted-foreground">(bake)</span> : null}
                      </td>
                      <td className="px-3 py-1.5 text-xs text-muted-foreground">{fmtDate(row.createdAt)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.clips)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.sources)}</td>
                      <td className={`px-3 py-1.5 text-right tabular-nums ${low ? "text-amber-500" : ""}`}>{fmt(row.unused)}</td>
                      <td className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums">
                        {fmt(used.one)} / {fmt(used.two)} / {fmt(used.more)}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.unusedHours, 1)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.videosLeftOnce, 1)}</td>
                      {cap > 0 ? (
                        <td className="px-3 py-1.5 text-right tabular-nums">{fmt(row.remainingHoursUnderCap, 1)}</td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      {/* 2. Cài đặt */}
      {draft ? (
        <div className="grid min-w-0 gap-3">
          <h3 className="text-sm font-semibold text-foreground">Cài đặt</h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={draft.enabled} onCheckedChange={(value) => update("enabled", value === true)} />
              Chạy tự động hằng ngày
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={draft.landscapeOnly} onCheckedChange={(value) => update("landscapeOnly", value === true)} />
              Chỉ video ngang
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={draft.useKeywordFile} onCheckedChange={(value) => update("useKeywordFile", value === true)} />
              Dùng keywords/video-keywords.txt
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={draft.autoCleanup}
                disabled={draft.maxClipUses <= 0}
                onCheckedChange={(value) => update("autoCleanup", value === true)}
              />
              Lượt hằng ngày tự xóa clip chạm giới hạn
            </label>
            <div className="flex items-center gap-3 text-sm">
              <span className="text-muted-foreground">Nguồn:</span>
              {PROVIDERS.map((provider) => (
                <label key={provider} className="flex items-center gap-1.5 capitalize">
                  <Checkbox
                    checked={draft.providers.includes(provider)}
                    onCheckedChange={(value) =>
                      update(
                        "providers",
                        value === true
                          ? PROVIDERS.filter((name) => name === provider || draft.providers.includes(name))
                          : draft.providers.filter((name) => name !== provider),
                      )
                    }
                  />
                  {provider}
                </label>
              ))}
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="grid min-w-0 content-start gap-1.5">
              <Label htmlFor="auto-source-target">Thư viện đích</Label>
              <select
                id="auto-source-target"
                value={draft.targetLibraryId}
                onChange={(event) => update("targetLibraryId", event.target.value)}
                className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm"
              >
                <option value="">— Chưa chọn (chỉ báo cáo) —</option>
                {libraries.map((lib) => (
                  <option key={lib.id} value={lib.id}>
                    {lib.name}
                  </option>
                ))}
              </select>
            </div>
            {NUMBER_FIELDS.map((field) => (
              <div key={field.key} className="grid min-w-0 content-start gap-1.5">
                <Label htmlFor={`auto-source-${field.key}`}>{field.label}</Label>
                <Input
                  id={`auto-source-${field.key}`}
                  type="number"
                  min={field.min}
                  max={field.max}
                  value={String(draft[field.key])}
                  onChange={(event) => update(field.key, Number(event.target.value || 0) as never)}
                  className="h-9"
                />
                {field.hint ? <span className="text-xs text-muted-foreground">{field.hint}</span> : null}
              </div>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" onClick={() => void handleSave()} disabled={!dirty || saving || draft.providers.length === 0}>
              {saving ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
              Lưu cài đặt
            </Button>
            {dirty ? <span className="text-xs text-amber-500">Có thay đổi chưa lưu.</span> : null}
          </div>
        </div>
      ) : null}

      {/* 3. Chạy + lịch sử */}
      <div className="grid min-w-0 gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground">Lượt chạy</h3>
          {running ? (
            <Button type="button" variant="outline" size="sm" onClick={() => void handleCancel()}>
              <Square className="mr-2 size-4" />
              Hủy lượt đang chạy
            </Button>
          ) : (
            <Button type="button" size="sm" onClick={() => void handleStart()} disabled={starting || !overview}>
              {starting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Play className="mr-2 size-4" />}
              Chạy ngay
            </Button>
          )}
          <span className="text-xs text-muted-foreground">
            {overview?.settings.enabled
              ? `Lượt tự động kế tiếp: ${overview.nextRunAt ? new Date(overview.nextRunAt).toLocaleString("vi-VN") : "-"}`
              : "Chưa bật chạy tự động hằng ngày."}
          </span>
        </div>
        {currentRun ? <RunSteps run={currentRun} /> : null}
        {overview?.runs.length ? (
          <div className="max-h-72 min-w-0 overflow-auto rounded-md border border-border/70">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-card text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">Bắt đầu</th>
                  <th className="px-3 py-2 font-medium">Kiểu</th>
                  <th className="px-3 py-2 font-medium">Trạng thái</th>
                  <th className="px-3 py-2 text-right font-medium">Kho chưa dùng (giờ)</th>
                  <th className="px-3 py-2 text-right font-medium" title="Video gốc tải mới / bỏ qua vì đã có">Tải mới / trùng</th>
                  <th className="px-3 py-2 text-right font-medium">Clip thêm</th>
                  <th className="px-3 py-2 font-medium">Ghi chú</th>
                </tr>
              </thead>
              <tbody>
                {overview.runs.map((run) => (
                  <tr key={run.runId} className="border-t border-border/50 align-top">
                    <td className="px-3 py-1.5 text-xs">{fmtDate(run.startedAt)}</td>
                    <td className="px-3 py-1.5 text-xs">{run.trigger === "schedule" ? "Hằng ngày" : "Tay"}</td>
                    <td className="px-3 py-1.5">
                      <Badge variant={run.status === "failed" ? "destructive" : "secondary"} className="rounded-full">
                        {RUN_STATUS[run.status] ?? run.status}
                      </Badge>
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmt(run.stock?.overall.unusedHours, 1)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {run.harvest ? `${fmt(run.harvest.downloaded)} / ${fmt(run.harvest.skippedKnown)}` : "-"}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmt(run.commit?.added)}</td>
                    <td className="px-3 py-1.5 text-xs text-muted-foreground">{run.error ?? run.message ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Chưa có lượt nào.</p>
        )}
      </div>

      {/* 4. Dọn clip */}
      <div className="grid min-w-0 gap-2">
        <h3 className="text-sm font-semibold text-foreground">Dọn clip đã dùng đủ lượt</h3>
        {cap <= 0 ? (
          <p className="text-sm text-muted-foreground">
            Chưa đặt "Mỗi clip dùng tối đa" nên render chế độ "mỗi clip 1 lần" không giới hạn và không có gì để dọn.
          </p>
        ) : report?.cleanup ? (
          <>
            <p className="text-sm text-muted-foreground">
              {report.cleanup.totalClips
                ? `${fmt(report.cleanup.totalClips)} clip đã dùng ≥ ${cap} video (${fmt(report.cleanup.totalHours, 1)} giờ) sẽ bị xóa khỏi thư viện. Video gốc vẫn được ghi nhớ nên không bị tải lại.`
                : `Chưa có clip nào dùng ≥ ${cap} video.`}
            </p>
            {report.cleanup.libraries.length ? (
              <ul className="text-sm">
                {report.cleanup.libraries.map((lib) => (
                  <li key={lib.id}>
                    {lib.name}: {fmt(lib.clips)} clip, còn lại {fmt(lib.remaining)}
                    {lib.emptyAfter ? <span className="ml-1 text-amber-500">(thư viện sẽ trống)</span> : null}
                  </li>
                ))}
              </ul>
            ) : null}
            <div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setCleanupOpen(true)}
                disabled={!report.cleanup.totalClips || cleaning}
              >
                <Trash2 className="mr-2 size-4" />
                Dọn ngay
              </Button>
            </div>
          </>
        ) : null}
      </div>

      {/* 5. Từ khóa lượt tới */}
      {plan ? (
        <div className="grid min-w-0 gap-2">
          <h3 className="text-sm font-semibold text-foreground">Từ khóa lượt tới</h3>
          <p className="text-xs text-muted-foreground">
            Còn {fmt(plan.available.file)} từ chưa dùng trong file và {fmt(plan.available.bank)}/{fmt(plan.bankSize)} từ trong bộ
            từ khóa toàn cầu (thiên nhiên, biển, núi, nông thôn, phố xá × quốc gia). Từ đã quét hết hoặc đã dùng bị bỏ qua;
            từ đang dở tải tiếp từ trang kế.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {plan.keywords.slice(0, draft?.keywordsPerRun ?? 15).map((item) => (
              <Badge key={item.keyword} variant="outline" className="gap-1 rounded-full font-normal">
                {item.keyword}
                <span className="text-muted-foreground">
                  · {KEYWORD_SOURCE[item.source] ?? item.source}
                  {Object.entries(item.providers)
                    .filter(([, state]) => state.state === "resume")
                    .map(([provider, state]) => ` · ${provider} trang ${state.page}`)
                    .join("")}
                </span>
              </Badge>
            ))}
          </div>
        </div>
      ) : null}

      <AlertDialog
        open={cleanupOpen}
        onOpenChange={(open) => {
          if (!open && !cleaning) setCleanupOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Xóa clip đã dùng ≥ {cap} video?</AlertDialogTitle>
            <AlertDialogDescription>
              {`Xóa ${fmt(report?.cleanup?.totalClips)} clip (${fmt(report?.cleanup?.totalHours, 1)} giờ) khỏi thư viện và ổ đĩa. Không thể hoàn tác. Hệ thống từ chối khi đang có batch render chạy.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={cleaning}>Hủy</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={cleaning}
              onClick={(event) => {
                event.preventDefault();
                void handleCleanup();
              }}
            >
              {cleaning ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Trash2 className="mr-2 size-4" />}
              Xóa
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function StockSummary({ report }: { report: AutoSourceReport }) {
  const overall: AutoSourceStockSummary = report.overall;
  const used = usedBuckets(overall.useCounts);
  const items: [string, string][] = [
    ["Clip (không trùng)", fmt(overall.uniqueClips)],
    ["Chưa dùng", `${fmt(overall.unused)} clip · ${fmt(overall.unusedHours, 1)} giờ`],
    ["Đã dùng 1 / 2 / ≥3", `${fmt(used.one)} / ${fmt(used.two)} / ${fmt(used.more)}`],
    ["≈ Video còn (1 lần)", fmt(overall.videosLeftOnce, 0)],
    ["≈ Ngày còn", fmt(overall.daysLeftOnce, 1)],
    ["Tốc độ dùng", `${fmt(report.usage.videosPerDay, 1)} video/ngày · TB ${fmt(report.usage.avgVideoMinutes, 1)} phút`],
    ["Ổ trống (nguồn / thư viện)", `${fmt(report.disk.rawFreeGb)} / ${fmt(report.disk.libraryFreeGb)} GB`],
  ];
  if (report.maxClipUses > 0) {
    items.push([`Giờ còn (≤${report.maxClipUses} lần)`, fmt(overall.remainingHoursUnderCap, 1)]);
  }
  if (report.target) {
    items.push(["Kho thư viện đích chưa dùng", `${fmt(report.target.unusedHours, 1)} giờ`]);
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {items.map(([label, value]) => (
        <div key={label} className="rounded-md border border-border/70 px-3 py-2">
          <div className="text-xs text-muted-foreground">{label}</div>
          <div className="text-sm font-medium tabular-nums">{value}</div>
        </div>
      ))}
    </div>
  );
}

function RunSteps({ run }: { run: AutoSourceRun }) {
  return (
    <div className="rounded-md border border-border/70 px-3 py-2 text-sm">
      <div className="mb-1 flex items-center gap-2">
        <Loader2 className="size-4 animate-spin text-primary" />
        <span className="font-medium">Đang chạy</span>
        <span className="text-xs text-muted-foreground">từ {fmtDate(run.startedAt)}</span>
      </div>
      <ul className="grid gap-0.5 text-xs">
        {run.steps.map((step, index) => (
          <li key={`${step.step}-${index}`}>
            <span className="font-medium">{STEP_LABEL[step.step] ?? step.step}:</span> {step.message}
          </li>
        ))}
      </ul>
    </div>
  );
}
