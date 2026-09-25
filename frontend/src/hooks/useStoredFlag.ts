import { useCallback, useState } from "react";

/** Một checkbox nhớ theo trình duyệt (localStorage). Lỗi storage thì dùng `fallback`. */
export function useStoredFlag(storageKey: string, fallback: boolean): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      return raw === null ? fallback : raw === "1";
    } catch {
      return fallback;
    }
  });
  const update = useCallback(
    (next: boolean) => {
      setValue(next);
      try {
        window.localStorage.setItem(storageKey, next ? "1" : "0");
      } catch {
        // Ignore storage failures (private mode, quota, etc.).
      }
    },
    [storageKey],
  );
  return [value, update];
}

/** Dùng chung cho harvest + prefetch: "Bỏ qua từ khóa đã dùng" (mặc định tắt = như cũ). */
export const SKIP_USED_KEYWORDS_STORAGE_KEY = "story-video-skip-used-keywords";
