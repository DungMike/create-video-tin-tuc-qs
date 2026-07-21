import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { getStoryLibraries } from "@/lib/api";
import {
  readActiveLibraryId,
  subscribeActiveLibrary,
  writeActiveLibraryId,
} from "@/lib/storyLibrarySelection";
import type { StoryLibrary } from "@/types/api";

export interface UseActiveStoryLibraryResult {
  libraries: StoryLibrary[];
  activeId: string;
  activeLibrary: StoryLibrary | null;
  defaultLibraryId: string;
  isLoading: boolean;
  error: string | null;
  setActiveId: (libraryId: string) => void;
  refresh: () => Promise<void>;
}

const DEFAULT_LIBRARY_ID = "default";

/**
 * Single source of truth for the active Story Video library, shared across pages.
 * Fetches the library list, validates the persisted selection (falling back to the
 * Default library when stale), and keeps the choice in sync via localStorage pub/sub.
 */
export function useActiveStoryLibrary(): UseActiveStoryLibraryResult {
  const [libraries, setLibraries] = useState<StoryLibrary[]>([]);
  const [defaultLibraryId, setDefaultLibraryId] = useState<string>(DEFAULT_LIBRARY_ID);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const persistedId = useSyncExternalStore(
    subscribeActiveLibrary,
    readActiveLibraryId,
    () => null,
  );

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await getStoryLibraries();
      setLibraries(res.libraries);
      setDefaultLibraryId(res.defaultLibraryId || DEFAULT_LIBRARY_ID);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không tải được danh sách thư viện.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Effective active id. Prefer the persisted choice. While the library list is
  // still loading (initial mount, or the refresh right after creating a library)
  // KEEP showing the persisted id instead of snapping to Default — snapping made
  // the picker "jump" to another library mid-flow and wiped the current search
  // selection. Only resolve to Default once we know (loaded, non-empty list) that
  // the persisted id is genuinely absent, or when nothing has been chosen yet.
  const persistedResolving = isLoading || libraries.length === 0;
  const activeId =
    persistedId && libraries.some((lib) => lib.id === persistedId)
      ? persistedId
      : persistedId && persistedResolving
        ? persistedId
        : defaultLibraryId;

  // Repair a stale persisted id once libraries have loaded.
  useEffect(() => {
    if (isLoading || libraries.length === 0) {
      return;
    }
    const stillValid = persistedId && libraries.some((lib) => lib.id === persistedId);
    if (stillValid || readActiveLibraryId() === activeId) {
      return;
    }
    // Defer the repair one tick and re-validate against the freshest state. A
    // just-created / just-selected library id can momentarily be absent from
    // this render's `libraries` while its refresh is still in flight; if that
    // refresh lands in between, this effect re-runs and cancels the timer, so we
    // never clobber a valid selection back to the default.
    const timer = window.setTimeout(() => {
      const current = readActiveLibraryId();
      if (current === activeId || (current && libraries.some((lib) => lib.id === current))) {
        return;
      }
      writeActiveLibraryId(activeId);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [isLoading, libraries, persistedId, activeId]);

  const setActiveId = useCallback((libraryId: string) => {
    writeActiveLibraryId(libraryId);
  }, []);

  const activeLibrary = libraries.find((lib) => lib.id === activeId) ?? null;

  return {
    libraries,
    activeId,
    activeLibrary,
    defaultLibraryId,
    isLoading,
    error,
    setActiveId,
    refresh,
  };
}
