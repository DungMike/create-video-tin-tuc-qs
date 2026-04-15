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
}

export interface RenderResponse {
  jobId: string;
  outputVideo: string | null;
  redirectUrl: string;
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
