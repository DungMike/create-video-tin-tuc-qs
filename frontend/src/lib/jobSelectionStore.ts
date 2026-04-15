import type { JobSelectionState } from "@/types/api";

const normalizeStringArray = (values: unknown): string[] => {
  const seen = new Set<string>();
  const normalized: string[] = [];

  (Array.isArray(values) ? values : []).forEach((value) => {
    if (typeof value !== "string") {
      return;
    }
    const cleaned = value.trim();
    if (!cleaned || seen.has(cleaned)) {
      return;
    }
    seen.add(cleaned);
    normalized.push(cleaned);
  });

  return normalized;
};

export const normalizeTags = (values: unknown): string[] => {
  const seen = new Set<string>();
  const normalized: string[] = [];

  (Array.isArray(values) ? values : []).forEach((value) => {
    const cleaned = String(value ?? "").trim().toLowerCase().replace(/\s+/g, " ");
    if (!cleaned || seen.has(cleaned)) {
      return;
    }
    seen.add(cleaned);
    normalized.push(cleaned);
  });

  return normalized;
};

export const emptySelectionState = (): JobSelectionState => ({
  selectedClipIds: [],
  selectedLibraryAssetIds: [],
  clipTags: {},
});

export const normalizeSelectionState = (state: unknown): JobSelectionState => {
  const source = typeof state === "object" && state !== null ? (state as Record<string, unknown>) : {};
  const normalized: JobSelectionState = {
    selectedClipIds: normalizeStringArray(source.selectedClipIds),
    selectedLibraryAssetIds: normalizeStringArray(source.selectedLibraryAssetIds),
    clipTags: {},
  };

  const rawClipTags =
    typeof source.clipTags === "object" && source.clipTags !== null
      ? (source.clipTags as Record<string, unknown>)
      : {};

  Object.entries(rawClipTags).forEach(([clipId, tags]) => {
    const normalizedTags = normalizeTags(tags);
    if (clipId && normalizedTags.length) {
      normalized.clipTags[clipId] = normalizedTags;
    }
  });

  return normalized;
};

export const mergeSelectionStates = (
  baseState: JobSelectionState,
  extraState: JobSelectionState,
): JobSelectionState => {
  const base = normalizeSelectionState(baseState);
  const extra = normalizeSelectionState(extraState);

  const merged: JobSelectionState = {
    selectedClipIds: normalizeStringArray([...base.selectedClipIds, ...extra.selectedClipIds]),
    selectedLibraryAssetIds: normalizeStringArray([
      ...base.selectedLibraryAssetIds,
      ...extra.selectedLibraryAssetIds,
    ]),
    clipTags: { ...base.clipTags },
  };

  Object.entries(extra.clipTags).forEach(([clipId, tags]) => {
    merged.clipTags[clipId] = normalizeTags([...(merged.clipTags[clipId] ?? []), ...tags]);
  });

  return merged;
};

const storageKeyForJob = (jobId: string) => `job-selection-state-${jobId}`;

export const readSelectionState = (jobId: string): JobSelectionState => {
  try {
    const raw = window.localStorage.getItem(storageKeyForJob(jobId));
    return normalizeSelectionState(raw ? JSON.parse(raw) : {});
  } catch {
    return emptySelectionState();
  }
};

export const writeSelectionState = (jobId: string, state: JobSelectionState): JobSelectionState => {
  const normalized = normalizeSelectionState(state);
  window.localStorage.setItem(storageKeyForJob(jobId), JSON.stringify(normalized));
  return normalized;
};

export const setClipSelected = (
  state: JobSelectionState,
  clipId: string,
  selected: boolean,
): JobSelectionState => {
  const selectedIds = new Set(state.selectedClipIds);
  if (selected) {
    selectedIds.add(clipId);
  } else {
    selectedIds.delete(clipId);
  }

  return {
    ...state,
    selectedClipIds: Array.from(selectedIds),
  };
};

export const setLibraryAssetSelected = (
  state: JobSelectionState,
  assetId: string,
  selected: boolean,
): JobSelectionState => {
  const selectedIds = new Set(state.selectedLibraryAssetIds);
  if (selected) {
    selectedIds.add(assetId);
  } else {
    selectedIds.delete(assetId);
  }

  return {
    ...state,
    selectedLibraryAssetIds: Array.from(selectedIds),
  };
};

export const setClipTags = (
  state: JobSelectionState,
  clipId: string,
  tags: string[],
): JobSelectionState => {
  const normalized = normalizeTags(tags);
  const nextClipTags = { ...state.clipTags };

  if (normalized.length) {
    nextClipTags[clipId] = normalized;
  } else {
    delete nextClipTags[clipId];
  }

  return {
    ...state,
    clipTags: nextClipTags,
  };
};

export const toggleClipTag = (
  state: JobSelectionState,
  clipId: string,
  tag: string,
): JobSelectionState => {
  const cleanedTag = normalizeTags([tag])[0];
  if (!cleanedTag) {
    return state;
  }

  const currentTags = normalizeTags(state.clipTags[clipId] ?? []);
  const nextTags = currentTags.includes(cleanedTag)
    ? currentTags.filter((value) => value !== cleanedTag)
    : [...currentTags, cleanedTag];

  return setClipTags(state, clipId, nextTags);
};
