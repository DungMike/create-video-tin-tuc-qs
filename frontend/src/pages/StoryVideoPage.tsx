import { ArrowDownToLine, ChevronDown, ChevronRight, Clapperboard, Download, Eye, FolderSearch, FolderUp, HardDrive, Loader2, Plus, RotateCcw, Save, Settings, Square, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { ChapterFileHelp } from "@/components/chapter-file-help";
import { ClipUsageModeField } from "@/components/ClipUsageModeField";
import { ColorInput } from "@/components/color-input";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { StoryIntroSelect } from "@/components/StoryIntroSelect";
import { StoryLibrarySelect } from "@/components/StoryLibrarySelect";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { useActiveStoryLibrary } from "@/hooks/useActiveStoryLibrary";
import { useClipUsageMode } from "@/hooks/useClipUsageMode";
import {
  ApiError,
  cancelStoryBatch,
  cancelStoryBatchItem,
  cancelStoryVideo,
  createStoryBatch,
  createStoryVideo,
  createSubtitleStyle,
  deleteSubtitleStyle,
  generateSubtitlePreview,
  getCtaOverlays,
  getStoryBatchProgress,
  getOutputDrives,
  getStoryBatchQueue,
  getStoryDriveAudioImport,
  getDecorImages,
  getEditStyles,
  getStoryIntros,
  getWaveformOverlays,
  getStoryVideoProgress,
  getSubtitleFonts,
  getSubtitlePresets,
  getSubtitleStyles,
  getVoices,
  purgeUsedDecorImages,
  restoreBuiltinSubtitleStyles,
  retryStoryBatchFailed,
  scanLocalAudioFolder,
  startStoryDriveAudioImport,
  uploadSubtitleFont,
} from "@/lib/api";
import type {
  OutputDriveOption,
  CreateStoryBatchItem,
  CreateStoryBatchRequest,
  CreateStoryVideoRequest,
  CtaOverlay,
  DriveAudioImportProgress,
  EditStyleRecord,
  EditStyleType,
  StoryBatchItemProgress,
  StoryBatchProgress,
  StoryBatchQueue,
  StoryBatchQueueEntry,
  StoryDecorImage,
  StoryIntro,
  StoryVideoProgress,
  SubtitleFontInfo,
  SubtitlePresetInfo,
  SubtitleStyle,
  VoiceRecord,
  WaveformOverlay,
} from "@/types/api";

type StoryMode = "single" | "batch";
type StoryInputType = "audio_file" | "script_url";
type BatchInputType = StoryInputType | "drive_audio";
type SubtitleFontLang = "all" | "vi" | "id" | "th";

const SUBTITLE_FONT_LANGS: { value: SubtitleFontLang; label: string }[] = [
  { value: "all", label: "Tất cả" },
  { value: "vi", label: "Việt [VI]" },
  { value: "id", label: "Indonesia [ID]" },
  { value: "th", label: "Thái [TH]" },
];

function subtitleFontLangMatcher(lang: SubtitleFontLang): (font: SubtitleFontInfo) => boolean {
  switch (lang) {
    case "vi":
      return (font) => font.supportsVietnamese;
    case "id":
      return (font) => font.supportsIndonesian;
    case "th":
      return (font) => font.supportsThai;
    default:
      return () => true;
  }
}

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
  /** Absolute path on the render machine — set when the item came from a local folder
   *  scan, so the file is read in place instead of uploaded. */
  audioPath?: string;
  subtitlePath?: string;
  subtitleName?: string;
  /** `<tên audio>.chapters.txt`: file đã chọn/ghép, hoặc đường dẫn khi quét thư mục local. */
  chaptersFile?: File | null;
  chaptersPath?: string;
  chaptersName?: string;
}

let batchIdCounter = 0;

function formatGigabytes(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(0)} GB`;
}

function nextBatchId(): string {
  batchIdCounter += 1;
  return `batch_item_${batchIdCounter}`;
}

function outputNameFromFileName(filename: string): string {
  return filename.replace(/\.[^/.]+$/, "").trim() || "story-item";
}

const AUDIO_EXTENSIONS = ["mp3", "wav", "m4a", "aac", "flac", "ogg"];

function fileExtension(name: string): string {
  const base = name.split(/[\\/]/).pop() ?? name;
  const dot = base.lastIndexOf(".");
  return dot >= 0 ? base.slice(dot + 1).toLowerCase() : "";
}

function isAudioFileName(name: string): boolean {
  return AUDIO_EXTENSIONS.includes(fileExtension(name));
}

function isSubtitleFileName(name: string): boolean {
  return fileExtension(name) === "srt";
}

// File chương đi kèm audio: `<tên audio>.chapters.txt`, ghép theo cùng luật với .srt.
const CHAPTERS_SUFFIX_RE = /\.chapters\.txt$/i;

function isChaptersFileName(name: string): boolean {
  return CHAPTERS_SUFFIX_RE.test(name);
}

// Pair audio with its .srt by same base name within the same folder.
// webkitRelativePath carries the folder when a directory is picked; for a flat
// multi-select it is empty, so all selected files share one (root) folder key.
function subtitlePairKey(file: File, suffix: RegExp = /\.[^/.]+$/): string {
  const rel = (file as { webkitRelativePath?: string }).webkitRelativePath || file.name;
  const segments = rel.split(/[\\/]/);
  const base = segments.pop() ?? rel;
  const folder = segments.join("/");
  const stem = base.replace(suffix, "").toLowerCase();
  return `${folder}\u0000${stem}`;
}

// Exact stem first; otherwise the longest companion whose stem prefixes the audio
// stem at a separator (`k10a_full.srt` ↔ `k10a_full-an-thai-kenh1.mp3`, never `k1a` ↔ `k10a`).
// Mirrors _match_companion_stem in story_video_routes.py.
function findCompanionFile(map: Map<string, File>, audio: File): File | null {
  const key = subtitlePairKey(audio);
  const exact = map.get(key);
  if (exact) return exact;
  let best: string | null = null;
  for (const candidate of map.keys()) {
    if (
      candidate.length < key.length &&
      key.startsWith(candidate) &&
      "-_ .".includes(key[candidate.length]) &&
      (best === null || candidate.length > best.length)
    ) {
      best = candidate;
    }
  }
  return best === null ? null : map.get(best) ?? null;
}

function clampInt(value: string, min: number, max: number, fallback: number): number {
  const num = Number(value);
  if (!Number.isFinite(num)) return fallback;
  return Math.min(max, Math.max(min, Math.round(num)));
}

const BATCH_DONE_STATUSES = new Set<StoryBatchProgress["status"]>(["completed", "failed", "cancelled"]);

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

const decorGroupOf = (item: StoryDecorImage) => (item.group ?? "").trim();

/** Ten cac khung TV, "chua phan nhom" luon xuong cuoi. */
const decorGroupNamesOf = (images: StoryDecorImage[]) => {
  const seen: string[] = [];
  for (const item of images) {
    const group = decorGroupOf(item);
    if (!seen.includes(group)) seen.push(group);
  }
  return seen.sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b, "vi")));
};

/** Mỗi ảnh decor chỉ dùng cho 1 video: dùng rồi là ra khỏi vòng xoay vĩnh viễn. */
const decorIsUsed = (item: StoryDecorImage) => item.used === true;

/** Ảnh còn dùng được của một khung: đang bật và chưa dùng lần nào. */
const decorAvailableIdsOf = (images: StoryDecorImage[], group: string) =>
  images
    .filter((item) => decorGroupOf(item) === group && item.enabled !== false && !decorIsUsed(item))
    .map((item) => item.id);

const decorGroupLabel = (group: string) => group || "Chưa phân nhóm";

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
  const [singleChaptersFile, setSingleChaptersFile] = useState<File | null>(null);
  const [subtitleInputKey, setSubtitleInputKey] = useState(0);
  const [subtitleFonts, setSubtitleFonts] = useState<SubtitleFontInfo[]>([]);
  const [subtitlePresets, setSubtitlePresets] = useState<SubtitlePresetInfo[]>([]);
  const [subtitleFont, setSubtitleFont] = useState("");
  const [subtitleFontLang, setSubtitleFontLang] = useState<SubtitleFontLang>("all");
  const [subtitlePreset, setSubtitlePreset] = useState("clean");
  const [subtitleMaxCharsPerLine, setSubtitleMaxCharsPerLine] = useState("42");
  const [subtitleMaxLines, setSubtitleMaxLines] = useState("2");
  const [subtitleFontScale, setSubtitleFontScale] = useState("1");
  // Màu phụ đề: "" nghĩa là chưa đụng tới -> không gửi lên, preset giữ nguyên màu
  // của nó. Chỉ ô nào người dùng đổi mới đè lên preset.
  const [subtitleTextColor, setSubtitleTextColor] = useState("");
  const [subtitleOutlineColor, setSubtitleOutlineColor] = useState("");
  const [subtitleOutlineWidth, setSubtitleOutlineWidth] = useState("");
  const [subtitleBackgroundEnabled, setSubtitleBackgroundEnabled] = useState(false);
  const [subtitleBackColor, setSubtitleBackColor] = useState("");
  const [subtitleBackOpacity, setSubtitleBackOpacity] = useState("50");
  // Danh sách cấu hình phụ đề đã lưu. Ở batch, chọn nhiều cái thì mỗi video bốc
  // một cái (bộ bài xáo như sóng âm/CTA); không chọn = cả batch dùng form bên dưới.
  const [subtitleStyles, setSubtitleStyles] = useState<SubtitleStyle[]>([]);
  const [hiddenBuiltinStyleCount, setHiddenBuiltinStyleCount] = useState(0);
  const [selectedSubtitleStyleIds, setSelectedSubtitleStyleIds] = useState<string[]>([]);
  const [newSubtitleStyleName, setNewSubtitleStyleName] = useState("");
  const [isSavingSubtitleStyle, setIsSavingSubtitleStyle] = useState(false);
  const [fontInputKey, setFontInputKey] = useState(0);
  const [isUploadingFont, setIsUploadingFont] = useState(false);
  const [isSubtitlePreviewing, setIsSubtitlePreviewing] = useState(false);
  const [subtitlePreviewPath, setSubtitlePreviewPath] = useState<string | null>(null);
  const [subtitlePreviewBust, setSubtitlePreviewBust] = useState(0);
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [defaultVoiceId, setDefaultVoiceId] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const {
    libraries,
    activeId: activeLibraryId,
    setActiveId: setActiveLibraryId,
    refresh: refreshLibraries,
    selectedIds: selectedLibraryIds,
    selectedLibraries,
    setSelectedIds: setSelectedLibraryIds,
  } = useActiveStoryLibrary();
  const [intros, setIntros] = useState<StoryIntro[]>([]);
  // "none" = không có intro (mặc định), còn lại = intro id.
  const [introId, setIntroId] = useState("none");
  // Optimize mode: khi bật, batch tạm dừng các app cạnh tranh (browser farm) và ưu tiên
  // CPU cho render; tắt (mặc định) thì render chạy song song với mọi thứ như bình thường.
  const [optimizeMode, setOptimizeMode] = useState(false);
  // Ổ đĩa lưu video output của batch; "" cho tới khi backend trả về ổ mặc định (E).
  const [outputDrives, setOutputDrives] = useState<OutputDriveOption[]>([]);
  const [outputDrive, setOutputDrive] = useState("");
  // Bỏ qua bước hiệu ứng TV khi render: dùng cho thư viện chưa bake hiệu ứng nhưng
  // chỉ cần video thô (clip + sóng âm/CTA + phụ đề). Thư viện đã bake luôn tự bỏ qua.
  const [skipTvEffect, setSkipTvEffect] = useState(false);
  // "reuse" (mặc định) = chọn clip như cũ; "once" = mỗi clip 1 lần (cần MongoDB).
  const [clipUsageMode, setClipUsageMode] = useClipUsageMode();
  const [storyId, setStoryId] = useState<string | null>(null);
  const [storyProgress, setStoryProgress] = useState<StoryVideoProgress | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<StoryBatchProgress | null>(null);
  // Hàng đợi batch: mỗi lúc backend chỉ render 1 batch, các batch sau chờ tới lượt.
  const [batchQueue, setBatchQueue] = useState<StoryBatchQueue | null>(null);
  const [cancellingQueueIds, setCancellingQueueIds] = useState<Set<string>>(new Set());
  const [queueInfoMessage, setQueueInfoMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [isCancellingStory, setIsCancellingStory] = useState(false);
  const [isCancellingBatch, setIsCancellingBatch] = useState(false);
  const [cancellingItemIds, setCancellingItemIds] = useState<Set<string>>(new Set());
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [driveWarningMessage, setDriveWarningMessage] = useState<string | null>(null);
  const [pairingInfoMessage, setPairingInfoMessage] = useState<string | null>(null);
  const [decorImages, setDecorImages] = useState<StoryDecorImage[]>([]);
  const [selectedDecorIds, setSelectedDecorIds] = useState<string[]>([]);
  const [isPurgingDecor, setIsPurgingDecor] = useState(false);
  const [decorPurgeMessage, setDecorPurgeMessage] = useState<string | null>(null);
  // Kiểu dựng: bố cục chọn ở đây xoay vòng qua các video (mỗi video một bố cục),
  // hiệu ứng bổ trợ tick ở đây áp cho mọi video. Bản ghi đang tắt không hiện ra.
  const [editTypes, setEditTypes] = useState<EditStyleType[]>([]);
  const [editStyles, setEditStyles] = useState<EditStyleRecord[]>([]);
  const [selectedLayoutIds, setSelectedLayoutIds] = useState<string[]>([]);
  const [selectedModifierIds, setSelectedModifierIds] = useState<string[]>([]);
  // Khung TV dang mo. Moi loai video (den chua, lang que, dieu tra pha an...)
  // chi xoay vong trong dung mot nhom, nen day la mot lua chon don.
  const [decorGroup, setDecorGroup] = useState<string | null>(null);
  // Song am / CTA tham gia vong xoay cua batch. Khong chon gi = giu hanh vi cu
  // (waveform mac dinh + CTA dang bat o trang cau hinh).
  const [waveformOverlays, setWaveformOverlays] = useState<WaveformOverlay[]>([]);
  const [selectedWaveformIds, setSelectedWaveformIds] = useState<string[]>([]);
  const [ctaOverlays, setCtaOverlays] = useState<CtaOverlay[]>([]);
  const [selectedCtaIds, setSelectedCtaIds] = useState<string[]>([]);
  const [isDriveDialogOpen, setIsDriveDialogOpen] = useState(false);
  const [driveFolderUrl, setDriveFolderUrl] = useState("");
  const [isLocalFolderDialogOpen, setIsLocalFolderDialogOpen] = useState(false);
  const [localFolderPath, setLocalFolderPath] = useState("");
  const [isScanningLocalFolder, setIsScanningLocalFolder] = useState(false);
  const [isStartingDriveImport, setIsStartingDriveImport] = useState(false);
  const [driveImportSessionId, setDriveImportSessionId] = useState<string | null>(null);
  const [driveImportProgress, setDriveImportProgress] = useState<DriveAudioImportProgress | null>(null);
  const isDriveImporting = isStartingDriveImport || Boolean(driveImportSessionId);

  // A fully-baked library has the waveform/CTA burned into its clips, so those
  // would end up shrunk inside the TV screen instead of on top of the photo —
  // the backend rejects that combination, so the option is closed off here too.
  // One such library anywhere in the selection is enough: its clips land in the
  // same pool as the rest.
  // Cung mot dieu kien, hai he qua: anh decor bi backend tu choi, con vong xoay
  // song am/CTA thi chi vo nghia (pipeline bo qua han buoc overlay).
  const hasFullyBakedLibrary = selectedLibraries.some((lib) => lib.fullyBaked);
  const decorBlockedByLibrary = hasFullyBakedLibrary;
  const hasBakedLibrary = selectedLibraries.some((lib) => lib.styled || lib.fullyBaked);
  const hasUnbakedLibrary = selectedLibraries.some((lib) => !lib.styled && !lib.fullyBaked);
  const mixedBakeSelection = hasBakedLibrary && hasUnbakedLibrary;
  const selectedClipCount = selectedLibraries.reduce((total, lib) => total + lib.clipCount, 0);
  const emptySelectedLibraries = selectedLibraries.filter((lib) => lib.clipCount === 0);
  const editTypeById = useMemo(() => new Map(editTypes.map((type) => [type.id, type])), [editTypes]);
  const layoutChoices = useMemo(
    () => editStyles.filter((style) => style.group === "layout" && style.enabled),
    [editStyles],
  );
  const modifierChoices = useMemo(
    () => editStyles.filter((style) => style.group === "modifier" && style.enabled),
    [editStyles],
  );
  // Cùng luật với ảnh decor: bố cục thu nhỏ khung hình sẽ thu cả sóng âm/CTA đã
  // bake vào clip, nên backend từ chối (layout_baked_library_conflict).
  const layoutBlockedReason = useCallback(
    (style: EditStyleRecord) =>
      hasFullyBakedLibrary && editTypeById.get(style.type)?.shrinksFrame
        ? "Thư viện đã bake sẵn sóng âm/CTA"
        : "",
    [hasFullyBakedLibrary, editTypeById],
  );
  // Thư viện bake đủ bỏ qua bước overlay, nên hiệu ứng làm trên sóng âm/CTA vô tác dụng.
  const modifierBlockedReason = useCallback(
    (style: EditStyleRecord) =>
      hasFullyBakedLibrary && editTypeById.get(style.type)?.needsOverlayPass
        ? "Thư viện đã bake sẵn sóng âm/CTA"
        : "",
    [hasFullyBakedLibrary, editTypeById],
  );
  const pickedLayouts = useMemo(
    () => layoutChoices.filter((style) => selectedLayoutIds.includes(style.id) && !layoutBlockedReason(style)),
    [layoutChoices, selectedLayoutIds, layoutBlockedReason],
  );
  const decorLayoutPicked = pickedLayouts.some((style) => editTypeById.get(style.type)?.requiresDecor);
  const decorEnabled = decorLayoutPicked && !decorBlockedByLibrary;
  const activeDecorIds = useMemo(
    () => selectedDecorIds.filter((id) => decorImages.some((item) => item.id === id && !decorIsUsed(item))),
    [selectedDecorIds, decorImages],
  );
  // Bố cục cần decor mà chưa chọn ảnh nào thì đứng ngoài vòng xoay (backend sẽ
  // trả layout_needs_decor nếu gửi lên).
  const activeLayoutIds = useMemo(
    () =>
      pickedLayouts
        .filter((style) => !editTypeById.get(style.type)?.requiresDecor || activeDecorIds.length > 0)
        .map((style) => style.id),
    [pickedLayouts, editTypeById, activeDecorIds],
  );
  const activeModifierIds = useMemo(
    () =>
      modifierChoices
        .filter((style) => selectedModifierIds.includes(style.id) && !modifierBlockedReason(style))
        .map((style) => style.id),
    [modifierChoices, selectedModifierIds, modifierBlockedReason],
  );
  // useMemo (khong phai bieu thuc thuong nhu activeDecorIds) vi nhanh "bi chan"
  // tra ve mang moi moi lan render, lam deps cua submitBatchItems doi lien tuc.
  const activeWaveformIds = useMemo(
    () =>
      hasFullyBakedLibrary
        ? []
        : selectedWaveformIds.filter((id) => waveformOverlays.some((item) => item.id === id)),
    [hasFullyBakedLibrary, selectedWaveformIds, waveformOverlays],
  );
  const activeCtaIds = useMemo(
    () =>
      hasFullyBakedLibrary
        ? []
        : selectedCtaIds.filter((id) => ctaOverlays.some((item) => item.id === id)),
    [hasFullyBakedLibrary, selectedCtaIds, ctaOverlays],
  );
  // Phụ đề burn lúc render nên không bị thư viện đã bake chặn như sóng âm/CTA.
  const activeSubtitleStyleIds = useMemo(
    () => selectedSubtitleStyleIds.filter((id) => subtitleStyles.some((item) => item.id === id)),
    [selectedSubtitleStyleIds, subtitleStyles],
  );

  // Màu phụ đề, gom một chỗ để render đơn, batch và preview luôn gửi giống hệt
  // nhau — preview mà lệch với render thật thì còn tệ hơn không có preview.
  // Chỉ ô nào đã đổi mới có mặt, nên form chưa đụng tới vẫn ra đúng màu preset.
  const subtitleColorOverrides = useMemo(() => {
    const overrides: {
      textColor?: string;
      outlineColor?: string;
      outlineWidth?: number;
      backgroundEnabled?: boolean;
      backColor?: string;
      backOpacity?: number;
    } = {};
    if (subtitleTextColor) overrides.textColor = subtitleTextColor;
    if (subtitleOutlineColor) overrides.outlineColor = subtitleOutlineColor;
    if (subtitleOutlineWidth !== "") {
      overrides.outlineWidth = clampInt(subtitleOutlineWidth, 0, 20, 3);
    }
    if (subtitleBackgroundEnabled) {
      overrides.backgroundEnabled = true;
      if (subtitleBackColor) overrides.backColor = subtitleBackColor;
      overrides.backOpacity = clampInt(subtitleBackOpacity, 0, 100, 50) / 100;
    }
    return overrides;
  }, [
    subtitleTextColor,
    subtitleOutlineColor,
    subtitleOutlineWidth,
    subtitleBackgroundEnabled,
    subtitleBackColor,
    subtitleBackOpacity,
  ]);

  // Cùng dữ liệu, đổi sang tên khoá của request /create và /batch/create.
  const subtitleColorPayload = useMemo(
    () => ({
      subtitleTextColor: subtitleColorOverrides.textColor,
      subtitleOutlineColor: subtitleColorOverrides.outlineColor,
      subtitleOutlineWidth: subtitleColorOverrides.outlineWidth,
      subtitleBackgroundEnabled: subtitleColorOverrides.backgroundEnabled,
      subtitleBackColor: subtitleColorOverrides.backColor,
      subtitleBackOpacity: subtitleColorOverrides.backOpacity,
    }),
    [subtitleColorOverrides],
  );

  const decorGroupEntries = useMemo(
    () =>
      decorGroupNamesOf(decorImages).map((group) => ({
        group,
        items: decorImages.filter((item) => decorGroupOf(item) === group),
      })),
    [decorImages],
  );

  /** Mo mot khung: chon san toan bo anh dang bat cua khung do lam vong xoay. */
  const pickDecorGroup = (group: string) => {
    setDecorGroup(group);
    // Doi khung thi thay ca vong xoay — khong tron anh cua hai chu de khac nhau
    // vao cung mot batch.
    setSelectedDecorIds(decorAvailableIdsOf(decorImages, group));
  };

  // Render đơn chỉ nhận một bố cục: sang Single thì giữ lại bố cục đầu tiên.
  useEffect(() => {
    if (mode === "single") setSelectedLayoutIds((ids) => (ids.length > 1 ? ids.slice(0, 1) : ids));
  }, [mode]);

  const resetProgress = useCallback(() => {
    setStoryId(null);
    setStoryProgress(null);
    setBatchId(null);
    setBatchProgress(null);
  }, []);

  const refreshIntros = useCallback(() => {
    return getStoryIntros()
      .then((res) => setIntros(res.intros))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    getOutputDrives()
      .then((res) => {
        setOutputDrives(res.drives);
        setOutputDrive((current) => current || res.defaultDrive);
      })
      .catch(() => undefined);
  }, []);

  const refreshBatchQueue = useCallback(() => {
    return getStoryBatchQueue()
      .then(setBatchQueue)
      .catch(() => undefined);
  }, []);

  /**
   * Tải lại ảnh decor sau khi render: những ảnh vừa được chia cho batch đã bị
   * đánh dấu "đã dùng" ở backend, bỏ luôn khỏi vòng xoay đang chọn để batch
   * sau không gửi lại đúng mấy ảnh đó.
   */
  const refreshDecorImages = useCallback(() => {
    return getDecorImages()
      .then((res) => {
        setDecorImages(res.images);
        const spent = new Set(res.images.filter(decorIsUsed).map((item) => item.id));
        setSelectedDecorIds((current) => current.filter((id) => !spent.has(id)));
      })
      .catch(() => undefined);
  }, []);

  const usedDecorCount = useMemo(() => decorImages.filter(decorIsUsed).length, [decorImages]);

  /** Xoá hẳn ảnh decor đã dùng (cả thư viện) để lấy lại dung lượng ổ đĩa. */
  const handlePurgeUsedDecor = async () => {
    const question =
      `Xoá vĩnh viễn ${usedDecorCount} ảnh decor đã dùng khỏi ổ đĩa (mọi khung TV)?\n\n` +
      "Ảnh đã xoá không thể bỏ dấu “đã dùng” để dùng lại, và không hiện lại khi search Pexels/Pixabay. " +
      "Ảnh mà batch còn cần render (đang chờ/chạy, hoặc item lỗi chờ thử lại) sẽ được giữ lại.";
    if (!window.confirm(question)) return;
    setErrorMessage(null);
    setDecorPurgeMessage(null);
    setIsPurgingDecor(true);
    try {
      const res = await purgeUsedDecorImages();
      const parts = [
        `Đã xoá ${res.deleted} ảnh đã dùng, giải phóng ${(res.freedBytes / 1024 / 1024).toFixed(1)} MB.`,
      ];
      if (res.kept) parts.push(`Giữ lại ${res.kept} ảnh batch còn cần render.`);
      if (res.failed) parts.push(`${res.failed} ảnh không xoá được file (đang bị khoá?) — bấm dọn lại sau.`);
      setDecorPurgeMessage(parts.join(" "));
      await refreshDecorImages();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the don anh decor da dung.");
    } finally {
      setIsPurgingDecor(false);
    }
  };

  /** Chuyển khung tiến độ sang một batch khác trong hàng đợi. */
  const viewBatch = useCallback((nextBatchId: string) => {
    if (nextBatchId === batchId) return;
    setBatchProgress(null);
    setCancellingItemIds(new Set());
    setIsCancellingBatch(false);
    setIsRetrying(false);
    setBatchId(nextBatchId);
  }, [batchId]);

  const submitBatchItems = useCallback(async (itemsToSubmit: BatchItem[]) => {
    setErrorMessage(null);
    setQueueInfoMessage(null);
    // Không reset batch đang xem: batch mới chỉ xếp vào hàng đợi, batch cũ vẫn chạy tiếp.
    setIsSubmitting(true);
    try {
      const audioFiles: File[] = [];
      const subtitleFiles: File[] = [];
      const chapterFiles: File[] = [];
      let localSubtitleCount = 0;
      const items: CreateStoryBatchItem[] = itemsToSubmit.map((item) => {
        let inputValue = item.inputValue;
        if (item.inputType === "audio_file") {
          // Local-folder items travel as absolute paths; only picked files get uploaded.
          if (item.audioPath) {
            inputValue = item.audioPath;
          } else {
            inputValue = String(audioFiles.length);
            if (item.audioFile) audioFiles.push(item.audioFile);
          }
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
        } else if (item.subtitlePath) {
          entry.subtitleFile = item.subtitlePath;
          localSubtitleCount += 1;
        }
        if (item.chaptersFile) {
          entry.chaptersFile = String(chapterFiles.length);
          chapterFiles.push(item.chaptersFile);
        } else if (item.chaptersPath) {
          entry.chaptersFile = item.chaptersPath;
        }
        return entry;
      });
      const sharedConfig: CreateStoryBatchRequest["sharedConfig"] = {
        libraryIds: selectedLibraryIds,
        introId: introId === "none" ? "" : introId,
        optimizeMode,
        outputDrive: outputDrive || undefined,
        skipTvEffect,
        clipUsageMode,
        clipTags: [],
        voiceId: voiceId || undefined,
      };
      if (activeLayoutIds.length) {
        sharedConfig.layoutIds = activeLayoutIds;
      }
      // Ảnh decor chỉ chia cho các video bốc trúng bố cục cần decor.
      if (decorEnabled && activeDecorIds.length) {
        sharedConfig.decorImageIds = activeDecorIds;
      }
      if (activeModifierIds.length) {
        sharedConfig.modifierIds = activeModifierIds;
      }
      if (activeWaveformIds.length) {
        sharedConfig.waveformOverlayIds = activeWaveformIds;
      }
      if (activeCtaIds.length) {
        sharedConfig.ctaOverlayIds = activeCtaIds;
      }
      if (subtitleFiles.length || localSubtitleCount) {
        sharedConfig.subtitleFont = subtitleFont || undefined;
        sharedConfig.subtitlePreset = subtitlePreset;
        sharedConfig.subtitleMaxCharsPerLine = clampInt(subtitleMaxCharsPerLine, 16, 60, 42);
        sharedConfig.subtitleMaxLines = clampInt(subtitleMaxLines, 1, 3, 2);
        sharedConfig.subtitleFontScale = Number(subtitleFontScale);
        Object.assign(sharedConfig, subtitleColorPayload);
        if (activeSubtitleStyleIds.length) {
          sharedConfig.subtitleStyleIds = activeSubtitleStyleIds;
        }
      }
      const response = await createStoryBatch(
        { items, sharedConfig },
        audioFiles.length ? audioFiles : undefined,
        subtitleFiles.length ? subtitleFiles : undefined,
        chapterFiles.length ? chapterFiles : undefined,
      );
      // Form chỉ khóa trong lúc upload: batch đã nằm trong hàng đợi, soạn được batch tiếp.
      setIsSubmitting(false);
      setBatchItems([]);
      setQueueInfoMessage(
        `Batch ${response.batchId} đã vào hàng đợi (vị trí ${response.queuePosition}). ` +
          "Mỗi lúc chỉ chạy 1 batch, batch sau tự chạy khi batch trước xong.",
      );
      // Đang xem một batch còn chạy thì giữ nguyên; batch mới xem được qua nút "Xem" ở hàng đợi.
      const viewedStillRunning = batchId !== null && (!batchProgress || !BATCH_DONE_STATUSES.has(batchProgress.status));
      if (!viewedStillRunning) viewBatch(response.batchId);
      void refreshBatchQueue();
      void refreshDecorImages();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the bat dau batch render.");
      setIsSubmitting(false);
    }
  }, [selectedLibraryIds, introId, optimizeMode, outputDrive, skipTvEffect, clipUsageMode, decorEnabled, activeDecorIds, activeLayoutIds, activeModifierIds, activeWaveformIds, activeCtaIds, voiceId, subtitleFont, subtitlePreset, subtitleMaxCharsPerLine, subtitleMaxLines, subtitleFontScale, subtitleColorPayload, activeSubtitleStyleIds, batchId, batchProgress, viewBatch, refreshBatchQueue, refreshDecorImages]);

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
    const loadIntros = getStoryIntros()
      .then((res) => {
        if (cancelled) return;
        setIntros(res.intros);
      })
      .catch(() => undefined);
    // Decor images are optional; a failure here must not block the render form.
    const loadDecor = getDecorImages()
      .then((res) => {
        if (cancelled) return;
        setDecorImages(res.images);
        // Mo san khung dau tien va chon het anh trong no; khi chua ai phan nhom
        // thi ca thu vien la mot khung duy nhat, dung nhu hanh vi cu. Anh dung
        // roi la het, nen bo qua cac khung da dung sach de khong mo vao mot
        // khung trong.
        const groups = decorGroupNamesOf(res.images);
        const first =
          groups.find((group) => decorAvailableIdsOf(res.images, group).length) ?? groups[0];
        if (first !== undefined) {
          setDecorGroup(first);
          setSelectedDecorIds(decorAvailableIdsOf(res.images, first));
        }
      })
      .catch(() => undefined);
    // Song am / CTA cung la tuy chon: hong o day thi form van render binh thuong.
    const loadWaveforms = getWaveformOverlays()
      .then((res) => {
        if (cancelled) return;
        setWaveformOverlays(res.overlays);
      })
      .catch(() => undefined);
    const loadCtas = getCtaOverlays()
      .then((res) => {
        if (cancelled) return;
        setCtaOverlays(res.overlays);
      })
      .catch(() => undefined);
    // Kiểu dựng cũng là tuỳ chọn: lỗi thì render như cũ (không khung).
    const loadEditStyles = getEditStyles()
      .then((res) => {
        if (cancelled) return;
        setEditTypes(res.types);
        setEditStyles(res.styles);
      })
      .catch(() => undefined);
    // Danh sách cấu hình phụ đề cũng là tuỳ chọn: lỗi thì form phụ đề vẫn dùng được.
    const loadSubtitleStyles = getSubtitleStyles()
      .then((res) => {
        if (cancelled) return;
        setSubtitleStyles(res.styles);
        setHiddenBuiltinStyleCount(res.hiddenBuiltinCount);
      })
      .catch(() => undefined);
    void Promise.all([
      loadVoices,
      loadFonts,
      loadPresets,
      loadIntros,
      loadDecor,
      loadWaveforms,
      loadCtas,
      loadEditStyles,
      loadSubtitleStyles,
    ]).finally(() => {
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

  // Hàng đợi batch: poll khi ở mode Batch để thấy batch nào đang chạy / đang chờ.
  useEffect(() => {
    if (mode !== "batch") return;
    void refreshBatchQueue();
    const interval = window.setInterval(() => void refreshBatchQueue(), 3000);
    return () => window.clearInterval(interval);
  }, [mode, refreshBatchQueue]);

  // Mở lại trang khi đang có batch chạy: tự chuyển sang mode Batch và xem batch đó.
  useEffect(() => {
    let cancelled = false;
    getStoryBatchQueue()
      .then((data) => {
        if (cancelled) return;
        setBatchQueue(data);
        if (data.activeBatchId) {
          setMode("batch");
          setBatchId((current) => current ?? data.activeBatchId);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

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
    setAudioInputKey((key) => key + 1);
    if (!files || files.length === 0) return;

    const all = Array.from(files);
    const audioFiles = all.filter((file) => isAudioFileName(file.name));
    const subtitleFiles = all.filter((file) => isSubtitleFileName(file.name));
    const chapterFiles = all.filter((file) => isChaptersFileName(file.name));

    // Index subtitles by folder + base name so each audio picks up its sibling .srt.
    const subtitleMap = new Map<string, File>();
    for (const srt of subtitleFiles) {
      subtitleMap.set(subtitlePairKey(srt), srt);
    }
    // Same for `<stem>.chapters.txt`: strip the whole double suffix to get the stem.
    const chaptersMap = new Map<string, File>();
    for (const chapters of chapterFiles) {
      chaptersMap.set(subtitlePairKey(chapters, CHAPTERS_SUFFIX_RE), chapters);
    }

    let pairedCount = 0;
    let chaptersCount = 0;
    const newItems: BatchItem[] = audioFiles.map((file) => {
      const subtitleFile = findCompanionFile(subtitleMap, file);
      if (subtitleFile) pairedCount += 1;
      const chaptersFile = findCompanionFile(chaptersMap, file);
      if (chaptersFile) chaptersCount += 1;
      return {
        id: nextBatchId(),
        inputType: "audio_file",
        inputValue: file.name,
        outputName: outputNameFromFileName(file.name),
        audioFile: file,
        subtitleFile,
        chaptersFile,
        sourceName: file.name,
      };
    });

    if (newItems.length) setBatchItems((prev) => [...prev, ...newItems]);

    if (audioFiles.length === 0) {
      setPairingInfoMessage(
        subtitleFiles.length
          ? `Chỉ tìm thấy ${subtitleFiles.length} file .srt, không có file audio nào để ghép.`
          : "Không tìm thấy file audio hợp lệ.",
      );
      return;
    }
    const withoutSrt = audioFiles.length - pairedCount;
    setPairingInfoMessage(
      `Đã thêm ${audioFiles.length} file audio — ${pairedCount} kèm .srt tự động` +
        (withoutSrt ? `, ${withoutSrt} chưa có .srt cùng tên.` : ".") +
        (chaptersCount ? ` ${chaptersCount} kèm file chương (.chapters.txt).` : ""),
    );
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

  // Local folder: backend and browser share this machine, so the batch only needs the
  // paths — nothing is uploaded and nothing is copied into storage.
  const handleScanLocalFolder = async () => {
    const path = localFolderPath.trim();
    if (!path) {
      setErrorMessage("Nhap duong dan thu muc tren may render.");
      return;
    }
    setErrorMessage(null);
    setIsScanningLocalFolder(true);
    try {
      const scan = await scanLocalAudioFolder(path);
      const newItems: BatchItem[] = scan.items.map((item) => ({
        id: nextBatchId(),
        inputType: "audio_file",
        inputValue: item.audioPath,
        outputName: item.outputName,
        audioFile: null,
        subtitleFile: null,
        sourceName: item.audioName,
        audioPath: item.audioPath,
        subtitlePath: item.subtitlePath || undefined,
        subtitleName: item.subtitleName || undefined,
        chaptersPath: item.chaptersPath || undefined,
        chaptersName: item.chaptersPath ? item.chaptersPath.split(/[\\/]/).pop() : undefined,
      }));
      setBatchItems((prev) => [...prev, ...newItems]);
      const withoutSrt = scan.items.length - scan.pairedCount;
      const chaptersCount = scan.items.filter((item) => item.chaptersPath).length;
      setPairingInfoMessage(
        `Đã thêm ${scan.items.length} file audio từ ${scan.path} (${scan.totalSizeMb} MB, đọc trực tiếp — không upload)` +
          ` — ${scan.pairedCount} kèm .srt tự động` +
          (withoutSrt ? `, ${withoutSrt} chưa có .srt cùng tên.` : ".") +
          (chaptersCount ? ` ${chaptersCount} kèm file chương (.chapters.txt).` : ""),
      );
      setIsLocalFolderDialogOpen(false);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the quet thu muc local.");
    } finally {
      setIsScanningLocalFolder(false);
    }
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

  const updateBatchItemChapters = (id: string, file: File | null) => {
    setBatchItems((prev) => prev.map((item) => (item.id === id ? { ...item, chaptersFile: file } : item)));
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
        styleOverrides: { fontScale: Number(subtitleFontScale), ...subtitleColorOverrides },
      });
      setSubtitlePreviewPath(res.previewPath);
      setSubtitlePreviewBust(Date.now());
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the render preview phu de.");
    } finally {
      setIsSubtitlePreviewing(false);
    }
  };

  /** Lưu form phụ đề hiện tại thành một cấu hình có tên — gửi đúng những gì render sẽ dùng. */
  const handleSaveSubtitleStyle = async () => {
    setIsSavingSubtitleStyle(true);
    setErrorMessage(null);
    try {
      const res = await createSubtitleStyle({
        name: newSubtitleStyleName.trim(),
        subtitleFont: subtitleFont || undefined,
        subtitlePreset,
        subtitleFontScale: Number(subtitleFontScale),
        ...subtitleColorPayload,
      });
      setSubtitleStyles((current) => [...current, res.style]);
      setNewSubtitleStyleName("");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the luu cau hinh phu de.");
    } finally {
      setIsSavingSubtitleStyle(false);
    }
  };

  /** Nạp một cấu hình vào form để xem thử, hoặc chỉnh tiếp rồi lưu thành cấu hình mới. */
  const applySubtitleStyle = (style: SubtitleStyle) => {
    // Cấu hình có sẵn không mang font/cỡ chữ: giữ nguyên lựa chọn đang có ở form.
    if (style.subtitleFont) setSubtitleFont(style.subtitleFont);
    if (style.subtitleFontScale) setSubtitleFontScale(String(style.subtitleFontScale));
    setSubtitlePreset(style.subtitlePreset);
    setSubtitleTextColor(style.subtitleTextColor ?? "");
    setSubtitleOutlineColor(style.subtitleOutlineColor ?? "");
    setSubtitleOutlineWidth(style.subtitleOutlineWidth === undefined ? "" : String(style.subtitleOutlineWidth));
    setSubtitleBackgroundEnabled(style.subtitleBackgroundEnabled === true);
    setSubtitleBackColor(style.subtitleBackColor ?? "");
    setSubtitleBackOpacity(
      style.subtitleBackOpacity === undefined ? "50" : String(Math.round(style.subtitleBackOpacity * 100)),
    );
  };

  const handleDeleteSubtitleStyle = async (style: SubtitleStyle) => {
    const question = style.builtin
      ? `Ẩn cấu hình có sẵn "${style.name}"? Hiệu ứng này cũng biến khỏi ô "Hiệu ứng phụ đề". Bấm "Khôi phục" để lấy lại.`
      : `Xoá cấu hình "${style.name}"?`;
    if (!window.confirm(question)) return;
    setErrorMessage(null);
    try {
      await deleteSubtitleStyle(style.id);
      setSubtitleStyles((current) => current.filter((item) => item.id !== style.id));
      setSelectedSubtitleStyleIds((current) => current.filter((id) => id !== style.id));
      if (style.builtin) {
        setHiddenBuiltinStyleCount((count) => count + 1);
        setSubtitlePresets((current) =>
          current.map((preset) => (preset.id === style.subtitlePreset ? { ...preset, hidden: true } : preset)),
        );
      }
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the xoa cau hinh phu de.");
    }
  };

  const handleRestoreBuiltinSubtitleStyles = async () => {
    setErrorMessage(null);
    try {
      const res = await restoreBuiltinSubtitleStyles();
      setSubtitleStyles(res.styles);
      setHiddenBuiltinStyleCount(res.hiddenBuiltinCount);
      setSubtitlePresets((current) => current.map((preset) => ({ ...preset, hidden: false })));
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the khoi phuc cau hinh phu de.");
    }
  };

  const removeBatchItem = (id: string) => {
    setBatchItems((prev) => prev.filter((item) => item.id !== id));
  };

  const handleSubmit = async () => {
    setErrorMessage(null);
    // Mode Batch giữ nguyên batch đang xem: batch mới chỉ xếp vào hàng đợi.
    if (mode === "single") resetProgress();

    if (!selectedLibraryIds.length) {
      setErrorMessage("Chon it nhat 1 thu vien clip nguon.");
      return;
    }

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
          libraryIds: selectedLibraryIds,
          clipTags: [],
          skipTvEffect,
          clipUsageMode,
          decorImageId: decorEnabled ? activeDecorIds[0] : undefined,
          layoutId: activeLayoutIds[0],
          modifierIds: activeModifierIds.length ? activeModifierIds : undefined,
          voiceId: voiceId || undefined,
        };
        if (singleSubtitleFile) {
          payload.subtitleFont = subtitleFont || undefined;
          payload.subtitlePreset = subtitlePreset;
          payload.subtitleMaxCharsPerLine = clampInt(subtitleMaxCharsPerLine, 16, 60, 42);
          payload.subtitleMaxLines = clampInt(subtitleMaxLines, 1, 3, 2);
          payload.subtitleFontScale = Number(subtitleFontScale);
          Object.assign(payload, subtitleColorPayload);
        }
        const res = await createStoryVideo(
          payload,
          singleInput.audioFile ?? undefined,
          singleSubtitleFile ?? undefined,
          singleChaptersFile ?? undefined,
        );
        setStoryId(res.storyId);
        void refreshDecorImages();
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
      if (item.inputType === "audio_file" && !item.audioFile && !item.audioPath) {
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
      setQueueInfoMessage(
        `Đã đưa ${res.retryCount} item lỗi vào hàng đợi (batch ${res.batchId}, vị trí ${res.queuePosition}).`,
      );
      void refreshBatchQueue();
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

  const handleCancelQueuedBatch = async (queuedBatchId: string) => {
    setCancellingQueueIds((current) => new Set(current).add(queuedBatchId));
    setErrorMessage(null);
    try {
      await cancelStoryBatch(queuedBatchId);
      await refreshBatchQueue();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the huy batch.");
    } finally {
      setCancellingQueueIds((current) => {
        const next = new Set(current);
        next.delete(queuedBatchId);
        return next;
      });
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
  const filteredSubtitleFonts = useMemo(
    () => subtitleFonts.filter(subtitleFontLangMatcher(subtitleFontLang)),
    [subtitleFonts, subtitleFontLang],
  );

  const handleSubtitleFontLangChange = (lang: SubtitleFontLang) => {
    setSubtitleFontLang(lang);
    const matches = subtitleFontLangMatcher(lang);
    // Nếu font đang chọn không hợp ngôn ngữ vừa lọc, tự chuyển sang font hợp lệ đầu tiên.
    const current = subtitleFonts.find((font) => font.family === subtitleFont);
    if (current && !matches(current)) {
      const fallback = subtitleFonts.find(matches);
      if (fallback) setSubtitleFont(fallback.family);
    }
  };
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
          {
            label: "Thư viện",
            value:
              selectedLibraries.length > 1
                ? `${selectedLibraries.length} thư viện`
                : selectedLibraries[0]?.name ?? "Mặc định",
          },
          { label: "Overlays", value: "Global settings" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}
      {driveWarningMessage ? <StatusAlert title="Import Drive co canh bao" message={driveWarningMessage} /> : null}
      {pairingInfoMessage ? <StatusAlert title="Đã ghép phụ đề tự động" message={pairingInfoMessage} /> : null}
      {queueInfoMessage ? <StatusAlert title="Hàng đợi batch" message={queueInfoMessage} /> : null}

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
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline">
              <Link to="/story-video/edit-styles">
                <Clapperboard className="mr-2 size-4" />
                Kiểu dựng
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link to="/story-video/settings">
                <Settings className="mr-2 size-4" />
                Cau hinh overlay
              </Link>
            </Button>
          </div>
        </div>

        <div className="grid gap-5">
          <div className="grid gap-2">
            <Label className="text-sm">Thư viện video (clip nguồn)</Label>
            <StoryLibrarySelect
              libraries={libraries}
              // Thư viện đầu tiên là mục tiêu của nút tạo/đổi tên/xóa.
              value={selectedLibraryIds[0] ?? activeLibraryId}
              onChange={setActiveLibraryId}
              onLibrariesChanged={() => void refreshLibraries()}
              manage
              disabled={isSubmitting}
              selectedIds={selectedLibraryIds}
              onSelectedIdsChange={setSelectedLibraryIds}
            />
            <p className="text-xs text-muted-foreground">
              Chọn nhiều thư viện để gộp clip: mỗi video rút ngẫu nhiên từ pool chung
              ({selectedClipCount} clip) và chỉ lặp lại clip sau khi đã dùng hết pool.
            </p>
            {selectedClipCount === 0 ? (
              <p className="text-xs text-amber-500">
                Các thư viện đang chọn chưa có clip nào — thêm clip ở{" "}
                <Link to="/story-video/settings" className="underline">
                  trang cấu hình
                </Link>{" "}
                trước khi render.
              </p>
            ) : emptySelectedLibraries.length ? (
              <p className="text-xs text-amber-500">
                Chưa có clip trong: {emptySelectedLibraries.map((lib) => lib.name).join(", ")}.
              </p>
            ) : null}
            {mixedBakeSelection ? (
              <p className="text-xs text-amber-500">
                Đang trộn thư viện đã bake và chưa bake — cả video sẽ render theo thư viện đã
                bake: bỏ qua bước hiệu ứng TV
                {decorBlockedByLibrary ? " và chỉ ghi phụ đề (không chèn sóng âm/CTA)" : ""}, nên
                clip từ thư viện chưa bake sẽ giữ nguyên hình gốc.
              </p>
            ) : null}
          </div>

          <ClipUsageModeField
            value={clipUsageMode}
            onChange={setClipUsageMode}
            libraryIds={selectedLibraryIds}
            disabled={isSubmitting}
            refreshKey={`${storyProgress?.status ?? ""}|${batchProgress?.status ?? ""}`}
          />

          <div className="grid gap-2">
            <div className="flex items-center gap-2">
              <Checkbox
                id="skip-tv-effect"
                checked={skipTvEffect}
                onCheckedChange={(checked) => setSkipTvEffect(Boolean(checked))}
                disabled={isSubmitting}
              />
              <Label htmlFor="skip-tv-effect" className="text-sm cursor-pointer">
                Bỏ qua hiệu ứng khi render
              </Label>
            </div>
            <p className="text-xs text-muted-foreground">
              {hasBakedLibrary ? (
                <>
                  {mixedBakeSelection ? "Có thư viện" : "Thư viện này"} đã bake hiệu ứng sẵn — bước
                  hiệu ứng luôn được bỏ qua khi render.
                </>
              ) : (
                <>
                  Khi bật: render thẳng từ clip gốc, không áp hiệu ứng TV (vẫn giữ TV noise, sóng âm, CTA và phụ đề)
                  — dùng cho thư viện chưa bake hiệu ứng và render nhanh hơn nhiều. Tắt (mặc định): áp hiệu ứng TV
                  đang chọn ở trang cấu hình.
                </>
              )}
            </p>
          </div>

          <div className="grid gap-4 rounded-lg border border-border/70 bg-background/50 p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="space-y-1">
                <h3 className="text-sm font-medium text-foreground">Kiểu dựng</h3>
                <p className="text-xs text-muted-foreground">
                  {mode === "batch"
                    ? "Chọn nhiều bố cục để mỗi video trong batch bốc một bố cục khác nhau; hiệu ứng bổ trợ được tick sẽ áp cho mọi video."
                    : "Chọn một bố cục cho video; hiệu ứng bổ trợ được tick sẽ chồng lên bố cục."}
                </p>
              </div>
              <Button asChild variant="outline" size="sm">
                <Link to="/story-video/edit-styles">
                  <Clapperboard className="mr-2 size-4" />
                  Cấu hình kiểu dựng
                </Link>
              </Button>
            </div>

            <EditStyleRotationPicker
              label="Bố cục"
              styles={layoutChoices}
              typeById={editTypeById}
              selectedIds={selectedLayoutIds}
              onChange={setSelectedLayoutIds}
              blockedReason={layoutBlockedReason}
              single={mode === "single"}
              disabled={isSubmitting}
              fallbackHint="Không chọn = không khung (clip phủ kín khung hình), như trước đây."
            />

            {decorLayoutPicked ? (
              <div className="grid gap-2 rounded-lg border border-border/60 bg-background/70 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Label className="text-sm">Ảnh decor cho bố cục khung TV</Label>
                  {usedDecorCount ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={isSubmitting || isPurgingDecor}
                      onClick={handlePurgeUsedDecor}
                      title="Xoá hẳn file các ảnh đã dùng để lấy lại dung lượng ổ đĩa"
                    >
                      {isPurgingDecor ? (
                        <Loader2 className="mr-2 size-4 animate-spin" />
                      ) : (
                        <Trash2 className="mr-2 size-4" />
                      )}
                      Dọn {usedDecorCount} ảnh đã dùng
                    </Button>
                  ) : null}
                </div>
                {decorPurgeMessage ? <p className="text-xs text-muted-foreground">{decorPurgeMessage}</p> : null}
                {!decorImages.length ? (
                  <p className="text-xs text-amber-500">
                    Chưa có ảnh decor nào nên bố cục khung TV sẽ đứng ngoài vòng xoay. Thêm ảnh ở{" "}
                    <Link to="/story-video/settings" className="underline">
                      trang cấu hình overlay
                    </Link>
                    .
                  </p>
                ) : (
                  <>
                    <div className="grid gap-2">
                      {decorGroupEntries.map(({ group, items }) => {
                        const isOpen = decorGroup === group;
                        // Ảnh đã dùng không được tính ở đâu cả: nó không vào vòng xoay nữa,
                        // nên cũng không hiện trong danh sách.
                        const unusedItems = items.filter((item) => !decorIsUsed(item));
                        const availableCount = unusedItems.length;
                        const pickedCount = unusedItems.filter((item) => selectedDecorIds.includes(item.id)).length;
                        const hiddenUsedCount = items.length - availableCount;
                        // "Chon tat ca" chi lay anh dang bat va chua dung, dung nhu luc mo
                        // khung; anh da tat van bam tay duoc neu that su muon dung.
                        const enabledIds = decorAvailableIdsOf(items, group);
                        return (
                          <div
                            key={group || "__none__"}
                            className={`overflow-hidden rounded-lg border transition-colors ${
                              isOpen && decorEnabled
                                ? "border-primary/60 bg-primary/5"
                                : "border-border/70 bg-background/70"
                            }`}
                          >
                            <button
                              type="button"
                              disabled={!decorEnabled || isSubmitting}
                              onClick={() => pickDecorGroup(group)}
                              className="flex w-full items-center gap-2 px-3 py-2 text-left disabled:opacity-50"
                            >
                              {isOpen ? (
                                <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
                              ) : (
                                <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                              )}
                              <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                                {decorGroupLabel(group)}
                              </span>
                              <span className="shrink-0 text-xs text-muted-foreground">
                                {isOpen
                                  ? `${pickedCount}/${availableCount} ảnh`
                                  : `${availableCount}/${items.length} ảnh chưa dùng`}
                              </span>
                            </button>

                            {isOpen ? (
                              <div className="grid gap-2 border-t border-border/60 p-3">
                                {!unusedItems.length ? (
                                  <p className="text-xs text-muted-foreground">
                                    Khung này đã dùng hết ảnh. Thêm ảnh mới, hoặc bỏ dấu “đã dùng” ở trang cấu hình
                                    overlay.
                                  </p>
                                ) : (
                                  <div className="flex max-h-80 flex-wrap gap-2 overflow-y-auto pr-1">
                                    {unusedItems.map((item) => {
                                      const picked = selectedDecorIds.includes(item.id);
                                      return (
                                        <button
                                          key={item.id}
                                          type="button"
                                          disabled={!decorEnabled || isSubmitting}
                                          onClick={() =>
                                            setSelectedDecorIds((current) =>
                                              current.includes(item.id)
                                                ? current.filter((id) => id !== item.id)
                                                : [...current, item.id],
                                            )
                                          }
                                          className={`flex items-center gap-2 rounded-lg border p-1.5 pr-3 text-left transition-colors disabled:opacity-50 ${
                                            picked && decorEnabled
                                              ? "border-primary bg-primary/10"
                                              : "border-border/70 bg-background/70"
                                          }`}
                                        >
                                          <img
                                            src={`/media/${item.processedRelativePath ?? item.relativePath}`}
                                            alt={item.name}
                                            loading="lazy"
                                            className="h-9 w-16 rounded border border-border/50 object-cover"
                                          />
                                          <span className="min-w-0">
                                            <span className="block truncate text-xs font-medium text-foreground">
                                              {item.name}
                                            </span>
                                            {item.enabled === false ? (
                                              <span className="block text-[11px] text-amber-500">
                                                Đang tắt ở trang cấu hình
                                              </span>
                                            ) : null}
                                          </span>
                                        </button>
                                      );
                                    })}
                                  </div>
                                )}
                                <div className="flex flex-wrap items-center gap-2">
                                  <button
                                    type="button"
                                    disabled={
                                      !decorEnabled ||
                                      isSubmitting ||
                                      enabledIds.every((id) => selectedDecorIds.includes(id))
                                    }
                                    onClick={() => setSelectedDecorIds(enabledIds)}
                                    className="text-xs text-muted-foreground underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
                                  >
                                    Chọn tất cả
                                  </button>
                                  <button
                                    type="button"
                                    disabled={!decorEnabled || isSubmitting || pickedCount === 0}
                                    onClick={() => setSelectedDecorIds([])}
                                    className="text-xs text-muted-foreground underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
                                  >
                                    Bỏ chọn hết
                                  </button>
                                  {hiddenUsedCount ? (
                                    <span className="ml-auto text-xs text-muted-foreground">
                                      Đã ẩn {hiddenUsedCount} ảnh đã dùng
                                    </span>
                                  ) : null}
                                </div>
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                    <p className={`text-xs ${activeDecorIds.length ? "text-muted-foreground" : "text-amber-500"}`}>
                      {activeDecorIds.length
                        ? `Khung "${decorGroupLabel(decorGroup ?? "")}" — đang chọn ${activeDecorIds.length} ảnh chưa dùng. Mỗi ảnh chỉ dùng cho 1 video rồi bị đánh dấu “đã dùng”, nên nhiều nhất ${activeDecorIds.length} video có khung TV; các video còn lại tự chuyển sang bố cục không dùng ảnh decor. Video nền chỉ chạy trong vùng xanh của ảnh; sóng âm, CTA và phụ đề nằm trên ảnh decor.`
                        : "Chưa chọn ảnh nào chưa dùng — bố cục khung TV đứng ngoài vòng xoay. Chọn khung khác, thêm ảnh mới, hoặc bỏ dấu “đã dùng” ở trang cấu hình overlay."}
                    </p>
                  </>
                )}
              </div>
            ) : null}

            <EditStyleRotationPicker
              label="Hiệu ứng bổ trợ"
              styles={modifierChoices}
              typeById={editTypeById}
              selectedIds={selectedModifierIds}
              onChange={setSelectedModifierIds}
              blockedReason={modifierBlockedReason}
              applyAll
              disabled={isSubmitting}
              fallbackHint="Không chọn = không thêm hiệu ứng."
            />

            {mode === "single" ? (
              <div className="grid gap-2">
                <div className="grid gap-2 md:w-1/2">
                  <Label>File chương (.chapters.txt, tùy chọn)</Label>
                  <Input
                    type="file"
                    accept=".txt"
                    onChange={(event) => setSingleChaptersFile(event.currentTarget.files?.[0] ?? null)}
                  />
                  <p className="text-xs text-muted-foreground">
                    {singleChaptersFile
                      ? `Đã chọn: ${singleChaptersFile.name}`
                      : "Đặt tên chương, nhãn và câu nhấn. Không có file thì video không hiện chữ chương."}
                  </p>
                </div>
                <ChapterFileHelp />
              </div>
            ) : (
              <ChapterFileHelp />
            )}
          </div>

          {mode === "batch" ? (
            <div className="grid gap-3 rounded-lg border border-border/70 bg-background/50 p-3">
              <div className="space-y-1">
                <h3 className="text-sm font-medium text-foreground">Xoay vòng sóng âm / CTA</h3>
                <p className="text-xs text-muted-foreground">
                  Chọn nhiều cấu hình để mỗi video trong batch bốc ngẫu nhiên một cái. Thêm hoặc sửa cấu hình ở{" "}
                  <Link to="/story-video/settings" className="underline">
                    trang cấu hình overlay
                  </Link>
                  .
                </p>
              </div>
              {hasFullyBakedLibrary ? (
                <p className="text-xs text-amber-500">
                  Thư viện này đã bake sẵn sóng âm và CTA vào clip nên render bỏ qua bước overlay — chọn ở
                  đây sẽ không có tác dụng. Chọn thư viện chưa bake để dùng vòng xoay.
                </p>
              ) : null}
              <div className="grid gap-4 md:grid-cols-2">
                <OverlayRotationPicker
                  label="Sóng âm"
                  overlays={waveformOverlays}
                  selectedIds={selectedWaveformIds}
                  onChange={setSelectedWaveformIds}
                  disabled={isSubmitting || hasFullyBakedLibrary}
                  emptyHint="Chưa có sóng âm nào."
                  fallbackHint="Không chọn = dùng sóng âm mặc định ở trang cấu hình."
                />
                <OverlayRotationPicker
                  label="CTA overlay (Like / Subscribe / Thông báo)"
                  overlays={ctaOverlays}
                  selectedIds={selectedCtaIds}
                  onChange={setSelectedCtaIds}
                  disabled={isSubmitting || hasFullyBakedLibrary}
                  emptyHint="Chưa có CTA overlay nào."
                  fallbackHint="Không chọn = dùng CTA đang bật ở trang cấu hình."
                />
              </div>
            </div>
          ) : null}

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
            <div className="grid gap-5">
              <div className="grid gap-2">
                <Label className="text-sm">Intro video (đầu mỗi video)</Label>
                <StoryIntroSelect
                  intros={intros}
                  value={introId}
                  onChange={setIntroId}
                  onIntrosChanged={() => void refreshIntros()}
                  disabled={isSubmitting}
                />
                <p className="text-xs text-muted-foreground">
                  Mặc định không có intro. Chọn một intro trong thư viện nếu muốn ghép vào đầu mỗi video của batch.
                </p>
              </div>

              <div className="grid gap-2">
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="batch-optimize-mode"
                    checked={optimizeMode}
                    onCheckedChange={(checked) => setOptimizeMode(Boolean(checked))}
                    disabled={isSubmitting}
                  />
                  <Label htmlFor="batch-optimize-mode" className="text-sm cursor-pointer">
                    Chế độ tối ưu render
                  </Label>
                </div>
                <p className="text-xs text-muted-foreground">
                  Khi bật: tạm dừng các trình duyệt/app khác (browser farm) và ưu tiên CPU cho render để chạy nhanh hơn — chúng tự động chạy lại khi batch kết thúc. Tắt (mặc định): render chạy song song, không ảnh hưởng app khác.
                </p>
              </div>

              {outputDrives.length ? (
                <div className="grid gap-2 md:w-1/2">
                  <Label htmlFor="batch-output-drive">Ổ đĩa lưu video output</Label>
                  <select
                    id="batch-output-drive"
                    value={outputDrive}
                    onChange={(event) => setOutputDrive(event.target.value)}
                    disabled={isSubmitting}
                    className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  >
                    {outputDrives.map((option) => (
                      <option key={option.drive} value={option.drive}>
                        Ổ {option.drive}: — trống {formatGigabytes(option.freeBytes)} / {formatGigabytes(option.totalBytes)} ({option.outputDir})
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-muted-foreground">
                    Video của batch được lưu vào &lt;ổ&gt;/…/story-video/&lt;batch id&gt;/. Retry các video lỗi vẫn ghi vào ổ của batch gốc.
                  </p>
                </div>
              ) : null}

              <BatchInputForm
                items={batchItems}
                audioInputKey={audioInputKey}
                onAddAudio={handleAddBatchAudio}
                onAddScript={handleAddBatchScript}
                onAddLocalFolder={() => setIsLocalFolderDialogOpen(true)}
                onAddDriveFolder={() => setIsDriveDialogOpen(true)}
                onUpdate={updateBatchItem}
                onUpdateSubtitle={updateBatchItemSubtitle}
                onUpdateChapters={updateBatchItemChapters}
                onRemove={removeBatchItem}
                isDriveImporting={isDriveImporting}
              />
            </div>
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
                Ở chế độ batch, dùng nút "Thư mục (Audio + SRT + chương)" để chọn cả thư mục — hệ thống tự ghép mỗi
                audio với file .srt và .chapters.txt cùng tên trong cùng thư mục. Bạn vẫn có thể chọn/đổi .srt riêng
                cho từng item phía trên.
              </p>
            )}

            <SubtitleStylePanel
              mode={mode}
              styles={subtitleStyles}
              presets={subtitlePresets}
              fonts={subtitleFonts}
              formFont={subtitleFont}
              formFontScale={subtitleFontScale}
              selectedIds={activeSubtitleStyleIds}
              onSelectedChange={setSelectedSubtitleStyleIds}
              onApply={applySubtitleStyle}
              onDelete={(style) => void handleDeleteSubtitleStyle(style)}
              hiddenBuiltinCount={hiddenBuiltinStyleCount}
              onRestoreBuiltin={() => void handleRestoreBuiltinSubtitleStyles()}
              newName={newSubtitleStyleName}
              onNewNameChange={setNewSubtitleStyleName}
              isSaving={isSavingSubtitleStyle}
              onSave={() => void handleSaveSubtitleStyle()}
              disabled={isSubmitting}
            />

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <div className="grid content-start gap-2">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor="subtitleFont">Font phụ đề</Label>
                  <div className="flex gap-1">
                    {selectedSubtitleFont?.supportsIndonesian ? (
                      <Badge variant="secondary" className="rounded-full text-[10px]">
                        ID
                      </Badge>
                    ) : null}
                    {selectedSubtitleFont?.supportsVietnamese ? (
                      <Badge variant="secondary" className="rounded-full text-[10px]">
                        VI
                      </Badge>
                    ) : null}
                    {selectedSubtitleFont?.supportsThai ? (
                      <Badge variant="secondary" className="rounded-full text-[10px]">
                        TH
                      </Badge>
                    ) : null}
                  </div>
                </div>
                <div className="flex flex-wrap gap-1">
                  {SUBTITLE_FONT_LANGS.map((lang) => (
                    <Button
                      key={lang.value}
                      type="button"
                      size="sm"
                      variant={subtitleFontLang === lang.value ? "default" : "outline"}
                      className="h-7 px-2 text-xs"
                      onClick={() => handleSubtitleFontLangChange(lang.value)}
                    >
                      {lang.label}
                    </Button>
                  ))}
                </div>
                <select
                  id="subtitleFont"
                  value={subtitleFont}
                  onChange={(event) => setSubtitleFont(event.target.value)}
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                >
                  {subtitleFont && !filteredSubtitleFonts.some((font) => font.family === subtitleFont) ? (
                    <option value={subtitleFont}>{subtitleFont}</option>
                  ) : null}
                  {filteredSubtitleFonts.map((font) => (
                    <option key={font.family} value={font.family}>
                      {font.family}
                      {font.supportsIndonesian ? " [ID]" : ""}
                      {font.supportsVietnamese ? " [VI]" : ""}
                      {font.supportsThai ? " [TH]" : ""}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-muted-foreground">
                  {filteredSubtitleFonts.length} font
                  {subtitleFontLang === "all" ? "" : ` hỗ trợ ${SUBTITLE_FONT_LANGS.find((lang) => lang.value === subtitleFontLang)?.label}`}
                </p>
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
                  {/* Preset đã xoá khỏi danh sách cấu hình thì ẩn, trừ khi form đang dùng nó. */}
                  {subtitlePresets
                    .filter((preset) => !preset.hidden || preset.id === subtitlePreset)
                    .map((preset) => (
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

              <div className="grid content-start gap-2">
                <Label htmlFor="subtitleFontScale">Cỡ chữ</Label>
                <select
                  id="subtitleFontScale"
                  value={subtitleFontScale}
                  onChange={(event) => setSubtitleFontScale(event.target.value)}
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                >
                  <option value="0.8">Nhỏ</option>
                  <option value="1">Vừa (mặc định)</option>
                  <option value="1.25">Lớn</option>
                  <option value="1.5">Rất lớn</option>
                </select>
              </div>
            </div>

            <div className="grid gap-4 rounded-md border border-border bg-background/40 p-4 md:grid-cols-2 xl:grid-cols-3">
              <div className="md:col-span-2 xl:col-span-3">
                <p className="text-sm font-medium">Màu sắc</p>
                <p className="text-xs text-muted-foreground">
                  Để trống = dùng màu của hiệu ứng đã chọn. Ô nào đổi thì đè lên hiệu ứng đó.
                </p>
              </div>

              <ColorInput
                id="subtitleTextColor"
                label="Màu chữ"
                value={subtitleTextColor}
                fallback="#FFFFFF"
                onChange={setSubtitleTextColor}
              />
              <ColorInput
                id="subtitleOutlineColor"
                label="Màu viền"
                value={subtitleOutlineColor}
                fallback="#000000"
                onChange={setSubtitleOutlineColor}
              />
              <div className="grid content-start gap-2">
                <Label htmlFor="subtitleOutlineWidth">Độ dày viền (px)</Label>
                <Input
                  id="subtitleOutlineWidth"
                  type="number"
                  min={0}
                  max={20}
                  step={1}
                  placeholder="Theo preset"
                  value={subtitleOutlineWidth}
                  onChange={(event) => setSubtitleOutlineWidth(event.currentTarget.value)}
                />
              </div>

              <div className="flex items-center gap-2 self-end pb-2">
                <Checkbox
                  id="subtitleBackgroundEnabled"
                  checked={subtitleBackgroundEnabled}
                  onCheckedChange={(checked) => setSubtitleBackgroundEnabled(checked === true)}
                />
                <Label htmlFor="subtitleBackgroundEnabled" className="cursor-pointer">
                  Nền chữ
                </Label>
              </div>
              <ColorInput
                id="subtitleBackColor"
                label="Màu nền"
                value={subtitleBackColor}
                fallback="#000000"
                onChange={setSubtitleBackColor}
              />
              <div className="grid content-start gap-2">
                <Label htmlFor="subtitleBackOpacity">Độ mờ nền (%)</Label>
                <Input
                  id="subtitleBackOpacity"
                  type="number"
                  min={0}
                  max={100}
                  step={5}
                  disabled={!subtitleBackgroundEnabled}
                  value={subtitleBackOpacity}
                  onChange={(event) => setSubtitleBackOpacity(event.currentTarget.value)}
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

      {mode === "batch" && batchQueue && batchQueue.batches.length > 0 ? (
        <PageSection>
          <BatchQueuePanel
            queue={batchQueue}
            viewedBatchId={batchId}
            cancellingIds={cancellingQueueIds}
            onView={viewBatch}
            onCancel={(queuedBatchId) => void handleCancelQueuedBatch(queuedBatchId)}
          />
        </PageSection>
      ) : null}

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
        open={isLocalFolderDialogOpen}
        onOpenChange={(open) => {
          if (!isScanningLocalFolder) setIsLocalFolderDialogOpen(open);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Thêm audio từ thư mục local</DialogTitle>
            <DialogDescription>
              Dán đường dẫn thư mục trên chính máy đang chạy render. Backend đọc file tại chỗ theo đường dẫn — không
              upload, không copy, nên thêm bao nhiêu GB cũng được. File .srt cùng tên trong cùng thư mục được ghép tự động.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <Label htmlFor="localFolderPath">Đường dẫn thư mục</Label>
            <Input
              id="localFolderPath"
              placeholder="VD: E:\\AN - story\\THAI\\AUDIO\\kenh 11 den 100"
              value={localFolderPath}
              onChange={(event) => setLocalFolderPath(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !isScanningLocalFolder) void handleScanLocalFolder();
              }}
              disabled={isScanningLocalFolder}
            />
          </div>
          <DialogFooter>
            <Button type="button" onClick={() => void handleScanLocalFolder()} disabled={isScanningLocalFolder}>
              {isScanningLocalFolder ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <FolderSearch className="mr-2 size-4" />
              )}
              {isScanningLocalFolder ? "Đang quét..." : "Quét thư mục"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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

/**
 * Chon nhieu cau hinh overlay (song am / CTA) cho vong xoay cua batch.
 *
 * File da xu ly la MOV alpha nen khong co thumbnail — moi the chi hien ten +
 * thoi luong/be ngang, giong hang danh sach o trang cau hinh overlay.
 */
function OverlayRotationPicker({
  label,
  overlays,
  selectedIds,
  onChange,
  disabled,
  emptyHint,
  fallbackHint,
}: {
  label: string;
  overlays: Pick<WaveformOverlay, "id" | "name" | "durationSeconds" | "scaleWidth">[];
  selectedIds: string[];
  onChange: (next: string[]) => void;
  disabled: boolean;
  emptyHint: string;
  fallbackHint: string;
}) {
  const allIds = overlays.map((item) => item.id);
  const pickedCount = allIds.filter((id) => selectedIds.includes(id)).length;

  if (!overlays.length) {
    return (
      <div className="grid content-start gap-2">
        <Label className="text-sm">{label}</Label>
        <p className="text-xs text-muted-foreground">
          {emptyHint}{" "}
          <Link to="/story-video/settings" className="underline">
            Upload ở trang cấu hình overlay
          </Link>
          .
        </p>
      </div>
    );
  }

  return (
    <div className="grid content-start gap-2">
      <Label className="text-sm">{label}</Label>
      <div className="flex flex-wrap gap-2">
        {overlays.map((item) => {
          const picked = selectedIds.includes(item.id);
          return (
            <button
              key={item.id}
              type="button"
              disabled={disabled}
              onClick={() =>
                onChange(
                  picked
                    ? selectedIds.filter((id) => id !== item.id)
                    : [...selectedIds, item.id],
                )
              }
              className={`min-w-0 rounded-lg border px-3 py-1.5 text-left transition-colors disabled:opacity-50 ${
                picked && !disabled
                  ? "border-primary bg-primary/10"
                  : "border-border/70 bg-background/70"
              }`}
            >
              <span className="block truncate text-xs font-medium text-foreground">{item.name}</span>
              <span className="block text-[11px] text-muted-foreground">
                {item.durationSeconds}s · {item.scaleWidth ?? "?"}px
              </span>
            </button>
          );
        })}
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={disabled || pickedCount === allIds.length}
          onClick={() => onChange(allIds)}
          className="text-xs text-muted-foreground underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
        >
          Chọn tất cả
        </button>
        <button
          type="button"
          disabled={disabled || pickedCount === 0}
          onClick={() => onChange([])}
          className="text-xs text-muted-foreground underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
        >
          Bỏ chọn hết
        </button>
      </div>
      <p className="text-xs text-muted-foreground">
        {pickedCount > 0 && !disabled
          ? `Đang chọn ${pickedCount} cấu hình — mỗi ${pickedCount} video liên tiếp dùng đủ ${pickedCount} cấu hình khác nhau, thứ tự ngẫu nhiên.`
          : fallbackHint}
      </p>
    </div>
  );
}

/**
 * Chọn bản ghi kiểu dựng cho render.
 *
 * Bố cục: chọn nhiều = xoay vòng (mỗi video một bố cục, như sóng âm/CTA); ở render
 * đơn chỉ chọn một. Hiệu ứng bổ trợ (`applyAll`): mọi bản được chọn áp cho mọi video.
 * Bản bị thư viện đang chọn chặn vẫn hiện nhưng khoá lại, kèm lý do.
 */
function EditStyleRotationPicker({
  label,
  styles,
  typeById,
  selectedIds,
  onChange,
  blockedReason,
  single = false,
  applyAll = false,
  disabled,
  fallbackHint,
}: {
  label: string;
  styles: EditStyleRecord[];
  typeById: Map<string, EditStyleType>;
  selectedIds: string[];
  onChange: (next: string[]) => void;
  blockedReason: (style: EditStyleRecord) => string;
  single?: boolean;
  applyAll?: boolean;
  disabled: boolean;
  fallbackHint: string;
}) {
  if (!styles.length) {
    return (
      <div className="grid content-start gap-2">
        <Label className="text-sm">{label}</Label>
        <p className="text-xs text-muted-foreground">
          Chưa có bản nào đang bật.{" "}
          <Link to="/story-video/edit-styles" className="underline">
            Bật ở trang kiểu dựng
          </Link>
          .
        </p>
      </div>
    );
  }

  const usableIds = styles.filter((style) => !blockedReason(style)).map((style) => style.id);
  const picked = styles.filter((style) => selectedIds.includes(style.id) && !blockedReason(style));
  const toggle = (id: string) => {
    const on = selectedIds.includes(id);
    if (single) onChange(on ? [] : [id]);
    else onChange(on ? selectedIds.filter((item) => item !== id) : [...selectedIds, id]);
  };

  let summary = fallbackHint;
  if (picked.length && applyAll) {
    summary = `Đang chọn ${picked.length} hiệu ứng — áp cho mọi video.`;
  } else if (picked.length && single) {
    summary = `Video dùng bố cục "${picked[0].name}".`;
  } else if (picked.length) {
    summary = `Đang chọn ${picked.length} bố cục — mỗi ${picked.length} video liên tiếp dùng đủ ${picked.length} bố cục khác nhau, thứ tự ngẫu nhiên.`;
  }

  return (
    <div className="grid content-start gap-2">
      <Label className="text-sm">{label}</Label>
      <div className="flex flex-wrap gap-2">
        {styles.map((style) => {
          const type = typeById.get(style.type);
          const blocked = blockedReason(style);
          const on = selectedIds.includes(style.id) && !blocked;
          const details = [
            type && type.name !== style.name ? type.name : "",
            type?.requiresDecor ? "cần decor" : "",
            type?.needsChapters ? "dùng chương" : "",
            type?.labRatio ? `render ×${type.labRatio.toFixed(2)}` : "",
            blocked,
          ].filter(Boolean);
          return (
            <button
              key={style.id}
              type="button"
              disabled={disabled || Boolean(blocked)}
              title={blocked || type?.description}
              onClick={() => toggle(style.id)}
              className={`min-w-0 max-w-[18rem] rounded-lg border px-3 py-1.5 text-left transition-colors disabled:opacity-50 ${
                on ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
              }`}
            >
              <span className="block truncate text-xs font-medium text-foreground">{style.name}</span>
              {details.length ? (
                <span className="block truncate text-[11px] text-muted-foreground">{details.join(" · ")}</span>
              ) : null}
            </button>
          );
        })}
      </div>
      {!single ? (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={disabled || picked.length === usableIds.length}
            onClick={() => onChange(usableIds)}
            className="text-xs text-muted-foreground underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
          >
            Chọn tất cả
          </button>
          <button
            type="button"
            disabled={disabled || picked.length === 0}
            onClick={() => onChange([])}
            className="text-xs text-muted-foreground underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
          >
            Bỏ chọn hết
          </button>
        </div>
      ) : null}
      <p className="text-xs text-muted-foreground">{summary}</p>
    </div>
  );
}

// Cùng nhãn với ô "Cỡ chữ" của form.
const FONT_SCALE_LABELS: Record<string, string> = { "0.8": "Nhỏ", "1": "Vừa", "1.25": "Lớn", "1.5": "Rất lớn" };

function fontScaleLabel(scale: number | string): string {
  const key = String(Number(scale));
  return FONT_SCALE_LABELS[key] ?? `x${key}`;
}

/**
 * Danh sách cấu hình phụ đề đã lưu.
 *
 * Batch: bấm thẻ để chọn/bỏ khỏi vòng xoay (mỗi video bốc một cấu hình). Đơn: bấm
 * thẻ để nạp cấu hình vào form. Cấu hình có sẵn không mang font/cỡ chữ nên theo
 * form — batch tiếng Thái chọn font Thái một lần là mọi cấu hình có sẵn dùng theo.
 */
function SubtitleStylePanel({
  mode,
  styles,
  presets,
  fonts,
  formFont,
  formFontScale,
  selectedIds,
  onSelectedChange,
  onApply,
  onDelete,
  hiddenBuiltinCount,
  onRestoreBuiltin,
  newName,
  onNewNameChange,
  isSaving,
  onSave,
  disabled,
}: {
  mode: StoryMode;
  styles: SubtitleStyle[];
  presets: SubtitlePresetInfo[];
  fonts: SubtitleFontInfo[];
  formFont: string;
  formFontScale: string;
  /** Đã lọc theo cấu hình còn tồn tại. */
  selectedIds: string[];
  onSelectedChange: (next: string[]) => void;
  onApply: (style: SubtitleStyle) => void;
  onDelete: (style: SubtitleStyle) => void;
  hiddenBuiltinCount: number;
  onRestoreBuiltin: () => void;
  newName: string;
  onNewNameChange: (next: string) => void;
  isSaving: boolean;
  onSave: () => void;
  disabled: boolean;
}) {
  const isBatch = mode === "batch";
  const pickedCount = selectedIds.length;
  const presetName = (id: string) => presets.find((preset) => preset.id === id)?.name ?? id;
  const rotationHint =
    pickedCount === 0
      ? "Chưa chọn — cả batch dùng cấu hình ở form bên dưới."
      : `${
          pickedCount === 1
            ? "Đang chọn 1 cấu hình — cả batch dùng cấu hình này."
            : `Đang chọn ${pickedCount} — mỗi ${pickedCount} video liên tiếp dùng đủ ${pickedCount} cấu hình, thứ tự ngẫu nhiên.`
        } Màu và hiệu ứng ở form không áp dụng; số ký tự/dòng và số dòng vẫn dùng chung.`;

  return (
    <div className="grid gap-3 rounded-md border border-border bg-background/40 p-4">
      <div className="space-y-1">
        <p className="text-sm font-medium">Cấu hình phụ đề</p>
        <p className="text-xs text-muted-foreground">
          {isBatch
            ? "Bấm thẻ để chọn cho vòng xoay — mỗi video trong batch bốc một cấu hình. Nút mũi tên nạp cấu hình vào form để xem thử."
            : "Bấm thẻ để nạp cấu hình vào form bên dưới."}{" "}
          Cấu hình "Có sẵn" dùng font và cỡ chữ đang chọn ở form. Thùng rác để bỏ cấu hình xấu.
        </p>
      </div>

      {styles.length ? (
        <div className="flex max-h-72 flex-wrap gap-2 overflow-y-auto pr-1">
          {styles.map((style) => {
            const picked = isBatch && selectedIds.includes(style.id);
            const family = style.subtitleFont || formFont;
            const fontInfo = fonts.find((font) => font.family === family);
            const langs = [
              fontInfo?.supportsVietnamese ? "VI" : "",
              fontInfo?.supportsIndonesian ? "ID" : "",
              fontInfo?.supportsThai ? "TH" : "",
            ].filter(Boolean);
            const swatches = [
              { label: "Màu chữ", value: style.subtitleTextColor },
              { label: "Màu viền", value: style.subtitleOutlineColor },
              {
                label: "Màu nền",
                value: style.subtitleBackgroundEnabled ? style.subtitleBackColor ?? "#000000" : undefined,
              },
            ].filter((swatch) => swatch.value);
            const size = fontScaleLabel(style.subtitleFontScale ?? formFontScale);
            const meta = style.builtin
              ? `Theo form: ${family || "font mặc định"} · ${size}`
              : `${family || "font mặc định"} · ${presetName(style.subtitlePreset)} · ${size}`;
            return (
              <div
                key={style.id}
                className={`flex min-w-0 max-w-full items-stretch rounded-lg border transition-colors ${
                  picked ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
                }`}
              >
                <button
                  type="button"
                  disabled={disabled}
                  title={style.description || undefined}
                  onClick={() => {
                    if (!isBatch) {
                      onApply(style);
                      return;
                    }
                    onSelectedChange(
                      picked ? selectedIds.filter((id) => id !== style.id) : [...selectedIds, style.id],
                    );
                  }}
                  className="min-w-0 px-3 py-1.5 text-left disabled:opacity-50"
                >
                  <span className="flex items-center gap-1.5">
                    <span className="truncate text-xs font-medium text-foreground">{style.name}</span>
                    {style.builtin ? (
                      <Badge variant="secondary" className="rounded-full px-1.5 text-[10px]">
                        Có sẵn
                      </Badge>
                    ) : null}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    {swatches.length ? (
                      <span className="flex shrink-0 gap-0.5">
                        {swatches.map((swatch) => (
                          <span
                            key={swatch.label}
                            title={`${swatch.label} ${swatch.value}`}
                            className="size-2.5 rounded-sm border border-border"
                            style={{ backgroundColor: swatch.value }}
                          />
                        ))}
                      </span>
                    ) : null}
                    <span className="truncate">{meta}</span>
                    {langs.length ? <span className="shrink-0">[{langs.join(" ")}]</span> : null}
                  </span>
                </button>
                <div className="flex flex-col justify-center border-l border-border/60">
                  {isBatch ? (
                    <button
                      type="button"
                      title="Nạp vào form"
                      disabled={disabled}
                      onClick={() => onApply(style)}
                      className="px-1.5 py-1 text-muted-foreground hover:text-foreground disabled:opacity-50"
                    >
                      <ArrowDownToLine className="size-3.5" />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    title={style.builtin ? "Ẩn cấu hình có sẵn" : "Xoá cấu hình"}
                    disabled={disabled}
                    onClick={() => onDelete(style)}
                    className="px-1.5 py-1 text-muted-foreground hover:text-destructive disabled:opacity-50"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Chưa có cấu hình nào.</p>
      )}

      {isBatch && styles.length ? (
        <div className="grid gap-1">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={disabled || pickedCount === styles.length}
              onClick={() => onSelectedChange(styles.map((style) => style.id))}
              className="text-xs text-muted-foreground underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
            >
              Chọn tất cả
            </button>
            <button
              type="button"
              disabled={disabled || pickedCount === 0}
              onClick={() => onSelectedChange([])}
              className="text-xs text-muted-foreground underline underline-offset-2 disabled:opacity-40 disabled:no-underline"
            >
              Bỏ chọn hết
            </button>
          </div>
          <p className="text-xs text-muted-foreground">{rotationHint}</p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={newName}
          onChange={(event) => onNewNameChange(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !isSaving) onSave();
          }}
          placeholder="Tên cấu hình (VD: Thái – nền đỏ)"
          className="h-9 w-full sm:w-64"
          disabled={isSaving}
        />
        <Button type="button" variant="outline" size="sm" onClick={onSave} disabled={isSaving}>
          {isSaving ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
          Lưu form hiện tại thành cấu hình
        </Button>
        {hiddenBuiltinCount > 0 ? (
          <button
            type="button"
            onClick={onRestoreBuiltin}
            className="text-xs text-muted-foreground underline underline-offset-2"
          >
            Khôi phục {hiddenBuiltinCount} cấu hình có sẵn đã xoá
          </button>
        ) : null}
      </div>
    </div>
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
  onAddLocalFolder,
  onAddDriveFolder,
  onUpdate,
  onUpdateSubtitle,
  onUpdateChapters,
  onRemove,
  isDriveImporting,
}: {
  items: BatchItem[];
  audioInputKey: number;
  onAddAudio: (files: FileList | null) => void;
  onAddScript: () => void;
  onAddLocalFolder: () => void;
  onAddDriveFolder: () => void;
  onUpdate: (id: string, field: keyof BatchItem, value: string) => void;
  onUpdateSubtitle: (id: string, file: File | null) => void;
  onUpdateChapters: (id: string, file: File | null) => void;
  onRemove: (id: string) => void;
  isDriveImporting: boolean;
}) {
  // Non-standard attributes like `webkitdirectory` are not reliably applied via
  // JSX/spread, so set them imperatively whenever the input element mounts.
  const setFolderInputRef = useCallback((el: HTMLInputElement | null) => {
    if (el) {
      el.setAttribute("webkitdirectory", "");
      el.setAttribute("directory", "");
      el.setAttribute("mozdirectory", "");
    }
  }, []);

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">Batch items ({items.length})</h3>
        <div className="flex flex-wrap gap-2">
          <label className="cursor-pointer">
            <Input
              key={`audio-${audioInputKey}`}
              type="file"
              accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,.srt,.txt"
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
          <label className="cursor-pointer">
            <input
              key={`folder-${audioInputKey}`}
              ref={setFolderInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(event) => onAddAudio(event.currentTarget.files)}
            />
            <Button type="button" variant="outline" size="sm" asChild>
              <span>
                <FolderUp className="mr-2 size-4" />
                Thư mục (Audio + SRT + chương)
              </span>
            </Button>
          </label>
          <Button type="button" variant="outline" size="sm" onClick={onAddLocalFolder}>
            <HardDrive className="mr-2 size-4" />
            Thư mục local (không upload)
          </Button>
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
                    {item.inputType === "audio_file"
                      ? item.audioPath
                        ? "Audio local"
                        : "Audio"
                      : item.inputType === "drive_audio"
                        ? "Drive Audio"
                        : "Script"}
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
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <div className="grid gap-1.5">
                  <Label className="text-xs">{item.inputType === "script_url" ? "Script URL" : "File audio"}</Label>
                  {item.inputType === "script_url" ? (
                    <Input
                      placeholder="https://docs.google.com/document/d/..."
                      value={item.inputValue}
                      onChange={(event) => onUpdate(item.id, "inputValue", event.target.value)}
                    />
                  ) : (
                    <Input value={item.sourceName} readOnly title={item.audioPath ?? item.sourceName} />
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
                  ) : item.subtitleName ? (
                    <p className="text-xs text-muted-foreground" title={item.subtitlePath}>
                      Ghép local: {item.subtitleName}
                    </p>
                  ) : null}
                </div>
                <div className="grid gap-1.5">
                  <Label className="text-xs" title="Đặt tên chương, nhãn và câu nhấn; không có file thì video không hiện chữ chương">
                    File chương (.chapters.txt, tùy chọn)
                  </Label>
                  <Input
                    type="file"
                    accept=".txt"
                    onChange={(event) => onUpdateChapters(item.id, event.currentTarget.files?.[0] ?? null)}
                  />
                  {item.chaptersFile ? (
                    <p className="text-xs text-muted-foreground">Đã chọn: {item.chaptersFile.name}</p>
                  ) : item.chaptersName ? (
                    <p className="text-xs text-muted-foreground" title={item.chaptersPath}>
                      Ghép local: {item.chaptersName}
                    </p>
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
          {progress.status === "queued" ? (
            <p className="mt-0.5 text-sm font-medium text-foreground">
              Đang chờ trong hàng đợi{progress.queuePosition ? ` (vị trí ${progress.queuePosition})` : ""}: sẽ tự chạy khi
              batch trước xong.
            </p>
          ) : null}
          {progress.outputDir ? (
            <p className="mt-0.5 break-all text-xs text-muted-foreground">Thư mục output: {progress.outputDir}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3">
          {progress.status === "queued" || progress.status === "pending" || progress.status === "processing" || progress.status === "cancelling" ? (
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

function batchQueueStatusLabel(entry: StoryBatchQueueEntry): string {
  switch (entry.status) {
    case "queued":
      return entry.queuePosition ? `Chờ #${entry.queuePosition}` : "Đang chờ";
    case "pending":
      return "Chuẩn bị";
    case "processing":
      return "Đang chạy";
    case "cancelling":
      return "Đang hủy";
    case "cancelled":
      return "Đã hủy";
    case "failed":
      return "Lỗi";
    default:
      return entry.failedItems > 0 ? "Xong (có lỗi)" : "Hoàn tất";
  }
}

function batchQueueStatusVariant(status: StoryBatchQueueEntry["status"]): "default" | "secondary" | "destructive" | "outline" {
  if (status === "processing" || status === "pending") return "default";
  if (status === "queued") return "secondary";
  if (status === "failed") return "destructive";
  return "outline";
}

function BatchQueuePanel({
  queue,
  viewedBatchId,
  cancellingIds,
  onView,
  onCancel,
}: {
  queue: StoryBatchQueue;
  viewedBatchId: string | null;
  cancellingIds: Set<string>;
  onView: (batchId: string) => void;
  onCancel: (batchId: string) => void;
}) {
  const waitingCount = queue.batches.filter((entry) => entry.status === "queued").length;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-foreground">Hàng đợi batch</h3>
        <p className="text-sm text-muted-foreground">Mỗi lúc chỉ chạy 1 batch · {waitingCount} batch đang chờ</p>
      </div>
      <div className="grid gap-2">
        {queue.batches.map((entry) => {
          const isViewed = entry.batchId === viewedBatchId;
          const isCancelling = cancellingIds.has(entry.batchId);
          return (
            <div
              key={entry.batchId}
              className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3 ${
                isViewed ? "border-primary bg-primary/5" : "border-border/70 bg-background/70"
              }`}
            >
              <div className="grid min-w-0 gap-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={batchQueueStatusVariant(entry.status)}>{batchQueueStatusLabel(entry)}</Badge>
                  <span className="text-sm font-semibold text-foreground">{entry.batchId}</span>
                  <span className="text-xs text-muted-foreground">
                    {entry.completedItems}/{entry.totalItems} xong
                    {entry.failedItems ? ` · ${entry.failedItems} lỗi` : ""}
                    {entry.cancelledItems ? ` · ${entry.cancelledItems} hủy` : ""}
                  </span>
                </div>
                {entry.outputDir ? (
                  <span className="truncate text-xs text-muted-foreground" title={entry.outputDir}>
                    Output: {entry.outputDir}
                  </span>
                ) : null}
              </div>
              <div className="flex shrink-0 gap-2">
                {entry.status === "queued" ? (
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    className="h-7 text-xs"
                    disabled={isCancelling}
                    onClick={() => onCancel(entry.batchId)}
                  >
                    {isCancelling ? <Loader2 className="mr-1 size-3 animate-spin" /> : <Square className="mr-1 size-3" />}
                    Hủy
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant={isViewed ? "default" : "outline"}
                  size="sm"
                  className="h-7 text-xs"
                  disabled={isViewed}
                  onClick={() => onView(entry.batchId)}
                >
                  <Eye className="mr-1 size-3" />
                  {isViewed ? "Đang xem" : "Xem"}
                </Button>
              </div>
            </div>
          );
        })}
      </div>
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
        <span className="flex shrink-0 items-center gap-2 whitespace-nowrap">
          {item.layoutName ? <span>Bố cục: {item.layoutName}</span> : null}
          {item.modifierNames?.length ? <span>Bổ trợ: {item.modifierNames.join(", ")}</span> : null}
          {item.decorImageName ? <span>Decor: {item.decorImageName}</span> : null}
          {item.waveformName ? <span>Sóng âm: {item.waveformName}</span> : null}
          {item.ctaOverlayName ? <span>CTA: {item.ctaOverlayName}</span> : null}
          {item.subtitleStyleName ? <span>Phụ đề: {item.subtitleStyleName}</span> : null}
          {item.stage ? <span>{stageLabel(item.stage)}</span> : null}
        </span>
      </div>
      {item.error ? <div className="mt-2 rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{item.error}</div> : null}
    </div>
  );
}
