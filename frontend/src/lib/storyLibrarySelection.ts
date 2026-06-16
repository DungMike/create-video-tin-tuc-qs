// Tracks which Story Video library ("folder") is currently active. Persisted to
// localStorage and shared across pages via a tiny pub/sub so the StoryVideoPage
// and the library manager stay in sync within a session.

const STORAGE_KEY = "story-video-active-library";

const listeners = new Set<() => void>();

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

export function subscribeActiveLibrary(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
