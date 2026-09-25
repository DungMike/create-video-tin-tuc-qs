import { useCallback, useState } from "react";

import type { ClipUsageMode } from "@/types/api";

const STORAGE_KEY = "story-video-clip-usage-mode";

function readStoredMode(): ClipUsageMode {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "once" ? "once" : "reuse";
  } catch {
    return "reuse";
  }
}

/**
 * Chế độ chọn clip khi render, nhớ theo trình duyệt. "reuse" (mặc định) = bốc ngẫu
 * nhiên như cũ; "once" = mỗi clip 1 lần (cần MongoDB).
 */
export function useClipUsageMode(): [ClipUsageMode, (mode: ClipUsageMode) => void] {
  const [mode, setMode] = useState<ClipUsageMode>(readStoredMode);
  const update = useCallback((next: ClipUsageMode) => {
    setMode(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Ignore storage failures (private mode, quota, etc.).
    }
  }, []);
  return [mode, update];
}
