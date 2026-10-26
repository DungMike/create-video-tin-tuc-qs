import { useCallback, useState } from "react";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { PipelineHealthPanel } from "@/components/pipeline/PipelineHealthPanel";
import { PipelineReviewDialog } from "@/components/pipeline/PipelineReviewDialog";
import { PipelineRunForm } from "@/components/pipeline/PipelineRunForm";
import { PipelineRunDetail, PipelineRunList } from "@/components/pipeline/PipelineRunView";
import { TopNav } from "@/components/top-nav";
import type { PipelineItem, PipelineRun } from "@/types/api";

const SELECTED_RUN_KEY = "pipeline.selectedRunId";

function readSelectedRun() {
  try {
    return window.localStorage.getItem(SELECTED_RUN_KEY);
  } catch {
    return null;
  }
}

function storeSelectedRun(runId: string) {
  try {
    window.localStorage.setItem(SELECTED_RUN_KEY, runId);
  } catch {
    // Chỉ mất phần nhớ lượt chạy đang xem.
  }
}

export function PipelinePage() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(readSelectedRun);
  const [listKey, setListKey] = useState(0);
  const [reviewTarget, setReviewTarget] = useState<{ run: PipelineRun; item: PipelineItem } | null>(null);
  const [detailKey, setDetailKey] = useState(0);

  const selectRun = useCallback((runId: string) => {
    setSelectedRunId(runId);
    storeSelectedRun(runId);
  }, []);

  const onCreated = useCallback(
    (run: PipelineRun) => {
      selectRun(run.runId);
      setListKey((key) => key + 1);
    },
    [selectRun],
  );

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Pipeline"
        title="Tạo video tự động"
        description="Kịch bản .docx/.txt → giọng đọc + phụ đề (app TTS) → video, hoặc dùng luôn audio + .srt có sẵn. Loại 1: ảnh minh hoạ qua TST Studio, AI viết prompt và dừng chờ duyệt trước khi tạo ảnh. Loại 2: ghép clip nền + hiệu ứng TV + phụ đề."
      />
      <PageSection>
        <PipelineHealthPanel />
      </PageSection>
      <PageSection>
        <PipelineRunForm onCreated={onCreated} />
      </PageSection>
      <PageSection>
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">Lượt chạy</h2>
          <PipelineRunList selectedRunId={selectedRunId} onSelect={selectRun} refreshKey={listKey} />
          {selectedRunId ? (
            <PipelineRunDetail
              key={`${selectedRunId}-${detailKey}`}
              runId={selectedRunId}
              onChanged={() => setListKey((key) => key + 1)}
              onReview={(run, item) => setReviewTarget({ run, item })}
            />
          ) : null}
        </div>
      </PageSection>
      {reviewTarget ? (
        <PipelineReviewDialog
          run={reviewTarget.run}
          item={reviewTarget.item}
          onClose={() => setReviewTarget(null)}
          onDone={() => {
            setDetailKey((key) => key + 1);
            setListKey((key) => key + 1);
          }}
        />
      ) : null}
    </AppShell>
  );
}
