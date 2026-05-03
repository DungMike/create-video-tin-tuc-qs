import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { LoadingCard } from "@/components/loading-card";
import { PaginationBar } from "@/components/pagination-bar";
import { ReviewClipCard } from "@/components/review-clip-card";
import { StatusAlert } from "@/components/status-alert";
import { TopNav } from "@/components/top-nav";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  createBatchDraft,
  getBatchDraft,
  getBatchLibrarySources,
  getBatchProgress,
  getBatchSourceSet,
  getBatchSourceSets,
  getDecorVideos,
  getVoices,
  prepareBatchSourceVideos,
  retryFailedBatch,
  submitBatchDraft,
  updateBatchDraft,
  uploadBatchDraftFiles,
} from "@/lib/api";
import {
  emptySelectionState,
  normalizeTags,
  setClipSelected,
  setClipTags,
  toggleClipTag,
} from "@/lib/jobSelectionStore";
import type {
  BatchDraftFile,
  BatchDraftResponse,
  BatchInputMode,
  BatchItemProgress,
  BatchProgressResponse,
  BatchSourceRef,
  BatchSourceSet,
  DecorVideo,
  JobSelectionState,
  ReviewClip,
  VoiceRecord,
} from "@/types/api";

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

type SourceMode = "links" | "sets" | "library";

const EMPTY_DOC_ENTRY: DocEntry = { docUrl: "", outputName: "", decorVideoId: "" };
const SOURCE_CLIP_PAGE_SIZE = 20;
const DEFAULT_TIMELINE_CONFIG = {
  firstPhaseSeconds: 300,
  firstPhaseVideoCount: 3,
  firstPhaseImageCount: 1,
  afterPhaseImageEveryMin: 2,
  afterPhaseImageEveryMax: 5,
};

function outputNameFromFileName(filename: string): string {
  return filename.replace(/\.[^/.]+$/, "").trim() || "audio-item";
}

function batchClipUiId(batchSourceId: string, clipId: string): string {
  return `batch_source:${batchSourceId}:${clipId}`;
}

function sourceRefKey(ref: BatchSourceRef): string {
  return ref.origin === "library" ? `library:${ref.assetId}` : batchClipUiId(ref.batchSourceId, ref.clipId);
}

function refFromClipId(clipId: string): BatchSourceRef | null {
  if (clipId.startsWith("library:")) {
    return { origin: "library", assetId: clipId.slice("library:".length) };
  }
  const parts = clipId.split(":");
  if (parts.length === 3 && parts[0] === "batch_source") {
    return { origin: "batch_source", batchSourceId: parts[1], clipId: parts[2] };
  }
  return null;
}

function toBatchSourceClip(batchSourceId: string, clip: ReviewClip): ReviewClip {
  return { ...clip, id: batchClipUiId(batchSourceId, clip.id) };
}

function mergeUniqueClips(existing: ReviewClip[], incoming: ReviewClip[]): ReviewClip[] {
  const seen = new Set(existing.map((clip) => clip.id));
  const merged = [...existing];
  incoming.forEach((clip) => {
    if (!seen.has(clip.id)) {
      seen.add(clip.id);
      merged.push(clip);
    }
  });
  return merged;
}

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    pending: "Cho xu ly",
    audio_source: "Audio nguon",
    tts_audio: "Tao audio (TTS)",
    job_setup: "Tao job",
    shared_image_pool: "Image pool",
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

function statusLabel(status: string): string {
  if (status === "completed") return "Done";
  if (status === "failed") return "Failed";
  if (status === "running") return "Running";
  return "Waiting";
}

export function BatchPipelinePage() {
  const { pipelineId } = useParams();
  const navigate = useNavigate();
  const hydratedDraftRef = useRef(false);

  const [inputMode, setInputMode] = useState<BatchInputMode>("docs");
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [defaultVoiceId, setDefaultVoiceId] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [speed, setSpeed] = useState(1);
  const [volume, setVolume] = useState(1);
  const [decorVideos, setDecorVideos] = useState<DecorVideo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [draftId, setDraftId] = useState("");
  const [docEntries, setDocEntries] = useState<DocEntry[]>([{ ...EMPTY_DOC_ENTRY }]);
  const [audioEntries, setAudioEntries] = useState<AudioEntry[]>([]);
  const [draftImageFiles, setDraftImageFiles] = useState<BatchDraftFile[]>([]);
  const [draftAudioFiles, setDraftAudioFiles] = useState<BatchDraftFile[]>([]);
  const [imageInputKey, setImageInputKey] = useState(0);
  const [audioInputKey, setAudioInputKey] = useState(0);

  const [batchId, setBatchId] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<BatchProgressResponse | null>(null);

  const [sourceMode, setSourceMode] = useState<SourceMode>("links");
  const [sourceVideoLinks, setSourceVideoLinks] = useState("");
  const [sourceVideoFiles, setSourceVideoFiles] = useState<File[]>([]);
  const [sourceVideoInputKey, setSourceVideoInputKey] = useState(0);
  const [isPreparingSourceVideos, setIsPreparingSourceVideos] = useState(false);
  const [sourceSets, setSourceSets] = useState<BatchSourceSet[]>([]);
  const [selectedSourceSetIds, setSelectedSourceSetIds] = useState<string[]>([]);
  const [isLoadingSourceSets, setIsLoadingSourceSets] = useState(false);
  const [libraryTags, setLibraryTags] = useState<string[]>([]);
  const [selectedLibraryTags, setSelectedLibraryTags] = useState<string[]>([]);
  const [isLoadingLibrary, setIsLoadingLibrary] = useState(false);
  const [sourceClips, setSourceClips] = useState<ReviewClip[]>([]);
  const [sourceClipRefs, setSourceClipRefs] = useState<Record<string, BatchSourceRef>>({});
  const [sourceDownloadErrors, setSourceDownloadErrors] = useState<string[]>([]);
  const [sourceAvailableTags, setSourceAvailableTags] = useState<string[]>([]);
  const [sourceSelectionState, setSourceSelectionState] = useState<JobSelectionState>(emptySelectionState);
  const [sourceClipPage, setSourceClipPage] = useState(1);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getVoices(), getDecorVideos(), getBatchSourceSets(), getBatchLibrarySources([])])
      .then(([voiceRes, decorRes, sourceSetRes, libraryRes]) => {
        if (cancelled) return;
        setVoices(voiceRes.voices);
        setDefaultVoiceId(voiceRes.defaultVoiceId);
        setVoiceId((current) => current || voiceRes.defaultVoiceId);
        setDecorVideos(decorRes.decorVideos);
        setSourceSets(sourceSetRes.sourceSets);
        setLibraryTags(libraryRes.availableTags);
        setSourceAvailableTags(libraryRes.availableTags);
      })
      .catch((err) => {
        if (!cancelled) setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai cau hinh.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const addRefsForClips = (clips: ReviewClip[], refs: Record<string, BatchSourceRef>) => {
    setSourceClips((current) => mergeUniqueClips(current, clips));
    setSourceClipRefs((current) => ({ ...current, ...refs }));
  };

  const loadSourceSet = async (batchSourceId: string) => {
    const response = await getBatchSourceSet(batchSourceId);
    const clips = response.reviewClips.map((clip) => toBatchSourceClip(batchSourceId, clip));
    const refs = Object.fromEntries(
      response.reviewClips.map((clip) => [
        batchClipUiId(batchSourceId, clip.id),
        { origin: "batch_source", batchSourceId, clipId: clip.id } as BatchSourceRef,
      ]),
    );
    addRefsForClips(clips, refs);
    setSourceAvailableTags(response.availableTags);
    setSourceDownloadErrors((current) => [...current, ...response.sourceSet.downloadErrors]);
    return response;
  };

  const hydrateDraft = async (draft: BatchDraftResponse) => {
    hydratedDraftRef.current = false;
    setDraftId(draft.draftId);
    setInputMode(draft.inputMode || "docs");
    setVoiceId(draft.voiceId || defaultVoiceId);
    setSpeed(Number(draft.speed || 1));
    setVolume(Number(draft.volume || 1));
    setDocEntries(draft.docEntries?.length ? draft.docEntries : [{ ...EMPTY_DOC_ENTRY }]);
    setAudioEntries(draft.audioEntries ?? []);
    setDraftImageFiles(draft.imageFiles ?? []);
    setDraftAudioFiles(draft.audioFiles ?? []);
    setSourceVideoLinks(draft.sourceVideoLinks || "");
    setSourceSelectionState({
      selectedClipIds: (draft.selectedSourceRefs ?? []).map(sourceRefKey),
      selectedLibraryAssetIds: [],
      clipTags: draft.clipTags ?? {},
    });
    const sourceSetIds = Array.from(
      new Set(
        (draft.selectedSourceRefs ?? [])
          .filter((ref): ref is Extract<BatchSourceRef, { origin: "batch_source" }> => ref.origin === "batch_source")
          .map((ref) => ref.batchSourceId),
      ),
    );
    setSelectedSourceSetIds(sourceSetIds);
    setSourceClips([]);
    setSourceClipRefs({});
    for (const sourceSetId of sourceSetIds) {
      await loadSourceSet(sourceSetId);
    }
    if ((draft.selectedSourceRefs ?? []).some((ref) => ref.origin === "library")) {
      const libraryRes = await getBatchLibrarySources([]);
      const refs = Object.fromEntries(
        libraryRes.clips.map((clip) => {
          const ref = refFromClipId(clip.id);
          return ref ? [clip.id, ref] : [clip.id, { origin: "library", assetId: clip.id.replace(/^library:/, "") }];
        }),
      ) as Record<string, BatchSourceRef>;
      addRefsForClips(libraryRes.clips, refs);
      setLibraryTags(libraryRes.availableTags);
      setSourceAvailableTags(libraryRes.availableTags);
    }
    window.setTimeout(() => {
      hydratedDraftRef.current = true;
    }, 0);
  };

  useEffect(() => {
    let cancelled = false;
    const init = async () => {
      setIsLoading(true);
      setErrorMessage(null);
      try {
        if (!pipelineId) {
          const draft = await createBatchDraft();
          if (!cancelled) navigate(`/batch-pipeline/${draft.draftId}`, { replace: true });
          return;
        }
        if (pipelineId.startsWith("batch_")) {
          setBatchId(pipelineId);
          hydratedDraftRef.current = false;
          return;
        }
        if (pipelineId.startsWith("draft_")) {
          const draft = await getBatchDraft(pipelineId);
          if (!cancelled) await hydrateDraft(draft);
          return;
        }
        setErrorMessage("Pipeline id khong hop le.");
      } catch (err) {
        if (!cancelled) setErrorMessage(err instanceof ApiError ? err.message : "Khong the mo batch pipeline.");
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    init();
    return () => {
      cancelled = true;
    };
  }, [pipelineId]);

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

  const selectedSourceRefs = useMemo(
    () =>
      sourceSelectionState.selectedClipIds
        .map((clipId) => sourceClipRefs[clipId] ?? refFromClipId(clipId))
        .filter((ref): ref is BatchSourceRef => Boolean(ref)),
    [sourceSelectionState.selectedClipIds, sourceClipRefs],
  );

  useEffect(() => {
    if (!draftId || !hydratedDraftRef.current || batchId) return;
    const timeout = window.setTimeout(() => {
      updateBatchDraft(draftId, {
        inputMode,
        voiceId,
        speed,
        volume,
        docEntries,
        audioEntries,
        sourceVideoLinks,
        selectedSourceRefs,
        clipTags: sourceSelectionState.clipTags,
        timelineConfig: DEFAULT_TIMELINE_CONFIG,
      }).catch(() => undefined);
    }, 700);
    return () => window.clearTimeout(timeout);
  }, [
    draftId,
    batchId,
    inputMode,
    voiceId,
    speed,
    volume,
    docEntries,
    audioEntries,
    sourceVideoLinks,
    selectedSourceRefs,
    sourceSelectionState.clipTags,
  ]);

  const updateDocEntry = (index: number, field: keyof DocEntry, value: string) => {
    setDocEntries((prev) => prev.map((entry, entryIndex) => (entryIndex === index ? { ...entry, [field]: value } : entry)));
  };

  const updateAudioEntry = (index: number, field: "outputName" | "decorVideoId", value: string) => {
    setAudioEntries((prev) =>
      prev.map((entry, entryIndex) => (entryIndex === index ? { ...entry, [field]: value } : entry)),
    );
  };

  const handleInputModeChange = (nextMode: BatchInputMode) => {
    setInputMode(nextMode);
    setErrorMessage(null);
    if (nextMode === "docs" && docEntries.length === 0) {
      setDocEntries([{ ...EMPTY_DOC_ENTRY }]);
    }
  };

  const handleDraftFilesChange = async (event: React.ChangeEvent<HTMLInputElement>, kind: "images" | "audio") => {
    if (!draftId) return;
    const files = Array.from(event.currentTarget.files ?? []);
    const formData = new FormData();
    formData.set("kind", kind);
    files.forEach((file) => formData.append(kind === "images" ? "images" : "audioFiles", file));
    try {
      const draft = await uploadBatchDraftFiles(draftId, formData);
      setDraftImageFiles(draft.imageFiles);
      setDraftAudioFiles(draft.audioFiles);
      setAudioEntries(draft.audioEntries);
      if (kind === "images") setImageInputKey((current) => current + 1);
      if (kind === "audio") setAudioInputKey((current) => current + 1);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the upload file vao draft.");
    }
  };

  const handlePrepareSourceVideos = async () => {
    setErrorMessage(null);
    const trimmedLinks = sourceVideoLinks.trim();
    if (!trimmedLinks && !sourceVideoFiles.length) {
      setErrorMessage("Can nhap link video hoac chon it nhat 1 file source video.");
      return;
    }

    const formData = new FormData();
    formData.set("youtube_links", trimmedLinks);
    sourceVideoFiles.forEach((file) => formData.append("videos", file));
    setIsPreparingSourceVideos(true);
    try {
      const response = await prepareBatchSourceVideos(formData);
      const clips = response.reviewClips.map((clip) => toBatchSourceClip(response.batchSourceId, clip));
      const refs = Object.fromEntries(
        response.reviewClips.map((clip) => [
          batchClipUiId(response.batchSourceId, clip.id),
          { origin: "batch_source", batchSourceId: response.batchSourceId, clipId: clip.id } as BatchSourceRef,
        ]),
      );
      addRefsForClips(clips, refs);
      setSelectedSourceSetIds((current) => Array.from(new Set([...current, response.batchSourceId])));
      setSourceDownloadErrors(response.downloadErrors);
      setSourceAvailableTags(response.availableTags);
      setSourceClipPage(1);
      setSourceVideoFiles([]);
      setSourceVideoInputKey((current) => current + 1);
      const sourceSetRes = await getBatchSourceSets();
      setSourceSets(sourceSetRes.sourceSets);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai/cat source video.");
    } finally {
      setIsPreparingSourceVideos(false);
    }
  };

  const handleLoadSelectedSourceSets = async () => {
    setErrorMessage(null);
    setIsLoadingSourceSets(true);
    try {
      for (const sourceSetId of selectedSourceSetIds) {
        await loadSourceSet(sourceSetId);
      }
      setSourceClipPage(1);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai source clips co san.");
    } finally {
      setIsLoadingSourceSets(false);
    }
  };

  const handleLoadLibrarySources = async () => {
    setErrorMessage(null);
    setIsLoadingLibrary(true);
    try {
      const response = await getBatchLibrarySources(selectedLibraryTags);
      const refs = Object.fromEntries(
        response.clips.map((clip) => {
          const ref = refFromClipId(clip.id);
          return ref ? [clip.id, ref] : [clip.id, { origin: "library", assetId: clip.id.replace(/^library:/, "") }];
        }),
      ) as Record<string, BatchSourceRef>;
      addRefsForClips(response.clips, refs);
      setLibraryTags(response.availableTags);
      setSourceAvailableTags(response.availableTags);
      setSourceClipPage(1);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai library source.");
    } finally {
      setIsLoadingLibrary(false);
    }
  };

  const addDocEntry = () => setDocEntries((prev) => [...prev, { ...EMPTY_DOC_ENTRY }]);
  const removeDocEntry = (index: number) => {
    setDocEntries((prev) => (prev.length <= 1 ? prev : prev.filter((_, entryIndex) => entryIndex !== index)));
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draftId) return;
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
      if (!draftAudioFiles.length) {
        setErrorMessage("Can chon it nhat 1 file audio.");
        return;
      }
      if (audioEntries.length !== draftAudioFiles.length) {
        setErrorMessage("So item audio khong khop voi so file audio da chon.");
        return;
      }
    }
    if (!draftImageFiles.length) {
      setErrorMessage("Can upload it nhat 1 anh nguon.");
      return;
    }
    const duplicateNames = outputNames.filter((name, index) => name && outputNames.indexOf(name) !== index);
    if (duplicateNames.length > 0) {
      setErrorMessage(`Ten output bi trung lap: ${duplicateNames.join(", ")}`);
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await submitBatchDraft(draftId, {
        inputMode,
        voiceId,
        speed,
        volume,
        docEntries,
        audioEntries,
        sourceVideoLinks,
        selectedSourceRefs,
        clipTags: sourceSelectionState.clipTags,
        timelineConfig: DEFAULT_TIMELINE_CONFIG,
      });
      setBatchId(response.batchId);
      setBatchProgress(null);
      navigate(`/batch-pipeline/${response.batchId}`, { replace: true });
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the khoi tao batch pipeline.");
      setIsSubmitting(false);
    }
  };

  const handleRetryFailed = async () => {
    if (!batchId || !batchProgress?.canRetryFailed) return;
    setErrorMessage(null);
    setIsRetrying(true);
    try {
      await retryFailedBatch(batchId);
      setBatchProgress((prev) => (prev ? { ...prev, status: "running", canRetryFailed: false } : prev));
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the retry cac item failed.");
      setIsRetrying(false);
    }
  };

  const sourceTotalPages = Math.max(1, Math.ceil(sourceClips.length / SOURCE_CLIP_PAGE_SIZE));
  const normalizedSourceClipPage = Math.min(Math.max(sourceClipPage, 1), sourceTotalPages);
  const sourceStartIndex = (normalizedSourceClipPage - 1) * SOURCE_CLIP_PAGE_SIZE;
  const sourcePageClips = sourceClips.slice(sourceStartIndex, sourceStartIndex + SOURCE_CLIP_PAGE_SIZE);
  const sourceEndItem = sourceClips.length ? sourceStartIndex + sourcePageClips.length : 0;
  const sourcePageClipIds = sourcePageClips.map((clip) => clip.id);
  const completedItems = batchProgress?.items.filter((item) => item.status === "completed") ?? [];
  const overallPercent =
    batchProgress && batchProgress.totalUrls > 0
      ? Math.round(batchProgress.items.reduce((sum, item) => sum + item.percent, 0) / batchProgress.totalUrls)
      : 0;
  const currentMode = batchProgress?.inputMode ?? inputMode;
  const listLabel = currentMode === "audio_upload" ? "Danh sach Audio" : "Danh sach Docs";

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai batch pipeline..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Batch Pipeline"
        title="Tu dong tao video tu Google Docs hoac nhieu file audio"
        description="Draft URL se luu form, file upload va source clip selection de co the reload va tiep tuc."
        stats={[
          { label: "Mode", value: currentMode === "audio_upload" ? "Audio Upload" : "Google Docs" },
          { label: "Draft", value: draftId || batchId || "-" },
          { label: "Source clips", value: batchProgress?.sourceVideoPool?.selectedClips ?? sourceSelectionState.selectedClipIds.length },
          { label: "Images", value: draftImageFiles.length },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}

      {!batchId ? (
        <PageSection>
          <form className="grid gap-6" onSubmit={handleSubmit}>
            <div className="grid gap-2">
              <Label>Input mode</Label>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant={inputMode === "docs" ? "default" : "outline"} onClick={() => handleInputModeChange("docs")}>
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
                <Input
                  key={imageInputKey}
                  id="images"
                  type="file"
                  accept=".jpg,.jpeg,.png,.webp"
                  multiple
                  onChange={(event) => handleDraftFilesChange(event, "images")}
                />
                {draftImageFiles.length ? (
                  <p className="text-xs text-muted-foreground">Da upload: {draftImageFiles.map((file) => file.name).join(", ")}</p>
                ) : null}
              </div>

              {inputMode === "docs" ? (
                <div className="grid gap-2">
                  <Label htmlFor="voiceId">Voice</Label>
                  <select
                    id="voiceId"
                    value={voiceId || defaultVoiceId}
                    onChange={(event) => setVoiceId(event.target.value)}
                    className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  >
                    <option value={defaultVoiceId}>{defaultVoiceId ? `Default voice (${defaultVoiceId})` : "TTS_DEFAULT_VOICE_ID"}</option>
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
                    type="file"
                    accept=".mp3,.wav,.m4a,.aac,.flac,.ogg"
                    multiple
                    onChange={(event) => handleDraftFilesChange(event, "audio")}
                  />
                  {draftAudioFiles.length ? (
                    <p className="text-xs text-muted-foreground">Da upload: {draftAudioFiles.map((file) => file.name).join(", ")}</p>
                  ) : null}
                </div>
              )}
            </div>

            {inputMode === "docs" ? (
              <div className="grid gap-4 md:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor="speed">Speed</Label>
                  <Input id="speed" type="number" step="0.1" min="0.5" max="1.5" value={speed} onChange={(event) => setSpeed(Number(event.target.value || 1))} />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="volume">Volume</Label>
                  <Input id="volume" type="number" step="0.1" min="0" value={volume} onChange={(event) => setVolume(Number(event.target.value || 1))} />
                </div>
              </div>
            ) : null}

            <Separator />

            <div className="grid gap-4">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-foreground">Source video</h3>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {inputMode === "audio_upload"
                      ? "Audio Upload dung cung source video da cat; chi bo qua buoc tao audio tu Google Docs."
                      : "Co the tao source moi, dung folder da cut trong batch_sources, hoac lay clip library theo tag."}
                  </p>
                </div>
                <div className="text-sm text-muted-foreground">
                  Da chon <span className="font-semibold text-foreground">{sourceSelectionState.selectedClipIds.length}</span>
                  {sourceClips.length ? ` / ${sourceClips.length}` : ""} clip
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button type="button" variant={sourceMode === "links" ? "default" : "outline"} onClick={() => setSourceMode("links")}>Tao tu link</Button>
                <Button type="button" variant={sourceMode === "sets" ? "default" : "outline"} onClick={() => setSourceMode("sets")}>Batch sources co san</Button>
                <Button type="button" variant={sourceMode === "library" ? "default" : "outline"} onClick={() => setSourceMode("library")}>Library theo tag</Button>
              </div>

              {sourceMode === "links" ? (
                <div className="grid gap-3">
                  <div className="grid gap-2">
                    <Label htmlFor="batch_source_links">Link video YouTube/Bilibili</Label>
                    <Textarea
                      id="batch_source_links"
                      rows={5}
                      value={sourceVideoLinks}
                      onChange={(event) => setSourceVideoLinks(event.target.value)}
                      placeholder="Moi dong mot link video"
                      className="min-h-[120px]"
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="batch_source_files">Hoac upload source video</Label>
                    <Input
                      key={sourceVideoInputKey}
                      id="batch_source_files"
                      type="file"
                      accept=".mp4,.mov,.mkv,.webm"
                      multiple
                      onChange={(event) => setSourceVideoFiles(Array.from(event.currentTarget.files ?? []))}
                    />
                    {sourceVideoFiles.length ? (
                      <p className="text-xs text-muted-foreground">Da chon: {sourceVideoFiles.map((file) => file.name).join(", ")}</p>
                    ) : null}
                  </div>
                  <Button type="button" variant="secondary" onClick={handlePrepareSourceVideos} disabled={isPreparingSourceVideos}>
                    {isPreparingSourceVideos ? "Dang tai/upload va cat clip..." : "Tai/upload va cat source video"}
                  </Button>
                </div>
              ) : null}

              {sourceMode === "sets" ? (
                <div className="grid gap-3">
                  <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                    {sourceSets.map((sourceSet) => (
                      <label key={sourceSet.batchSourceId} className="grid cursor-pointer gap-1 rounded-lg border border-border/70 bg-background/70 p-3 text-sm">
                        <span className="flex items-center gap-2 font-semibold text-foreground">
                          <input
                            type="checkbox"
                            checked={selectedSourceSetIds.includes(sourceSet.batchSourceId)}
                            onChange={(event) => {
                              setSelectedSourceSetIds((current) =>
                                event.target.checked
                                  ? Array.from(new Set([...current, sourceSet.batchSourceId]))
                                  : current.filter((id) => id !== sourceSet.batchSourceId),
                              );
                            }}
                          />
                          {sourceSet.displayName}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {sourceSet.validClipCount}/{sourceSet.clipCount} clips | {sourceSet.batchSourceId}
                        </span>
                        {sourceSet.sourceNames.length ? <span className="truncate text-xs text-muted-foreground">{sourceSet.sourceNames.join(", ")}</span> : null}
                      </label>
                    ))}
                  </div>
                  <Button type="button" variant="secondary" onClick={handleLoadSelectedSourceSets} disabled={isLoadingSourceSets || !selectedSourceSetIds.length}>
                    {isLoadingSourceSets ? "Dang tai clips..." : "Tai clip tu folder da chon"}
                  </Button>
                </div>
              ) : null}

              {sourceMode === "library" ? (
                <div className="grid gap-3">
                  <div className="flex flex-wrap gap-2">
                    {libraryTags.map((tag) => (
                      <Button
                        key={tag}
                        type="button"
                        size="sm"
                        variant={selectedLibraryTags.includes(tag) ? "default" : "outline"}
                        onClick={() =>
                          setSelectedLibraryTags((current) =>
                            current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag],
                          )
                        }
                      >
                        {tag}
                      </Button>
                    ))}
                  </div>
                  <Button type="button" variant="secondary" onClick={handleLoadLibrarySources} disabled={isLoadingLibrary}>
                    {isLoadingLibrary ? "Dang tai library..." : "Tai clip library theo tag"}
                  </Button>
                </div>
              ) : null}

              {sourceDownloadErrors.length ? (
                <StatusAlert title="Co link source video tai that bai" message={sourceDownloadErrors.join(" | ")} variant="destructive" />
              ) : null}

              {sourceClips.length ? (
                <div className="grid gap-4">
                  <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
                    <span>Dang xem {sourceStartIndex + 1}-{sourceEndItem} / {sourceClips.length} clip</span>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setSourceSelectionState((current) => ({
                            ...current,
                            selectedClipIds: Array.from(new Set([...current.selectedClipIds, ...sourcePageClipIds])),
                          }))
                        }
                      >
                        Chon trang nay
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setSourceSelectionState((current) => ({
                            ...current,
                            selectedClipIds: current.selectedClipIds.filter((clipId) => !sourcePageClipIds.includes(clipId)),
                          }))
                        }
                      >
                        Bo chon trang nay
                      </Button>
                      <Button type="button" variant="outline" size="sm" onClick={() => setSourceSelectionState((current) => ({ ...current, selectedClipIds: [] }))}>
                        Bo chon tat ca
                      </Button>
                    </div>
                    <PaginationBar
                      page={normalizedSourceClipPage}
                      totalPages={sourceTotalPages}
                      onPrevious={() => setSourceClipPage((current) => Math.max(1, current - 1))}
                      onNext={() => setSourceClipPage((current) => Math.min(sourceTotalPages, current + 1))}
                    />
                  </div>

                  <section className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
                    {sourcePageClips.map((clip) => (
                      <ReviewClipCard
                        key={clip.id}
                        clip={clip}
                        checked={sourceSelectionState.selectedClipIds.includes(clip.id)}
                        activeTags={sourceSelectionState.clipTags[clip.id] ?? []}
                        suggestedTags={sourceAvailableTags}
                        onCheckedChange={(checked) => setSourceSelectionState((current) => setClipSelected(current, clip.id, checked))}
                        onToggleTag={(tag) => setSourceSelectionState((current) => toggleClipTag(current, clip.id, tag))}
                        onAddTag={(tag) => {
                          const normalized = normalizeTags([tag]);
                          if (!normalized.length) return;
                          setSourceSelectionState((current) => setClipTags(current, clip.id, [...(current.clipTags[clip.id] ?? []), ...normalized]));
                        }}
                      />
                    ))}
                  </section>

                  <PaginationBar
                    page={normalizedSourceClipPage}
                    totalPages={sourceTotalPages}
                    onPrevious={() => setSourceClipPage((current) => Math.max(1, current - 1))}
                    onNext={() => setSourceClipPage((current) => Math.min(sourceTotalPages, current + 1))}
                  />
                </div>
              ) : null}
            </div>

            <Separator />

            <div className="grid gap-4">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold text-foreground">
                  {listLabel} ({inputMode === "docs" ? docEntries.length : audioEntries.length})
                </h3>
                {inputMode === "docs" ? (
                  <Button type="button" variant="outline" size="sm" onClick={addDocEntry}>+ Them Doc</Button>
                ) : null}
              </div>

              {inputMode === "docs"
                ? docEntries.map((entry, index) => (
                    <div key={`doc-${index}`} className="relative grid gap-3 rounded-xl border border-border/70 bg-card/50 p-4">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-semibold text-muted-foreground">Doc #{index + 1}</span>
                        {docEntries.length > 1 ? (
                          <Button type="button" variant="ghost" size="sm" className="h-7 text-xs text-destructive hover:text-destructive" onClick={() => removeDocEntry(index)}>Xoa</Button>
                        ) : null}
                      </div>
                      <div className="grid gap-3 md:grid-cols-3">
                        <div className="grid gap-1.5 md:col-span-2">
                          <Label className="text-xs">Google Docs URL</Label>
                          <Input placeholder="https://docs.google.com/document/d/..." value={entry.docUrl} onChange={(event) => updateDocEntry(index, "docUrl", event.target.value)} required />
                        </div>
                        <div className="grid gap-1.5">
                          <Label className="text-xs">Ten output</Label>
                          <Input placeholder="VD: tin-tuc-1" value={entry.outputName} onChange={(event) => updateDocEntry(index, "outputName", event.target.value)} required />
                        </div>
                      </div>
                      <div className="grid gap-1.5">
                        <Label className="text-xs">PiP Overlay</Label>
                        <select value={entry.decorVideoId} onChange={(event) => updateDocEntry(index, "decorVideoId", event.target.value)} className="h-9 rounded-md border border-input bg-background px-3 text-sm">
                          <option value="">Auto (random tu thu vien)</option>
                          {decorVideos.map((decorVideo) => (
                            <option key={decorVideo.id} value={decorVideo.id}>{decorVideo.name} ({decorVideo.durationSeconds}s)</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  ))
                : audioEntries.map((entry, index) => (
                    <div key={`audio-${entry.sourceAudioName}-${index}`} className="relative grid gap-3 rounded-xl border border-border/70 bg-card/50 p-4">
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
                          <Label className="text-xs">Ten output</Label>
                          <Input value={entry.outputName || outputNameFromFileName(entry.sourceAudioName)} onChange={(event) => updateAudioEntry(index, "outputName", event.target.value)} required />
                        </div>
                      </div>
                      <div className="grid gap-1.5">
                        <Label className="text-xs">PiP Overlay</Label>
                        <select value={entry.decorVideoId} onChange={(event) => updateAudioEntry(index, "decorVideoId", event.target.value)} className="h-9 rounded-md border border-input bg-background px-3 text-sm">
                          <option value="">Auto (random tu thu vien)</option>
                          {decorVideos.map((decorVideo) => (
                            <option key={decorVideo.id} value={decorVideo.id}>{decorVideo.name} ({decorVideo.durationSeconds}s)</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  ))}
            </div>

            <Separator />

            <div className="flex flex-wrap items-center justify-between gap-4">
              <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
                Draft autosave theo URL hien tai. Reload se giu form, file da upload, source clips da chon va tag.
              </p>
              <Button type="submit" size="lg" disabled={isSubmitting}>
                {isSubmitting ? "Dang xu ly..." : `Bat dau Batch Pipeline (${inputMode === "docs" ? docEntries.length : audioEntries.length})`}
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
                <h3 className="text-base font-semibold text-foreground">Batch: {batchProgress.batchId}</h3>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  Trang thai: <span className="font-medium text-foreground">{batchProgress.status}</span> | Hoan tat: {batchProgress.completedUrls}/{batchProgress.totalUrls}
                </p>
              </div>
              <div className="text-2xl font-semibold text-foreground">{overallPercent}%</div>
            </div>

            <div className="h-3 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary transition-all duration-500" style={{ width: `${overallPercent}%` }} />
            </div>

            <div className="grid gap-3">
              {batchProgress.items.map((item) => <ItemProgressCard key={item.index} item={item} />)}
            </div>

            <div className="flex gap-3">
              {batchProgress.canRetryFailed ? (
                <Button onClick={handleRetryFailed} disabled={isRetrying}>{isRetrying ? "Dang khoi dong retry..." : batchProgress.retryFailedLabel}</Button>
              ) : null}
              <Button variant="outline" onClick={async () => {
                const draft = await createBatchDraft();
                navigate(`/batch-pipeline/${draft.draftId}`);
              }}>
                Tao batch moi
              </Button>
            </div>
          </div>
        </PageSection>
      ) : null}

      {completedItems.length > 0 ? (
        <PageSection>
          <h3 className="mb-4 text-base font-semibold text-foreground">Video da hoan tat ({completedItems.length})</h3>
          <div className="grid gap-3">
            {completedItems.map((item) => (
              <div key={item.index} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/70 bg-card/80 p-4">
                <div>
                  <span className="font-semibold text-foreground">{item.outputName}.mp4</span>
                  {item.decorVideoName ? <span className="ml-2 text-xs text-muted-foreground">PiP: {item.decorVideoName}</span> : null}
                  {item.sourceType === "uploaded_audio" && item.sourceAudioName ? <span className="ml-2 text-xs text-muted-foreground">Nguon: {item.sourceAudioName}</span> : null}
                </div>
                <div className="flex gap-2">
                  {item.outputVideo ? (
                    <>
                      <Button asChild variant="outline" size="sm"><a href={`/media/${item.outputVideo}`} target="_blank" rel="noopener noreferrer">Preview</a></Button>
                      <Button asChild size="sm"><a href={`/media/${item.outputVideo}`} download={`${item.outputName}.mp4`}>Download</a></Button>
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
  const barColorClass = item.status === "completed" ? "bg-green-500" : item.status === "failed" ? "bg-destructive" : "bg-primary";
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
          <span className="w-16 text-xs font-semibold text-muted-foreground">{statusLabel(item.status)}</span>
          <span className="truncate text-sm font-semibold text-foreground">{item.outputName}</span>
          {item.decorVideoName ? <span className="hidden text-xs text-muted-foreground sm:inline">PiP: {item.decorVideoName}</span> : null}
          {item.sourceType === "uploaded_audio" && item.sourceAudioName ? <span className="hidden text-xs text-muted-foreground sm:inline">Nguon: {item.sourceAudioName}</span> : null}
        </div>
        <span className="whitespace-nowrap text-sm font-semibold text-foreground">{pct}%</span>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
        <div className={`h-full rounded-full transition-all duration-300 ${barColorClass}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="truncate">{item.message}</span>
        <span className="whitespace-nowrap">{stageLabel(item.stage)}</span>
      </div>
      {audioCacheMessage ? <div className="mt-2 rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">{audioCacheMessage}</div> : null}
      {item.error ? <div className="mt-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{item.error}</div> : null}
    </div>
  );
}
