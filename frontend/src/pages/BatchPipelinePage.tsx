import { useEffect, useState } from "react";
import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { TopNav } from "@/components/top-nav";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ApiError, getBatchProgress, getDecorVideos, getVoices, retryFailedBatch, startBatchPipeline } from "@/lib/api";
import type { BatchInputMode, BatchItemProgress, BatchProgressResponse, DecorVideo, VoiceRecord } from "@/types/api";

interface DocEntry {
  docUrl: string;
  outputName: string;
  decorVideoId: string;
}

interface AudioEntry {
  outputName: string;
  decorVideoId: string;
  sourceAudioName: string;
  audioFileIndex: number;
}

const EMPTY_DOC_ENTRY: DocEntry = { docUrl: "", outputName: "", decorVideoId: "" };

function outputNameFromFileName(filename: string): string {
  return filename.replace(/\.[^/.]+$/, "").trim() || "audio-item";
}

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    pending: "Cho xu ly",
    audio_source: "Audio nguon",
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
  const [inputMode, setInputMode] = useState<BatchInputMode>("docs");
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [defaultVoiceId, setDefaultVoiceId] = useState("");
  const [decorVideos, setDecorVideos] = useState<DecorVideo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [docEntries, setDocEntries] = useState<DocEntry[]>([{ ...EMPTY_DOC_ENTRY }]);
  const [audioEntries, setAudioEntries] = useState<AudioEntry[]>([]);
  const [audioFiles, setAudioFiles] = useState<File[]>([]);
  const [audioInputKey, setAudioInputKey] = useState(0);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<BatchProgressResponse | null>(null);
  const [isRetrying, setIsRetrying] = useState(false);

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
              setIsRetrying(false);
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

  const updateDocEntry = (index: number, field: keyof DocEntry, value: string) => {
    setDocEntries((prev) => prev.map((entry, entryIndex) => (entryIndex === index ? { ...entry, [field]: value } : entry)));
  };

  const updateAudioEntry = (index: number, field: "outputName" | "decorVideoId", value: string) => {
    setAudioEntries((prev) =>
      prev.map((entry, entryIndex) => (entryIndex === index ? { ...entry, [field]: value } : entry))
    );
  };

  const addDocEntry = () => setDocEntries((prev) => [...prev, { ...EMPTY_DOC_ENTRY }]);

  const removeDocEntry = (index: number) => {
    setDocEntries((prev) => {
      if (prev.length <= 1) return prev;
      return prev.filter((_, entryIndex) => entryIndex !== index);
    });
  };

  const handleAudioFilesChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const nextFiles = Array.from(event.currentTarget.files ?? []);
    setAudioFiles(nextFiles);
    setAudioEntries((prev) => {
      const previousByName = new Map(prev.map((entry) => [entry.sourceAudioName, entry]));
      return nextFiles.map((file, index) => {
        const previous = previousByName.get(file.name);
        return {
          outputName: previous?.outputName || outputNameFromFileName(file.name),
          decorVideoId: previous?.decorVideoId || "",
          sourceAudioName: file.name,
          audioFileIndex: index,
        };
      });
    });
  };

  const handleInputModeChange = (nextMode: BatchInputMode) => {
    setInputMode(nextMode);
    setErrorMessage(null);
    if (nextMode === "docs") {
      setAudioEntries([]);
      setAudioFiles([]);
      setAudioInputKey((prev) => prev + 1);
      if (docEntries.length === 0) {
        setDocEntries([{ ...EMPTY_DOC_ENTRY }]);
      }
    }
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);

    const activeEntries = inputMode === "docs" ? docEntries : audioEntries;
    const outputNames = activeEntries.map((entry) => entry.outputName.trim());

    if (inputMode === "docs") {
      for (let index = 0; index < docEntries.length; index += 1) {
        if (!docEntries[index].docUrl.trim()) {
          setErrorMessage(`Doc #${index + 1}: Google Docs URL la bat buoc.`);
          return;
        }
        if (!outputNames[index]) {
          setErrorMessage(`Doc #${index + 1}: Ten output la bat buoc.`);
          return;
        }
      }
    } else {
      if (!audioFiles.length) {
        setErrorMessage("Can chon it nhat 1 file audio.");
        return;
      }
      if (audioEntries.length !== audioFiles.length) {
        setErrorMessage("So item audio khong khop voi so file audio da chon.");
        return;
      }
      for (let index = 0; index < audioEntries.length; index += 1) {
        if (!outputNames[index]) {
          setErrorMessage(`Audio #${index + 1}: Ten output la bat buoc.`);
          return;
        }
      }
    }

    const duplicateNames = outputNames.filter((name, index) => name && outputNames.indexOf(name) !== index);
    if (duplicateNames.length > 0) {
      setErrorMessage(`Ten output bi trung lap: ${duplicateNames.join(", ")}`);
      return;
    }

    const formData = new FormData(event.currentTarget);
    formData.set("inputMode", inputMode);
    if (inputMode === "docs") {
      formData.delete("audioFiles");
    } else {
      formData.delete("voiceId");
      formData.delete("speed");
      formData.delete("volume");
    }

    const items =
      inputMode === "docs"
        ? docEntries.map((entry) => ({
            sourceType: "doc_url" as const,
            docUrl: entry.docUrl.trim(),
            outputName: entry.outputName.trim(),
            decorVideoId: entry.decorVideoId || "",
          }))
        : audioEntries.map((entry) => ({
            sourceType: "uploaded_audio" as const,
            outputName: entry.outputName.trim(),
            decorVideoId: entry.decorVideoId || "",
            audioFileIndex: entry.audioFileIndex,
            sourceAudioName: entry.sourceAudioName,
          }));

    formData.set("items", JSON.stringify(items));

    setIsSubmitting(true);
    setBatchProgress(null);

    try {
      const response = await startBatchPipeline(formData);
      setBatchId(response.batchId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the khoi tao batch pipeline.");
      setIsSubmitting(false);
    }
  };

  const handleRetryFailed = async () => {
    if (!batchId || !batchProgress?.canRetryFailed) return;
    const confirmed = window.confirm(
      `Retry all failed cho ${batchProgress.failedUrls} item?\n\nHe thong se reuse audio/chunk da co khi hop le de tiet kiem credit TTS.`
    );
    if (!confirmed) return;

    setErrorMessage(null);
    setIsRetrying(true);
    try {
      await retryFailedBatch(batchId);
      setBatchProgress((prev) =>
        prev
          ? {
              ...prev,
              status: "running",
              canRetryFailed: false,
              items: prev.items.map((item) =>
                item.status === "failed"
                  ? {
                      ...item,
                      status: "pending",
                      stage: "pending",
                      percent: 0,
                      message: "Cho retry...",
                      error: null,
                      retryable: false,
                      failureCode: null,
                      failureStage: null,
                    }
                  : item
              ),
            }
          : prev
      );
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the retry cac item failed.");
      setIsRetrying(false);
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai cau hinh..." />
      </AppShell>
    );
  }

  const completedItems = batchProgress?.items.filter((item) => item.status === "completed") ?? [];
  const overallPercent =
    batchProgress && batchProgress.totalUrls > 0
      ? Math.round(batchProgress.items.reduce((sum, item) => sum + item.percent, 0) / batchProgress.totalUrls)
      : 0;
  const batchDone = batchProgress?.status === "completed" || batchProgress?.status === "failed";
  const currentMode = batchProgress?.inputMode ?? inputMode;
  const listLabel = currentMode === "audio_upload" ? "Danh sach Audio" : "Danh sach Docs";
  const countLabel = currentMode === "audio_upload" ? "audio files" : "docs";

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Batch Pipeline"
        title="Tu dong tao video tu Google Docs hoac nhieu file audio"
        description="Chon mot mode cho toan batch. He thong se tai audio tu Google Docs hoac dung file audio upload, sau do xu ly anh va render video cho tung item."
        stats={[
          { label: "Mode", value: currentMode === "audio_upload" ? "Audio Upload" : "Google Docs" },
          { label: "Voices", value: voices.length },
          { label: "Decor Videos", value: decorVideos.length },
          { label: "Xu ly", value: "Tuan tu" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}

      {!batchId ? (
        <PageSection>
          <form className="grid gap-6" onSubmit={handleSubmit}>
            <div className="grid gap-2">
              <Label>Input mode</Label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant={inputMode === "docs" ? "default" : "outline"}
                  onClick={() => handleInputModeChange("docs")}
                >
                  Google Docs
                </Button>
                <Button
                  type="button"
                  variant={inputMode === "audio_upload" ? "default" : "outline"}
                  onClick={() => handleInputModeChange("audio_upload")}
                >
                  Upload Audio
                </Button>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="images">Anh nguon (shared cho tat ca items)</Label>
                <Input id="images" name="images" type="file" accept=".jpg,.jpeg,.png,.webp" multiple required />
              </div>
              {inputMode === "docs" ? (
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
                    {voices.map((voice) => (
                      <option key={voice.voiceId} value={voice.voiceId}>
                        {voice.voiceName} - {voice.voiceId}
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <div className="grid gap-2">
                  <Label htmlFor="audioFiles">File audio (multiple)</Label>
                  <Input
                    key={audioInputKey}
                    id="audioFiles"
                    name="audioFiles"
                    type="file"
                    accept=".mp3,.wav,.m4a,.aac,.flac,.ogg"
                    multiple
                    required={inputMode === "audio_upload"}
                    onChange={handleAudioFilesChange}
                  />
                </div>
              )}
            </div>

            {inputMode === "docs" ? (
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
            ) : null}

            <Separator />

            <div className="grid gap-4">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold text-foreground">
                  {listLabel} ({inputMode === "docs" ? docEntries.length : audioEntries.length})
                </h3>
                {inputMode === "docs" ? (
                  <Button type="button" variant="outline" size="sm" onClick={addDocEntry}>
                    + Them Doc
                  </Button>
                ) : null}
              </div>

              {inputMode === "docs"
                ? docEntries.map((entry, index) => (
                    <div
                      key={`doc-${index}`}
                      className="relative grid gap-3 rounded-xl border border-border/70 bg-card/50 p-4"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-semibold text-muted-foreground">Doc #{index + 1}</span>
                        {docEntries.length > 1 ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-7 text-xs text-destructive hover:text-destructive"
                            onClick={() => removeDocEntry(index)}
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
                            onChange={(event) => updateDocEntry(index, "docUrl", event.target.value)}
                            required
                          />
                        </div>
                        <div className="grid gap-1.5">
                          <Label className="text-xs">Ten output (bat buoc, duy nhat)</Label>
                          <Input
                            placeholder="VD: tin-tuc-iran-1"
                            value={entry.outputName}
                            onChange={(event) => updateDocEntry(index, "outputName", event.target.value)}
                            required
                          />
                        </div>
                      </div>

                      <div className="grid gap-1.5">
                        <Label className="text-xs">PiP Overlay</Label>
                        <select
                          value={entry.decorVideoId}
                          onChange={(event) => updateDocEntry(index, "decorVideoId", event.target.value)}
                          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                        >
                          <option value="">Auto (random tu thu vien)</option>
                          {decorVideos.map((decorVideo) => (
                            <option key={decorVideo.id} value={decorVideo.id}>
                              {decorVideo.name} ({decorVideo.durationSeconds}s)
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  ))
                : audioEntries.map((entry, index) => (
                    <div
                      key={`audio-${entry.sourceAudioName}-${index}`}
                      className="relative grid gap-3 rounded-xl border border-border/70 bg-card/50 p-4"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-semibold text-muted-foreground">Audio #{index + 1}</span>
                        <span className="text-xs text-muted-foreground">{entry.sourceAudioName}</span>
                      </div>

                      <div className="grid gap-3 md:grid-cols-3">
                        <div className="grid gap-1.5 md:col-span-2">
                          <Label className="text-xs">File audio nguon</Label>
                          <Input value={entry.sourceAudioName} readOnly />
                        </div>
                        <div className="grid gap-1.5">
                          <Label className="text-xs">Ten output (bat buoc, duy nhat)</Label>
                          <Input
                            placeholder="VD: kenh-7-17-4"
                            value={entry.outputName}
                            onChange={(event) => updateAudioEntry(index, "outputName", event.target.value)}
                            required
                          />
                        </div>
                      </div>

                      <div className="grid gap-1.5">
                        <Label className="text-xs">PiP Overlay</Label>
                        <select
                          value={entry.decorVideoId}
                          onChange={(event) => updateAudioEntry(index, "decorVideoId", event.target.value)}
                          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                        >
                          <option value="">Auto (random tu thu vien)</option>
                          {decorVideos.map((decorVideo) => (
                            <option key={decorVideo.id} value={decorVideo.id}>
                              {decorVideo.name} ({decorVideo.durationSeconds}s)
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
                {inputMode === "audio_upload"
                  ? "He thong se dung file audio upload lam nguon, bo qua Docs/TTS, sau do xu ly anh va render video cho tung audio."
                  : "He thong se xu ly tuan tu tung Google Docs. Moi item: TTS Audio -> Xu ly anh (shuffle) -> Render video + PiP overlay."}
              </p>
              <Button type="submit" size="lg" disabled={isSubmitting}>
                {isSubmitting
                  ? "Dang xu ly..."
                  : `Bat dau Batch Pipeline (${inputMode === "docs" ? docEntries.length : audioEntries.length} ${countLabel})`}
              </Button>
            </div>
          </form>
        </PageSection>
      ) : null}

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

            <div className="h-3 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all duration-500"
                style={{ width: `${overallPercent}%` }}
              />
            </div>

            <div className="grid gap-3">
              {batchProgress.items.map((item) => (
                <ItemProgressCard key={item.index} item={item} />
              ))}
            </div>

            {batchDone ? (
              <div className="flex gap-3">
                {batchProgress.canRetryFailed ? (
                  <Button onClick={handleRetryFailed} disabled={isRetrying}>
                    {isRetrying ? "Dang khoi dong retry..." : batchProgress.retryFailedLabel}
                  </Button>
                ) : null}
                <Button
                  variant="outline"
                  onClick={() => {
                    setBatchId(null);
                    setBatchProgress(null);
                    setInputMode("docs");
                    setDocEntries([{ ...EMPTY_DOC_ENTRY }]);
                    setAudioEntries([]);
                    setAudioFiles([]);
                    setAudioInputKey((prev) => prev + 1);
                    setIsRetrying(false);
                  }}
                >
                  Tao batch moi
                </Button>
              </div>
            ) : null}
          </div>
        </PageSection>
      ) : null}

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
                  {item.sourceType === "uploaded_audio" && item.sourceAudioName ? (
                    <span className="ml-2 text-xs text-muted-foreground">Nguon: {item.sourceAudioName}</span>
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
  const audioCacheMessage =
    item.audioStatus === "ready" && item.audioRelativePath
      ? item.sourceType === "uploaded_audio"
        ? "Audio source ready. Retry se bo qua TTS."
        : "Final audio ready. Retry se bo qua TTS."
      : item.audioStatus === "partial"
        ? `Audio cache: ${item.chunkSummary.completed}/${item.chunkSummary.total} chunks`
        : item.status === "failed" && item.chunkSummary.total > 0
          ? `Audio cache: ${item.chunkSummary.completed}/${item.chunkSummary.total} chunks`
          : null;

  return (
    <div className="rounded-lg border border-border/70 bg-background/70 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-base">{statusIcon(item.status)}</span>
          <span className="truncate font-semibold text-sm text-foreground">{item.outputName}</span>
          {item.decorVideoName ? (
            <span className="hidden text-xs text-muted-foreground sm:inline">PiP: {item.decorVideoName}</span>
          ) : null}
          {item.sourceType === "uploaded_audio" && item.sourceAudioName ? (
            <span className="hidden text-xs text-muted-foreground sm:inline">Nguon: {item.sourceAudioName}</span>
          ) : null}
        </div>
        <span className="whitespace-nowrap text-sm font-semibold text-foreground">{pct}%</span>
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

      {audioCacheMessage ? (
        <div className="mt-2 rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">{audioCacheMessage}</div>
      ) : null}

      {item.error ? (
        <div className="mt-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{item.error}</div>
      ) : null}
    </div>
  );
}
