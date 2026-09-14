export interface ApiErrorPayload {
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
}

export interface VoiceRecord {
  voiceId: string;
  voiceName: string;
  sourceFileName: string;
  createdAt: string;
}

export interface VoicesResponse {
  voices: VoiceRecord[];
  defaultVoiceId: string;
}

export type StoryVideoProvider = "pixabay" | "pexels";

export interface StoryLibrary {
  id: string;
  name: string;
  isDefault: boolean;
  clipCount: number;
  createdAt: string;
  updatedAt?: string;
  // Present when this library is a pre-baked "styled" library.
  styled?: boolean;
  styleId?: string;
  styleLabel?: string;
  sourceLibraryId?: string;
  // Present when style + waveform + CTA are all baked in (runtime = subtitle only).
  fullyBaked?: boolean;
  clipDuration?: number;
  waveformLabel?: string;
  ctaLabel?: string;
  // Bake state, carried on the library so a paused bake can be resumed from the
  // library list even after the server restarted. A paused library is usable —
  // it just holds fewer clips than its source until the bake finishes.
  bakeStatus?: StoryLibraryBakeStatus;
  bakeJobId?: string;
  bakeCompleted?: number;
  bakeTotal?: number;
}

export interface BakeStoryLibraryRequest {
  sourceLibraryId: string;
  name: string;
  styleId?: string;
  params?: TVEffectParams;
  // "full" (default) bakes waveform + CTA in too; "style" bakes style only.
  mode?: "style" | "full";
  waveformId?: string;
  ctaId?: string;
  unitSeconds?: number;
}

export interface BakeStoryLibraryResponse {
  jobId: string;
  targetLibraryId: string;
}

export type StoryLibraryBakeStatus =
  | "pending"
  | "running"
  | "pausing"
  | "paused"
  | "cancelling"
  | "completed"
  | "partial"
  | "cancelled"
  | "failed";

export interface StoryLibraryBakeJob {
  jobId: string;
  status: StoryLibraryBakeStatus;
  sourceLibraryId: string;
  targetLibraryId: string;
  targetName: string;
  styleId: string;
  styleLabel: string;
  /** Clips in the whole source library — stable across pause/resume. */
  total: number;
  /** Clips baked so far, including earlier runs of the same job. */
  completed: number;
  /** Clips left in the current run. */
  remaining?: number;
  failed: number;
  percent: number;
  resumed?: boolean;
  message: string;
  error?: string;
  startedAt: string;
  updatedAt: string;
}

export interface StoryLibrariesResponse {
  libraries: StoryLibrary[];
  defaultLibraryId: string;
}

// Intro clips prepended to the front of each batch video.
export interface StoryIntro {
  id: string;
  name: string;
  relativePath: string;
  duration: number;
  createdAt: string;
}

export interface StoryIntrosResponse {
  intros: StoryIntro[];
}

export interface StoryIntroMutationResponse {
  intro: StoryIntro;
}

export interface CreateStoryLibraryRequest {
  name: string;
}

export interface UpdateStoryLibraryRequest {
  name: string;
}

export interface StoryLibraryMutationResponse {
  library: StoryLibrary;
}

export interface StoryLibraryDeleteResponse {
  deleted: boolean;
  libraryId: string;
  deletedClips: number;
  /** Library promoted to default when the deleted one was the default; "" if none. */
  newDefaultLibraryId: string;
}

export interface StoryClip {
  id: string;
  sourceType: StoryVideoProvider | "local" | "local_upload" | "direct";
  sourceName: string;
  relativePath: string;
  duration: number;
  tags: string[];
  createdAt: string;
}

export interface StoryProviderVideo {
  provider: StoryVideoProvider;
  id: string;
  title: string;
  tags: string;
  thumbnailUrl: string;
  previewUrl: string;
  pageUrl: string;
  duration: number;
  width: number;
  height: number;
  author: string;
}

export interface StoryProviderVideoSearchResponse {
  provider: StoryVideoProvider;
  items: StoryProviderVideo[];
  total: number;
  page: number;
  perPage: number;
}

// === Bulk harvest: tải hết video theo từ khoá trước, chọn lọc sau ===

export type StoryHarvestStatus =
  | "running"
  | "cancelling"
  | "completed"
  | "cancelled"
  | "failed"
  | "stopped_disk";

export interface StoryHarvestJob {
  jobId: string;
  status: StoryHarvestStatus;
  libraryId: string;
  keywords: string[];
  providers: StoryVideoProvider[];
  tags: string[];
  landscapeOnly: boolean;
  /** 0 = tải hết theo totalHits của provider. */
  maxPerKeyword: number;
  keywordIndex: number;
  keywordTotal: number;
  currentKeyword: string;
  currentProvider: StoryVideoProvider;
  /** Số request API đã dùng — chỉ search mới tốn quota, tải file thì không. */
  searchRequests: number;
  downloaded: number;
  skipped: number;
  failed: number;
  bytesDownloaded: number;
  message: string;
  startedAt: string;
  updatedAt: string;
  error: string | null;
  /** Chỉ có ở endpoint list. */
  keptItems?: number;
  totalItems?: number;
}

export interface StoryHarvestItem {
  itemId: string;
  provider: StoryVideoProvider;
  videoId: string;
  keyword: string;
  title: string;
  filename: string;
  relativePath: string;
  /** URL /media/... — file nằm trên máy này, preview không gọi tới provider. */
  previewPath: string;
  duration: number;
  width: number;
  height: number;
  pageUrl: string;
  author: string;
  bytes: number;
  status: "kept" | "deleted" | "committed";
  createdAt: string;
}

export interface StoryHarvestItemsResponse {
  items: StoryHarvestItem[];
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
  keywords: string[];
  keptTotal: number;
}

export interface StartStoryHarvestRequest {
  libraryId: string;
  keywords: string[];
  providers: StoryVideoProvider[];
  tags?: string[];
  landscapeOnly?: boolean;
  maxPerKeyword?: number;
}

export type StoryHarvestDeleteRequest =
  | { scope: "ids"; itemIds: string[] }
  | { scope: "keyword"; keyword: string }
  | { scope: "all" };

export interface StoryHarvestDeleteResponse {
  scope: "ids" | "keyword" | "all";
  deletedCount: number;
  failedItemIds: string[];
  remainingCount: number;
}

export interface CommitStoryHarvestResponse {
  sessionId: string;
  total: number;
}

export interface StoryLibraryResponse {
  clips: StoryClip[];
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
}

// === Prefetch branch: download every search hit first, review locally, then cut ===

export type StoryPrefetchOrientation = "landscape" | "portrait" | "square";

export type StoryPrefetchStatus =
  | "searching"
  | "downloading"
  | "cancelling"
  /** Sweep finished; waiting for the user to prune before cutting. */
  | "ready"
  | "committing"
  | "completed"
  | "cancelled"
  | "failed";

export type StoryPrefetchItemStatus = "downloaded" | "failed" | "deleted" | "committed";

export interface StoryPrefetchItem {
  /** `"pixabay:12345"` — stable across the whole session. */
  itemId: string;
  provider: StoryVideoProvider;
  videoId: string;
  title: string;
  pageUrl: string;
  thumbnailUrl: string;
  width: number;
  height: number;
  duration: number;
  author: string;
  filename: string;
  /** Append to `/media/` to play the staged file locally. */
  mediaPath: string;
  sizeBytes: number;
  status: StoryPrefetchItemStatus;
  clipCount: number;
  error: string | null;
}

export interface StoryPrefetchFilters {
  orientation: StoryPrefetchOrientation | null;
  minWidth: number | null;
  minHeight: number | null;
  skipImported: boolean;
}

export interface StoryPrefetchSession {
  sessionId: string;
  libraryId: string;
  provider: StoryVideoProvider;
  query: string;
  tags: string[];
  filters: StoryPrefetchFilters;
  status: StoryPrefetchStatus;
  current: number;
  total: number;
  message: string;
  pagesFetched: number;
  providerTotal: number;
  skippedImported: number;
  skippedFiltered: number;
  failedCount: number;
  downloadedBytes: number;
  addedClips: number;
  /** Set when the page backstop, not the provider, ended the sweep — the result
   *  is partial and the UI must say so rather than implying full coverage. */
  truncated: string | null;
  items: StoryPrefetchItem[];
  error: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface StoryPrefetchStartRequest {
  provider: StoryVideoProvider;
  query: string;
  tags?: string[];
  orientation?: StoryPrefetchOrientation | null;
  minWidth?: number | null;
  minHeight?: number | null;
  skipImported?: boolean;
}

export interface StoryPrefetchDiscardResponse {
  sessionId: string;
  requestedCount: number;
  deletedCount: number;
  failedItemIds: string[];
  session: StoryPrefetchSession;
}

export interface StoryLibraryStats {
  totalClips: number;
  totalDuration: number;
  bySource: Record<string, number>;
  clipDurationSeconds: number;
}

export interface StoryLibraryNormalizeCombo {
  spec: string;
  count: number;
}

export interface StoryLibraryNormalizeLibraryScan {
  libraryId: string;
  name: string;
  totalClips: number;
  mismatchedClips: number;
  combos: StoryLibraryNormalizeCombo[];
}

export interface StoryLibraryNormalizeScan {
  expected: string;
  libraries: StoryLibraryNormalizeLibraryScan[];
  totalClips: number;
  mismatchedClips: number;
}

export interface StoryLibraryNormalizeJob {
  jobId: string;
  status: "pending" | "running" | "cancelling" | "completed" | "partial" | "cancelled" | "failed";
  libraryIds: string[];
  expected: string;
  total: number;
  completed: number;
  failed: number;
  percent: number;
  message: string;
  startedAt: string;
  updatedAt: string;
}

export interface StartStoryLibraryNormalizeResponse {
  jobId: string;
  total: number;
}

export type StoryLibraryBulkDeleteRequest =
  | { scope: "ids"; clipIds: string[] }
  | { scope: "all" };

export interface StoryLibraryBulkDeleteResponse {
  scope: "ids" | "all";
  requestedCount: number;
  deletedCount: number;
  remainingCount: number;
  missingClipIds: string[];
  failedClipIds: string[];
  failedFiles: string[];
}

export interface DownloadProgress {
  sessionId: string;
  status: "downloading" | "splitting" | "completed" | "failed";
  current: number;
  total: number;
  message: string;
  addedClips: number;
}

export interface CRTSettings {
  noiseStrength: number;
  scanlineOpacity: number;
  vignetteAngle: string;
  colorBleed: boolean;
  flickerIntensity: number;
}

export interface CRTDemoResponse {
  demoPath: string;
}

export type TVEffectTone = "none" | "warm" | "cool" | "vintage" | "sepia" | "bw" | "fade";

export interface TVEffectParams {
  tone: TVEffectTone;
  saturation: number;
  contrast: number;
  brightness: number;
  gamma: number;
  noise: number;
  chromaShift: number;
  scanlines: number;
  vignette: number;
  flicker: number;
  flickerSpeed: number;
  soften: number;
  bloom: number;
  bloomThreshold: number;
  bloomRadius: number;
}

export interface TVEffectStyle {
  id: string;
  name: string;
  description: string;
  previewPath: string | null;
  params?: TVEffectParams;
}

export interface TVEffectStylesResponse {
  styles: TVEffectStyle[];
  selectedId: string;
  customParams: TVEffectParams;
  customPreviewPath: string | null;
}

export interface TVEffectPreviewResponse {
  previewPath: string;
}

export interface TVEffectCustomSaveResponse {
  selectedId: string;
  customParams: TVEffectParams;
}

export interface TVNoiseOverlay {
  id: string;
  name: string;
  filename: string;
  processedFilename?: string;
  relativePath?: string;
  processedRelativePath?: string;
  durationSeconds: number;
  status: "processing" | "ready" | "failed";
  enabled: boolean;
  order: number;
  // "alpha" (lumakey, default), "screen" (black-background textures: dust, light
  // leak...), or "luma" (alpha from the source's own brightness — sparkle layers).
  blendMode?: "alpha" | "screen" | "luma";
  opacity: number;
  tolerance: number;
  softness: number;
  // "luma" only: pushes the layer's colour to white and normalises the alpha peak
  // before opacity is applied.
  lumaGain?: number;
  // Set on generated layers (sparkle); absent on uploaded/imported ones.
  kind?: string;
  meta?: { presetId?: string; params?: Record<string, number> };
  sourceUrl?: string;
  error?: string | null;
  createdAt: string;
  updatedAt?: string;
}

export interface SparklePreset {
  id: string;
  name: string;
  description: string;
  paramsUsed: string[];
  params: Record<string, number>;
}

export interface SparkleParamSpec {
  min: number;
  max: number;
  default: number;
}

export interface SparklePresetsResponse {
  presets: SparklePreset[];
  paramSpec: Record<string, SparkleParamSpec>;
}

export interface SparkleCreateResponse {
  sessionId: string;
  presetId: string;
  params: Record<string, number>;
}

export interface EffectPreviewClip {
  name: string;
  durationSeconds: number;
}

export interface EffectPreviewSource {
  id: string;
  name: string;
  clipCount: number;
  clips: EffectPreviewClip[];
  durationSeconds: number;
  basePath: string;
  previewPath: string | null;
  previewSeconds?: number;
  appliedStyle?: boolean;
  appliedOverlays?: boolean;
  appliedCompare?: boolean;
  appliedDecorId?: string | null;
  appliedDecorName?: string | null;
  appliedLayers?: { id?: string; name?: string; kind?: string; blendMode?: string; opacity?: number }[];
  createdAt: string;
  updatedAt?: string;
}

export interface EffectPreviewJob {
  sessionId: string;
  status: "processing" | "completed" | "failed";
  message: string;
  sourceId: string;
  source: EffectPreviewSource | null;
  error?: string | null;
}

export interface TVNoiseOverlayJob {
  sessionId: string;
  status: "processing" | "downloading" | "generating" | "completed" | "failed";
  action: "upload" | "import_youtube" | "create_sparkle" | string;
  current: number;
  total: number;
  message: string;
  overlayId?: string;
  error?: string | null;
}

export interface TVNoiseOverlayUploadResponse {
  sessionId: string;
  overlay: TVNoiseOverlay;
}

export interface DriveAudioImportItem {
  token: string;
  fileName: string;
  outputName: string;
  durationSeconds: number;
  sizeBytes: number;
}

export interface DriveAudioImportSkippedItem {
  fileName: string;
  reason: string;
}

export interface DriveAudioImportProgress {
  sessionId: string;
  status: "listing" | "downloading" | "completed" | "failed";
  current: number;
  total: number;
  message: string;
  items: DriveAudioImportItem[];
  skipped: DriveAudioImportSkippedItem[];
  error?: string | null;
}

export interface LocalAudioFolderItem {
  audioPath: string;
  audioName: string;
  outputName: string;
  subtitlePath: string;
  subtitleName: string;
  sizeMb: number;
}

export interface LocalAudioFolderScan {
  path: string;
  items: LocalAudioFolderItem[];
  totalSizeMb: number;
  pairedCount: number;
  orphanSubtitles: number;
}

export interface CreateStoryVideoRequest {
  inputType: "audio_file" | "script_url";
  inputValue: string;
  outputName: string;
  /** @deprecated Dùng `libraryIds`; backend vẫn nhận key này cho client cũ. */
  libraryId?: string;
  /**
   * Các thư viện clip nguồn cho lần render này. Backend gộp clip của tất cả
   * thư viện thành một pool rồi rút ngẫu nhiên không lặp lại.
   */
  libraryIds?: string[];
  clipTags?: string[];
  crtSettings?: CRTSettings;
  /** Bỏ qua bước hiệu ứng TV khi render (thư viện chưa bake hiệu ứng). */
  skipTvEffect?: boolean;
  waveformOverlayId?: string;
  /** Ảnh decor (khung TV) dùng cho render đơn. "" = tắt. */
  decorImageId?: string;
  voiceId?: string;
  subtitleFont?: string;
  subtitlePreset?: string;
  subtitleMaxCharsPerLine?: number;
  subtitleMaxLines?: number;
  subtitleFontScale?: number;
}

export interface StoryVideoProgress {
  storyId: string;
  status: "pending" | "running" | "processing" | "cancelling" | "cancelled" | "completed" | "failed";
  stage: string;
  percent: number;
  message: string;
  outputName: string;
  result?: { videoPath: string };
  error?: string;
}

export interface CreateStoryBatchItem {
  id: string;
  inputType: "audio_file" | "script_url" | "drive_audio";
  /** Upload index ("0", "1", ...) for uploaded audio, or an absolute local path. */
  inputValue: string;
  outputName: string;
  /** Upload index for an uploaded .srt, or an absolute local path. */
  subtitleFile?: string;
}

export interface CreateStoryBatchRequest {
  items: CreateStoryBatchItem[];
  sharedConfig: {
    /** @deprecated Dùng `libraryIds`; backend vẫn nhận key này cho client cũ. */
    libraryId?: string;
    /** Các thư viện clip nguồn dùng chung cho cả batch (pool gộp). */
    libraryIds?: string[];
    introId?: string;
    /** Optimize mode: suspend competing apps + boost ffmpeg priority for this batch. */
    optimizeMode?: boolean;
    clipTags?: string[];
    crtSettings?: CRTSettings;
    /** Bỏ qua bước hiệu ứng TV khi render (thư viện chưa bake hiệu ứng). */
    skipTvEffect?: boolean;
    /** @deprecated Dùng `waveformOverlayIds`; backend vẫn nhận key này cho client cũ. */
    waveformOverlayId?: string;
    /**
     * Sóng âm tham gia xoay vòng cho batch này (cùng cơ chế bộ bài xáo như
     * `decorImageIds`). Rỗng = dùng sóng âm mặc định ở trang cấu hình.
     */
    waveformOverlayIds?: string[];
    /**
     * CTA overlay tham gia xoay vòng cho batch này. Rỗng = dùng CTA đang bật ở
     * trang cấu hình (hành vi cũ).
     */
    ctaOverlayIds?: string[];
    /**
     * Ảnh decor tham gia xoay vòng cho batch này. Backend xáo bộ bài rồi chia
     * lần lượt, nên mỗi N video liên tiếp dùng đủ N ảnh theo thứ tự ngẫu nhiên.
     * Rỗng = không dùng ảnh decor.
     */
    decorImageIds?: string[];
    voiceId?: string;
    subtitleFont?: string;
    subtitlePreset?: string;
    subtitleMaxCharsPerLine?: number;
    subtitleMaxLines?: number;
    subtitleFontScale?: number;
  };
}

export interface SubtitleFontInfo {
  family: string;
  supportsIndonesian: boolean;
  supportsVietnamese: boolean;
  supportsThai: boolean;
  source: "system" | "user";
}

export interface SubtitlePresetInfo {
  id: string;
  name: string;
  description: string;
}

export interface StoryBatchItemProgress {
  id: string;
  outputName: string;
  status: "pending" | "processing" | "cancelling" | "cancelled" | "completed" | "failed";
  stage?: string;
  percent?: number;
  message?: string;
  result?: { videoPath: string };
  error?: string;
  /** Decor image this item drew from the batch rotation; "" when decor is off. */
  decorImageName?: string;
  /** Sóng âm item này bốc được; "" khi batch không xoay vòng sóng âm. */
  waveformName?: string;
  /** CTA overlay item này bốc được; "" khi batch không xoay vòng CTA. */
  ctaOverlayName?: string;
}

export interface StoryBatchProgress {
  batchId: string;
  status: "pending" | "processing" | "cancelling" | "cancelled" | "completed" | "failed";
  totalItems: number;
  completedItems: number;
  failedItems: number;
  cancelledItems: number;
  currentIndex: number;
  items: StoryBatchItemProgress[];
}

/** Rectangle, in 1920x1080 output coordinates, that the video is fitted into. */
export interface StoryDecorFrame {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** How the transparent screen area of a decor image is produced. */
export type StoryDecorMaskMode = "chroma" | "manual";

/**
 * A full-frame photo with a transparent screen area the story video plays
 * inside. `processedRelativePath` is the RGBA PNG the render overlays;
 * `relativePath` is the untouched upload.
 *
 * The hole comes either from keying a green screen already in the photo
 * (`maskMode: "chroma"`) or from punching the `frame` rectangle straight into
 * the alpha (`maskMode: "manual"`), which is what makes a photo with no green
 * screen usable at all.
 */
export interface StoryDecorImage {
  id: string;
  name: string;
  /** Theme this decor belongs to ("den chua", "lang que"...); "" = chua phan nhom. */
  group?: string;
  filename: string;
  relativePath?: string;
  processedFilename?: string;
  processedRelativePath?: string;
  keyColor?: string;
  similarity?: number;
  blend?: number;
  frame: StoryDecorFrame;
  /** Missing on records created before manual mode existed; treat as "chroma". */
  maskMode?: StoryDecorMaskMode;
  /** Rounded corners of the self-drawn area, px in 1920x1080. 0 = square. */
  cornerRadius?: number;
  /** Grows the video past the frame edges so a green fringe can never show. */
  overscan?: number;
  /** False when the green region had to be placed by hand. */
  autoDetected?: boolean;
  enabled?: boolean;
  createdAt: string;
  updatedAt?: string;
}

export interface WaveformOverlay {
  id: string;
  name: string;
  filename: string;
  processedFilename?: string;
  relativePath?: string;
  processedRelativePath?: string;
  durationSeconds: number;
  isDefault?: boolean;
  keyColor?: string;
  similarity?: number;
  blend?: number;
  scaleWidth?: number;
  position?: "top_left" | "top_right" | "bottom_left" | "bottom_right";
  margin?: number;
  /**
   * Free placement: top-left corner in output-frame px. Unset = use position +
   * margin. Sending null on an update clears it back to the corner.
   */
  x?: number | null;
  y?: number | null;
  /** Real size of the processed alpha MOV, so the editor can draw it true to scale. */
  processedWidth?: number;
  processedHeight?: number;
  createdAt: string;
  updatedAt?: string;
}

export interface CtaOverlay {
  id: string;
  name: string;
  filename: string;
  processedFilename?: string;
  relativePath?: string;
  processedRelativePath?: string;
  durationSeconds: number;
  isDefault?: boolean;
  enabled?: boolean;
  keyColor?: string;
  similarity?: number;
  blend?: number;
  scaleWidth?: number;
  position?: "top_left" | "top_right" | "bottom_left" | "bottom_right";
  margin?: number;
  /**
   * Free placement: top-left corner in output-frame px. Unset = use position +
   * margin. Sending null on an update clears it back to the corner.
   */
  x?: number | null;
  y?: number | null;
  /** Real size of the processed alpha MOV, so the editor can draw it true to scale. */
  processedWidth?: number;
  processedHeight?: number;
  createdAt: string;
  updatedAt?: string;
}
