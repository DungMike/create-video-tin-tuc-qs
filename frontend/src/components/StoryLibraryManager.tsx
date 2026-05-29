import { Check, ExternalLink, Loader2, Search, Trash2, Upload, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyCard } from "@/components/empty-card";
import { PaginationBar } from "@/components/pagination-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ApiError,
  deleteStoryClip,
  getStoryDownloadProgress,
  getStoryLibrary,
  getStoryLibraryStats,
  importSelectedStoryVideos,
  searchStoryProviderVideos,
  uploadStoryVideos,
} from "@/lib/api";
import type {
  DownloadProgress,
  StoryClip,
  StoryLibraryStats,
  StoryProviderVideo,
  StoryVideoProvider,
} from "@/types/api";

const PAGE_SIZE = 20;
const PROVIDER_PAGE_SIZE = 12;
const PROVIDERS: StoryVideoProvider[] = ["pixabay", "pexels"];

export interface StoryLibraryManagerProps {
  selectionMode?: boolean;
  selectedClipIds?: string[];
  onSelectionChange?: (ids: string[]) => void;
  allSelected?: boolean;
  onAllSelectedChange?: (enabled: boolean) => void;
  filterTags?: string[];
}

function providerVideoKey(item: Pick<StoryProviderVideo, "provider" | "id">) {
  return `${item.provider}:${item.id}`;
}

function parseTags(value: string) {
  return value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function ProviderSearchPanel({
  provider,
  query,
  results,
  page,
  totalPages,
  isSearching,
  selectedItems,
  onQueryChange,
  onSearch,
  onPageChange,
  onToggleSelected,
}: {
  provider: StoryVideoProvider;
  query: string;
  results: StoryProviderVideo[];
  page: number;
  totalPages: number;
  isSearching: boolean;
  selectedItems: Record<string, StoryProviderVideo>;
  onQueryChange: (value: string) => void;
  onSearch: () => void;
  onPageChange: (page: number) => void;
  onToggleSelected: (item: StoryProviderVideo) => void;
}) {
  const providerLabel = provider === "pixabay" ? "Pixabay" : "Pexels";

  return (
    <div className="grid min-w-0 gap-4">
      <div className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
        <div className="grid min-w-0 gap-2">
          <Label className="text-xs">Keyword</Label>
          <Input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onSearch();
            }}
            placeholder={`Search ${providerLabel} videos`}
          />
        </div>
        <Button type="button" className="self-end whitespace-nowrap" onClick={onSearch} disabled={isSearching}>
          {isSearching ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Search className="mr-2 size-4" />}
          Search
        </Button>
      </div>

      {results.length ? (
        <>
          <section className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {results.map((item) => {
              const itemKey = providerVideoKey(item);
              const selected = Boolean(selectedItems[itemKey]);
              return (
                <Card key={itemKey} className="min-w-0 overflow-hidden border-border/70 bg-card/90">
                  <CardContent className="grid min-w-0 gap-3 p-3">
                    <div className="relative aspect-video w-full min-w-0 overflow-hidden rounded-lg bg-black">
                      <video
                        controls
                        preload="metadata"
                        poster={item.thumbnailUrl || undefined}
                        src={item.previewUrl}
                        className="absolute inset-0 block h-full w-full max-w-full object-cover"
                      />
                    </div>
                    <div className="grid min-w-0 gap-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold text-foreground">{item.title || item.id}</div>
                          <div className="text-xs text-muted-foreground">
                            {item.duration}s | {item.width}x{item.height}
                          </div>
                        </div>
                        {item.pageUrl ? (
                          <a
                            href={item.pageUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="shrink-0 rounded-md p-1 text-muted-foreground hover:text-foreground"
                            title={`Open ${providerLabel}`}
                          >
                            <ExternalLink className="size-4" />
                          </a>
                        ) : null}
                      </div>
                      {item.author ? <div className="truncate text-xs text-muted-foreground">{item.author}</div> : null}
                      <Button
                        type="button"
                        variant={selected ? "default" : "outline"}
                        size="sm"
                        className="w-full"
                        onClick={() => onToggleSelected(item)}
                      >
                        {selected ? <Check className="mr-2 size-4" /> : null}
                        {selected ? "Da chon" : "Chon"}
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </section>
          <PaginationBar
            page={page}
            totalPages={totalPages}
            onPrevious={() => onPageChange(Math.max(1, page - 1))}
            onNext={() => onPageChange(Math.min(totalPages, page + 1))}
          />
        </>
      ) : (
        <EmptyCard title={`Chua co ket qua ${providerLabel}`} description="Nhap keyword va bam Search de tim video." />
      )}
    </div>
  );
}

export function StoryLibraryManager({
  selectionMode = false,
  selectedClipIds = [],
  onSelectionChange,
  allSelected = false,
  onAllSelectedChange,
  filterTags,
}: StoryLibraryManagerProps) {
  const [clips, setClips] = useState<StoryClip[]>([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalClips, setTotalClips] = useState(0);
  const [stats, setStats] = useState<StoryLibraryStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [providerQueries, setProviderQueries] = useState<Record<StoryVideoProvider, string>>({ pixabay: "", pexels: "" });
  const [providerResults, setProviderResults] = useState<Record<StoryVideoProvider, StoryProviderVideo[]>>({ pixabay: [], pexels: [] });
  const [providerPages, setProviderPages] = useState<Record<StoryVideoProvider, number>>({ pixabay: 1, pexels: 1 });
  const [providerTotalPages, setProviderTotalPages] = useState<Record<StoryVideoProvider, number>>({ pixabay: 1, pexels: 1 });
  const [searchingProvider, setSearchingProvider] = useState<StoryVideoProvider | null>(null);
  const [selectedProviderVideos, setSelectedProviderVideos] = useState<Record<string, StoryProviderVideo>>({});
  const [providerTags, setProviderTags] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [importSessionId, setImportSessionId] = useState<string | null>(null);
  const [importProgress, setImportProgress] = useState<DownloadProgress | null>(null);

  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadTags, setUploadTags] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadSessionId, setUploadSessionId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<DownloadProgress | null>(null);

  const [fileInputKey, setFileInputKey] = useState(0);
  const [selectedTagFilter, setSelectedTagFilter] = useState<string[]>(filterTags ?? []);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadLibrary = useCallback(async (targetPage: number) => {
    setIsLoading(true);
    setErrorMessage(null);
    try {
      const [libResponse, statsResponse] = await Promise.all([
        getStoryLibrary(targetPage, PAGE_SIZE, selectedTagFilter.length ? selectedTagFilter : undefined),
        getStoryLibraryStats(),
      ]);
      setClips(libResponse.clips);
      setTotalPages(libResponse.totalPages);
      setTotalClips(libResponse.total);
      setStats(statsResponse);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai thu vien clip.");
    } finally {
      setIsLoading(false);
    }
  }, [selectedTagFilter]);

  useEffect(() => {
    loadLibrary(page);
  }, [page, loadLibrary]);

  useEffect(() => {
    const activeSessionId = importSessionId || uploadSessionId;
    if (!activeSessionId) return;

    const poll = () => {
      getStoryDownloadProgress(activeSessionId)
        .then((progress) => {
          if (importSessionId) setImportProgress(progress);
          if (uploadSessionId) setUploadProgress(progress);
          if (progress.status === "completed" || progress.status === "failed") {
            if (intervalRef.current) {
              clearInterval(intervalRef.current);
              intervalRef.current = null;
            }
            if (progress.status === "completed") {
              setIsImporting(false);
              setIsUploading(false);
              setImportSessionId(null);
              setUploadSessionId(null);
              setSelectedProviderVideos({});
              setUploadFiles([]);
              setFileInputKey((current) => current + 1);
              loadLibrary(1);
              setPage(1);
            } else {
              setIsImporting(false);
              setIsUploading(false);
            }
          }
        })
        .catch(() => undefined);
    };
    poll();
    intervalRef.current = setInterval(poll, 2000);
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [importSessionId, uploadSessionId, loadLibrary]);

  const selectedProviderList = useMemo(() => Object.values(selectedProviderVideos), [selectedProviderVideos]);

  const handleProviderSearch = async (provider: StoryVideoProvider, targetPage = 1) => {
    const query = providerQueries[provider].trim();
    if (!query) {
      setErrorMessage("Nhap keyword de search video.");
      return;
    }
    setErrorMessage(null);
    setSearchingProvider(provider);
    try {
      const response = await searchStoryProviderVideos(provider, query, targetPage, PROVIDER_PAGE_SIZE);
      setProviderResults((current) => ({ ...current, [provider]: response.items }));
      setProviderPages((current) => ({ ...current, [provider]: response.page }));
      setProviderTotalPages((current) => ({
        ...current,
        [provider]: Math.max(1, Math.ceil(response.total / Math.max(response.perPage, 1))),
      }));
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : `Khong the search ${provider}.`);
    } finally {
      setSearchingProvider(null);
    }
  };

  const handleToggleProviderVideo = (item: StoryProviderVideo) => {
    const itemKey = providerVideoKey(item);
    setSelectedProviderVideos((current) => {
      if (current[itemKey]) {
        const next = { ...current };
        delete next[itemKey];
        return next;
      }
      return { ...current, [itemKey]: item };
    });
  };

  const handleImportSelected = async () => {
    if (!selectedProviderList.length) {
      setErrorMessage("Chon it nhat 1 video de import.");
      return;
    }
    setErrorMessage(null);
    setIsImporting(true);
    setImportProgress(null);
    try {
      const res = await importSelectedStoryVideos(selectedProviderList, parseTags(providerTags));
      setImportSessionId(res.sessionId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the import video da chon.");
      setIsImporting(false);
    }
  };

  const handleStartUpload = async () => {
    if (!uploadFiles.length) {
      setErrorMessage("Chon it nhat 1 file video.");
      return;
    }
    setErrorMessage(null);
    setIsUploading(true);
    setUploadProgress(null);
    try {
      const res = await uploadStoryVideos(uploadFiles, parseTags(uploadTags));
      setUploadSessionId(res.sessionId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the upload video.");
      setIsUploading(false);
    }
  };

  const handleDeleteClip = async (clipId: string) => {
    setErrorMessage(null);
    try {
      await deleteStoryClip(clipId);
      if (selectionMode && onSelectionChange) {
        onSelectionChange(selectedClipIds.filter((id) => id !== clipId));
      }
      loadLibrary(page);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Xoa clip that bai.");
    }
  };

  const handleToggleClip = (clipId: string, checked: boolean) => {
    if (!onSelectionChange) return;
    if (allSelected) {
      onAllSelectedChange?.(false);
      const pageClipIds = clips.map((clip) => clip.id);
      onSelectionChange(checked ? pageClipIds : pageClipIds.filter((id) => id !== clipId));
      return;
    }
    if (checked) {
      onSelectionChange([...selectedClipIds, clipId]);
    } else {
      onSelectionChange(selectedClipIds.filter((id) => id !== clipId));
    }
  };

  const sourceTypeBadge = (sourceType: StoryClip["sourceType"]) => {
    const variants: Record<string, string> = {
      pixabay: "bg-green-500/15 text-green-400 border-green-500/30",
      pexels: "bg-blue-500/15 text-blue-400 border-blue-500/30",
      local: "bg-amber-500/15 text-amber-400 border-amber-500/30",
      local_upload: "bg-amber-500/15 text-amber-400 border-amber-500/30",
      direct: "bg-slate-500/15 text-slate-300 border-slate-500/30",
    };
    return (
      <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${variants[sourceType] || ""}`}>
        {sourceType}
      </span>
    );
  };

  const availableTags = stats ? Object.keys(stats.bySource) : [];
  const selectedCountLabel = allSelected ? `Tat ca ${totalClips} clip` : `${selectedClipIds.length} clip`;
  const activeImportPercent = importProgress ? Math.round((importProgress.current / Math.max(importProgress.total, 1)) * 100) : 0;
  const activeUploadPercent = uploadProgress ? Math.round((uploadProgress.current / Math.max(uploadProgress.total, 1)) * 100) : 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {selectionMode ? (
            <>
              <Badge variant={allSelected ? "default" : "secondary"} className="rounded-full">
                Dang dung: {selectedCountLabel}
              </Badge>
              {!allSelected ? (
                <Button type="button" variant="outline" size="sm" onClick={() => onAllSelectedChange?.(true)}>
                  Chon tat ca
                </Button>
              ) : null}
            </>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {availableTags.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {availableTags.map((tag) => (
                <Badge
                  key={tag}
                  variant={selectedTagFilter.includes(tag) ? "default" : "secondary"}
                  className="cursor-pointer rounded-full px-2 py-0.5"
                  onClick={() =>
                    setSelectedTagFilter((current) =>
                      current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag],
                    )
                  }
                >
                  {tag}
                </Badge>
              ))}
            </div>
          ) : null}
          {stats ? (
            <span className="text-sm text-muted-foreground">
              {totalClips} clips | {Math.round(stats.totalDuration)}s total
            </span>
          ) : null}
        </div>
      </div>

      {errorMessage ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {errorMessage}
        </div>
      ) : null}

      <Tabs defaultValue="pixabay" className="grid min-w-0 gap-3">
        <TabsList className="w-fit">
          <TabsTrigger value="pixabay">Pixabay</TabsTrigger>
          <TabsTrigger value="pexels">Pexels</TabsTrigger>
          <TabsTrigger value="upload">Upload</TabsTrigger>
        </TabsList>

        {PROVIDERS.map((provider) => (
          <TabsContent key={provider} value={provider} className="mt-0">
            <Card className="min-w-0 overflow-hidden border-border/70 bg-card/90">
              <CardContent className="grid min-w-0 gap-4 p-4">
                <ProviderSearchPanel
                  provider={provider}
                  query={providerQueries[provider]}
                  results={providerResults[provider]}
                  page={providerPages[provider]}
                  totalPages={providerTotalPages[provider]}
                  isSearching={searchingProvider === provider}
                  selectedItems={selectedProviderVideos}
                  onQueryChange={(value) => setProviderQueries((current) => ({ ...current, [provider]: value }))}
                  onSearch={() => void handleProviderSearch(provider, 1)}
                  onPageChange={(nextPage) => void handleProviderSearch(provider, nextPage)}
                  onToggleSelected={handleToggleProviderVideo}
                />

                <div className="grid min-w-0 gap-3 border-t border-border/70 pt-4 md:grid-cols-[minmax(0,1fr)_auto]">
                  <div className="grid min-w-0 gap-2">
                    <Label className="text-xs">Tags khi luu vao thu vien</Label>
                    <Input
                      value={providerTags}
                      onChange={(event) => setProviderTags(event.target.value)}
                      placeholder="nature, city, abstract"
                    />
                  </div>
                  <div className="flex min-w-0 flex-wrap items-end gap-2">
                    <Badge variant="secondary" className="h-9 rounded-full px-3">
                      Da chon {selectedProviderList.length} video
                    </Badge>
                    {selectedProviderList.length ? (
                      <Button type="button" variant="outline" onClick={() => setSelectedProviderVideos({})} disabled={isImporting}>
                        <X className="mr-2 size-4" />
                        Bo chon
                      </Button>
                    ) : null}
                    <Button type="button" onClick={handleImportSelected} disabled={isImporting || !selectedProviderList.length}>
                      {isImporting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Check className="mr-2 size-4" />}
                      Submit tai & cat clip
                    </Button>
                  </div>
                </div>

                {importProgress ? (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>{importProgress.message}</span>
                      <span>{activeImportPercent}%</span>
                    </div>
                    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                      <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${activeImportPercent}%` }} />
                    </div>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </TabsContent>
        ))}

        <TabsContent value="upload" className="mt-0">
          <Card className="min-w-0 overflow-hidden border-border/70 bg-card/90">
            <CardContent className="space-y-4 p-4">
              <div className="grid gap-3">
                <div className="grid gap-2">
                  <Label className="text-xs">Chon file video</Label>
                  <Input
                    key={fileInputKey}
                    type="file"
                    accept="video/*"
                    multiple
                    onChange={(event) => setUploadFiles(Array.from(event.currentTarget.files ?? []))}
                  />
                  {uploadFiles.length > 0 ? (
                    <p className="text-xs text-muted-foreground">Da chon: {uploadFiles.map((file) => file.name).join(", ")}</p>
                  ) : null}
                </div>
                <div className="grid gap-2">
                  <Label className="text-xs">Tags khi luu vao thu vien</Label>
                  <Input value={uploadTags} onChange={(event) => setUploadTags(event.target.value)} placeholder="cinematic, loop, overlay" />
                </div>
                {uploadProgress ? (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>{uploadProgress.message}</span>
                      <span>{activeUploadPercent}%</span>
                    </div>
                    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                      <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${activeUploadPercent}%` }} />
                    </div>
                  </div>
                ) : null}
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setUploadFiles([]);
                      setFileInputKey((current) => current + 1);
                    }}
                    disabled={isUploading}
                  >
                    Xoa file da chon
                  </Button>
                  <Button type="button" onClick={handleStartUpload} disabled={isUploading || !uploadFiles.length}>
                    {isUploading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Upload className="mr-2 size-4" />}
                    Upload & cat clip
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {isLoading ? (
        <div className="flex items-center gap-3 py-8 text-sm text-muted-foreground">
          <div className="size-4 animate-spin rounded-full border-2 border-primary/25 border-t-primary" />
          <span>Dang tai thu vien clip...</span>
        </div>
      ) : clips.length === 0 ? (
        <EmptyCard title="Chua co clip trong thu vien" description="Search Pixabay/Pexels hoac upload file video de bat dau." />
      ) : (
        <>
          <section className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {clips.map((clip) => (
              <Card key={clip.id} className="relative min-w-0 overflow-hidden border-border/70 bg-card/90 shadow-lg">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="absolute right-2 top-2 z-10 h-8 w-8 rounded-full bg-destructive/80 p-0 text-white shadow-md hover:bg-destructive hover:text-white"
                  onClick={() => handleDeleteClip(clip.id)}
                  title="Xoa clip"
                >
                  <Trash2 className="size-4" />
                </Button>
                <CardContent className="space-y-3 p-3">
                  <div className="relative aspect-video w-full min-w-0 overflow-hidden rounded-lg bg-black">
                    <video
                      controls
                      preload="metadata"
                      src={`/media/${clip.relativePath}`}
                      className="absolute inset-0 block h-full w-full max-w-full object-cover"
                    />
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      {selectionMode ? (
                        <Checkbox
                          id={`clip-${clip.id}`}
                          checked={allSelected || selectedClipIds.includes(clip.id)}
                          onCheckedChange={(checked) => handleToggleClip(clip.id, Boolean(checked))}
                        />
                      ) : null}
                      <span className="truncate text-sm font-semibold text-foreground">{clip.sourceName || clip.id}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs text-muted-foreground">{clip.duration}s</span>
                      {sourceTypeBadge(clip.sourceType)}
                    </div>
                    {clip.tags.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {clip.tags.map((tag) => (
                          <Badge key={tag} variant="secondary" className="rounded-full px-2 py-0.5 text-[10px]">
                            {tag}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
            ))}
          </section>

          <PaginationBar
            page={page}
            totalPages={totalPages}
            onPrevious={() => setPage((current) => Math.max(1, current - 1))}
            onNext={() => setPage((current) => Math.min(totalPages, current + 1))}
          />
        </>
      )}
    </div>
  );
}
