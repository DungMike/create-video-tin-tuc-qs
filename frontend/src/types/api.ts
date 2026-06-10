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

export interface StoryLibraryStats {
  totalClips: number;
  totalDuration: number;
  bySource: Record<string, number>;
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
  clipTags?: string[];
  crtSettings?: CRTSettings;
  waveformOverlayId?: string;
  voiceId?: string;
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
}

export interface CreateStoryBatchRequest {
  items: CreateStoryBatchItem[];
  sharedConfig: {
    clipTags?: string[];
    crtSettings?: CRTSettings;
    waveformOverlayId?: string;
    voiceId?: string;
  };
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

