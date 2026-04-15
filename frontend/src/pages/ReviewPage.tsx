import { startTransition, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { PaginationBar } from "@/components/pagination-bar";
import { ReviewClipCard } from "@/components/review-clip-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { getReviewPage, renderJob, ApiError } from "@/lib/api";
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

export function ReviewPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(Number(searchParams.get("page") || 1), 1);

  const [data, setData] = useState<ReviewPageResponse | null>(null);
  const [selectionState, setSelectionState] = useState<JobSelectionState>(emptySelectionState);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

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
        const merged = mergeSelectionStates(
          readSelectionState(jobId),
          {
            selectedClipIds: response.job.selectedClipIds,
            selectedLibraryAssetIds: response.job.selectedLibraryAssetIds,
            clipTags: response.job.clipTags,
          },
        );
        setSelectionState(merged);
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorMessage(error instanceof ApiError ? error.message : "Không thể tải dữ liệu review.");
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
    if (!jobId) {
      return;
    }
    writeSelectionState(jobId, selectionState);
  }, [jobId, selectionState]);

  const handleRender = async () => {
    if (!data) {
      return;
    }
    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      const response = await renderJob(jobId, {
        selectedClipIds: selectionState.selectedClipIds,
        selectedLibraryAssetIds: selectionState.selectedLibraryAssetIds,
        clipTags: selectionState.clipTags,
        currentPage: page,
      });
      startTransition(() => navigate(response.redirectUrl));
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Render thất bại.");
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
        <LoadingCard message="Đang tải dữ liệu review..." />
      </AppShell>
    );
  }

  if (!data) {
    return (
      <AppShell>
        <StatusAlert
          title="Không thể mở màn review"
          message={errorMessage || "Dữ liệu review không khả dụng."}
          variant="destructive"
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <HeroCard
        eyebrow={`Job ${data.job.jobId}`}
        title="Review clip và gắn tag tài nguyên"
        description="Chọn clip dùng cho lần render hiện tại, đồng thời gắn tag để lưu lại clip có giá trị tái sử dụng vào thư viện."
        stats={[
          { label: "Audio", value: `${data.job.audioDuration}s` },
          { label: "Images", value: data.job.imagePaths.length },
          { label: "Clip mới", value: data.pagination.totalItems },
          { label: "Tài nguyên thư viện", value: data.libraryAssetCount },
        ]}
      />

      {errorMessage ? <StatusAlert title="Có lỗi xảy ra" message={errorMessage} variant="destructive" /> : null}

      {data.job.downloadErrors.length ? (
        <StatusAlert
          title="Có lỗi khi tải video nguồn"
          message={data.job.downloadErrors.join(" | ")}
          variant="destructive"
        />
      ) : null}

      <PageSection className="border-none bg-transparent p-0 shadow-none">
        <div className="flex flex-wrap items-center gap-3">
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
            Chọn toàn bộ clip trang này
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
            Bỏ chọn clip trang này
          </Button>
          <Button asChild variant="secondary">
            <Link to={`/jobs/${jobId}/resources`}>Mở thư viện tài nguyên</Link>
          </Button>
          <div className="text-sm text-muted-foreground">
            Clip render đã chọn: <span className="font-semibold text-foreground">{selectionState.selectedClipIds.length}</span>
          </div>
          <div className="text-sm text-muted-foreground">
            Asset thư viện đã chọn: <span className="font-semibold text-foreground">{selectionState.selectedLibraryAssetIds.length}</span>
          </div>
          <div className="text-sm text-muted-foreground">
            Đang xem {data.pagination.startItem}-{data.pagination.endItem} / {data.pagination.totalItems}
          </div>
        </div>
      </PageSection>

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
      ) : (
        <EmptyCard title="Không có clip ở trang này" description="Hãy upload thêm video hoặc quay lại page khác." />
      )}

      <PageSection className="border-none bg-transparent p-0 shadow-none">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <PaginationBar
            page={data.pagination.page}
            totalPages={data.pagination.totalPages}
            onPrevious={() => updatePage(data.pagination.page - 1)}
            onNext={() => updatePage(data.pagination.page + 1)}
          />
          <Button size="lg" onClick={handleRender} disabled={isSubmitting}>
            {isSubmitting ? "Đang render..." : "Render với selection hiện tại"}
          </Button>
        </div>
      </PageSection>
    </AppShell>
  );
}
