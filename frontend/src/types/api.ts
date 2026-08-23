export interface ReviewClip {
  id: string;
  relativePath: string;
  sourceName: string | null;
  start: number;
  end: number;
  duration: number;
}

export interface LibraryAsset {
  assetId: string;
  relativePath: string;
  sourceJobId: string | null;
  sourceClipId: string | null;
  sourceName: string | null;
  start: number;
  end: number;
  duration: number;
  tags: string[];
  createdAt: string | null;
  updatedAt: string | null;
}

export interface JobData {
  jobId: string;
  createdAt: string | null;
  audioRelativePath: string | null;
  audioDuration: number;
  renderMode: "image_audio_only" | "mixed_media" | "video_only";
  imagePaths: string[];
  sourceVideos: string[];
  downloadErrors: string[];
  reviewClips: ReviewClip[];
  selectedClipIds: string[];
  selectedLibraryAssetIds: string[];
  clipTags: Record<string, string[]>;
  outputVideo: string | null;
}

export interface Pagination {
  page: number;
  pageSize: number;
  totalItems: number;
  totalPages: number;
  startItem: number;
  endItem: number;
}

export interface ReviewPageResponse {
  job: JobData;
  pageClips: ReviewClip[];
  pagination: Pagination;
  availableTags: string[];
  libraryAssetCount: number;
}

export interface ResourcesPageResponse {
  job: JobData;
  assets: LibraryAsset[];
  availableTags: string[];
  selectedTags: string[];
  totalAssetCount: number;
}

export interface ResultPageResponse {
  job: JobData;
}

export interface CreateJobResponse {
  jobId: string;
  redirectUrl: string;
}

export interface RenderRequest {
  selectedClipIds: string[];
  selectedLibraryAssetIds: string[];
  clipTags: Record<string, string[]>;
  currentPage: number;
  decorVideoId?: string;
}

export interface RenderResponse {
  jobId: string;
  outputVideo: string | null;
  redirectUrl: string;
}

export interface JobProgressLogItem {
  time: string;
  level: string;
  message: string;
}

export interface JobProgress {
  jobId: string;
  status: "idle" | "running" | "completed" | "failed";
  stage: string;
  percent: number;
  message: string;
  startedAt: string | null;
  updatedAt: string | null;
  totals: {
    images?: number;
    reviewClips?: number;
    libraryAssets?: number;
    segments?: number;
    chunks?: number;
  };
  current: {
    image?: number;
    segment?: number;
    chunk?: number;
  };
  recentLogs: JobProgressLogItem[];
  outputVideo?: string;
  timelineMode?: string;
}

export interface JobProgressResponse {
  progress: JobProgress;
}

export interface ApiErrorPayload {
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
}

export interface JobSelectionState {
  selectedClipIds: string[];
  selectedLibraryAssetIds: string[];
  clipTags: Record<string, string[]>;
}

export interface EffectAnimationPreset {
  id: string;
  type: "animation";
  name: string;
  description: string;
  ffmpegFilter: string;
  previewRelativePath: string;
  sourceImageRelativePath: string;
  durationSeconds: number;
  active: boolean;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface EffectTransitionPreset {
  id: string;
  type: "transition";
  name: string;
  description: string;
  xfadeTransition: string;
  previewRelativePath: string;
  sourceImageARelativePath: string;
  sourceImageBRelativePath: string;
  clipDurationSeconds: number;
  transitionDurationSeconds: number;
  active: boolean;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface EffectsLibraryResponse {
  animations: EffectAnimationPreset[];
  transitions: EffectTransitionPreset[];
}

export interface UpdateEffectsConfigRequest {
  activeAnimationIds: string[];
  activeTransitionIds: string[];
}

export interface GeneratedAudio {
  id: string;
  name: string;
  relativePath: string;
  duration: number;
  createdAt: string;
}

export interface AudioLibraryResponse {
  audios: GeneratedAudio[];
  defaultOutputName: string;
}

export interface DocsToAudioRequest {
  docUrl: string;
  outputName?: string;
  voiceId?: string;
  speed?: number;
  volume?: number;
}

export interface DocsToAudioResponse {
  audio: GeneratedAudio;
  chunkCount: number;
  textRelativePath: string;
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

export interface CloneVoiceResponse {
  voice: VoiceRecord;
}

export interface DecorVideo {
  id: string;
  name: string;
  filename: string;
  durationSeconds: number;
  createdAt: string;
}

export interface DecorVideoListResponse {
  decorVideos: DecorVideo[];
}

export interface DecorVideoUploadResponse {
  decorVideo: DecorVideo;
}

export interface DecorImage {
  id: string;
  channelId: string;
  name: string;
  filename: string;
  relativePath: string;
  width: number;
  height: number;
  titleOffsetX: number;
  titleOffsetY: number;
  titleMaxWidth: number;
  titleFontSize: number;
  titleColor: string;
  titleFont: string;
  createdAt: string;
}

export interface DecorImagesResponse {
  decorImages: DecorImage[];
  channelId: string;
}

export interface DecorImageUploadResponse {
  decorImage: DecorImage;
}

// --- Batch Pipeline ---

export type BatchInputMode = "docs" | "audio_upload";
export type BatchSourceType = "doc_url" | "uploaded_audio";

export interface BatchPipelineItem {
  sourceType: BatchSourceType;
  outputName: string;
  decorVideoId?: string;
  sourceText?: string;
  docUrl?: string | null;
  audioFileIndex?: number | null;
  sourceAudioName?: string | null;
  sourceAudioRelativePath?: string | null;
}

export interface SourceTextOption {
  key: string;
  label: string;
}

export interface SourceTextOptionsResponse {
  sourceTextOptions: SourceTextOption[];
  defaultKey: string;
}

export interface BatchPipelineResponse {
  batchId: string;
  totalUrls: number;
  inputMode: BatchInputMode;
  message: string;
}

export interface BatchSourceVideoResponse {
  batchSourceId: string;
  reviewClips: ReviewClip[];
  downloadErrors: string[];
  availableTags: string[];
}

export type BatchSourceRef =
  | { origin: "batch_source"; batchSourceId: string; clipId: string }
  | { origin: "library"; assetId: string };

export interface BatchSourceSet {
  batchSourceId: string;
  displayName: string;
  createdAt: string | null;
  sourceNames: string[];
  clipCount: number;
  validClipCount: number;
  downloadErrors: string[];
}

export interface BatchSourceSetsResponse {
  sourceSets: BatchSourceSet[];
}

export interface BatchSourceSetDetailResponse {
  sourceSet: BatchSourceSet;
  reviewClips: ReviewClip[];
  availableTags: string[];
}

export interface BatchSourceTagResponse {
  clipTags: Record<string, string[]>;
  availableTags: string[];
}

export interface BatchLibrarySourcesResponse {
  clips: ReviewClip[];
  assets: LibraryAsset[];
  availableTags: string[];
  selectedTags: string[];
  totalAssetCount: number;
}

export interface BatchDraftFile {
  index: number;
  name: string;
  filename: string;
  relativePath: string;
  size: number;
}

export interface BatchDraftResponse {
  draftId: string;
  createdAt: string;
  updatedAt: string;
  inputMode: BatchInputMode;
  voiceId?: string;
  speed?: number;
  volume?: number;
  docEntries: Array<{ docUrl: string; outputName: string; decorVideoId: string; sourceText: string }>;
  audioEntries: Array<{ outputName: string; decorVideoId: string; sourceText: string; sourceAudioName: string; audioFileIndex: number }>;
  sourceVideoLinks: string;
  selectedSourceRefs: BatchSourceRef[];
  clipTags: Record<string, string[]>;
  timelineConfig: Record<string, number>;
  imageFiles: BatchDraftFile[];
  audioFiles: BatchDraftFile[];
}

export interface BatchRetryResponse {
  batchId: string;
  failedUrls: number;
  message: string;
}

export interface BatchChunkSummary {
  completed: number;
  failed: number;
  total: number;
}

export interface BatchItemProgress {
  index: number;
  sourceType: BatchSourceType;
  outputName: string;
  docUrl: string | null;
  sourceAudioName: string | null;
  sourceAudioRelativePath: string | null;
  decorVideoId: string;
  decorVideoName: string;
  sourceText: string;
  status: "pending" | "running" | "completed" | "failed";
  stage: string;
  percent: number;
  message: string;
  outputVideo: string | null;
  jobId: string | null;
  error: string | null;
  audioRelativePath: string | null;
  audioStatus: "none" | "partial" | "ready";
  chunkSummary: BatchChunkSummary;
  retryable: boolean;
  failureCode: string | null;
  failureStage: string | null;
  lastRetryAt: string | null;
}

export interface BatchProgressResponse {
  batchId: string;
  inputMode: BatchInputMode;
  status: "pending" | "running" | "completed" | "failed";
  totalUrls: number;
  completedUrls: number;
  failedUrls: number;
  canRetryFailed: boolean;
  retryFailedLabel: string;
  expiresAt: string;
  startedAt: string;
  updatedAt: string;
  items: BatchItemProgress[];
  sourceVideoPool?: {
    batchSourceId: string;
    selectedClips: number;
    downloadErrors: string[];
  };
}

// --- Channel Management ---

export interface ChannelGroup {
  groupId: string;
  groupName: string;
  language: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface Channel {
  channelId: string;
  channelName: string;
  groupId: string;
  voiceId: string;
  introVideoPath?: string;
  transitionVideoPath: string;
  outroVideoPath?: string;
  decorVideoId: string;
  sourceText: string;
  isActive: boolean;
  createdAt?: string;
  updatedAt?: string;
}

export interface ChannelsResponse {
  channels: Channel[];
  groups: ChannelGroup[];
}

// --- News Bulletin ---

export interface ParsedNewsItem {
  id: number;
  resumeText: string;
  detailText: string;
}

export interface ParsedScript {
  intro: { text: string };
  detailIntro?: { text: string };
  newsItems: ParsedNewsItem[];
  outro: { text: string };
}

export interface ParseScriptResponse {
  parsed: ParsedScript;
  newsCount: number;
}

export interface BulletinCreateResponse {
  bulletinId: string;
  newsCount: number;
  channelIds: string[];
  channelDecorImageIds?: Record<string, string>;
}

export interface BulletinResourceSummary {
  [newsId: string]: {
    vidClips: number;
    images: number;
  };
}

export interface BulletinResourceItem {
  filename: string;
  relativePath: string;
}

export interface BulletinResourceDetails {
  [newsId: string]: {
    vidClips: BulletinResourceItem[];
    images: BulletinResourceItem[];
  };
}

export interface BulletinChannelProgress {
  channelId: string;
  channelName: string;
  status: "pending" | "running" | "completed" | "failed";
  stage: string;
  percent: number;
  message: string;
  outputVideo: string | null;
  error: string | null;
}

export interface BulletinDetailResponse {
  bulletinId: string;
  scriptText: string;
  parsedScript: ParsedScript;
  channelIds: string[];
  newsCount: number;
  resourceSummary: BulletinResourceSummary;
  resourceDetails: BulletinResourceDetails;
  segmentResourceSummary?: Record<string, { vidClips: number; images: number }>;
  status: string;
  channels: Record<string, BulletinChannelProgress>;
  createdAt: string;
  updatedAt: string;
}

export interface BulletinProgressResponse {
  bulletinId: string;
  status: string;
  newsCount: number;
  channelIds: string[];
  channels: Record<string, BulletinChannelProgress>;
  createdAt: string;
  updatedAt: string;
}

export interface BulletinScriptUpdateResponse {
  bulletinId: string;
  parsed: ParsedScript;
  newsCount: number;
  resourceSummary: BulletinResourceSummary;
  resourceDetails: BulletinResourceDetails;
  resourcesReset: boolean;
}

export interface BulletinListItem {
  bulletinId: string;
  status: string;
  newsCount: number;
  channelCount: number;
  createdAt: string;
  updatedAt: string;
}

// --- Story Video ---

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

// === YouTube Downloader (standalone page) ===

export type YoutubeDownloadItemStatus =
  | "pending"
  | "downloading"
  | "extracting"
  | "completed"
  | "failed"
  | "cancelled";

export interface YoutubeDownloadItem {
  id: string;
  url: string;
  title: string;
  status: YoutubeDownloadItemStatus;
  percent: number;
  speed: string;
  eta: string;
  videoFile: string;
  audioFile: string;
  sizeBytes: number;
  error: string | null;
}

export interface YoutubeDownloadJob {
  sessionId: string;
  status: "downloading" | "completed" | "failed" | "cancelled";
  outputDir: string;
  downloadVideo: boolean;
  extractAudio: boolean;
  current: number;
  total: number;
  message: string;
  cancelRequested: boolean;
  items: YoutubeDownloadItem[];
}

export interface YoutubeDownloadFile {
  name: string;
  kind: string;
  sizeBytes: number;
  modifiedAt: string;
}

export interface YoutubeDownloadFilesResponse {
  outputDir: string;
  files: YoutubeDownloadFile[];
}

export interface YoutubeDownloadConfigResponse {
  defaultOutputDir: string;
}

export interface CRTSettings {
  noiseStrength: number;
  scanlineOpacity: number;
  vignetteAngle: string;
  colorBleed: boolean;
  flickerIntensity: number;
}

export interface CRTPreset {
  name: string;
  label: string;
  settings: CRTSettings;
}

export interface CRTPresetsResponse {
  presets: CRTPreset[];
  currentConfig: CRTSettings;
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
  // "alpha" (lumakey, default) or "screen" (black-background textures: dust, light leak...).
  blendMode?: "alpha" | "screen";
  opacity: number;
  tolerance: number;
  softness: number;
  sourceUrl?: string;
  error?: string | null;
  createdAt: string;
  updatedAt?: string;
}

export interface TVNoiseOverlayJob {
  sessionId: string;
  status: "processing" | "downloading" | "completed" | "failed";
  action: "upload" | "import_youtube" | string;
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

export interface CreateStoryVideoRequest {
  inputType: "audio_file" | "script_url";
  inputValue: string;
  outputName: string;
  libraryId?: string;
  clipTags?: string[];
  crtSettings?: CRTSettings;
  waveformOverlayId?: string;
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
  inputValue: string;
  outputName: string;
  subtitleFile?: string;
}

export interface CreateStoryBatchRequest {
  items: CreateStoryBatchItem[];
  sharedConfig: {
    libraryId?: string;
    introId?: string;
    /** Optimize mode: suspend competing apps + boost ffmpeg priority for this batch. */
    optimizeMode?: boolean;
    clipTags?: string[];
    crtSettings?: CRTSettings;
    waveformOverlayId?: string;
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
  supportsKorean: boolean;
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
  createdAt: string;
  updatedAt?: string;
}

