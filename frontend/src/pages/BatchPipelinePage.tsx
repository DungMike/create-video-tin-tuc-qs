import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ApiError, getBatchProgress, getDecorVideos, getVoices, startBatchPipeline } from "@/lib/api";
import type { BatchItemProgress, BatchProgressResponse, DecorVideo, VoiceRecord } from "@/types/api";

interface UrlEntry {
  docUrl: string;
  outputName: string;
  decorVideoId: string;
}

const EMPTY_ENTRY: UrlEntry = { docUrl: "", outputName: "", decorVideoId: "" };

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    pending: "Cho xu ly",
    tts_audio: "Tao audio (TTS)",
    job_setup: "Tao job",
    image_processing: "Xu ly anh",
    timeline: "Tao timeline",
    render_video: "Render video",
    render_chunks: "Render chunks",
    join_chunks: "Noi chunks",
    finalize: "Hoan thien",
    overlay: "Overlay",
    completed: "Hoan tat",
    failed: "That bai",
  };
  return labels[stage] ?? stage;
}

function statusIcon(status: string): string {
  if (status === "completed") return "✅";
  if (status === "failed") return "❌";
  if (status === "running") return "🔄";
  return "⏳";
}

export function BatchPipelinePage() {
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [defaultVoiceId, setDefaultVoiceId] = useState("");
  const [decorVideos, setDecorVideos] = useState<DecorVideo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [urlEntries, setUrlEntries] = useState<UrlEntry[]>([{ ...EMPTY_ENTRY }]);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<BatchProgressResponse | null>(null);

  // Load voices + decor on mount
  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    Promise.all([getVoices(), getDecorVideos()])
      .then(([voiceRes, decorRes]) => {
        if (cancelled) return;
        setVoices(voiceRes.voices);
        setDefaultVoiceId(voiceRes.defaultVoiceId);
        setDecorVideos(decorRes.decorVideos);
      })
      .catch((err) => {
        if (!cancelled) setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai cau hinh.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Poll batch progress
  useEffect(() => {
    if (!batchId) return;
    let cancelled = false;
    const poll = () => {
      getBatchProgress(batchId)
        .then((data) => {
          if (!cancelled) {
            setBatchProgress(data);
            if (data.status === "completed" || data.status === "failed") {
              setIsSubmitting(false);
            }
          }
        })
        .catch(() => undefined);
    };
    poll();
    const interval = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [batchId]);

  const updateEntry = (index: number, field: keyof UrlEntry, value: string) => {
    setUrlEntries((prev) => prev.map((e, i) => (i === index ? { ...e, [field]: value } : e)));
  };

  const addEntry = () => setUrlEntries((prev) => [...prev, { ...EMPTY_ENTRY }]);

  const removeEntry = (index: number) => {
    setUrlEntries((prev) => {
      if (prev.length <= 1) return prev;
      return prev.filter((_, i) => i !== index);
    });
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);

    // Client-side validation
    const outputNames = urlEntries.map((e) => e.outputName.trim());
    for (let i = 0; i < urlEntries.length; i++) {
      if (!urlEntries[i].docUrl.trim()) {
        setErrorMessage(`URL #${i + 1}: Google Docs URL la bat buoc.`);
        return;
      }
      if (!outputNames[i]) {
        setErrorMessage(`URL #${i + 1}: Ten output la bat buoc.`);
        return;
      }
    }
    const dupes = outputNames.filter((n, i) => outputNames.indexOf(n) !== i);
    if (dupes.length > 0) {
      setErrorMessage(`Ten output bi trung lap: ${dupes.join(", ")}`);
      return;
    }

    const formData = new FormData(event.currentTarget);

    // Build items JSON
    const items = urlEntries.map((e) => ({
      docUrl: e.docUrl.trim(),
      outputName: e.outputName.trim(),
      decorVideoId: e.decorVideoId || "",
    }));
    formData.set("items", JSON.stringify(items));

    // Remove individual entry fields from formData (they're in items JSON now)
    // The images and shared settings are already captured via the form's name attrs

    setIsSubmitting(true);
    setBatchProgress(null);

    try {
      const res = await startBatchPipeline(formData);
      setBatchId(res.batchId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the khoi tao batch pipeline.");
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai cau hinh..." />
      </AppShell>
    );
  }

  const completedItems = batchProgress?.items.filter((it) => it.status === "completed") ?? [];
  const overallPercent =
    batchProgress && batchProgress.totalUrls > 0
      ? Math.round(batchProgress.items.reduce((sum, it) => sum + it.percent, 0) / batchProgress.totalUrls)
      : 0;
  const batchDone = batchProgress?.status === "completed" || batchProgress?.status === "failed";

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Batch Pipeline"
        title="Tu dong tao video tu nhieu Google Docs"
        description="Nhap nhieu Google Docs URL, anh nguon chung va PiP overlay. He thong se tuan tu tao audio, xu ly anh, va render video hoan chinh cho tung URL."
        stats={[
          { label: "Mode", value: "Image + Audio" },
          { label: "Voices", value: voices.length },
          { label: "Decor Videos", value: decorVideos.length },
          { label: "Xu ly", value: "Tuan tu" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}

      {/* ---- INPUT FORM ---- */}
      {!batchId ? (
        <PageSection>
          <form className="grid gap-6" onSubmit={handleSubmit}>
            {/* Shared settings */}
            <div className="grid gap-4 md:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="images">Anh nguon (shared cho tat ca URLs)</Label>
                <Input id="images" name="images" type="file" accept=".jpg,.jpeg,.png,.webp" multiple required />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="voiceId">Voice</Label>
                <select
                  id="voiceId"
                  name="voiceId"
                  defaultValue={defaultVoiceId}
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                >
                  <option value={defaultVoiceId}>
                    {defaultVoiceId ? `Default voice (${defaultVoiceId})` : "TTS_DEFAULT_VOICE_ID"}
                  </option>
                  {voices.map((v) => (
                    <option key={v.voiceId} value={v.voiceId}>
                      {v.voiceName} - {v.voiceId}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="speed">Speed</Label>
                <Input id="speed" name="speed" type="number" step="0.1" min="0.5" max="1.5" defaultValue="1" />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="volume">Volume</Label>
                <Input id="volume" name="volume" type="number" step="0.1" min="0" defaultValue="1" />
              </div>
            </div>

            <Separator />

            {/* Per-URL entries */}
            <div className="grid gap-4">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold text-foreground">
                  Danh sach URLs ({urlEntries.length})
                </h3>
                <Button type="button" variant="outline" size="sm" onClick={addEntry}>
                  + Them URL
                </Button>
              </div>

              {urlEntries.map((entry, idx) => (
                <div
                  key={idx}
                  className="relative grid gap-3 rounded-xl border border-border/70 bg-card/50 p-4"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-muted-foreground">URL #{idx + 1}</span>
                    {urlEntries.length > 1 ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs text-destructive hover:text-destructive"
                        onClick={() => removeEntry(idx)}
                      >
                        Xoa
                      </Button>
                    ) : null}
                  </div>

                  <div className="grid gap-3 md:grid-cols-3">
                    <div className="grid gap-1.5 md:col-span-2">
                      <Label className="text-xs">Google Docs URL</Label>
                      <Input
                        placeholder="https://docs.google.com/document/d/..."
                        value={entry.docUrl}
                        onChange={(e) => updateEntry(idx, "docUrl", e.target.value)}
                        required
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label className="text-xs">Ten output (bat buoc, duy nhat)</Label>
                      <Input
                        placeholder="VD: tin-tuc-iran-1"
                        value={entry.outputName}
                        onChange={(e) => updateEntry(idx, "outputName", e.target.value)}
                        required
                      />
                    </div>
                  </div>

                  <div className="grid gap-1.5">
                    <Label className="text-xs">PiP Overlay</Label>
                    <select
                      value={entry.decorVideoId}
                      onChange={(e) => updateEntry(idx, "decorVideoId", e.target.value)}
                      className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                    >
                      <option value="">Auto (random tu thu vien)</option>
                      {decorVideos.map((dv) => (
                        <option key={dv.id} value={dv.id}>
                          {dv.name} ({dv.durationSeconds}s)
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              ))}
            </div>

            <Separator />

            <div className="flex flex-wrap items-center justify-between gap-4">
              <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
                He thong se xu ly tuan tu tung URL. Moi URL: TTS Audio → Xu ly anh (shuffle) → Render video + PiP
                overlay. URL loi se bi skip.
              </p>
              <Button type="submit" size="lg" disabled={isSubmitting}>
                {isSubmitting ? "Dang xu ly..." : `Bat dau Batch Pipeline (${urlEntries.length} URLs)`}
              </Button>
            </div>
          </form>
        </PageSection>
      ) : null}

      {/* ---- PROGRESS ---- */}
      {batchProgress ? (
        <PageSection>
          <div className="grid gap-5">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-foreground">
                  Batch: {batchProgress.batchId}
                </h3>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  Trang thai: <span className="font-medium text-foreground">{batchProgress.status}</span> |{" "}
                  Hoan tat: {batchProgress.completedUrls}/{batchProgress.totalUrls}
                </p>
              </div>
              <div className="text-2xl font-semibold text-foreground">{overallPercent}%</div>
            </div>

            {/* Overall progress bar */}
            <div className="h-3 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all duration-500"
                style={{ width: `${overallPercent}%` }}
              />
            </div>

            {/* Per-item progress */}
            <div className="grid gap-3">
              {batchProgress.items.map((item: BatchItemProgress) => (
                <ItemProgressCard key={item.index} item={item} />
              ))}
            </div>

            {batchDone ? (
              <div className="flex gap-3">
                <Button
                  variant="outline"
                  onClick={() => {
                    setBatchId(null);
                    setBatchProgress(null);
                    setUrlEntries([{ ...EMPTY_ENTRY }]);
                  }}
                >
                  Tao batch moi
                </Button>
              </div>
            ) : null}
          </div>
        </PageSection>
      ) : null}

      {/* ---- RESULTS ---- */}
      {completedItems.length > 0 ? (
        <PageSection>
          <h3 className="mb-4 text-base font-semibold text-foreground">
            Video da hoan tat ({completedItems.length})
          </h3>
          <div className="grid gap-3">
            {completedItems.map((item) => (
              <div
                key={item.index}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/70 bg-card/80 p-4"
              >
                <div>
                  <span className="font-semibold text-foreground">{item.outputName}.mp4</span>
                  {item.decorVideoName ? (
                    <span className="ml-2 text-xs text-muted-foreground">PiP: {item.decorVideoName}</span>
                  ) : null}
                </div>
                <div className="flex gap-2">
                  {item.outputVideo ? (
                    <>
                      <Button asChild variant="outline" size="sm">
                        <a href={`/media/${item.outputVideo}`} target="_blank" rel="noopener noreferrer">
                          Preview
                        </a>
                      </Button>
                      <Button asChild size="sm">
                        <a href={`/media/${item.outputVideo}`} download={`${item.outputName}.mp4`}>
                          Download
                        </a>
                      </Button>
                    </>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </PageSection>
      ) : null}
    </AppShell>
  );
}

function ItemProgressCard({ item }: { item: BatchItemProgress }) {
  const pct = Math.round(Math.max(0, Math.min(100, item.percent)));
  const barColorClass =
    item.status === "completed"
      ? "bg-green-500"
      : item.status === "failed"
        ? "bg-destructive"
        : "bg-primary";

  return (
    <div className="rounded-lg border border-border/70 bg-background/70 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-base">{statusIcon(item.status)}</span>
          <span className="truncate font-semibold text-sm text-foreground">{item.outputName}</span>
          {item.decorVideoName ? (
            <span className="hidden sm:inline text-xs text-muted-foreground">PiP: {item.decorVideoName}</span>
          ) : null}
        </div>
        <span className="text-sm font-semibold text-foreground whitespace-nowrap">{pct}%</span>
      </div>

      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-all duration-300 ${barColorClass}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-1.5 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="truncate">{item.message}</span>
        <span className="whitespace-nowrap">{stageLabel(item.stage)}</span>
      </div>

      {item.error ? (
        <div className="mt-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{item.error}</div>
      ) : null}
    </div>
  );
}

function TopNav() {
  return (
    <nav className="flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-border/70 bg-card/90 px-4 py-3 shadow-lg backdrop-blur md:px-6">
      <Link to="/" className="text-sm font-semibold tracking-tight text-foreground">
        Auto Video Review Studio
      </Link>
      <div className="flex flex-wrap items-center gap-2">
        <Button asChild variant="ghost">
          <Link to="/">Upload</Link>
        </Button>
        <Button asChild variant="ghost">
          <Link to="/docs-to-audio">Docs to Audio</Link>
        </Button>
        <Button asChild variant="secondary">
          <Link to="/batch-pipeline">Batch Pipeline</Link>
        </Button>
        <Button asChild variant="ghost">
          <Link to="/voices">Voices</Link>
        </Button>
        <Button asChild variant="ghost">
          <Link to="/decor-library">Decor Library</Link>
        </Button>
        <Button asChild variant="ghost">
          <Link to="/effects-library">Effects Library</Link>
        </Button>
      </div>
    </nav>
  );
}
