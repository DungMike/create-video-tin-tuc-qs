import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { TopNav } from "@/components/top-nav";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { ApiError, getResultPage } from "@/lib/api";
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
          setErrorMessage(error instanceof ApiError ? error.message : "Khong the tai ket qua render.");
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
        <LoadingCard message="Dang tai ket qua render..." />
      </AppShell>
    );
  }

  if (!data) {
    return (
      <AppShell>
        <StatusAlert
          title="Khong the mo ket qua render"
          message={errorMessage || "Ket qua khong kha dung."}
          variant="destructive"
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Hoan tat"
        title="Video da duoc render"
        description="He thong da render tu clip moi, asset thu vien va image clip cua job hien tai. Cac clip moi duoc gan tag da duoc luu vao thu vien de dung lai."
        stats={[
          { label: "Clip moi dung de render", value: data.job.selectedClipIds.length },
          { label: "Asset thu vien da chon", value: data.job.selectedLibraryAssetIds.length },
          { label: "Anh nguon", value: data.job.imagePaths.length },
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
        <EmptyCard title="Chua co output video" description="Render chua sinh ra file output hop le." />
      )}

      <PageSection className="border-none bg-transparent p-0 shadow-none">
        <div className="flex flex-wrap gap-3">
          <Button asChild size="lg">
            <Link to="/">Tao job moi</Link>
          </Button>
          <Button asChild variant="secondary" size="lg">
            <Link to={`/jobs/${jobId}/resources`}>Xem lai thu vien tai nguyen</Link>
          </Button>
          <Button asChild variant="secondary" size="lg">
            <Link to="/effects-library">Cau hinh hieu ung</Link>
          </Button>
        </div>
      </PageSection>
    </AppShell>
  );
}
