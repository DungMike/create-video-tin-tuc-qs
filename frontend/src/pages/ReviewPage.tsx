import { startTransition, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { PaginationBar } from "@/components/pagination-bar";
import { ReviewClipCard } from "@/components/review-clip-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { ApiError, getDecorVideos, getJobProgress, getReviewPage, renderJob } from "@/lib/api";
import type { DecorVideo } from "@/types/api";
import {
  emptySelectionState,
  mergeSelectionStates,
  normalizeTags,
  readSelectionState,
  setClipSelected,
  setClipTags,
  toggleClipTag,
  writeSelectionState,
} from "@/lib/jobSelectionStore";
import type { JobSelectionState, ReviewPageResponse } from "@/types/api";
import type { JobProgress } from "@/types/api";

export function ReviewPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(Number(searchParams.get("page") || 1), 1);

  const [data, setData] = useState<ReviewPageResponse | null>(null);
  const [selectionState, setSelectionState] = useState<JobSelectionState>(emptySelectionState);
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [decorVideos, setDecorVideos] = useState<DecorVideo[]>([]);
  const [selectedDecorId, setSelectedDecorId] = useState("");

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);

    getReviewPage(jobId, page)
      .then((response) => {
        if (cancelled) {
          return;
        }
        setData(response);
        setSelectionState(
          mergeSelectionStates(readSelectionState(jobId), {
            selectedClipIds: response.job.selectedClipIds,
            selectedLibraryAssetIds: response.job.selectedLibraryAssetIds,
            clipTags: response.job.clipTags,
          }),
        );
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorMessage(error instanceof ApiError ? error.message : "Khong the tai du lieu review.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [jobId, page]);

  useEffect(() => {
    getDecorVideos()
      .then((r) => setDecorVideos(r.decorVideos))
      .catch(() => setDecorVideos([]));
  }, []);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    writeSelectionState(jobId, selectionState);
  }, [jobId, selectionState]);

  useEffect(() => {
    if (!jobId || !isSubmitting) {
      return;
    }

    let cancelled = false;
    const loadProgress = () => {
      getJobProgress(jobId)
        .then((response) => {
          if (!cancelled) {
            setProgress(response.progress);
          }
        })
        .catch(() => undefined);
    };

    loadProgress();
    const intervalId = window.setInterval(loadProgress, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [jobId, isSubmitting]);

  const handleRender = async () => {
    if (!data) {
      return;
    }
    setIsSubmitting(true);
    setErrorMessage(null);
    setProgress(null);

    try {
      const response = await renderJob(jobId, {
        selectedClipIds: selectionState.selectedClipIds,
        selectedLibraryAssetIds: selectionState.selectedLibraryAssetIds,
        clipTags: selectionState.clipTags,
        currentPage: page,
        decorVideoId: selectedDecorId || undefined,
      });
      startTransition(() => navigate(response.redirectUrl));
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Render that bai.");
      try {
        const response = await getJobProgress(jobId);
        setProgress(response.progress);
      } catch {
        // Keep the visible error from the render request.
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const updatePage = (nextPage: number) => {
    const params = new URLSearchParams(searchParams);
    if (nextPage <= 1) {
      params.delete("page");
    } else {
      params.set("page", String(nextPage));
    }
    setSearchParams(params);
  };

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai du lieu review..." />
      </AppShell>
    );
  }

  if (!data) {
    return (
      <AppShell>
        <StatusAlert
          title="Khong the mo man review"
          message={errorMessage || "Du lieu review khong kha dung."}
          variant="destructive"
        />
      </AppShell>
    );
  }

  const isImageOnly =
    data.job.renderMode === "image_audio_only" ||
    (data.job.imagePaths.length > 0 && data.job.sourceVideos.length === 0 && data.pagination.totalItems === 0);
  const selectedAssetCount = selectionState.selectedLibraryAssetIds.length;
  const renderButtonText = isImageOnly ? "Render video tu anh va audio" : "Render voi selection hien tai";
  const progressPercent = Math.max(0, Math.min(100, Math.round(progress?.percent ?? 0)));
  const progressLogs = (progress?.recentLogs ?? []).slice(-8).reverse();
  const showProgress = isSubmitting || progress?.status === "running" || progress?.status === "failed";

  return (
    <AppShell>
      <HeroCard
        eyebrow={`Job ${data.job.jobId}`}
        title={isImageOnly ? "Render tu anh va audio" : "Review clip va gan tag tai nguyen"}
        description={
          isImageOnly
            ? "Khong co video nguon trong job nay. He thong se dung cac anh da upload, them animation, transition va lap lai den het audio."
            : "Chon clip dung cho lan render hien tai, dong thoi gan tag de luu lai clip co gia tri tai su dung vao thu vien."
        }
        stats={[
          { label: "Audio", value: `${data.job.audioDuration}s` },
          { label: "Images", value: data.job.imagePaths.length },
          { label: isImageOnly ? "Che do" : "Clip moi", value: isImageOnly ? "Image + audio" : data.pagination.totalItems },
          { label: "Tai nguyen thu vien", value: data.libraryAssetCount },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}

      {data.job.downloadErrors.length ? (
        <StatusAlert
          title="Co loi khi tai video nguon"
          message={data.job.downloadErrors.join(" | ")}
          variant="destructive"
        />
      ) : null}

      <PageSection className="border-none bg-transparent p-0 shadow-none">
        <div className="flex flex-wrap items-center gap-3">
          {!isImageOnly ? (
            <>
              <Button
                variant="outline"
                onClick={() => {
                  const currentIds = new Set(selectionState.selectedClipIds);
                  data.pageClips.forEach((clip) => currentIds.add(clip.id));
                  setSelectionState((current) => ({
                    ...current,
                    selectedClipIds: Array.from(currentIds),
                  }));
                }}
              >
                Chon toan bo clip trang nay
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  const pageIds = new Set(data.pageClips.map((clip) => clip.id));
                  setSelectionState((current) => ({
                    ...current,
                    selectedClipIds: current.selectedClipIds.filter((clipId) => !pageIds.has(clipId)),
                  }));
                }}
              >
                Bo chon clip trang nay
              </Button>
            </>
          ) : null}
          <Button asChild variant="secondary">
            <Link to={`/jobs/${jobId}/resources`}>Mo thu vien tai nguyen</Link>
          </Button>
          <Button asChild variant="secondary">
            <Link to="/effects-library">Cau hinh hieu ung anh</Link>
          </Button>
          {!isImageOnly ? (
            <div className="text-sm text-muted-foreground">
              Clip render da chon:{" "}
              <span className="font-semibold text-foreground">{selectionState.selectedClipIds.length}</span>
            </div>
          ) : null}
          <div className="text-sm text-muted-foreground">
            Asset thu vien da chon:{" "}
            <span className="font-semibold text-foreground">{selectedAssetCount}</span>
          </div>
          {!isImageOnly ? (
            <div className="text-sm text-muted-foreground">
              Dang xem {data.pagination.startItem}-{data.pagination.endItem} / {data.pagination.totalItems}
            </div>
          ) : null}
          {decorVideos.length ? (
            <div className="flex items-center gap-2">
              <label htmlFor="decor-select" className="text-sm text-muted-foreground whitespace-nowrap">
                Decor video:
              </label>
              <select
                id="decor-select"
                value={selectedDecorId}
                onChange={(e) => setSelectedDecorId(e.target.value)}
                className="h-8 rounded-md border border-input bg-background px-2 text-sm"
              >
                <option value="">Khong dung decor</option>
                {decorVideos.map((dv) => (
                  <option key={dv.id} value={dv.id}>
                    {dv.name} ({dv.durationSeconds}s)
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </div>
      </PageSection>

      {showProgress ? (
        <PageSection>
          <div className="grid gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-foreground">
                  Tien trinh render: {progress?.message ?? "Dang khoi dong render..."}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  Stage: {progress?.stage ?? "starting"} | Status: {progress?.status ?? "running"}
                </div>
              </div>
              <div className="text-2xl font-semibold text-foreground">{progressPercent}%</div>
            </div>

            <div className="h-3 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${progressPercent}%` }}
              />
            </div>

            <div className="grid gap-3 text-sm sm:grid-cols-3">
              <div className="rounded-md border border-border/70 bg-background/70 p-3">
                <div className="text-muted-foreground">Anh da xu ly</div>
                <div className="mt-1 font-semibold text-foreground">
                  {progress?.current.image ?? 0} / {progress?.totals.images ?? data.job.imagePaths.length}
                </div>
              </div>
              <div className="rounded-md border border-border/70 bg-background/70 p-3">
                <div className="text-muted-foreground">Segment render</div>
                <div className="mt-1 font-semibold text-foreground">
                  {progress?.current.segment ?? 0} / {progress?.totals.segments ?? 0}
                </div>
              </div>
              <div className="rounded-md border border-border/70 bg-background/70 p-3">
                <div className="text-muted-foreground">Chunk render</div>
                <div className="mt-1 font-semibold text-foreground">
                  {progress?.current.chunk ?? 0} / {progress?.totals.chunks ?? 0}
                </div>
              </div>
            </div>

            {progressLogs.length ? (
              <div className="max-h-48 overflow-auto rounded-md border border-border/70 bg-background p-3 font-mono text-xs leading-5">
                {progressLogs.map((entry) => (
                  <div key={`${entry.time}-${entry.message}`} className="text-muted-foreground">
                    <span className="text-foreground">{entry.time}</span> [{entry.level}] {entry.message}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </PageSection>
      ) : null}

      {data.pageClips.length ? (
        <section className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {data.pageClips.map((clip) => (
            <ReviewClipCard
              key={clip.id}
              clip={clip}
              checked={selectionState.selectedClipIds.includes(clip.id)}
              activeTags={selectionState.clipTags[clip.id] ?? []}
              suggestedTags={data.availableTags}
              onCheckedChange={(checked) => {
                setSelectionState((current) => setClipSelected(current, clip.id, checked));
              }}
              onToggleTag={(tag) => {
                setSelectionState((current) => toggleClipTag(current, clip.id, tag));
              }}
              onAddTag={(tag) => {
                const normalized = normalizeTags([tag]);
                if (!normalized.length) {
                  return;
                }
                setSelectionState((current) =>
                  setClipTags(current, clip.id, [...(current.clipTags[clip.id] ?? []), ...normalized]),
                );
              }}
            />
          ))}
        </section>
      ) : isImageOnly ? (
        <EmptyCard
          title="San sang render tu anh"
          description={`Job nay co ${data.job.imagePaths.length} anh nguon. Neu so anh khong du, renderer se lap lai anh den het audio.`}
        />
      ) : (
        <EmptyCard title="Khong co clip o trang nay" description="Hay upload them video hoac quay lai page khac." />
      )}

      <PageSection className="border-none bg-transparent p-0 shadow-none">
        <div className="flex flex-wrap items-center justify-between gap-4">
          {!isImageOnly ? (
            <PaginationBar
              page={data.pagination.page}
              totalPages={data.pagination.totalPages}
              onPrevious={() => updatePage(data.pagination.page - 1)}
              onNext={() => updatePage(data.pagination.page + 1)}
            />
          ) : (
            <div className="text-sm text-muted-foreground">
              Anh nguon: <span className="font-semibold text-foreground">{data.job.imagePaths.length}</span>
            </div>
          )}
          <Button size="lg" onClick={handleRender} disabled={isSubmitting}>
            {isSubmitting ? "Dang render..." : renderButtonText}
          </Button>
        </div>
      </PageSection>
    </AppShell>
  );
}
