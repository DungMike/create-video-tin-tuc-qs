import { useCallback, useState } from "react";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { PipelineHealthPanel } from "@/components/pipeline/PipelineHealthPanel";
import { PipelineRunForm } from "@/components/pipeline/PipelineRunForm";
import { PipelineRunDetail, PipelineRunList } from "@/components/pipeline/PipelineRunView";
import { TopNav } from "@/components/top-nav";
import type { PipelineRun } from "@/types/api";

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
        description="Kịch bản .docx/.txt → giọng đọc + phụ đề (app TTS) → video. Loại 1: ảnh minh hoạ qua TST Studio, AI viết prompt và dừng chờ duyệt trước khi tạo ảnh. Loại 2: ghép clip nền + hiệu ứng TV + phụ đề."
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
            <PipelineRunDetail key={selectedRunId} runId={selectedRunId} onChanged={() => setListKey((key) => key + 1)} />
          ) : null}
        </div>
      </PageSection>
    </AppShell>
  );
}
