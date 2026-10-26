import type { PipelineGate, PipelineItem, PipelineItemStatus, PipelineRunStatus } from "@/types/api";

export const STAGE_LABEL: Record<string, string> = {
  INGEST: "Đọc kịch bản",
  TTS: "Tạo giọng đọc",
  SRT_CHECK: "Kiểm tra phụ đề",
  SV_RENDER: "Dựng video nền",
  TST_STORY: "Tạo truyện TST",
  AI: "AI viết prompt",
  BUILD: "Build prompt",
  GATE_PROMPTS: "Duyệt prompt",
  CHARACTERS: "Ảnh nhân vật",
  GATE_CHARACTERS: "Duyệt ảnh nhân vật",
  SCENES: "Ảnh cảnh",
  VIDEO: "Dựng video ảnh",
  COLLECT: "Gom video",
  DONE: "Hoàn tất",
};

export const ITEM_STATUS_LABEL: Record<PipelineItemStatus, string> = {
  pending: "Chờ",
  running: "Đang chạy",
  waiting_service: "Chờ dịch vụ",
  awaiting_review: "Chờ duyệt",
  needs_attention: "Cần xử lý",
  failed: "Lỗi",
  cancelled: "Đã huỷ",
  done: "Xong",
};

export const ITEM_STATUS_CLASS: Record<PipelineItemStatus, string> = {
  pending: "border-border bg-muted text-muted-foreground",
  running: "border-sky-300 bg-sky-100 text-sky-800 dark:border-sky-700 dark:bg-sky-950 dark:text-sky-200",
  waiting_service: "border-amber-300 bg-amber-100 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200",
  awaiting_review: "border-violet-300 bg-violet-100 text-violet-900 dark:border-violet-700 dark:bg-violet-950 dark:text-violet-200",
  needs_attention: "border-orange-300 bg-orange-100 text-orange-900 dark:border-orange-700 dark:bg-orange-950 dark:text-orange-200",
  failed: "border-red-300 bg-red-100 text-red-800 dark:border-red-700 dark:bg-red-950 dark:text-red-200",
  cancelled: "border-border bg-muted text-muted-foreground line-through",
  done: "border-emerald-300 bg-emerald-100 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-200",
};

export const RUN_STATUS_LABEL: Record<PipelineRunStatus, string> = {
  queued: "Chờ",
  running: "Đang chạy",
  paused: "Tạm dừng",
  completed: "Xong",
  completed_with_errors: "Xong (có lỗi)",
  cancelled: "Đã huỷ",
};

export const RUN_FINISHED = new Set<PipelineRunStatus>(["completed", "completed_with_errors", "cancelled"]);

export const STAGES_TYPE1 = [
  "INGEST", "TTS", "SRT_CHECK", "TST_STORY", "AI", "BUILD", "GATE_PROMPTS",
  "CHARACTERS", "GATE_CHARACTERS", "SCENES", "VIDEO", "COLLECT", "DONE",
];
export const STAGES_TYPE2 = ["INGEST", "TTS", "SRT_CHECK", "SV_RENDER", "DONE"];

/** Các bước của item; item có sẵn audio + srt bỏ qua đọc kịch bản và tạo giọng đọc (khớp stages_for ở server). */
export function stagesOf(item: PipelineItem) {
  const stages = item.type === 1 ? STAGES_TYPE1 : STAGES_TYPE2;
  return item.input === "media" ? stages.filter((stage) => stage !== "INGEST" && stage !== "TTS") : stages;
}

export const AUDIO_EXTENSIONS = ["mp3", "wav", "m4a", "aac", "flac", "ogg"];

export const SUBTITLE_LANGS: { value: string; label: string }[] = [
  { value: "", label: "Tự nhận diện" },
  { value: "vi", label: "Tiếng Việt" },
  { value: "th", label: "Tiếng Thái" },
  { value: "ko", label: "Tiếng Hàn" },
  { value: "ja", label: "Tiếng Nhật" },
  { value: "zh-TW", label: "Tiếng Trung (phồn thể)" },
  { value: "es", label: "Tiếng Tây Ban Nha" },
  { value: "en", label: "Tiếng Anh" },
];

export function formatBytes(bytes: number) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function formatTime(iso?: string | null) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("vi-VN", { hour12: false });
}

export const selectClassName = "h-9 w-full rounded-md border border-input bg-background px-3 text-sm";

/** Cổng duyệt của item (loại 1), null nếu không ở bước duyệt. */
export function gateOf(item: PipelineItem): PipelineGate | null {
  if (item.stage === "GATE_PROMPTS") return "prompts";
  if (item.stage === "GATE_CHARACTERS") return "characters";
  return null;
}
