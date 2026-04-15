import { Link, useParams } from "react-router-dom";
import { useEffect, useState } from "react";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { getResultPage, ApiError } from "@/lib/api";
import type { ResultPageResponse } from "@/types/api";

export function ResultPage() {
  const { jobId = "" } = useParams();
  const [data, setData] = useState<ResultPageResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);

    getResultPage(jobId)
      .then((response) => {
        if (!cancelled) {
          setData(response);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorMessage(error instanceof ApiError ? error.message : "Không thể tải kết quả render.");
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
  }, [jobId]);

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Đang tải kết quả render..." />
      </AppShell>
    );
  }

  if (!data) {
    return (
      <AppShell>
        <StatusAlert
          title="Không thể mở kết quả render"
          message={errorMessage || "Kết quả không khả dụng."}
          variant="destructive"
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <HeroCard
        eyebrow="Hoàn tất"
        title="Video đã được render"
        description="Hệ thống đã render từ clip mới, asset thư viện và image clip của job hiện tại. Các clip mới được gắn tag đã được lưu vào thư viện để dùng lại."
        stats={[
          { label: "Clip mới dùng để render", value: data.job.selectedClipIds.length },
          { label: "Asset thư viện đã chọn", value: data.job.selectedLibraryAssetIds.length },
          { label: "Ảnh nguồn", value: data.job.imagePaths.length },
          { label: "Audio", value: `${data.job.audioDuration}s` },
        ]}
      />

      {data.job.outputVideo ? (
        <PageSection>
          <div className="space-y-4">
            <video
              controls
              src={`/media/${data.job.outputVideo}`}
              className="aspect-video w-full rounded-3xl bg-black object-cover"
            />
            <p className="text-sm text-muted-foreground">Output: {data.job.outputVideo}</p>
          </div>
        </PageSection>
      ) : (
        <EmptyCard title="Chưa có output video" description="Render chưa sinh ra file output hợp lệ." />
      )}

      <PageSection className="border-none bg-transparent p-0 shadow-none">
        <div className="flex flex-wrap gap-3">
          <Button asChild size="lg">
            <Link to="/">Tạo job mới</Link>
          </Button>
          <Button asChild variant="secondary" size="lg">
            <Link to={`/jobs/${jobId}/resources`}>Xem lại thư viện tài nguyên</Link>
          </Button>
        </div>
      </PageSection>
    </AppShell>
  );
}
