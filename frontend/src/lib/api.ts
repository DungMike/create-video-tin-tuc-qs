import type {
  ApiErrorPayload,
  AudioLibraryResponse,
  BatchDraftResponse,
  BatchLibrarySourcesResponse,
  BatchPipelineResponse,
  BatchProgressResponse,
  BatchRetryResponse,
  BatchSourceSetDetailResponse,
  BatchSourceSetsResponse,
  BatchSourceTagResponse,
  BatchSourceVideoResponse,
  CloneVoiceResponse,
  CreateJobResponse,
  DecorVideoListResponse,
  DecorVideoUploadResponse,
  DocsToAudioRequest,
  DocsToAudioResponse,
  EffectsLibraryResponse,
  JobProgressResponse,
  RenderRequest,
  RenderResponse,
  ResourcesPageResponse,
  ResultPageResponse,
  ReviewPageResponse,
  SourceTextOptionsResponse,
  UpdateEffectsConfigRequest,
  VoicesResponse,
} from "@/types/api";

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;

  constructor(message: string, status: number, code = "unknown_error", details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const body = isJson ? ((await response.json()) as T | ApiErrorPayload) : await response.text();

  if (!response.ok) {
    if (isJson && typeof body === "object" && body && "error" in body) {
      const payload = body as ApiErrorPayload;
      throw new ApiError(payload.error.message, response.status, payload.error.code, payload.error.details);
    }

    throw new ApiError(typeof body === "string" ? body : "Request failed", response.status);
  }

  return body as T;
}

export const createJob = (formData: FormData) =>
  requestJson<CreateJobResponse>("/api/jobs", {
    method: "POST",
    body: formData,
  });

export const getReviewPage = (jobId: string, page: number) =>
  requestJson<ReviewPageResponse>(`/api/jobs/${jobId}/review?page=${page}`);

export const getResourcesPage = (jobId: string, tags: string[]) => {
  const params = new URLSearchParams();
  tags.forEach((tag) => params.append("tag", tag));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson<ResourcesPageResponse>(`/api/jobs/${jobId}/resources${suffix}`);
};

export const renderJob = (jobId: string, payload: RenderRequest) =>
  requestJson<RenderResponse>(`/api/jobs/${jobId}/render`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

export const getJobProgress = (jobId: string) =>
  requestJson<JobProgressResponse>(`/api/jobs/${jobId}/progress`);

export const getResultPage = (jobId: string) =>
  requestJson<ResultPageResponse>(`/api/jobs/${jobId}/result`);

export const getEffectsLibrary = () =>
  requestJson<EffectsLibraryResponse>("/api/effects-library");

export const updateEffectsLibraryConfig = (payload: UpdateEffectsConfigRequest) =>
  requestJson<EffectsLibraryResponse>("/api/effects-library/config", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

export const getAudioLibrary = () =>
  requestJson<AudioLibraryResponse>("/api/audio-library");

export const createAudioFromDocs = (payload: DocsToAudioRequest) =>
  requestJson<DocsToAudioResponse>("/api/docs-to-audio", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

export const getVoices = () =>
  requestJson<VoicesResponse>("/api/voices");

export const cloneVoice = (formData: FormData) =>
  requestJson<CloneVoiceResponse>("/api/voices/clone", {
    method: "POST",
    body: formData,
  });

export const getDecorVideos = () =>
  requestJson<DecorVideoListResponse>("/api/decor-videos");

export const getSourceTextOptions = () =>
  requestJson<SourceTextOptionsResponse>("/api/source-text-options");

export const uploadDecorVideo = (formData: FormData) =>
  requestJson<DecorVideoUploadResponse>("/api/decor-videos", {
    method: "POST",
    body: formData,
  });

export const deleteDecorVideoApi = (decorId: string) =>
  requestJson<{ deleted: boolean }>(`/api/decor-videos/${decorId}`, {
    method: "DELETE",
  });

// --- Batch Pipeline ---

export const startBatchPipeline = (formData: FormData) =>
  requestJson<BatchPipelineResponse>("/api/batch-pipeline", {
    method: "POST",
    body: formData,
  });

export const prepareBatchSourceVideos = (formData: FormData) =>
  requestJson<BatchSourceVideoResponse>("/api/batch-pipeline/source-videos", {
    method: "POST",
    body: formData,
  });

export const createBatchDraft = () =>
  requestJson<BatchDraftResponse>("/api/batch-pipeline/drafts", {
    method: "POST",
  });

export const getBatchDraft = (draftId: string) =>
  requestJson<BatchDraftResponse>(`/api/batch-pipeline/drafts/${draftId}`);

export const updateBatchDraft = (draftId: string, payload: Partial<BatchDraftResponse>) =>
  requestJson<BatchDraftResponse>(`/api/batch-pipeline/drafts/${draftId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

export const uploadBatchDraftFiles = (draftId: string, formData: FormData) =>
  requestJson<BatchDraftResponse>(`/api/batch-pipeline/drafts/${draftId}/files`, {
    method: "POST",
    body: formData,
  });

export const submitBatchDraft = (draftId: string, payload: Partial<BatchDraftResponse>) =>
  requestJson<BatchPipelineResponse>(`/api/batch-pipeline/drafts/${draftId}/submit`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

export const getBatchSourceSets = () =>
  requestJson<BatchSourceSetsResponse>("/api/batch-pipeline/source-sets");

export const getBatchSourceSet = (batchSourceId: string) =>
  requestJson<BatchSourceSetDetailResponse>(`/api/batch-pipeline/source-sets/${batchSourceId}`);

export const deleteBatchSourceClip = (batchSourceId: string, clipId: string) =>
  requestJson<{ deleted: boolean; clipId: string; batchSourceId: string; remainingClips: number }>(
    `/api/batch-pipeline/source-sets/${batchSourceId}/clips/${clipId}`,
    { method: "DELETE" },
  );

export const getBatchLibrarySources = (tags: string[]) => {
  const params = new URLSearchParams();
  tags.forEach((tag) => params.append("tags", tag));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson<BatchLibrarySourcesResponse>(`/api/batch-pipeline/library-sources${suffix}`);
};

export const addBatchDraftSourceTag = (
  draftId: string,
  payload: { clipRef: BatchDraftResponse["selectedSourceRefs"][number]; tags: string[] },
) =>
  requestJson<BatchSourceTagResponse>(`/api/batch-pipeline/drafts/${draftId}/source-tags`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

export const getBatchProgress = (batchId: string) =>
  requestJson<BatchProgressResponse>(`/api/batch-pipeline/${batchId}/progress`);

export const retryFailedBatch = (batchId: string) =>
  requestJson<BatchRetryResponse>(`/api/batch-pipeline/${batchId}/retry-failed`, {
    method: "POST",
  });

// --- Channel Management ---

import type {
  BulletinCreateResponse,
  BulletinDetailResponse,
  BulletinListItem,
  BulletinProgressResponse,
  BulletinResourceDetails,
  BulletinResourceSummary,
  BulletinScriptUpdateResponse,
  Channel,
  ChannelGroup,
  ChannelsResponse,
  CRTDemoResponse,
  CRTPresetsResponse,
  CRTSettings,
  CommitStoryHarvestResponse,
  CreateStoryBatchRequest,
  CreateStoryLibraryRequest,
  CreateStoryVideoRequest,
  CtaOverlay,
  DecorImage,
  DecorImagesResponse,
  DecorImageUploadResponse,
  DriveAudioImportProgress,
  DownloadProgress,
  BakeStoryLibraryRequest,
  BakeStoryLibraryResponse,
  LocalAudioFolderScan,
  ParseScriptResponse,
  StartStoryHarvestRequest,
  StoryBatchProgress,
  StoryDecorFrame,
  StoryDecorImage,
  StoryDecorMaskMode,
  StoryHarvestDeleteRequest,
  StoryHarvestDeleteResponse,
  StoryHarvestItemsResponse,
  StoryHarvestJob,
  StoryLibrariesResponse,
  StoryLibraryBakeJob,
  StoryLibraryBulkDeleteRequest,
  StoryLibraryBulkDeleteResponse,
  StoryLibraryDeleteResponse,
  StoryLibraryMutationResponse,
  StoryLibraryNormalizeJob,
  StoryLibraryNormalizeScan,
  StoryLibraryResponse,
  StoryLibraryStats,
  StartStoryLibraryNormalizeResponse,
  StoryIntroMutationResponse,
  StoryIntrosResponse,
  StoryPrefetchDiscardResponse,
  StoryPrefetchSession,
  StoryPrefetchStartRequest,
  StoryProviderVideo,
  UpdateStoryLibraryRequest,
  StoryProviderVideoSearchResponse,
  StoryVideoProvider,
  StoryVideoProgress,
  SubtitleFontInfo,
  SubtitlePresetInfo,
  TVEffectCustomSaveResponse,
  EffectPreviewJob,
  EffectPreviewSource,
  SparkleCreateResponse,
  SparklePresetsResponse,
  TVEffectParams,
  TVEffectPreviewResponse,
  TVEffectStylesResponse,
  TVNoiseOverlay,
  TVNoiseOverlayJob,
  TVNoiseOverlayUploadResponse,
  WaveformOverlay,
  YoutubeDownloadConfigResponse,
  YoutubeDownloadFilesResponse,
  YoutubeDownloadJob,
} from "@/types/api";

export const getChannels = (groupId?: string) => {
  const suffix = groupId ? `?groupId=${groupId}` : "";
  return requestJson<ChannelsResponse>(`/api/channels${suffix}`);
};

export const createChannelApi = (data: Partial<Channel>) =>
  requestJson<{ channel: Channel }>("/api/channels", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

export const updateChannelApi = (channelId: string, data: Partial<Channel>) =>
  requestJson<{ channel: Channel }>(`/api/channels/${channelId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

export const deleteChannelApi = (channelId: string) =>
  requestJson<{ deleted: boolean }>(`/api/channels/${channelId}`, { method: "DELETE" });

export const createGroupApi = (data: Partial<ChannelGroup>) =>
  requestJson<{ group: ChannelGroup }>("/api/channel-groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

export const updateGroupApi = (groupId: string, data: Partial<ChannelGroup>) =>
  requestJson<{ group: ChannelGroup }>(`/api/channel-groups/${groupId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

export const deleteGroupApi = (groupId: string) =>
  requestJson<{ deleted: boolean }>(`/api/channel-groups/${groupId}`, { method: "DELETE" });

export const uploadChannelTransition = (channelId: string, formData: FormData) =>
  requestJson<{ channel: Channel; transitionVideoPath: string }>(
    `/api/channels/${channelId}/transition-video`,
    { method: "POST", body: formData },
  );

export const uploadChannelMedia = (channelId: string, role: "intro" | "transition" | "outro", formData: FormData) =>
  requestJson<{ channel: Channel; role: string; introVideoPath?: string; transitionVideoPath?: string; outroVideoPath?: string }>(
    `/api/channels/${channelId}/media/${role}`,
    { method: "POST", body: formData },
  );

// --- Channel Decor Images ---

export const getChannelDecorImages = (channelId: string) =>
  requestJson<DecorImagesResponse>(`/api/channels/${channelId}/decor-images`);

export const uploadChannelDecorImage = (channelId: string, formData: FormData) =>
  requestJson<DecorImageUploadResponse>(`/api/channels/${channelId}/decor-images`, {
    method: "POST",
    body: formData,
  });

export const deleteChannelDecorImage = (channelId: string, imageId: string) =>
  requestJson<{ deleted: boolean }>(`/api/channels/${channelId}/decor-images/${imageId}`, {
    method: "DELETE",
  });

export const updateDecorImageConfig = (channelId: string, imageId: string, config: Partial<DecorImage>) =>
  requestJson<DecorImageUploadResponse>(`/api/channels/${channelId}/decor-images/${imageId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });

// --- News Bulletin ---

export const parseNewsScript = (scriptText: string) =>
  requestJson<ParseScriptResponse>("/api/news-bulletin/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scriptText }),
  });

export const createNewsBulletin = (scriptText: string, channelIds: string[], channelDecorImageIds?: Record<string, string>) =>
  requestJson<BulletinCreateResponse>("/api/news-bulletin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scriptText, channelIds, channelDecorImageIds }),
  });

export const getNewsBulletin = (bulletinId: string) =>
  requestJson<BulletinDetailResponse>(`/api/news-bulletin/${bulletinId}`);

export const getNewsBulletinProgress = (bulletinId: string) =>
  requestJson<BulletinProgressResponse>(`/api/news-bulletin/${bulletinId}/progress`);

export const updateNewsBulletinScript = (bulletinId: string, scriptText: string) =>
  requestJson<BulletinScriptUpdateResponse>(`/api/news-bulletin/${bulletinId}/script`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scriptText }),
  });

export const listNewsBulletins = () =>
  requestJson<{ bulletins: BulletinListItem[] }>("/api/news-bulletin/list");

export const uploadBulletinResources = (bulletinId: string, newsIdx: number, formData: FormData) =>
  requestJson<{ newsIdx: number; addedVideos: number; addedImages: number; resourceSummary: BulletinResourceSummary; resourceDetails: BulletinResourceDetails }>(
    `/api/news-bulletin/${bulletinId}/resources/${newsIdx}`,
    { method: "POST", body: formData },
  );

export const clearBulletinResources = (bulletinId: string, newsIdx: number) =>
  requestJson<{ newsIdx: number; resourceSummary: BulletinResourceSummary; resourceDetails: BulletinResourceDetails }>(
    `/api/news-bulletin/${bulletinId}/resources/${newsIdx}`,
    { method: "DELETE" },
  );

export const updateBulletinChannels = (bulletinId: string, channelIds: string[], channelDecorImageIds?: Record<string, string>) =>
  requestJson<{ bulletinId: string; channelIds: string[] }>(
    `/api/news-bulletin/${bulletinId}/channels`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channelIds, channelDecorImageIds }),
    },
  );

export const startBulletinRender = (bulletinId: string) =>
  requestJson<{ bulletinId: string; status: string; channelCount: number }>(
    `/api/news-bulletin/${bulletinId}/start-render`,
    { method: "POST" },
  );

export const retryBulletinRender = (bulletinId: string) =>
  requestJson<{ bulletinId: string; status: string; retriedChannels: string[]; retriedCount: number }>(
    `/api/news-bulletin/${bulletinId}/retry-render`,
    { method: "POST" },
  );

export const addBulletinResourcesFromSource = (
  bulletinId: string,
  newsIdx: number,
  clipPaths: string[],
) =>
  requestJson<{ newsIdx: number; addedVideos: number; addedImages: number; resourceSummary: BulletinResourceSummary; resourceDetails: BulletinResourceDetails }>(
    `/api/news-bulletin/${bulletinId}/resources/${newsIdx}/from-source`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clipPaths }),
    },
  );


// --- News Bulletin: Segment Resources (intro / detail_intro / outro) ---

export type SegmentType = "intro" | "detail_intro" | "outro";

export interface SegmentResourceSummary {
  intro: { vidClips: number; images: number };
  detail_intro: { vidClips: number; images: number };
  outro: { vidClips: number; images: number };
}

export const uploadSegmentResources = (bulletinId: string, segmentType: SegmentType, formData: FormData) =>
  requestJson<{ segmentType: string; added: { images: number; vidClips: number }; segmentResourceSummary: SegmentResourceSummary }>(
    `/api/news-bulletin/${bulletinId}/segment-resources/${segmentType}`,
    { method: "POST", body: formData },
  );

export const clearSegmentResources = (bulletinId: string, segmentType: SegmentType) =>
  requestJson<{ segmentType: string; segmentResourceSummary: SegmentResourceSummary }>(
    `/api/news-bulletin/${bulletinId}/segment-resources/${segmentType}`,
    { method: "DELETE" },
  );

export const getSegmentResources = (bulletinId: string, segmentType: SegmentType) =>
  requestJson<{ segmentType: string; pool: { vid_clips: Array<{ path: string; relative_path: string }>; img_clips: Array<{ path: string; relative_path: string }> } }>(
    `/api/news-bulletin/${bulletinId}/segment-resources/${segmentType}`,
  );


// === Story Video Libraries (folders) ===

export async function getStoryLibraries() {
  return requestJson<StoryLibrariesResponse>("/api/story-video/libraries");
}

export async function createStoryLibrary(payload: CreateStoryLibraryRequest) {
  return requestJson<StoryLibraryMutationResponse>("/api/story-video/libraries", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function renameStoryLibrary(libraryId: string, payload: UpdateStoryLibraryRequest) {
  return requestJson<StoryLibraryMutationResponse>(`/api/story-video/libraries/${libraryId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteStoryLibrary(libraryId: string) {
  return requestJson<StoryLibraryDeleteResponse>(`/api/story-video/libraries/${libraryId}`, {
    method: "DELETE",
  });
}

// === Story Video Intros (opening clips) ===

export async function getStoryIntros() {
  return requestJson<StoryIntrosResponse>("/api/story-video/intros");
}

export async function uploadStoryIntro(file: File, name: string) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("name", name);
  return requestJson<StoryIntroMutationResponse>("/api/story-video/intros", { method: "POST", body: fd });
}

export async function renameStoryIntro(introId: string, name: string) {
  return requestJson<StoryIntroMutationResponse>(`/api/story-video/intros/${introId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function deleteStoryIntro(introId: string) {
  return requestJson<{ deleted: boolean; introId: string }>(`/api/story-video/intros/${introId}`, {
    method: "DELETE",
  });
}

export async function bakeStoryLibrary(payload: BakeStoryLibraryRequest) {
  return requestJson<BakeStoryLibraryResponse>("/api/story-video/library/bake", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getStoryLibraryBakeJob(jobId: string) {
  return requestJson<StoryLibraryBakeJob>(`/api/story-video/library/bake/${jobId}`);
}

/** Abandon a bake — the partial target library is deleted. Use pause to keep it. */
export async function cancelStoryLibraryBakeJob(jobId: string) {
  return requestJson<{ jobId: string; status: string }>(
    `/api/story-video/library/bake/${jobId}/cancel`,
    { method: "POST" },
  );
}

/** Park a bake, keeping every clip baked so far (library stays renderable). */
export async function pauseStoryLibraryBakeJob(jobId: string) {
  return requestJson<{ jobId: string; status: string; completed: number; total: number }>(
    `/api/story-video/library/bake/${jobId}/pause`,
    { method: "POST" },
  );
}

/** Continue a paused bake, processing only the clips the target library lacks. */
export async function resumeStoryLibraryBakeJob(jobId: string) {
  return requestJson<{ jobId: string; status: string; completed: number; total: number; remaining: number }>(
    `/api/story-video/library/bake/${jobId}/resume`,
    { method: "POST" },
  );
}

// === Story Video Library (clips within a folder) ===

export async function getStoryLibrary(libraryId: string, page = 1, perPage = 20, tags?: string[]) {
  const params = new URLSearchParams({ page: String(page), per_page: String(perPage), libraryId });
  if (tags?.length) params.set("tags", tags.join(","));
  return requestJson<StoryLibraryResponse>(`/api/story-video/library?${params}`);
}

export async function downloadStoryVideos(libraryId: string, links: string[], tags: string[] = []) {
  return requestJson<{ sessionId: string }>("/api/story-video/library/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ libraryId, links, tags }),
  });
}

export async function uploadStoryVideos(libraryId: string, files: File[], tags: string[] = []) {
  const fd = new FormData();
  fd.append("libraryId", libraryId);
  files.forEach((f) => fd.append("files", f));
  if (tags.length) fd.append("tags", JSON.stringify(tags));
  return requestJson<{ sessionId: string }>("/api/story-video/library/upload", {
    method: "POST",
    body: fd,
  });
}

export async function getStoryDownloadProgress(sessionId: string) {
  return requestJson<DownloadProgress>(`/api/story-video/library/download-progress/${sessionId}`);
}

export async function searchStoryProviderVideos(
  provider: StoryVideoProvider,
  query: string,
  page = 1,
  perPage = 20,
) {
  const params = new URLSearchParams({ q: query, page: String(page), per_page: String(perPage) });
  return requestJson<StoryProviderVideoSearchResponse>(`/api/story-video/video-search/${provider}?${params}`);
}

export async function importSelectedStoryVideos(
  libraryId: string,
  items: StoryProviderVideo[],
  tags: string[] = [],
) {
  return requestJson<{ sessionId: string }>("/api/story-video/library/import-selected", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      libraryId,
      items: items.map((item) => ({
        provider: item.provider,
        id: item.id,
        pageUrl: item.pageUrl,
        // Gửi kèm download URL từ search để backend không phải resolve lại
        // từng video (tránh 429 rate limit từ Pexels/Pixabay).
        previewUrl: item.previewUrl,
      })),
      tags,
    }),
  });
}

// === Bulk harvest: tải hết theo từ khoá trước, chọn lọc sau ===
// Luồng riêng, độc lập với searchStoryProviderVideos/importSelectedStoryVideos ở trên.

export async function startStoryHarvest(payload: StartStoryHarvestRequest) {
  return requestJson<StoryHarvestJob>("/api/story-video/library/harvest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listStoryHarvestJobs() {
  return requestJson<{ jobs: StoryHarvestJob[] }>("/api/story-video/library/harvest");
}

export async function getStoryHarvestJob(jobId: string) {
  return requestJson<StoryHarvestJob>(`/api/story-video/library/harvest/${jobId}`);
}

export async function getStoryHarvestItems(jobId: string, page = 1, perPage = 24, keyword?: string) {
  const params = new URLSearchParams({ page: String(page), per_page: String(perPage) });
  if (keyword) params.set("keyword", keyword);
  return requestJson<StoryHarvestItemsResponse>(
    `/api/story-video/library/harvest/${jobId}/items?${params}`,
  );
}

export async function cancelStoryHarvest(jobId: string) {
  return requestJson<StoryHarvestJob>(`/api/story-video/library/harvest/${jobId}/cancel`, {
    method: "POST",
  });
}

export async function deleteStoryHarvestItems(jobId: string, payload: StoryHarvestDeleteRequest) {
  return requestJson<StoryHarvestDeleteResponse>(
    `/api/story-video/library/harvest/${jobId}/items/delete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function commitStoryHarvest(jobId: string, libraryId: string, deleteStaging = true) {
  return requestJson<CommitStoryHarvestResponse>(
    `/api/story-video/library/harvest/${jobId}/commit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ libraryId, deleteStaging }),
    },
  );
}

export async function deleteStoryHarvestJob(jobId: string) {
  return requestJson<{ deleted: boolean; jobId: string }>(
    `/api/story-video/library/harvest/${jobId}`,
    { method: "DELETE" },
  );
}

export async function deleteStoryClip(libraryId: string, clipId: string) {
  return requestJson<void>(`/api/story-video/library/${clipId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ libraryId }),
  });
}

export async function deleteStoryClipsBulk(libraryId: string, payload: StoryLibraryBulkDeleteRequest) {
  return requestJson<StoryLibraryBulkDeleteResponse>("/api/story-video/library/bulk-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ libraryId, ...payload }),
  });
}

export async function updateStoryClipTags(libraryId: string, clipId: string, tags: string[]) {
  return requestJson<void>(`/api/story-video/library/${clipId}/tags`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ libraryId, tags }),
  });
}

export async function getStoryLibraryStats(libraryId: string) {
  const params = new URLSearchParams({ libraryId });
  return requestJson<StoryLibraryStats>(`/api/story-video/library/stats?${params}`);
}

// === Story Video Library prefetch (download all → review → cut) ===
// The alternative to searchStoryProviderVideos + importSelectedStoryVideos above:
// the sweep downloads every hit up front so the review runs on local files.

export async function startStoryPrefetch(libraryId: string, payload: StoryPrefetchStartRequest) {
  return requestJson<StoryPrefetchSession>("/api/story-video/library/prefetch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ libraryId, ...payload }),
  });
}

export async function listStoryPrefetchSessions(libraryId: string, includeCompleted = false) {
  const params = new URLSearchParams({ libraryId });
  if (includeCompleted) params.set("includeCompleted", "true");
  return requestJson<{ sessions: StoryPrefetchSession[] }>(`/api/story-video/library/prefetch?${params}`);
}

export async function getStoryPrefetchSession(sessionId: string) {
  return requestJson<StoryPrefetchSession>(`/api/story-video/library/prefetch/${sessionId}`);
}

export async function cancelStoryPrefetch(sessionId: string) {
  return requestJson<StoryPrefetchSession>(`/api/story-video/library/prefetch/${sessionId}/cancel`, {
    method: "POST",
  });
}

/** Poster frame cut from the staged file itself — the fallback for items whose
 *  provider gave us no thumbnail (every Pixabay sweep). Cached server-side. */
export function storyPrefetchPosterUrl(sessionId: string, itemId: string) {
  return `/api/story-video/library/prefetch/${encodeURIComponent(sessionId)}/items/${encodeURIComponent(itemId)}/poster`;
}

export async function discardStoryPrefetchItems(sessionId: string, itemIds: string[]) {
  return requestJson<StoryPrefetchDiscardResponse>(
    `/api/story-video/library/prefetch/${sessionId}/discard-items`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ itemIds }),
    },
  );
}

export async function commitStoryPrefetch(
  sessionId: string,
  tags: string[] = [],
  deleteRawAfter = false,
) {
  return requestJson<{ sessionId: string; total: number }>(
    `/api/story-video/library/prefetch/${sessionId}/commit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags, deleteRawAfter }),
    },
  );
}

export async function deleteStoryPrefetchSession(sessionId: string) {
  return requestJson<{ deleted: boolean; sessionId: string }>(
    `/api/story-video/library/prefetch/${sessionId}`,
    { method: "DELETE" },
  );
}

// === Story Video Library normalization (canonical clip format) ===

export async function scanStoryLibraryNormalize(libraryId?: string) {
  const params = new URLSearchParams(libraryId ? { libraryId } : { scope: "all" });
  return requestJson<StoryLibraryNormalizeScan>(`/api/story-video/library/normalize/scan?${params}`);
}

export async function startStoryLibraryNormalize(payload: {
  libraryId?: string;
  scope?: "all";
  includeAll?: boolean;
}) {
  return requestJson<StartStoryLibraryNormalizeResponse>("/api/story-video/library/normalize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getStoryLibraryNormalizeJob(jobId: string) {
  return requestJson<StoryLibraryNormalizeJob>(`/api/story-video/library/normalize/${jobId}`);
}

export async function cancelStoryLibraryNormalizeJob(jobId: string) {
  return requestJson<{ jobId: string; status: string }>(
    `/api/story-video/library/normalize/${jobId}/cancel`,
    { method: "POST" },
  );
}

// === CRT Effect ===

export async function getCRTPresets() {
  return requestJson<CRTPresetsResponse>("/api/story-video/crt-presets");
}

export async function generateCRTDemo(settings: CRTSettings, sampleClipId?: string) {
  return requestJson<CRTDemoResponse>("/api/story-video/crt-demo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings, sampleClipId }),
  });
}

// === TV Effect Styles (1990s looks) ===

export async function getTVEffectStyles() {
  return requestJson<TVEffectStylesResponse>("/api/story-video/tv-effects");
}

export async function selectTVEffectStyle(styleId: string) {
  return requestJson<{ selectedId: string }>("/api/story-video/tv-effects/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ styleId }),
  });
}

export async function generateTVEffectStylePreview(styleId: string, sampleClipId?: string, duration?: number) {
  return requestJson<TVEffectPreviewResponse>("/api/story-video/tv-effects/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ styleId, sampleClipId, duration }),
  });
}

export async function generateCustomTVEffectPreview(params: TVEffectParams, sampleClipId?: string, duration?: number) {
  return requestJson<TVEffectPreviewResponse>("/api/story-video/tv-effects/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ params, sampleClipId, duration }),
  });
}

export async function saveCustomTVEffect(params: TVEffectParams) {
  return requestJson<TVEffectCustomSaveResponse>("/api/story-video/tv-effects/custom", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ params }),
  });
}

// === TV Noise Overlay ===

export async function getTVNoiseOverlays() {
  return requestJson<{ overlays: TVNoiseOverlay[] }>("/api/story-video/tv-noise-overlays");
}

export async function uploadTVNoiseOverlay(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return requestJson<TVNoiseOverlayUploadResponse>("/api/story-video/tv-noise-overlays", { method: "POST", body: fd });
}

export async function importTVNoiseOverlayFromYoutube(url: string, name?: string) {
  return requestJson<TVNoiseOverlayUploadResponse>("/api/story-video/tv-noise-overlays/import-youtube", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, name }),
  });
}

export async function getTVNoiseOverlayJob(sessionId: string) {
  return requestJson<TVNoiseOverlayJob>(`/api/story-video/tv-noise-overlays/jobs/${sessionId}`);
}

export async function updateTVNoiseOverlay(id: string, payload: Partial<TVNoiseOverlay>) {
  return requestJson<{ overlay: TVNoiseOverlay }>(`/api/story-video/tv-noise-overlays/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getEffectPreviewSources() {
  return requestJson<{ sources: EffectPreviewSource[] }>("/api/story-video/effect-preview/sources");
}

export async function uploadEffectPreviewSource(files: File[], name?: string) {
  const fd = new FormData();
  for (const file of files) fd.append("files", file);
  if (name) fd.append("name", name);
  return requestJson<{ source: EffectPreviewSource }>("/api/story-video/effect-preview/sources", {
    method: "POST",
    body: fd,
  });
}

export async function deleteEffectPreviewSource(id: string) {
  return requestJson<{ deleted: string }>(`/api/story-video/effect-preview/sources/${id}`, { method: "DELETE" });
}

export async function renderEffectPreview(payload: {
  sourceId: string;
  includeStyle?: boolean;
  includeOverlays?: boolean;
  compare?: boolean;
  maxSeconds?: number;
  decorImageId?: string;
}) {
  return requestJson<{ sessionId: string }>("/api/story-video/effect-preview/render", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getEffectPreviewJob(sessionId: string) {
  return requestJson<EffectPreviewJob>(`/api/story-video/effect-preview/jobs/${sessionId}`);
}

export async function getSparklePresets() {
  return requestJson<SparklePresetsResponse>("/api/story-video/sparkle-presets");
}

export async function createSparkleOverlay(payload: {
  presetId: string;
  params?: Record<string, number>;
  name?: string;
  opacity?: number;
  lumaGain?: number;
}) {
  return requestJson<SparkleCreateResponse>("/api/story-video/sparkle-overlays", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteTVNoiseOverlay(id: string) {
  return requestJson<void>(`/api/story-video/tv-noise-overlays/${id}`, { method: "DELETE" });
}

export async function generateTVNoiseDemo(overlayId?: string, sampleClipId?: string) {
  return requestJson<CRTDemoResponse>("/api/story-video/tv-noise-demo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ overlayId, sampleClipId }),
  });
}

// === Story Video ===

export async function createStoryVideo(payload: CreateStoryVideoRequest, audioFile?: File, subtitleFile?: File) {
  if (audioFile || subtitleFile) {
    const fd = new FormData();
    if (audioFile) fd.append("audio", audioFile);
    if (subtitleFile) fd.append("subtitle", subtitleFile);
    fd.append("payload", JSON.stringify(payload));
    return requestJson<{ storyId: string }>("/api/story-video/create", { method: "POST", body: fd });
  }
  return requestJson<{ storyId: string }>("/api/story-video/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getStoryVideoProgress(storyId: string) {
  return requestJson<StoryVideoProgress>(`/api/story-video/${storyId}/progress`);
}

export async function getStoryVideoResult(storyId: string) {
  return requestJson<{ videoPath: string }>(`/api/story-video/${storyId}/result`);
}

export async function cancelStoryVideo(storyId: string) {
  return requestJson<{ storyId: string; status: string }>(`/api/story-video/${storyId}/cancel`, { method: "POST" });
}

// === Story Video Batch ===

export async function createStoryBatch(payload: CreateStoryBatchRequest, audioFiles?: File[], subtitleFiles?: File[]) {
  const fd = new FormData();
  fd.append("payload", JSON.stringify(payload));
  if (audioFiles?.length) {
    audioFiles.forEach((f) => fd.append("audio_files", f));
  }
  if (subtitleFiles?.length) {
    subtitleFiles.forEach((f) => fd.append("subtitle_files", f));
  }
  return requestJson<{ batchId: string }>("/api/story-video/batch/create", { method: "POST", body: fd });
}

/** Scan a folder on the machine running the backend — no upload, just paths. */
export async function scanLocalAudioFolder(path: string) {
  return requestJson<LocalAudioFolderScan>("/api/story-video/batch/local-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export async function startStoryDriveAudioImport(folderUrl: string) {
  return requestJson<{ sessionId: string }>("/api/story-video/batch/drive-audio-imports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folderUrl }),
  });
}

export async function getStoryDriveAudioImport(sessionId: string) {
  return requestJson<DriveAudioImportProgress>(`/api/story-video/batch/drive-audio-imports/${sessionId}`);
}

export async function getStoryBatchProgress(batchId: string) {
  return requestJson<StoryBatchProgress>(`/api/story-video/batch/${batchId}/progress`);
}

export async function cancelStoryBatchItem(batchId: string, storyId: string) {
  return requestJson<{ batchId: string; storyId: string; status: string }>(
    `/api/story-video/batch/${batchId}/items/${storyId}/cancel`,
    { method: "POST" },
  );
}

export async function cancelStoryBatch(batchId: string) {
  return requestJson<{ batchId: string; status: string }>(`/api/story-video/batch/${batchId}/cancel`, { method: "POST" });
}

export async function retryStoryBatchFailed(batchId: string) {
  return requestJson<{ batchId: string; retryCount: number }>(`/api/story-video/batch/${batchId}/retry-failed`, { method: "POST" });
}

// === Story Subtitles ===

export async function getSubtitleFonts() {
  return requestJson<{ fonts: SubtitleFontInfo[]; defaultFamily: string }>("/api/story-video/subtitle-fonts");
}

export async function uploadSubtitleFont(file: File) {
  const fd = new FormData();
  fd.append("font", file);
  return requestJson<{ fonts: SubtitleFontInfo[]; defaultFamily: string }>("/api/story-video/subtitle-fonts", { method: "POST", body: fd });
}

export async function getSubtitlePresets() {
  return requestJson<{ presets: SubtitlePresetInfo[] }>("/api/story-video/subtitle-presets");
}

export async function generateSubtitlePreview(body: {
  font: string;
  presetId: string;
  maxCharsPerLine: number;
  maxLines: number;
  sampleClipId?: string;
  styleOverrides?: { fontScale?: number };
}) {
  return requestJson<{ previewPath: string }>("/api/story-video/subtitle-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// === Waveform Overlay ===

export async function getWaveformOverlays() {
  return requestJson<{ overlays: WaveformOverlay[] }>("/api/story-video/waveform-overlays");
}

export async function uploadWaveformOverlay(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return requestJson<{ overlay: WaveformOverlay }>("/api/story-video/waveform-overlays", { method: "POST", body: fd });
}

export async function updateWaveformOverlay(id: string, payload: Partial<WaveformOverlay>) {
  return requestJson<{ overlay: WaveformOverlay }>(`/api/story-video/waveform-overlays/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteWaveformOverlay(id: string) {
  return requestJson<void>(`/api/story-video/waveform-overlays/${id}`, { method: "DELETE" });
}

// === CTA Overlay (Like/Subscribe/Notification corner) ===

export async function getCtaOverlays() {
  return requestJson<{ overlays: CtaOverlay[] }>("/api/story-video/cta-overlays");
}

export async function uploadCtaOverlay(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return requestJson<{ overlay: CtaOverlay }>("/api/story-video/cta-overlays", { method: "POST", body: fd });
}

export async function updateCtaOverlay(id: string, payload: Partial<CtaOverlay>) {
  return requestJson<{ overlay: CtaOverlay }>(`/api/story-video/cta-overlays/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteCtaOverlay(id: string) {
  return requestJson<void>(`/api/story-video/cta-overlays/${id}`, { method: "DELETE" });
}

// === YouTube Downloader ===

export async function getYoutubeDownloadConfig() {
  return requestJson<YoutubeDownloadConfigResponse>("/api/youtube-download/config");
}

export async function startYoutubeDownload(payload: {
  links: string[];
  outputDir: string;
  downloadVideo: boolean;
  extractAudio: boolean;
}) {
  return requestJson<{ sessionId: string }>("/api/youtube-download/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getDecorImages() {
  return requestJson<{ images: StoryDecorImage[]; groups?: string[] }>(
    "/api/story-video/decor-images",
  );
}

export async function uploadDecorImage(
  file: File,
  group?: string,
  mode?: StoryDecorMaskMode,
) {
  const form = new FormData();
  form.append("file", file);
  if (group) form.append("group", group);
  // "manual" skips green detection so the user draws the screen area even on a
  // photo that happens to contain some green.
  if (mode) form.append("mode", mode);
  return requestJson<{ image: StoryDecorImage }>("/api/story-video/decor-images", {
    method: "POST",
    body: form,
  });
}

export async function renameDecorGroup(from: string, to: string) {
  return requestJson<{ moved: number; groups: string[] }>(
    "/api/story-video/decor-images/group",
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from, to }),
    },
  );
}

export async function updateDecorImage(id: string, payload: Partial<StoryDecorImage>) {
  return requestJson<{ image: StoryDecorImage }>(`/api/story-video/decor-images/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function getYoutubeDownloadJob(sessionId: string) {
  return requestJson<YoutubeDownloadJob>(`/api/youtube-download/jobs/${sessionId}`);
}

export async function cancelYoutubeDownload(sessionId: string) {
  return requestJson<YoutubeDownloadJob>(`/api/youtube-download/jobs/${sessionId}/cancel`, { method: "POST" });
}

export async function listYoutubeDownloadFiles(outputDir: string) {
  const suffix = outputDir ? `?dir=${encodeURIComponent(outputDir)}` : "";
  return requestJson<YoutubeDownloadFilesResponse>(`/api/youtube-download/files${suffix}`);
}

export async function deleteYoutubeDownloadFile(outputDir: string, name: string) {
  return requestJson<{ deleted: string; outputDir: string }>("/api/youtube-download/files", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ outputDir, name }),
  });
}

export async function deleteDecorImage(id: string) {
  return requestJson<void>(`/api/story-video/decor-images/${id}`, { method: "DELETE" });
}

/** Re-run green-region detection on the original upload. */
export async function detectDecorFrame(id: string) {
  return requestJson<{ frame: StoryDecorFrame; keyColor: string }>(
    `/api/story-video/decor-images/${id}/detect-frame`,
    { method: "POST" },
  );
}

/** Compose one still (a library frame fitted into the decor frame) for alignment. */
export async function renderDecorFramePreview(
  id: string,
  payload: { libraryId?: string; sampleClipId?: string } = {},
) {
  return requestJson<{ previewPath: string }>(
    `/api/story-video/decor-images/${id}/frame-preview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}
