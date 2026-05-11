import { useEffect, useMemo, useState } from "react";
import { PaginationBar } from "@/components/pagination-bar";
import { ReviewClipCard } from "@/components/review-clip-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  deleteBatchSourceClip,
  getBatchLibrarySources,
  getBatchSourceSet,
  getBatchSourceSets,
  prepareBatchSourceVideos,
} from "@/lib/api";
import {
  emptySelectionState,
  normalizeTags,
  setClipSelected,
  setClipTags,
  toggleClipTag,
} from "@/lib/jobSelectionStore";
import type { BatchSourceRef, BatchSourceSet, JobSelectionState, ReviewClip } from "@/types/api";

type SourceMode = "links" | "sets" | "library";

const SOURCE_CLIP_PAGE_SIZE = 12;

function batchClipUiId(batchSourceId: string, clipId: string): string {
  return `batch_source:${batchSourceId}:${clipId}`;
}

function refFromClipId(clipId: string): BatchSourceRef | null {
  if (clipId.startsWith("library:")) {
    return { origin: "library", assetId: clipId.slice("library:".length) };
  }
  const parts = clipId.split(":");
  if (parts.length === 3 && parts[0] === "batch_source") {
    return { origin: "batch_source", batchSourceId: parts[1], clipId: parts[2] };
  }
  return null;
}

function toBatchSourceClip(batchSourceId: string, clip: ReviewClip): ReviewClip {
  return { ...clip, id: batchClipUiId(batchSourceId, clip.id) };
}

function mergeUniqueClips(existing: ReviewClip[], incoming: ReviewClip[]): ReviewClip[] {
  const seen = new Set(existing.map((clip) => clip.id));
  const merged = [...existing];
  incoming.forEach((clip) => {
    if (!seen.has(clip.id)) {
      seen.add(clip.id);
      merged.push(clip);
    }
  });
  return merged;
}

export function BatchSourcePicker({
  disabled = false,
  onSelectionChange,
}: {
  disabled?: boolean;
  onSelectionChange: (clips: ReviewClip[]) => void;
}) {
  const [sourceMode, setSourceMode] = useState<SourceMode>("links");
  const [sourceVideoLinks, setSourceVideoLinks] = useState("");
  const [sourceVideoFiles, setSourceVideoFiles] = useState<File[]>([]);
  const [sourceVideoInputKey, setSourceVideoInputKey] = useState(0);
  const [isPreparingSourceVideos, setIsPreparingSourceVideos] = useState(false);
  const [sourceSets, setSourceSets] = useState<BatchSourceSet[]>([]);
  const [selectedSourceSetIds, setSelectedSourceSetIds] = useState<string[]>([]);
  const [isLoadingSourceSets, setIsLoadingSourceSets] = useState(false);
  const [libraryTags, setLibraryTags] = useState<string[]>([]);
  const [selectedLibraryTags, setSelectedLibraryTags] = useState<string[]>([]);
  const [isLoadingLibrary, setIsLoadingLibrary] = useState(false);
  const [sourceClips, setSourceClips] = useState<ReviewClip[]>([]);
  const [sourceClipRefs, setSourceClipRefs] = useState<Record<string, BatchSourceRef>>({});
  const [sourceDownloadErrors, setSourceDownloadErrors] = useState<string[]>([]);
  const [sourceAvailableTags, setSourceAvailableTags] = useState<string[]>([]);
  const [sourceSelectionState, setSourceSelectionState] = useState<JobSelectionState>(emptySelectionState);
  const [sourceClipPage, setSourceClipPage] = useState(1);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getBatchSourceSets(), getBatchLibrarySources([])])
      .then(([sourceSetRes, libraryRes]) => {
        if (cancelled) return;
        setSourceSets(sourceSetRes.sourceSets);
        setLibraryTags(libraryRes.availableTags);
        setSourceAvailableTags(libraryRes.availableTags);
      })
      .catch((err) => {
        if (!cancelled) setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai source picker.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const addRefsForClips = (clips: ReviewClip[], refs: Record<string, BatchSourceRef>) => {
    setSourceClips((current) => mergeUniqueClips(current, clips));
    setSourceClipRefs((current) => ({ ...current, ...refs }));
  };

  const selectedClips = useMemo(() => {
    const selected = new Set(sourceSelectionState.selectedClipIds);
    return sourceClips.filter((clip) => selected.has(clip.id));
  }, [sourceClips, sourceSelectionState.selectedClipIds]);

  useEffect(() => {
    onSelectionChange(selectedClips);
  }, [onSelectionChange, selectedClips]);

  const loadSourceSet = async (batchSourceId: string) => {
    const response = await getBatchSourceSet(batchSourceId);
    const clips = response.reviewClips.map((clip) => toBatchSourceClip(batchSourceId, clip));
    const refs = Object.fromEntries(
      response.reviewClips.map((clip) => [
        batchClipUiId(batchSourceId, clip.id),
        { origin: "batch_source", batchSourceId, clipId: clip.id } as BatchSourceRef,
      ]),
    );
    addRefsForClips(clips, refs);
    setSourceAvailableTags(response.availableTags);
    setSourceDownloadErrors((current) => [...current, ...response.sourceSet.downloadErrors]);
  };

  const handlePrepareSourceVideos = async () => {
    setErrorMessage(null);
    const trimmedLinks = sourceVideoLinks.trim();
    if (!trimmedLinks && !sourceVideoFiles.length) {
      setErrorMessage("Can nhap link video hoac chon it nhat 1 file source video.");
      return;
    }

    const formData = new FormData();
    formData.set("youtube_links", trimmedLinks);
    sourceVideoFiles.forEach((file) => formData.append("videos", file));
    setIsPreparingSourceVideos(true);
    try {
      const response = await prepareBatchSourceVideos(formData);
      const clips = response.reviewClips.map((clip) => toBatchSourceClip(response.batchSourceId, clip));
      const refs = Object.fromEntries(
        response.reviewClips.map((clip) => [
          batchClipUiId(response.batchSourceId, clip.id),
          { origin: "batch_source", batchSourceId: response.batchSourceId, clipId: clip.id } as BatchSourceRef,
        ]),
      );
      addRefsForClips(clips, refs);
      setSelectedSourceSetIds((current) => Array.from(new Set([...current, response.batchSourceId])));
      setSourceDownloadErrors(response.downloadErrors);
      setSourceAvailableTags(response.availableTags);
      setSourceClipPage(1);
      setSourceVideoFiles([]);
      setSourceVideoInputKey((current) => current + 1);
      const sourceSetRes = await getBatchSourceSets();
      setSourceSets(sourceSetRes.sourceSets);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai/upload va cat source video.");
    } finally {
      setIsPreparingSourceVideos(false);
    }
  };

  const handleLoadSelectedSourceSets = async () => {
    setErrorMessage(null);
    setIsLoadingSourceSets(true);
    try {
      for (const sourceSetId of selectedSourceSetIds) {
        await loadSourceSet(sourceSetId);
      }
      setSourceClipPage(1);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai source clips co san.");
    } finally {
      setIsLoadingSourceSets(false);
    }
  };

  const handleLoadLibrarySources = async () => {
    setErrorMessage(null);
    setIsLoadingLibrary(true);
    try {
      const response = await getBatchLibrarySources(selectedLibraryTags);
      const refs = Object.fromEntries(
        response.clips.map((clip) => {
          const ref = refFromClipId(clip.id);
          return ref ? [clip.id, ref] : [clip.id, { origin: "library", assetId: clip.id.replace(/^library:/, "") }];
        }),
      ) as Record<string, BatchSourceRef>;
      addRefsForClips(response.clips, refs);
      setLibraryTags(response.availableTags);
      setSourceAvailableTags(response.availableTags);
      setSourceClipPage(1);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai library source.");
    } finally {
      setIsLoadingLibrary(false);
    }
  };

  const handleDeleteClip = async (clipId: string) => {
    const ref = sourceClipRefs[clipId] ?? refFromClipId(clipId);
    if (!ref || ref.origin !== "batch_source") return;
    try {
      await deleteBatchSourceClip(ref.batchSourceId, ref.clipId);
      setSourceClips((current) => current.filter((clip) => clip.id !== clipId));
      setSourceSelectionState((current) => ({
        ...current,
        selectedClipIds: current.selectedClipIds.filter((id) => id !== clipId),
      }));
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the xoa clip tu server.");
    }
  };

  const sourceTotalPages = Math.max(1, Math.ceil(sourceClips.length / SOURCE_CLIP_PAGE_SIZE));
  const normalizedSourceClipPage = Math.min(Math.max(sourceClipPage, 1), sourceTotalPages);
  const sourceStartIndex = (normalizedSourceClipPage - 1) * SOURCE_CLIP_PAGE_SIZE;
  const sourcePageClips = sourceClips.slice(sourceStartIndex, sourceStartIndex + SOURCE_CLIP_PAGE_SIZE);
  const sourceEndItem = sourceClips.length ? sourceStartIndex + sourcePageClips.length : 0;
  const sourcePageClipIds = sourcePageClips.map((clip) => clip.id);

  return (
    <div className="grid gap-4">
      {errorMessage ? <StatusAlert title="Loi source picker" message={errorMessage} variant="destructive" /> : null}

      <div className="flex flex-wrap gap-2">
        <Button type="button" variant={sourceMode === "links" ? "default" : "outline"} onClick={() => setSourceMode("links")} disabled={disabled}>
          Tao tu link
        </Button>
        <Button type="button" variant={sourceMode === "sets" ? "default" : "outline"} onClick={() => setSourceMode("sets")} disabled={disabled}>
          Batch sources co san
        </Button>
        <Button type="button" variant={sourceMode === "library" ? "default" : "outline"} onClick={() => setSourceMode("library")} disabled={disabled}>
          Library theo tag
        </Button>
      </div>

      {sourceMode === "links" ? (
        <div className="grid gap-3">
          <div className="grid gap-2">
            <Label htmlFor="news_source_links">Link video YouTube/Bilibili</Label>
            <Textarea
              id="news_source_links"
              rows={4}
              value={sourceVideoLinks}
              onChange={(event) => setSourceVideoLinks(event.target.value)}
              placeholder="Moi link mot dong"
              disabled={disabled}
            />
          </div>
          <div className="grid gap-2">
            <Label>Hoac upload source video</Label>
            <Input
              key={sourceVideoInputKey}
              type="file"
              accept=".mp4,.mov,.mkv,.webm,.avi"
              multiple
              disabled={disabled}
              onChange={(event) => setSourceVideoFiles(Array.from(event.currentTarget.files ?? []))}
            />
            {sourceVideoFiles.length ? (
              <p className="text-xs text-muted-foreground">Da chon: {sourceVideoFiles.map((file) => file.name).join(", ")}</p>
            ) : null}
          </div>
          <Button type="button" variant="secondary" onClick={handlePrepareSourceVideos} disabled={disabled || isPreparingSourceVideos}>
            {isPreparingSourceVideos ? "Dang tai/upload va cat clip..." : "Tai/upload va cat source video"}
          </Button>
        </div>
      ) : null}

      {sourceMode === "sets" ? (
        <div className="grid gap-3">
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {sourceSets.map((sourceSet) => (
              <label key={sourceSet.batchSourceId} className="grid cursor-pointer gap-1 rounded-lg border border-border/70 bg-background/70 p-3 text-sm">
                <span className="flex items-center gap-2 font-semibold text-foreground">
                  <input
                    type="checkbox"
                    checked={selectedSourceSetIds.includes(sourceSet.batchSourceId)}
                    disabled={disabled}
                    onChange={(event) => {
                      setSelectedSourceSetIds((current) =>
                        event.target.checked
                          ? Array.from(new Set([...current, sourceSet.batchSourceId]))
                          : current.filter((id) => id !== sourceSet.batchSourceId),
                      );
                    }}
                  />
                  {sourceSet.displayName}
                </span>
                <span className="text-xs text-muted-foreground">
                  {sourceSet.validClipCount}/{sourceSet.clipCount} clips | {sourceSet.batchSourceId}
                </span>
                {sourceSet.sourceNames.length ? <span className="truncate text-xs text-muted-foreground">{sourceSet.sourceNames.join(", ")}</span> : null}
              </label>
            ))}
          </div>
          <Button type="button" variant="secondary" onClick={handleLoadSelectedSourceSets} disabled={disabled || isLoadingSourceSets || !selectedSourceSetIds.length}>
            {isLoadingSourceSets ? "Dang tai clips..." : "Tai clip tu folder da chon"}
          </Button>
        </div>
      ) : null}

      {sourceMode === "library" ? (
        <div className="grid gap-3">
          <div className="flex flex-wrap gap-2">
            {libraryTags.map((tag) => (
              <Button
                key={tag}
                type="button"
                size="sm"
                variant={selectedLibraryTags.includes(tag) ? "default" : "outline"}
                disabled={disabled}
                onClick={() =>
                  setSelectedLibraryTags((current) =>
                    current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag],
                  )
                }
              >
                {tag}
              </Button>
            ))}
          </div>
          <Button type="button" variant="secondary" onClick={handleLoadLibrarySources} disabled={disabled || isLoadingLibrary}>
            {isLoadingLibrary ? "Dang tai library..." : "Tai clip library theo tag"}
          </Button>
        </div>
      ) : null}

      {sourceDownloadErrors.length ? (
        <StatusAlert title="Co link source video tai that bai" message={sourceDownloadErrors.join(" | ")} variant="destructive" />
      ) : null}

      {sourceClips.length ? (
        <div className="grid gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
            <span>
              Dang xem {sourceStartIndex + 1}-{sourceEndItem} / {sourceClips.length} clip | Da chon {selectedClips.length}
            </span>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={disabled}
                onClick={() =>
                  setSourceSelectionState((current) => ({
                    ...current,
                    selectedClipIds: Array.from(new Set([...current.selectedClipIds, ...sourcePageClipIds])),
                  }))
                }
              >
                Chon trang nay
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={disabled}
                onClick={() =>
                  setSourceSelectionState((current) => ({
                    ...current,
                    selectedClipIds: current.selectedClipIds.filter((clipId) => !sourcePageClipIds.includes(clipId)),
                  }))
                }
              >
                Bo chon trang nay
              </Button>
              <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => setSourceSelectionState((current) => ({ ...current, selectedClipIds: [] }))}>
                Bo chon tat ca
              </Button>
            </div>
            <PaginationBar
              page={normalizedSourceClipPage}
              totalPages={sourceTotalPages}
              onPrevious={() => setSourceClipPage((current) => Math.max(1, current - 1))}
              onNext={() => setSourceClipPage((current) => Math.min(sourceTotalPages, current + 1))}
            />
          </div>

          <section className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {sourcePageClips.map((clip) => {
              const ref = sourceClipRefs[clip.id] ?? refFromClipId(clip.id);
              return (
                <ReviewClipCard
                  key={clip.id}
                  clip={clip}
                  checked={sourceSelectionState.selectedClipIds.includes(clip.id)}
                  activeTags={sourceSelectionState.clipTags[clip.id] ?? []}
                  suggestedTags={sourceAvailableTags}
                  onCheckedChange={(checked) => setSourceSelectionState((current) => setClipSelected(current, clip.id, checked))}
                  onToggleTag={(tag) => setSourceSelectionState((current) => toggleClipTag(current, clip.id, tag))}
                  onAddTag={(tag) => {
                    const normalized = normalizeTags([tag]);
                    if (!normalized.length) return;
                    setSourceSelectionState((current) => setClipTags(current, clip.id, [...(current.clipTags[clip.id] ?? []), ...normalized]));
                  }}
                  onDelete={!disabled && ref?.origin === "batch_source" ? () => handleDeleteClip(clip.id) : undefined}
                />
              );
            })}
          </section>
        </div>
      ) : null}
    </div>
  );
}
