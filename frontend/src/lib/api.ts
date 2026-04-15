import type {
  ApiErrorPayload,
  CreateJobResponse,
  RenderRequest,
  RenderResponse,
  ResourcesPageResponse,
  ResultPageResponse,
  ReviewPageResponse,
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

export const getResultPage = (jobId: string) =>
  requestJson<ResultPageResponse>(`/api/jobs/${jobId}/result`);
