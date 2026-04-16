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

// --- Batch Pipeline ---

export interface BatchPipelineItem {
  docUrl: string;
  outputName: string;
  decorVideoId?: string;
}

export interface BatchPipelineResponse {
  batchId: string;
  totalUrls: number;
  message: string;
}

export interface BatchItemProgress {
  index: number;
  outputName: string;
  docUrl: string;
  decorVideoId: string;
  decorVideoName: string;
  status: "pending" | "running" | "completed" | "failed";
  stage: string;
  percent: number;
  message: string;
  outputVideo: string | null;
  jobId: string | null;
  error: string | null;
}

export interface BatchProgressResponse {
  batchId: string;
  status: "pending" | "running" | "completed" | "failed";
  totalUrls: number;
  completedUrls: number;
  startedAt: string;
  updatedAt: string;
  items: BatchItemProgress[];
}
