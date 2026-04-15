import { startTransition, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { EmptyCard } from "@/components/empty-card";
import { LibraryAssetCard } from "@/components/library-asset-card";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getResourcesPage, renderJob, ApiError } from "@/lib/api";
import {
  mergeSelectionStates,
  normalizeTags,
  readSelectionState,
  setLibraryAssetSelected,
  writeSelectionState,
} from "@/lib/jobSelectionStore";
import type { JobSelectionState, ResourcesPageResponse } from "@/types/api";

export function ResourcesPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedTags = normalizeTags(searchParams.getAll("tag"));
  const selectedTagsKey = JSON.stringify(selectedTags);

  const [data, setData] = useState<ResourcesPageResponse | null>(null);
  const [selectionState, setSelectionState] = useState<JobSelectionState>(readSelectionState(jobId));
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);

    const requestedTags = JSON.parse(selectedTagsKey) as string[];

    getResourcesPage(jobId, requestedTags)
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
          setErrorMessage(error instanceof ApiError ? error.message : "Không thể tải thư viện tài nguyên.");
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
  }, [jobId, selectedTagsKey]);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    writeSelectionState(jobId, selectionState);
  }, [jobId, selectionState]);

  const toggleFilterTag = (tag: string) => {
    const nextTags = selectedTags.includes(tag)
      ? selectedTags.filter((value) => value !== tag)
      : [...selectedTags, tag];
    const params = new URLSearchParams();
    normalizeTags(nextTags).forEach((value) => params.append("tag", value));
    setSearchParams(params);
  };

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
        currentPage: 1,
      });
      startTransition(() => navigate(response.redirectUrl));
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Render thất bại.");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Đang tải thư viện tài nguyên..." />
      </AppShell>
    );
  }

  if (!data) {
    return (
      <AppShell>
        <StatusAlert
          title="Không thể mở thư viện tài nguyên"
          message={errorMessage || "Dữ liệu tài nguyên không khả dụng."}
          variant="destructive"
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <HeroCard
        eyebrow={`Job ${data.job.jobId}`}
        title="Chọn clip từ thư viện tài nguyên"
        description="Lọc theo tag để lấy clip đã lưu từ các job trước, rồi trộn với clip mới của job hiện tại khi render."
        stats={[
          { label: "Tổng asset thư viện", value: data.totalAssetCount },
          { label: "Đang hiển thị", value: data.assets.length },
          { label: "Tag đang lọc", value: selectedTags.length },
          { label: "Asset đã chọn", value: selectionState.selectedLibraryAssetIds.length },
        ]}
      />

      {errorMessage ? <StatusAlert title="Có lỗi xảy ra" message={errorMessage} variant="destructive" /> : null}

      <PageSection className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button asChild variant="secondary">
            <Link to={`/jobs/${jobId}/review`}>Quay lại review clip mới</Link>
          </Button>
          <Button variant="outline" onClick={() => setSearchParams(new URLSearchParams())}>
            Clear filter
          </Button>
        </div>

        <div className="flex flex-wrap gap-2">
          {data.availableTags.length ? (
            data.availableTags.map((tag) => {
              const isActive = selectedTags.includes(tag);
              return (
                <Badge
                  key={tag}
                  variant={isActive ? "default" : "secondary"}
                  className="cursor-pointer rounded-full px-3 py-1"
                  onClick={() => toggleFilterTag(tag)}
                >
                  {tag}
                </Badge>
              );
            })
          ) : (
            <span className="text-sm text-muted-foreground">Thư viện chưa có tag nào.</span>
          )}
        </div>
      </PageSection>

      {data.assets.length ? (
        <section className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {data.assets.map((asset) => (
            <LibraryAssetCard
              key={asset.assetId}
              asset={asset}
              checked={selectionState.selectedLibraryAssetIds.includes(asset.assetId)}
              onCheckedChange={(checked) =>
                setSelectionState((current) => setLibraryAssetSelected(current, asset.assetId, checked))
              }
            />
          ))}
        </section>
      ) : (
        <EmptyCard
          title="Không có asset phù hợp"
          description="Thử bỏ bớt tag filter hoặc quay lại review để gắn tag cho clip mới."
        />
      )}

      <PageSection className="border-none bg-transparent p-0 shadow-none">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="text-sm text-muted-foreground">
            Asset đã chọn: <span className="font-semibold text-foreground">{selectionState.selectedLibraryAssetIds.length}</span>
          </div>
          <Button size="lg" onClick={handleRender} disabled={isSubmitting}>
            {isSubmitting ? "Đang render..." : "Render với selection hiện tại"}
          </Button>
        </div>
      </PageSection>
    </AppShell>
  );
}
