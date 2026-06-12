import { Download, Eye, Loader2, Plus, RotateCcw, Settings, Square, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { TopNav } from "@/components/top-nav";
import { Badge } from "@/components/ui/badge";
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
import { Separator } from "@/components/ui/separator";
import {
  ApiError,
  cancelStoryBatch,
  cancelStoryBatchItem,
  cancelStoryVideo,
  createStoryBatch,
  createStoryVideo,
  generateSubtitlePreview,
  getStoryBatchProgress,
  getStoryDriveAudioImport,
  getStoryVideoProgress,
  getSubtitleFonts,
  getSubtitlePresets,
  getVoices,
  retryStoryBatchFailed,
  startStoryDriveAudioImport,
  uploadSubtitleFont,
} from "@/lib/api";
import type {
  CreateStoryBatchItem,
  CreateStoryBatchRequest,
  CreateStoryVideoRequest,
  DriveAudioImportProgress,
  StoryBatchItemProgress,
  StoryBatchProgress,
  StoryVideoProgress,
  SubtitleFontInfo,
  SubtitlePresetInfo,
  VoiceRecord,
} from "@/types/api";

type StoryMode = "single" | "batch";
type StoryInputType = "audio_file" | "script_url";
type BatchInputType = StoryInputType | "drive_audio";

interface SingleInput {
  inputType: StoryInputType;
  inputValue: string;
  outputName: string;
  audioFile: File | null;
}

interface BatchItem {
  id: string;
  inputType: BatchInputType;
  inputValue: string;
  outputName: string;
  audioFile: File | null;
  subtitleFile: File | null;
  sourceName: string;
}

let batchIdCounter = 0;

function nextBatchId(): string {
  batchIdCounter += 1;
  return `batch_item_${batchIdCounter}`;
}

function outputNameFromFileName(filename: string): string {
  return filename.replace(/\.[^/.]+$/, "").trim() || "story-item";
}

function clampInt(value: string, min: number, max: number, fallback: number): number {
  const num = Number(value);
  if (!Number.isFinite(num)) return fallback;
  return Math.min(max, Math.max(min, Math.round(num)));
}

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    pending: "Cho xu ly",
    prepare_audio: "Chuan bi audio",
    audio_source: "Audio nguon",
    tts_audio: "Tao audio",
    select_clips: "Chon clip",
    prepare_clips: "Tao clip 5 giay",
    render_video: "Render video",
    waveform_overlay: "Song am",
    story_overlays: "TV noise / song am",
    finalize: "Hoan thien",
    completed: "Hoan tat",
    cancelling: "Dang huy",
    cancelled: "Da huy",
    failed: "That bai",
  };
  return labels[stage] ?? stage;
}

export function StoryVideoPage() {
  const [mode, setMode] = useState<StoryMode>("single");
  const [singleInput, setSingleInput] = useState<SingleInput>({
    inputType: "audio_file",
    inputValue: "",
    outputName: "",
    audioFile: null,
  });
  const [batchItems, setBatchItems] = useState<BatchItem[]>([]);
  const [audioInputKey, setAudioInputKey] = useState(0);
  const [singleSubtitleFile, setSingleSubtitleFile] = useState<File | null>(null);
  const [subtitleInputKey, setSubtitleInputKey] = useState(0);
  const [subtitleFonts, setSubtitleFonts] = useState<SubtitleFontInfo[]>([]);
  const [subtitlePresets, setSubtitlePresets] = useState<SubtitlePresetInfo[]>([]);
  const [subtitleFont, setSubtitleFont] = useState("");
  const [subtitlePreset, setSubtitlePreset] = useState("clean");
  const [subtitleMaxCharsPerLine, setSubtitleMaxCharsPerLine] = useState("42");
  const [subtitleMaxLines, setSubtitleMaxLines] = useState("2");
  const [fontInputKey, setFontInputKey] = useState(0);
  const [isUploadingFont, setIsUploadingFont] = useState(false);
  const [isSubtitlePreviewing, setIsSubtitlePreviewing] = useState(false);
  const [subtitlePreviewPath, setSubtitlePreviewPath] = useState<string | null>(null);
  const [subtitlePreviewBust, setSubtitlePreviewBust] = useState(0);
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [defaultVoiceId, setDefaultVoiceId] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [storyId, setStoryId] = useState<string | null>(null);
  const [storyProgress, setStoryProgress] = useState<StoryVideoProgress | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<StoryBatchProgress | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isCancellingStory, setIsCancellingStory] = useState(false);
  const [isCancellingBatch, setIsCancellingBatch] = useState(false);
  const [cancellingItemIds, setCancellingItemIds] = useState<Set<string>>(new Set());
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [driveWarningMessage, setDriveWarningMessage] = useState<string | null>(null);
  const [isDriveDialogOpen, setIsDriveDialogOpen] = useState(false);
  const [driveFolderUrl, setDriveFolderUrl] = useState("");
  const [isStartingDriveImport, setIsStartingDriveImport] = useState(false);
  const [driveImportSessionId, setDriveImportSessionId] = useState<string | null>(null);
  const [driveImportProgress, setDriveImportProgress] = useState<DriveAudioImportProgress | null>(null);
  const isDriveImporting = isStartingDriveImport || Boolean(driveImportSessionId);

  const resetProgress = useCallback(() => {
    setStoryId(null);
    setStoryProgress(null);
    setBatchId(null);
    setBatchProgress(null);
  }, []);

  const submitBatchItems = useCallback(async (itemsToSubmit: BatchItem[]) => {
    setErrorMessage(null);
    resetProgress();
    setIsSubmitting(true);
    try {
      const audioFiles: File[] = [];
      const subtitleFiles: File[] = [];
      const items: CreateStoryBatchItem[] = itemsToSubmit.map((item) => {
        let inputValue = item.inputValue;
        if (item.inputType === "audio_file") {
          inputValue = String(audioFiles.length);
          if (item.audioFile) audioFiles.push(item.audioFile);
        }
        const entry: CreateStoryBatchItem = {
          id: item.id,
          inputType: item.inputType,
          inputValue,
          outputName: item.outputName,
        };
        if (item.subtitleFile) {
          entry.subtitleFile = String(subtitleFiles.length);
          subtitleFiles.push(item.subtitleFile);
        }
        return entry;
      });
      const sharedConfig: CreateStoryBatchRequest["sharedConfig"] = {
        clipTags: [],
        voiceId: voiceId || undefined,
      };
      if (subtitleFiles.length) {
        sharedConfig.subtitleFont = subtitleFont || undefined;
        sharedConfig.subtitlePreset = subtitlePreset;
        sharedConfig.subtitleMaxCharsPerLine = clampInt(subtitleMaxCharsPerLine, 16, 60, 42);
        sharedConfig.subtitleMaxLines = clampInt(subtitleMaxLines, 1, 3, 2);
      }
      const response = await createStoryBatch(
        { items, sharedConfig },
        audioFiles.length ? audioFiles : undefined,
        subtitleFiles.length ? subtitleFiles : undefined,
      );
      setBatchId(response.batchId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the bat dau batch render.");
      setIsSubmitting(false);
    }
  }, [resetProgress, voiceId, subtitleFont, subtitlePreset, subtitleMaxCharsPerLine, subtitleMaxLines]);

  useEffect(() => {
    let cancelled = false;
    const loadVoices = getVoices()
      .then((voiceRes) => {
        if (cancelled) return;
        setVoices(voiceRes.voices);
        setDefaultVoiceId(voiceRes.defaultVoiceId);
        setVoiceId(voiceRes.defaultVoiceId);
      })
      .catch((err) => {
        if (!cancelled) setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai cau hinh.");
      });
    const loadFonts = getSubtitleFonts()
      .then((res) => {
        if (cancelled) return;
        setSubtitleFonts(res.fonts);
        setSubtitleFont((current) => current || res.defaultFamily);
      })
      .catch((err) => {
        if (!cancelled) setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai danh sach font phu de.");
      });
    const loadPresets = getSubtitlePresets()
      .then((res) => {
        if (cancelled) return;
        setSubtitlePresets(res.presets);
      })
      .catch((err) => {
        if (!cancelled) setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai preset phu de.");
      });
    void Promise.all([loadVoices, loadFonts, loadPresets]).finally(() => {
      if (!cancelled) setIsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!storyId) return;
    let cancelled = false;
    const poll = () => {
      getStoryVideoProgress(storyId)
        .then((data) => {
          if (!cancelled) {
            setStoryProgress(data);
            if (data.status === "completed" || data.status === "failed" || data.status === "cancelled") {
              setIsSubmitting(false);
              setIsCancellingStory(false);
            }
          }
        })
        .catch(() => undefined);
    };
    poll();
    const interval = window.setInterval(poll, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [storyId]);

  useEffect(() => {
    if (!batchId) return;
    let cancelled = false;
    const poll = () => {
      getStoryBatchProgress(batchId)
        .then((data) => {
          if (!cancelled) {
            setBatchProgress(data);
            setCancellingItemIds((current) => {
              const next = new Set(current);
              data.items.forEach((item) => {
                if (item.status === "completed" || item.status === "failed" || item.status === "cancelled") next.delete(item.id);
              });
              return next;
            });
            if (data.status === "completed" || data.status === "failed" || data.status === "cancelled") {
              setIsSubmitting(false);
              setIsRetrying(false);
              setIsCancellingBatch(false);
            }
          }
        })
        .catch(() => undefined);
    };
    poll();
    const interval = window.setInterval(poll, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [batchId]);

  useEffect(() => {
    if (!driveImportSessionId) return;
    let cancelled = false;
    let settled = false;
    const poll = () => {
      getStoryDriveAudioImport(driveImportSessionId)
        .then((progress) => {
          if (cancelled || settled) return;
          setDriveImportProgress(progress);
          if (progress.status === "completed") {
            settled = true;
            const importedItems: BatchItem[] = progress.items.map((item) => ({
              id: nextBatchId(),
              inputType: "drive_audio",
              inputValue: item.token,
              outputName: item.outputName,
              audioFile: null,
              subtitleFile: null,
              sourceName: item.fileName,
            }));
            setBatchItems(importedItems);
            setDriveWarningMessage(
              progress.skipped.length
                ? `Da bo qua ${progress.skipped.length} file: ${progress.skipped.map((item) => `${item.fileName} (${item.reason})`).join(" | ")}`
                : null,
            );
            setDriveImportSessionId(null);
            setDriveFolderUrl("");
            setIsDriveDialogOpen(false);
            void submitBatchItems(importedItems);
          } else if (progress.status === "failed") {
            settled = true;
            setErrorMessage(progress.error || progress.message || "Khong the tai audio tu Google Drive folder.");
            setDriveImportSessionId(null);
          }
        })
        .catch((err) => {
          if (cancelled || settled) return;
          settled = true;
          setErrorMessage(err instanceof ApiError ? err.message : "Khong the doc tien do import Google Drive.");
          setDriveImportSessionId(null);
        });
    };
    poll();
    const interval = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [driveImportSessionId, submitBatchItems]);

  const handleAddBatchAudio = (files: FileList | null) => {
    if (!files) return;
    const newItems: BatchItem[] = Array.from(files).map((file) => ({
      id: nextBatchId(),
      inputType: "audio_file",
      inputValue: file.name,
      outputName: outputNameFromFileName(file.name),
      audioFile: file,
      subtitleFile: null,
      sourceName: file.name,
    }));
    setBatchItems((prev) => [...prev, ...newItems]);
    setAudioInputKey((key) => key + 1);
  };

  const handleAddBatchScript = () => {
    setBatchItems((prev) => [
      ...prev,
      {
        id: nextBatchId(),
        inputType: "script_url",
        inputValue: "",
        outputName: "",
        audioFile: null,
        subtitleFile: null,
        sourceName: "",
      },
    ]);
  };

  const handleStartDriveImport = async () => {
    const folderUrl = driveFolderUrl.trim();
    if (!folderUrl) {
      setErrorMessage("Nhap link Google Drive folder.");
      return;
    }
    setErrorMessage(null);
    setDriveWarningMessage(null);
    setDriveImportProgress(null);
    setIsStartingDriveImport(true);
    try {
      const response = await startStoryDriveAudioImport(folderUrl);
      setDriveImportSessionId(response.sessionId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the bat dau import Google Drive folder.");
    } finally {
      setIsStartingDriveImport(false);
    }
  };

  const updateBatchItem = (id: string, field: keyof BatchItem, value: string) => {
    setBatchItems((prev) => prev.map((item) => (item.id === id ? { ...item, [field]: value } : item)));
  };

  const updateBatchItemSubtitle = (id: string, file: File | null) => {
    setBatchItems((prev) => prev.map((item) => (item.id === id ? { ...item, subtitleFile: file } : item)));
  };

  const handleFontUpload = async (file: File | null) => {
    if (!file) return;
    setIsUploadingFont(true);
    setErrorMessage(null);
    try {
      const res = await uploadSubtitleFont(file);
      setSubtitleFonts(res.fonts);
      setSubtitleFont((current) => current || res.defaultFamily);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the upload font phu de.");
    } finally {
      setIsUploadingFont(false);
      setFontInputKey((key) => key + 1);
    }
  };

  const handleSubtitlePreview = async () => {
    setIsSubtitlePreviewing(true);
    setErrorMessage(null);
    try {
      const res = await generateSubtitlePreview({
        font: subtitleFont,
        presetId: subtitlePreset,
        maxCharsPerLine: clampInt(subtitleMaxCharsPerLine, 16, 60, 42),
        maxLines: clampInt(subtitleMaxLines, 1, 3, 2),
      });
      setSubtitlePreviewPath(res.previewPath);
      setSubtitlePreviewBust(Date.now());
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the render preview phu de.");
    } finally {
      setIsSubtitlePreviewing(false);
    }
  };

  const removeBatchItem = (id: string) => {
    setBatchItems((prev) => prev.filter((item) => item.id !== id));
  };

  const handleSubmit = async () => {
    setErrorMessage(null);
    resetProgress();

    if (mode === "single") {
      if (singleInput.inputType === "audio_file" && !singleInput.audioFile) {
        setErrorMessage("Chon file audio de upload.");
        return;
      }
      if (singleInput.inputType === "script_url" && !singleInput.inputValue.trim()) {
        setErrorMessage("Nhap URL script Google Drive.");
        return;
      }
      if (!singleInput.outputName.trim()) {
        setErrorMessage("Nhap ten output cho video.");
        return;
      }

      setIsSubmitting(true);
      try {
        const payload: CreateStoryVideoRequest = {
          inputType: singleInput.inputType,
          inputValue: singleInput.inputType === "script_url" ? singleInput.inputValue : singleInput.audioFile?.name ?? "",
          outputName: singleInput.outputName,
          clipTags: [],
          voiceId: voiceId || undefined,
        };
        if (singleSubtitleFile) {
          payload.subtitleFont = subtitleFont || undefined;
          payload.subtitlePreset = subtitlePreset;
          payload.subtitleMaxCharsPerLine = clampInt(subtitleMaxCharsPerLine, 16, 60, 42);
          payload.subtitleMaxLines = clampInt(subtitleMaxLines, 1, 3, 2);
        }
        const res = await createStoryVideo(payload, singleInput.audioFile ?? undefined, singleSubtitleFile ?? undefined);
        setStoryId(res.storyId);
      } catch (err) {
        setErrorMessage(err instanceof ApiError ? err.message : "Khong the bat dau render story video.");
        setIsSubmitting(false);
      }
      return;
    }

    if (!batchItems.length) {
      setErrorMessage("Them it nhat 1 item vao batch.");
      return;
    }
    if (isDriveImporting) {
      setErrorMessage("Cho import Google Drive folder hoan tat truoc khi render.");
      return;
    }
    for (let i = 0; i < batchItems.length; i += 1) {
      const item = batchItems[i];
      if (item.inputType === "audio_file" && !item.audioFile) {
        setErrorMessage(`Item #${i + 1}: Thieu file audio.`);
        return;
      }
      if (item.inputType === "script_url" && !item.inputValue.trim()) {
        setErrorMessage(`Item #${i + 1}: Nhap URL script.`);
        return;
      }
      if (item.inputType === "drive_audio" && !item.inputValue.trim()) {
        setErrorMessage(`Item #${i + 1}: Drive audio token khong hop le.`);
        return;
      }
      if (!item.outputName.trim()) {
        setErrorMessage(`Item #${i + 1}: Nhap ten output.`);
        return;
      }
    }

    await submitBatchItems(batchItems);
  };

  const handleRetryFailed = async () => {
    if (!batchId) return;
    setIsRetrying(true);
    setErrorMessage(null);
    try {
      const res = await retryStoryBatchFailed(batchId);
      setBatchProgress(null);
      setBatchId(res.batchId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the retry cac item that bai.");
      setIsRetrying(false);
    }
  };

  const handleCancelStory = async () => {
    if (!storyId) return;
    setIsCancellingStory(true);
    setErrorMessage(null);
    try {
      await cancelStoryVideo(storyId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the huy process.");
      setIsCancellingStory(false);
    }
  };

  const handleCancelBatch = async () => {
    if (!batchId) return;
    setIsCancellingBatch(true);
    setErrorMessage(null);
    try {
      await cancelStoryBatch(batchId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the huy batch.");
      setIsCancellingBatch(false);
    }
  };

  const handleCancelBatchItem = async (storyItemId: string) => {
    if (!batchId) return;
    setCancellingItemIds((current) => new Set(current).add(storyItemId));
    setErrorMessage(null);
    try {
      await cancelStoryBatchItem(batchId, storyItemId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the huy item.");
      setCancellingItemIds((current) => {
        const next = new Set(current);
        next.delete(storyItemId);
        return next;
      });
    }
  };

  const selectedSubtitleFont = subtitleFonts.find((font) => font.family === subtitleFont);
  const selectedSubtitlePreset = subtitlePresets.find((preset) => preset.id === subtitlePreset);
  const singleProgressPercent = storyProgress ? Math.round(Math.max(0, Math.min(100, storyProgress.percent))) : 0;
  const batchOverallPercent =
    batchProgress && batchProgress.totalItems > 0
      ? Math.round(batchProgress.items.reduce((sum, item) => sum + (item.percent ?? 0), 0) / batchProgress.totalItems)
      : 0;

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai cau hinh Story Video..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Story Video"
        title="Video Ke Chuyen"
        description="Render tu audio/script voi clip 5 giay, TV noise va song am mac dinh."
        stats={[
          { label: "Mode", value: mode === "single" ? "Single" : "Batch" },
          { label: "Clips", value: "Default library" },
          { label: "Overlays", value: "Global settings" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}
      {driveWarningMessage ? <StatusAlert title="Import Drive co canh bao" message={driveWarningMessage} /> : null}

      <PageSection>
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-2">
            <Button type="button" variant={mode === "single" ? "default" : "outline"} onClick={() => setMode("single")}>
              Single
            </Button>
            <Button type="button" variant={mode === "batch" ? "default" : "outline"} onClick={() => setMode("batch")}>
              Batch
            </Button>
          </div>
          <Button asChild variant="outline">
            <Link to="/story-video/settings">
              <Settings className="mr-2 size-4" />
              Cau hinh overlay
            </Link>
          </Button>
        </div>

        <div className="grid gap-5">
          <div className="grid gap-2 md:w-1/2">
            <Label htmlFor="voiceId">Voice cho TTS</Label>
            <select
              id="voiceId"
              value={voiceId || defaultVoiceId}
              onChange={(event) => setVoiceId(event.target.value)}
              className="h-10 rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value={defaultVoiceId}>{defaultVoiceId ? `Default (${defaultVoiceId})` : "TTS Default"}</option>
              {voices.map((voice) => (
                <option key={voice.voiceId} value={voice.voiceId}>
                  {voice.voiceName} - {voice.voiceId}
                </option>
              ))}
            </select>
          </div>

          <Separator />

          {mode === "single" ? (
            <SingleInputForm
              value={singleInput}
              audioInputKey={audioInputKey}
              onChange={setSingleInput}
              onAudioInputKeyChange={setAudioInputKey}
            />
          ) : (
            <BatchInputForm
              items={batchItems}
              audioInputKey={audioInputKey}
              onAddAudio={handleAddBatchAudio}
              onAddScript={handleAddBatchScript}
              onAddDriveFolder={() => setIsDriveDialogOpen(true)}
              onUpdate={updateBatchItem}
              onUpdateSubtitle={updateBatchItemSubtitle}
              onRemove={removeBatchItem}
              isDriveImporting={isDriveImporting}
            />
          )}

          <Separator />

          <div className="grid gap-4 rounded-lg border border-border/70 bg-background/70 p-4">
            <div className="space-y-1">
              <h3 className="text-sm font-semibold text-foreground">Phụ đề</h3>
              <p className="text-xs text-muted-foreground">
                Burn phụ đề từ file .srt vào video (hiển thị trên TV noise / sóng âm). Cấu hình chỉ áp dụng khi có file .srt.
              </p>
            </div>

            {mode === "single" ? (
              <div className="grid gap-2 md:w-1/2">
                <Label>File phụ đề (.srt, tùy chọn)</Label>
                <Input
                  key={subtitleInputKey}
                  type="file"
                  accept=".srt"
                  onChange={(event) => {
                    const file = event.currentTarget.files?.[0] ?? null;
                    setSingleSubtitleFile(file);
                    setSubtitleInputKey((key) => key + 1);
                  }}
                />
                {singleSubtitleFile ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span>Đã chọn: {singleSubtitleFile.name}</span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                      onClick={() => setSingleSubtitleFile(null)}
                    >
                      <Trash2 className="mr-1 size-3" />
                      Bỏ chọn
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Ở chế độ batch, chọn file .srt riêng cho từng item trong danh sách phía trên.
              </p>
            )}

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <div className="grid content-start gap-2">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor="subtitleFont">Font phụ đề</Label>
                  <div className="flex gap-1">
                    {selectedSubtitleFont?.supportsKorean ? (
                      <Badge variant="secondary" className="rounded-full text-[10px]">
                        KR
                      </Badge>
                    ) : null}
                    {selectedSubtitleFont?.supportsVietnamese ? (
                      <Badge variant="secondary" className="rounded-full text-[10px]">
                        VI
                      </Badge>
                    ) : null}
                  </div>
                </div>
                <select
                  id="subtitleFont"
                  value={subtitleFont}
                  onChange={(event) => setSubtitleFont(event.target.value)}
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                >
                  {subtitleFont && !subtitleFonts.some((font) => font.family === subtitleFont) ? (
                    <option value={subtitleFont}>{subtitleFont}</option>
                  ) : null}
                  {subtitleFonts.map((font) => (
                    <option key={font.family} value={font.family}>
                      {font.family}
                      {font.supportsKorean ? " [KR]" : ""}
                      {font.supportsVietnamese ? " [VI]" : ""}
                    </option>
                  ))}
                </select>
                <label className="cursor-pointer justify-self-start">
                  <Input
                    key={fontInputKey}
                    type="file"
                    accept=".ttf,.otf,.ttc"
                    className="hidden"
                    disabled={isUploadingFont}
                    onChange={(event) => void handleFontUpload(event.currentTarget.files?.[0] ?? null)}
                  />
                  <Button type="button" variant="outline" size="sm" asChild>
                    <span>
                      {isUploadingFont ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Plus className="mr-2 size-4" />}
                      {isUploadingFont ? "Đang thêm font..." : "Thêm font..."}
                    </span>
                  </Button>
                </label>
              </div>

              <div className="grid content-start gap-2">
                <Label htmlFor="subtitlePreset">Hiệu ứng phụ đề</Label>
                <select
                  id="subtitlePreset"
                  value={subtitlePreset}
                  onChange={(event) => setSubtitlePreset(event.target.value)}
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                >
                  {subtitlePresets.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.name}
                    </option>
                  ))}
                </select>
                {selectedSubtitlePreset?.description ? (
                  <p className="text-xs text-muted-foreground">{selectedSubtitlePreset.description}</p>
                ) : null}
              </div>

              <div className="grid content-start gap-2">
                <Label htmlFor="subtitleMaxChars">Ký tự tối đa/dòng</Label>
                <Input
                  id="subtitleMaxChars"
                  type="number"
                  min={16}
                  max={60}
                  step={1}
                  value={subtitleMaxCharsPerLine}
                  onChange={(event) => setSubtitleMaxCharsPerLine(event.target.value)}
                />
              </div>

              <div className="grid content-start gap-2">
                <Label htmlFor="subtitleMaxLines">Số dòng tối đa</Label>
                <Input
                  id="subtitleMaxLines"
                  type="number"
                  min={1}
                  max={3}
                  step={1}
                  value={subtitleMaxLines}
                  onChange={(event) => setSubtitleMaxLines(event.target.value)}
                />
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-[1fr_minmax(280px,420px)]">
              <div>
                <Button type="button" variant="outline" onClick={() => void handleSubtitlePreview()} disabled={isSubtitlePreviewing}>
                  {isSubtitlePreviewing ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Eye className="mr-2 size-4" />}
                  {isSubtitlePreviewing ? "Đang render..." : "Xem thử phụ đề"}
                </Button>
              </div>
              {subtitlePreviewPath ? (
                <video
                  src={`/media/${subtitlePreviewPath}${subtitlePreviewBust ? `?t=${subtitlePreviewBust}` : ""}`}
                  controls
                  autoPlay
                  loop
                  muted
                  className="aspect-video w-full self-start rounded-md bg-black object-contain"
                />
              ) : (
                <div className="flex aspect-video w-full items-center justify-center self-start rounded-md border border-dashed border-border bg-background/50 text-xs text-muted-foreground">
                  Chưa có preview phụ đề — bấm Xem thử phụ đề
                </div>
              )}
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <Button type="button" onClick={() => void handleSubmit()} disabled={isSubmitting || isDriveImporting}>
              {isSubmitting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Upload className="mr-2 size-4" />}
              Bat dau render
            </Button>
          </div>
        </div>
      </PageSection>

      {(isSubmitting || storyProgress || batchProgress) ? (
        <PageSection>
          {mode === "single" ? (
            <SingleProgress
              progress={storyProgress}
              percent={singleProgressPercent}
              isSubmitting={isSubmitting}
              isCancelling={isCancellingStory}
              onCancel={() => void handleCancelStory()}
            />
          ) : (
            <BatchProgressView
              progress={batchProgress}
              percent={batchOverallPercent}
              isSubmitting={isSubmitting}
              isRetrying={isRetrying}
              isCancellingBatch={isCancellingBatch}
              cancellingItemIds={cancellingItemIds}
              onRetry={() => void handleRetryFailed()}
              onCancelBatch={() => void handleCancelBatch()}
              onCancelItem={(storyItemId) => void handleCancelBatchItem(storyItemId)}
            />
          )}
        </PageSection>
      ) : null}

      <Dialog
        open={isDriveDialogOpen}
        onOpenChange={(open) => {
          if (!isDriveImporting) setIsDriveDialogOpen(open);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Import audio tu Google Drive folder</DialogTitle>
            <DialogDescription>
              Folder phai public. Tai xong audio trong folder goc, he thong se tu dong bat dau render batch.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <Label htmlFor="driveFolderUrl">Google Drive folder URL</Label>
            <Input
              id="driveFolderUrl"
              placeholder="https://drive.google.com/drive/folders/..."
              value={driveFolderUrl}
              onChange={(event) => setDriveFolderUrl(event.target.value)}
              disabled={isDriveImporting}
            />
            {driveImportProgress ? (
              <div className="grid gap-2 rounded-md border border-border/70 bg-muted/30 p-3 text-sm">
                <div className="flex items-center gap-2 text-muted-foreground">
                  {isDriveImporting ? <Loader2 className="size-4 animate-spin text-primary" /> : null}
                  <span>{driveImportProgress.message}</span>
                </div>
                <span className="text-xs text-muted-foreground">
                  Da xu ly: {driveImportProgress.current}/{driveImportProgress.total}
                </span>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="button" onClick={() => void handleStartDriveImport()} disabled={isDriveImporting}>
              {isDriveImporting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Download className="mr-2 size-4" />}
              {isDriveImporting ? "Dang import..." : "Tai audio va render"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}

function SingleInputForm({
  value,
  audioInputKey,
  onChange,
  onAudioInputKeyChange,
}: {
  value: SingleInput;
  audioInputKey: number;
  onChange: (next: SingleInput | ((current: SingleInput) => SingleInput)) => void;
  onAudioInputKeyChange: (next: number | ((current: number) => number)) => void;
}) {
  return (
    <div className="grid gap-4">
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          variant={value.inputType === "audio_file" ? "default" : "outline"}
          onClick={() => onChange((prev) => ({ ...prev, inputType: "audio_file", inputValue: "" }))}
        >
          <Upload className="mr-2 size-4" />
          Upload Audio
        </Button>
        <Button
          type="button"
          size="sm"
          variant={value.inputType === "script_url" ? "default" : "outline"}
          onClick={() => onChange((prev) => ({ ...prev, inputType: "script_url", audioFile: null }))}
        >
          Script URL
        </Button>
      </div>

      {value.inputType === "audio_file" ? (
        <div className="grid gap-2">
          <Label>File audio</Label>
          <Input
            key={audioInputKey}
            type="file"
            accept=".mp3,.wav,.m4a,.aac,.flac,.ogg"
            onChange={(event) => {
              const file = event.currentTarget.files?.[0] ?? null;
              onChange((prev) => ({
                ...prev,
                audioFile: file,
                inputValue: file?.name ?? "",
                outputName: prev.outputName || (file ? outputNameFromFileName(file.name) : ""),
              }));
              onAudioInputKeyChange((key) => key + 1);
            }}
          />
          {value.audioFile ? <p className="text-xs text-muted-foreground">Da chon: {value.audioFile.name}</p> : null}
        </div>
      ) : (
        <div className="grid gap-2">
          <Label>Google Drive Script URL</Label>
          <Input
            placeholder="https://docs.google.com/document/d/..."
            value={value.inputValue}
            onChange={(event) => onChange((prev) => ({ ...prev, inputValue: event.target.value }))}
          />
        </div>
      )}

      <div className="grid gap-2 md:w-1/2">
        <Label>Ten output</Label>
        <Input
          placeholder="VD: cau-chuyen-1"
          value={value.outputName}
          onChange={(event) => onChange((prev) => ({ ...prev, outputName: event.target.value }))}
        />
      </div>
    </div>
  );
}

function BatchInputForm({
  items,
  audioInputKey,
  onAddAudio,
  onAddScript,
  onAddDriveFolder,
  onUpdate,
  onUpdateSubtitle,
  onRemove,
  isDriveImporting,
}: {
  items: BatchItem[];
  audioInputKey: number;
  onAddAudio: (files: FileList | null) => void;
  onAddScript: () => void;
  onAddDriveFolder: () => void;
  onUpdate: (id: string, field: keyof BatchItem, value: string) => void;
  onUpdateSubtitle: (id: string, file: File | null) => void;
  onRemove: (id: string) => void;
  isDriveImporting: boolean;
}) {
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">Batch items ({items.length})</h3>
        <div className="flex flex-wrap gap-2">
          <label className="cursor-pointer">
            <Input
              key={audioInputKey}
              type="file"
              accept=".mp3,.wav,.m4a,.aac,.flac,.ogg"
              multiple
              className="hidden"
              onChange={(event) => onAddAudio(event.currentTarget.files)}
            />
            <Button type="button" variant="outline" size="sm" asChild>
              <span>
                <Plus className="mr-2 size-4" />
                Audio File
              </span>
            </Button>
          </label>
          <Button type="button" variant="outline" size="sm" onClick={onAddScript}>
            <Plus className="mr-2 size-4" />
            Script URL
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={onAddDriveFolder} disabled={isDriveImporting}>
            {isDriveImporting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Download className="mr-2 size-4" />}
            Drive Folder
          </Button>
        </div>
      </div>

      {items.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">Chua co item nao.</p>
      ) : (
        <div className="grid gap-3">
          {items.map((item, index) => (
            <div key={item.id} className="relative grid gap-3 rounded-lg border border-border/70 bg-card/50 p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-muted-foreground">#{index + 1}</span>
                  <Badge variant="secondary" className="rounded-full text-[10px]">
                    {item.inputType === "audio_file" ? "Audio" : item.inputType === "drive_audio" ? "Drive Audio" : "Script"}
                  </Badge>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs text-destructive hover:text-destructive"
                  onClick={() => onRemove(item.id)}
                >
                  <Trash2 className="mr-1 size-3" />
                  Xoa
                </Button>
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                <div className="grid gap-1.5">
                  <Label className="text-xs">{item.inputType === "script_url" ? "Script URL" : "File audio"}</Label>
                  {item.inputType === "script_url" ? (
                    <Input
                      placeholder="https://docs.google.com/document/d/..."
                      value={item.inputValue}
                      onChange={(event) => onUpdate(item.id, "inputValue", event.target.value)}
                    />
                  ) : (
                    <Input value={item.sourceName} readOnly />
                  )}
                </div>
                <div className="grid gap-1.5">
                  <Label className="text-xs">Ten output</Label>
                  <Input
                    placeholder="VD: story-1"
                    value={item.outputName}
                    onChange={(event) => onUpdate(item.id, "outputName", event.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label className="text-xs">Phụ đề (.srt, tùy chọn)</Label>
                  <Input
                    type="file"
                    accept=".srt"
                    onChange={(event) => onUpdateSubtitle(item.id, event.currentTarget.files?.[0] ?? null)}
                  />
                  {item.subtitleFile ? (
                    <p className="text-xs text-muted-foreground">Đã chọn: {item.subtitleFile.name}</p>
                  ) : null}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SingleProgress({
  progress,
  percent,
  isSubmitting,
  isCancelling,
  onCancel,
}: {
  progress: StoryVideoProgress | null;
  percent: number;
  isSubmitting: boolean;
  isCancelling: boolean;
  onCancel: () => void;
}) {
  if (!progress && isSubmitting) {
    return (
      <div className="flex items-center gap-3 py-4 text-sm text-muted-foreground">
        <Loader2 className="size-5 animate-spin text-primary" />
        <span>Dang khoi tao render...</span>
      </div>
    );
  }
  if (!progress) return null;

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-sm text-muted-foreground">Trang thai: </span>
          <span className="font-semibold text-foreground">{progress.status}</span>
        </div>
        <span className="text-2xl font-semibold text-foreground">{percent}%</span>
      </div>
      <div className="h-3 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            progress.status === "completed" ? "bg-green-500" : progress.status === "failed" ? "bg-destructive" : progress.status === "cancelled" ? "bg-muted-foreground" : "bg-primary"
          }`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="truncate">{progress.message}</span>
        <span className="whitespace-nowrap">{stageLabel(progress.stage)}</span>
      </div>
      {progress.error ? <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{progress.error}</div> : null}
      {progress.status === "pending" || progress.status === "running" || progress.status === "processing" || progress.status === "cancelling" ? (
        <div>
          <Button type="button" variant="destructive" onClick={onCancel} disabled={isCancelling || progress.status === "cancelling"}>
            {isCancelling || progress.status === "cancelling" ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Square className="mr-2 size-4" />}
            {isCancelling || progress.status === "cancelling" ? "Dang huy..." : "Huy process"}
          </Button>
        </div>
      ) : null}
      {progress.status === "completed" && progress.result?.videoPath ? (
        <div className="flex gap-2">
          <Button asChild variant="outline">
            <a href={`/media/${progress.result.videoPath}`} target="_blank" rel="noopener noreferrer">
              Preview
            </a>
          </Button>
          <Button asChild>
            <a href={`/media/${progress.result.videoPath}`} download={`${progress.outputName}.mp4`}>
              <Download className="mr-2 size-4" />
              Download
            </a>
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function BatchProgressView({
  progress,
  percent,
  isSubmitting,
  isRetrying,
  isCancellingBatch,
  cancellingItemIds,
  onRetry,
  onCancelBatch,
  onCancelItem,
}: {
  progress: StoryBatchProgress | null;
  percent: number;
  isSubmitting: boolean;
  isRetrying: boolean;
  isCancellingBatch: boolean;
  cancellingItemIds: Set<string>;
  onRetry: () => void;
  onCancelBatch: () => void;
  onCancelItem: (storyItemId: string) => void;
}) {
  if (!progress && isSubmitting) {
    return (
      <div className="flex items-center gap-3 py-4 text-sm text-muted-foreground">
        <Loader2 className="size-5 animate-spin text-primary" />
        <span>Dang khoi tao batch render...</span>
      </div>
    );
  }
  if (!progress) return null;

  return (
    <div className="grid gap-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-foreground">Batch: {progress.batchId}</h3>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Trang thai: <span className="font-medium text-foreground">{progress.status}</span> | Hoan tat:{" "}
            {progress.completedItems}/{progress.totalItems} | Da huy: {progress.cancelledItems}
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          {progress.status === "pending" || progress.status === "processing" || progress.status === "cancelling" ? (
            <Button type="button" variant="destructive" size="sm" onClick={onCancelBatch} disabled={isCancellingBatch || progress.status === "cancelling"}>
              {isCancellingBatch || progress.status === "cancelling" ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Square className="mr-2 size-4" />}
              {isCancellingBatch || progress.status === "cancelling" ? "Dang huy batch..." : "Huy batch"}
            </Button>
          ) : null}
          <div className="text-2xl font-semibold text-foreground">{percent}%</div>
        </div>
      </div>

      <div className="h-3 w-full overflow-hidden rounded-full bg-muted">
        <div className={`h-full rounded-full transition-all duration-500 ${progress.status === "cancelled" ? "bg-muted-foreground" : "bg-primary"}`} style={{ width: `${percent}%` }} />
      </div>

      <div className="grid gap-3">
        {progress.items.map((item) => (
          <BatchItemProgressCard
            key={item.id}
            item={item}
            isCancelling={cancellingItemIds.has(item.id)}
            onCancel={() => onCancelItem(item.id)}
          />
        ))}
      </div>

      {progress.failedItems > 0 && (progress.status === "completed" || progress.status === "failed") ? (
        <Button onClick={onRetry} disabled={isRetrying}>
          {isRetrying ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RotateCcw className="mr-2 size-4" />}
          Retry {progress.failedItems} items that bai
        </Button>
      ) : null}

      {progress.items.filter((item) => item.status === "completed" && item.result?.videoPath).length > 0 ? (
        <div className="grid gap-3">
          <h3 className="text-sm font-semibold text-foreground">Video da hoan tat</h3>
          {progress.items
            .filter((item) => item.status === "completed" && item.result?.videoPath)
            .map((item) => (
              <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/70 bg-card/80 p-4">
                <span className="font-semibold text-foreground">{item.outputName}.mp4</span>
                <div className="flex gap-2">
                  <Button asChild variant="outline" size="sm">
                    <a href={`/media/${item.result!.videoPath}`} target="_blank" rel="noopener noreferrer">
                      Preview
                    </a>
                  </Button>
                  <Button asChild size="sm">
                    <a href={`/media/${item.result!.videoPath}`} download={`${item.outputName}.mp4`}>
                      Download
                    </a>
                  </Button>
                </div>
              </div>
            ))}
        </div>
      ) : null}
    </div>
  );
}

function BatchItemProgressCard({
  item,
  isCancelling,
  onCancel,
}: {
  item: StoryBatchItemProgress;
  isCancelling: boolean;
  onCancel: () => void;
}) {
  const pct = Math.round(Math.max(0, Math.min(100, item.percent ?? 0)));
  const barColorClass =
    item.status === "completed" ? "bg-green-500" : item.status === "failed" ? "bg-destructive" : item.status === "cancelled" ? "bg-muted-foreground" : "bg-primary";

  return (
    <div className="rounded-lg border border-border/70 bg-background/70 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="w-20 text-xs font-semibold text-muted-foreground">
            {item.status === "completed"
              ? "Done"
              : item.status === "failed"
                ? "Failed"
                : item.status === "cancelled"
                  ? "Cancelled"
                  : item.status === "cancelling"
                    ? "Cancelling"
                    : item.status === "processing"
                      ? "Running"
                      : "Waiting"}
          </span>
          <span className="truncate text-sm font-semibold text-foreground">{item.outputName}</span>
        </div>
        <div className="flex items-center gap-2">
          {item.status === "pending" || item.status === "processing" || item.status === "cancelling" ? (
            <Button type="button" variant="destructive" size="sm" className="h-7 text-xs" onClick={onCancel} disabled={isCancelling || item.status === "cancelling"}>
              {isCancelling || item.status === "cancelling" ? <Loader2 className="mr-1 size-3 animate-spin" /> : <Square className="mr-1 size-3" />}
              {isCancelling || item.status === "cancelling" ? "Dang huy" : "Huy item"}
            </Button>
          ) : null}
          <span className="whitespace-nowrap text-sm font-semibold text-foreground">{pct}%</span>
        </div>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
        <div className={`h-full rounded-full transition-all duration-300 ${barColorClass}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="truncate">{item.message || ""}</span>
        {item.stage ? <span className="whitespace-nowrap">{stageLabel(item.stage)}</span> : null}
      </div>
      {item.error ? <div className="mt-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{item.error}</div> : null}
    </div>
  );
}
