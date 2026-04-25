import type {
  ApiErrorPayload,
  AudioLibraryResponse,
  BatchPipelineResponse,
  BatchProgressResponse,
  BatchRetryResponse,
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

export const getBatchProgress = (batchId: string) =>
  requestJson<BatchProgressResponse>(`/api/batch-pipeline/${batchId}/progress`);

export const retryFailedBatch = (batchId: string) =>
  requestJson<BatchRetryResponse>(`/api/batch-pipeline/${batchId}/retry-failed`, {
    method: "POST",
  });
