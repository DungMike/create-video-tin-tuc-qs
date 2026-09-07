// Tracks which Story Video library ("folder") is currently active. Persisted to
// localStorage and shared across pages via a tiny pub/sub so the StoryVideoPage
// and the library manager stay in sync within a session.
//
// Two selections live here: the ACTIVE one (a single library — what the library
// manager uploads into, renames, deletes) and the RENDER one (one or more
// libraries a render draws its clips from). The active id is kept as the first
// render library so both stay coherent.

const STORAGE_KEY = "story-video-active-library";
const RENDER_STORAGE_KEY = "story-video-render-libraries";

const listeners = new Set<() => void>();

// Last parse of the render selection, kept so repeated reads hand back one
// stable array reference (see readRenderLibraryIds).
let renderIdsCache: { raw: string | null; ids: string[] | null } = { raw: null, ids: null };

export function readActiveLibraryId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function writeActiveLibraryId(libraryId: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, libraryId);
  } catch {
    // Ignore storage failures (private mode, quota, etc.).
  }
  listeners.forEach((listener) => listener());
}

/**
 * Libraries the next render draws clips from. Returns null when nothing has been
 * chosen yet, so callers can fall back to the active library instead of treating
 * "not chosen" as "none selected". A string cached by an older build is read as a
 * single-library selection.
 */
export function readRenderLibraryIds(): string[] | null {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(RENDER_STORAGE_KEY);
  } catch {
    return null;
  }
  // useSyncExternalStore compares snapshots by identity, so the parsed array has
  // to be the SAME reference until the stored string actually changes.
  if (raw === renderIdsCache.raw) return renderIdsCache.ids;

  let ids: string[] | null = null;
  try {
    const parsed = raw ? JSON.parse(raw) : null;
    if (typeof parsed === "string") {
      ids = parsed ? [parsed] : null;
    } else if (Array.isArray(parsed)) {
      const valid = parsed.filter((id): id is string => typeof id === "string" && id.length > 0);
      ids = valid.length ? valid : null;
    }
  } catch {
    ids = null;
  }
  renderIdsCache = { raw, ids };
  return ids;
}

export function writeRenderLibraryIds(libraryIds: string[]): void {
  try {
    window.localStorage.setItem(RENDER_STORAGE_KEY, JSON.stringify(libraryIds));
  } catch {
    // Ignore storage failures (private mode, quota, etc.).
  }
  listeners.forEach((listener) => listener());
}

export function subscribeActiveLibrary(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
