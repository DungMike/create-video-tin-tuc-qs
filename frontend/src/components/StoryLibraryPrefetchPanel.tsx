import { AlertTriangle, Check, Download, ExternalLink, Loader2, Scissors, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyCard } from "@/components/empty-card";
import { PaginationBar } from "@/components/pagination-bar";
import { StatusAlert } from "@/components/status-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  cancelStoryPrefetch,
  commitStoryPrefetch,
  deleteStoryPrefetchSession,
  discardStoryPrefetchItems,
  getStoryPrefetchSession,
  listStoryPrefetchSessions,
  startStoryPrefetch,
  storyPrefetchPosterUrl,
} from "@/lib/api";
import type {
  StoryPrefetchItem,
  StoryPrefetchOrientation,
  StoryPrefetchSession,
  StoryVideoProvider,
} from "@/types/api";

/** Statuses worth polling — everything else is a resting state. */
const RUNNING = new Set(["searching", "downloading", "cancelling", "committing"]);
/** How many staged videos to mount at once. Several hundred <video> elements in
 *  one grid stalls the tab even with preload="none". */
const REVIEW_PAGE_SIZE = 24;

const MIN_RESOLUTIONS: { label: string; width: number; height: number }[] = [
  { label: "Khong loc", width: 0, height: 0 },
  { label: ">= 1280x720", width: 1280, height: 720 },
  { label: ">= 1920x1080", width: 1920, height: 1080 },
];

function formatBytes(bytes: number) {
  if (!bytes) return "0 MB";
  const mb = bytes / (1024 * 1024);
  if (mb < 1024) return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

function parseTags(value: string) {
  return value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

export interface StoryLibraryPrefetchPanelProps {
  libraryId: string;
  /** Called once clips have landed in the library, so the grid above can reload. */
  onCommitted?: () => void;
  disabled?: boolean;
}

/**
 * The download-first import branch.
 *
 * The Pixabay/Pexels tabs preview candidates straight off the provider CDN and
 * only download what was picked. That costs a network round trip per judgement,
 * which is the wrong trade when nearly everything is worth keeping. This panel
 * inverts it: sweep every result page, download the lot into staging, then review
 * local files (instant, and no further provider quota) and delete the rejects
 * before anything is cut into the library.
 */
export function StoryLibraryPrefetchPanel({
  libraryId,
  onCommitted,
  disabled = false,
}: StoryLibraryPrefetchPanelProps) {
  const [session, setSession] = useState<StoryPrefetchSession | null>(null);
  /** Every sweep of this library still waiting to be reviewed. Batch imports park
   *  one session per keyword, so the panel has to be able to reach all of them. */
  const [sessions, setSessions] = useState<StoryPrefetchSession[]>([]);
  const [isRestoring, setIsRestoring] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [provider, setProvider] = useState<StoryVideoProvider>("pixabay");
  const [query, setQuery] = useState("");
  const [tags, setTags] = useState("");
  const [orientation, setOrientation] = useState<StoryPrefetchOrientation | "">("landscape");
  const [minResIndex, setMinResIndex] = useState(2);
  const [skipImported, setSkipImported] = useState(true);

  const [markedForDelete, setMarkedForDelete] = useState<Set<string>>(new Set());
  const [reviewPage, setReviewPage] = useState(1);
  const [commitTags, setCommitTags] = useState("");
  const [deleteRawAfter, setDeleteRawAfter] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const committedRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  // Reconnect to a sweep already in flight (or one parked at the review step).
  // The settings page remounts this whole subtree whenever it bumps its library
  // refresh key, and a sweep can run for many minutes, so the manifest on the
  // server — not component state — is the source of truth.
  useEffect(() => {
    if (!libraryId) return;
    let cancelled = false;
    setIsRestoring(true);
    listStoryPrefetchSessions(libraryId)
      .then((res) => {
        if (cancelled) return;
        setSessions(res.sessions);
        setSession(res.sessions[0] ?? null);
        setCommitTags((res.sessions[0]?.tags ?? []).join(", "));
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setIsRestoring(false);
      });
    return () => {
      cancelled = true;
    };
  }, [libraryId]);

  // Manual, not polled: a session list carries every manifest in full (hundreds of
  // items each), so refetching it on a timer would move megabytes a second.
  const refreshSessions = async () => {
    setBusy(true);
    try {
      const res = await listStoryPrefetchSessions(libraryId);
      setSessions(res.sessions);
      if (!res.sessions.some((item) => item.sessionId === session?.sessionId)) {
        setSession(res.sessions[0] ?? null);
        setCommitTags((res.sessions[0]?.tags ?? []).join(", "));
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the tai danh sach luot.");
    } finally {
      setBusy(false);
    }
  };

  const handleSelectSession = async (nextId: string) => {
    if (nextId === session?.sessionId) return;
    setError(null);
    setBusy(true);
    try {
      const next = await getStoryPrefetchSession(nextId);
      setSession(next);
      setSessions((current) =>
        current.map((item) => (item.sessionId === nextId ? next : item)),
      );
      setMarkedForDelete(new Set());
      setReviewPage(1);
      setCommitTags((next.tags ?? []).join(", "));
      setDeleteRawAfter(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the mo luot nay.");
    } finally {
      setBusy(false);
    }
  };

  const sessionId = session?.sessionId ?? null;
  const status = session?.status ?? null;

  useEffect(() => {
    if (!sessionId || !status || !RUNNING.has(status)) {
      stopPolling();
      return;
    }
    const tick = async () => {
      try {
        const next = await getStoryPrefetchSession(sessionId);
        setSession(next);
      } catch {
        /* transient failures are tolerated; keep polling */
      }
    };
    void tick();
    pollRef.current = setInterval(tick, 2000);
    return stopPolling;
  }, [sessionId, status, stopPolling]);

  // Fire onCommitted exactly once per finished commit.
  useEffect(() => {
    if (status !== "completed") {
      committedRef.current = false;
      return;
    }
    if (committedRef.current) return;
    committedRef.current = true;
    // Committed sweeps are no longer pending: drop this one from the queue bar
    // while leaving it on screen, so the success panel still has something to show.
    setSessions((current) => current.filter((item) => item.sessionId !== sessionId));
    onCommitted?.();
  }, [status, sessionId, onCommitted]);

  const keptItems = useMemo(
    () => (session?.items ?? []).filter((item) => item.status === "downloaded"),
    [session],
  );
  const committedCount = useMemo(
    () => (session?.items ?? []).filter((item) => item.status === "committed").length,
    [session],
  );

  const reviewTotalPages = Math.max(1, Math.ceil(keptItems.length / REVIEW_PAGE_SIZE));
  const visibleItems = useMemo(() => {
    const start = (Math.min(reviewPage, reviewTotalPages) - 1) * REVIEW_PAGE_SIZE;
    return keptItems.slice(start, start + REVIEW_PAGE_SIZE);
  }, [keptItems, reviewPage, reviewTotalPages]);

  const resetForm = () => {
    setSession(null);
    setMarkedForDelete(new Set());
    setReviewPage(1);
    setCommitTags("");
    setDeleteRawAfter(false);
  };

  const handleStart = async () => {
    if (!query.trim()) {
      setError("Nhap keyword de tai video.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const resolution = MIN_RESOLUTIONS[minResIndex];
      const next = await startStoryPrefetch(libraryId, {
        provider,
        query: query.trim(),
        tags: parseTags(tags),
        orientation: orientation || null,
        minWidth: resolution.width || null,
        minHeight: resolution.height || null,
        skipImported,
      });
      setMarkedForDelete(new Set());
      setReviewPage(1);
      setCommitTags(tags);
      setSession(next);
      setSessions((current) => [next, ...current]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the bat dau tai video.");
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
      setSession(await cancelStoryPrefetch(sessionId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the huy luot tai.");
    } finally {
      setBusy(false);
    }
  };

  const handleDiscardMarked = async () => {
    if (!sessionId || markedForDelete.size === 0) return;
    setError(null);
    setBusy(true);
    try {
      const res = await discardStoryPrefetchItems(sessionId, Array.from(markedForDelete));
      setSession(res.session);
      setMarkedForDelete(new Set());
      if (res.failedItemIds.length) {
        setError(`Khong the xoa ${res.failedItemIds.length} file.`);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the xoa video da chon.");
    } finally {
      setBusy(false);
    }
  };

  const handleDiscardOne = async (itemId: string) => {
    if (!sessionId) return;
    setError(null);
    try {
      const res = await discardStoryPrefetchItems(sessionId, [itemId]);
      setSession(res.session);
      setMarkedForDelete((current) => {
        const next = new Set(current);
        next.delete(itemId);
        return next;
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the xoa video.");
    }
  };

  const handleCommit = async () => {
    if (!sessionId) return;
    setError(null);
    setBusy(true);
    try {
      await commitStoryPrefetch(sessionId, parseTags(commitTags), deleteRawAfter);
      const next = await getStoryPrefetchSession(sessionId);
      setSession(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the cat clip.");
    } finally {
      setBusy(false);
    }
  };

  const handleDiscardSession = async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
      await deleteStoryPrefetchSession(sessionId);
      setSessions((current) => current.filter((item) => item.sessionId !== sessionId));
      resetForm();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Khong the huy luot nay.");
    } finally {
      setBusy(false);
    }
  };

  const toggleMarked = (itemId: string) => {
    setMarkedForDelete((current) => {
      const next = new Set(current);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  };

  const percent = session && session.total > 0
    ? Math.min(100, Math.round((session.current / session.total) * 100))
    : 0;

  const controlsDisabled = disabled || busy;

  return (
    <Card className="min-w-0 overflow-hidden border-border/70 bg-card/90">
      <CardContent className="grid min-w-0 gap-4 p-4">
        {error ? (
          <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        ) : null}

        {isRestoring ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            <span>Dang kiem tra luot tai truoc do...</span>
          </div>
        ) : null}

        {/* ---------------- Step 0: pick which pending sweep to review ----------------
            A batch import parks one sweep per keyword; without this bar only the
            newest one would ever be reachable. */}
        {!isRestoring && sessions.length > 1 ? (
          <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg border border-border/70 bg-muted/30 px-3 py-2">
            <span className="text-xs font-medium text-muted-foreground">
              {sessions.length} luot dang cho:
            </span>
            {sessions.map((item) => {
              const kept = item.items.filter((entry) => entry.status === "downloaded").length;
              return (
                <Button
                  key={item.sessionId}
                  type="button"
                  size="sm"
                  variant={item.sessionId === sessionId ? "default" : "outline"}
                  className="h-7 rounded-full px-3 text-xs"
                  disabled={controlsDisabled}
                  onClick={() => void handleSelectSession(item.sessionId)}
                >
                  {RUNNING.has(item.status) ? <Loader2 className="mr-1 size-3 animate-spin" /> : null}
                  {item.query} · {kept}
                </Button>
              );
            })}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="ml-auto h-7 px-2 text-xs"
              disabled={controlsDisabled}
              onClick={() => void refreshSessions()}
            >
              Lam moi
            </Button>
          </div>
        ) : null}

        {/* ---------------- Step 1: configure the sweep ---------------- */}
        {!isRestoring && !session ? (
          <div className="grid min-w-0 gap-4">
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm text-muted-foreground">
              <div className="mb-1 flex items-center gap-2 font-medium text-foreground">
                <AlertTriangle className="size-4 text-amber-500" />
                Tai truoc, chon sau
              </div>
              Luot nay se tai <span className="font-medium text-foreground">toan bo</span> ket qua cua
              keyword ve may truoc, roi moi cho xem va xoa. Xem lai la doc file local nen nhanh va khong
              ton them luot API. Doi lai, mot keyword rong co the ra vai nghin video (hang chuc GB) —
              bam <span className="font-medium text-foreground">Huy</span> bat cu luc nao de dung.
            </div>

            <div className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,160px)_minmax(0,1fr)]">
              <div className="grid min-w-0 gap-2">
                <Label className="text-xs">Provider</Label>
                <select
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  value={provider}
                  disabled={controlsDisabled}
                  onChange={(event) => setProvider(event.target.value as StoryVideoProvider)}
                >
                  <option value="pixabay">Pixabay</option>
                  <option value="pexels">Pexels</option>
                </select>
              </div>
              <div className="grid min-w-0 gap-2">
                <Label className="text-xs">Keyword</Label>
                <Input
                  value={query}
                  disabled={controlsDisabled}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void handleStart();
                  }}
                  placeholder="vd: vintage tv static"
                />
              </div>
            </div>

            <div className="grid min-w-0 gap-3 md:grid-cols-3">
              <div className="grid min-w-0 gap-2">
                <Label className="text-xs">Huong khung hinh</Label>
                <select
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  value={orientation}
                  disabled={controlsDisabled}
                  onChange={(event) => setOrientation(event.target.value as StoryPrefetchOrientation | "")}
                >
                  <option value="">Tat ca</option>
                  <option value="landscape">Ngang (landscape)</option>
                  <option value="portrait">Doc (portrait)</option>
                  <option value="square">Vuong</option>
                </select>
              </div>
              <div className="grid min-w-0 gap-2">
                <Label className="text-xs">Do phan giai toi thieu</Label>
                <select
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  value={minResIndex}
                  disabled={controlsDisabled}
                  onChange={(event) => setMinResIndex(Number(event.target.value))}
                >
                  {MIN_RESOLUTIONS.map((option, index) => (
                    <option key={option.label} value={index}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid min-w-0 gap-2">
                <Label className="text-xs">Tags khi luu vao thu vien</Label>
                <Input
                  value={tags}
                  disabled={controlsDisabled}
                  onChange={(event) => setTags(event.target.value)}
                  placeholder="nature, city"
                />
              </div>
            </div>

            <label className="flex w-fit cursor-pointer items-center gap-2 text-sm text-muted-foreground">
              <Checkbox
                checked={skipImported}
                disabled={controlsDisabled}
                onCheckedChange={(checked) => setSkipImported(Boolean(checked))}
              />
              Bo qua video da import vao thu vien nay
            </label>

            <div className="flex justify-end">
              <Button type="button" onClick={() => void handleStart()} disabled={controlsDisabled}>
                {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Download className="mr-2 size-4" />}
                Tai tat ca ve
              </Button>
            </div>
          </div>
        ) : null}

        {/* ---------------- Step 2: sweep in progress ---------------- */}
        {session && RUNNING.has(session.status) ? (
          <div className="grid min-w-0 gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">
                  {session.provider} · "{session.query}"
                </div>
                <div className="text-xs text-muted-foreground">{session.message}</div>
              </div>
              {session.status === "committing" ? null : (
                <Button type="button" variant="outline" onClick={() => void handleCancel()} disabled={controlsDisabled}>
                  {session.status === "cancelling" ? (
                    <Loader2 className="mr-2 size-4 animate-spin" />
                  ) : (
                    <X className="mr-2 size-4" />
                  )}
                  Huy
                </Button>
              )}
            </div>

            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all duration-300"
                style={{ width: `${session.total > 0 ? percent : 100}%` }}
              />
            </div>

            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              <Badge variant="secondary" className="rounded-full">{session.pagesFetched} trang da quet</Badge>
              <Badge variant="secondary" className="rounded-full">{session.current} da tai</Badge>
              <Badge variant="secondary" className="rounded-full">{formatBytes(session.downloadedBytes)}</Badge>
              {session.skippedImported > 0 ? (
                <Badge variant="secondary" className="rounded-full">{session.skippedImported} da co san</Badge>
              ) : null}
              {session.skippedFiltered > 0 ? (
                <Badge variant="secondary" className="rounded-full">{session.skippedFiltered} bi loc</Badge>
              ) : null}
              {session.failedCount > 0 ? (
                <Badge variant="secondary" className="rounded-full">{session.failedCount} loi</Badge>
              ) : null}
            </div>
          </div>
        ) : null}

        {/* ---------------- Step 3: review the staged files ---------------- */}
        {session && (session.status === "ready" || session.status === "cancelled") ? (
          <div className="grid min-w-0 gap-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-foreground">
                  {session.provider} · "{session.query}"
                </div>
                <div className="text-xs text-muted-foreground">{session.message}</div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="h-9 rounded-full px-3">
                  Giu {keptItems.length} · Danh dau xoa {markedForDelete.size}
                </Badge>
                {markedForDelete.size > 0 ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive"
                    onClick={() => void handleDiscardMarked()}
                    disabled={controlsDisabled}
                  >
                    <Trash2 className="mr-2 size-4" />
                    Xoa {markedForDelete.size} video da chon
                  </Button>
                ) : null}
              </div>
            </div>

            {session.truncated ? (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm text-muted-foreground">
                <span className="font-medium text-foreground">Ket qua chua day du:</span> {session.truncated}{" "}
                Thu keyword hep hon neu can quet het.
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              <Badge variant="secondary" className="rounded-full">{formatBytes(session.downloadedBytes)}</Badge>
              {session.skippedImported > 0 ? (
                <Badge variant="secondary" className="rounded-full">{session.skippedImported} da co san</Badge>
              ) : null}
              {session.skippedFiltered > 0 ? (
                <Badge variant="secondary" className="rounded-full">{session.skippedFiltered} bi loc</Badge>
              ) : null}
              {session.failedCount > 0 ? (
                <Badge variant="secondary" className="rounded-full">{session.failedCount} tai loi</Badge>
              ) : null}
            </div>

            {keptItems.length === 0 ? (
              <EmptyCard
                title="Khong con video nao"
                description="Tat ca video da bi xoa hoac tai that bai. Huy luot nay va thu keyword khac."
              />
            ) : (
              <>
                <PaginationBar
                  page={Math.min(reviewPage, reviewTotalPages)}
                  totalPages={reviewTotalPages}
                  onPrevious={() => setReviewPage((current) => Math.max(1, current - 1))}
                  onNext={() => setReviewPage((current) => Math.min(reviewTotalPages, current + 1))}
                  onPageChange={setReviewPage}
                  className="justify-start"
                />
                <section className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  {visibleItems.map((item) => (
                    <PrefetchReviewCard
                      key={item.itemId}
                      item={item}
                      sessionId={session.sessionId}
                      marked={markedForDelete.has(item.itemId)}
                      disabled={controlsDisabled}
                      onToggleMarked={() => toggleMarked(item.itemId)}
                      onDelete={() => void handleDiscardOne(item.itemId)}
                    />
                  ))}
                </section>
                <PaginationBar
                  page={Math.min(reviewPage, reviewTotalPages)}
                  totalPages={reviewTotalPages}
                  onPrevious={() => setReviewPage((current) => Math.max(1, current - 1))}
                  onNext={() => setReviewPage((current) => Math.min(reviewTotalPages, current + 1))}
                  onPageChange={setReviewPage}
                />
              </>
            )}

            <div className="grid min-w-0 gap-3 border-t border-border/70 pt-4 md:grid-cols-[minmax(0,1fr)_auto]">
              <div className="grid min-w-0 gap-2">
                <Label className="text-xs">Tags khi luu vao thu vien</Label>
                <Input
                  value={commitTags}
                  disabled={controlsDisabled}
                  onChange={(event) => setCommitTags(event.target.value)}
                  placeholder="nature, city, abstract"
                />
                <label className="flex w-fit cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                  <Checkbox
                    checked={deleteRawAfter}
                    disabled={controlsDisabled}
                    onCheckedChange={(checked) => setDeleteRawAfter(Boolean(checked))}
                  />
                  Xoa file goc sau khi cat (tiet kiem dung luong, khong cat lai duoc)
                </label>
              </div>
              <div className="flex min-w-0 flex-wrap items-start gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void handleDiscardSession()}
                  disabled={controlsDisabled}
                >
                  <X className="mr-2 size-4" />
                  Huy ca luot nay
                </Button>
                <Button
                  type="button"
                  onClick={() => void handleCommit()}
                  disabled={controlsDisabled || keptItems.length === 0}
                >
                  {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Scissors className="mr-2 size-4" />}
                  Cat clip &amp; them vao thu vien
                </Button>
              </div>
            </div>
          </div>
        ) : null}

        {/* ---------------- Step 4: done / failed ---------------- */}
        {session && session.status === "completed" ? (
          <div className="grid min-w-0 gap-3">
            <StatusAlert
              title="Hoan tat"
              message={`Da them ${session.addedClips} clip tu ${committedCount} video vao thu vien.`}
            />
            <div className="flex justify-end">
              <Button type="button" onClick={resetForm} disabled={disabled}>
                <Check className="mr-2 size-4" />
                Bat dau luot moi
              </Button>
            </div>
          </div>
        ) : null}

        {session && session.status === "failed" ? (
          <div className="grid min-w-0 gap-3">
            <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {session.message || session.error || "Luot tai that bai."}
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => void handleDiscardSession()} disabled={controlsDisabled}>
                <Trash2 className="mr-2 size-4" />
                Xoa luot nay
              </Button>
              {/* Video da tai van con tren dia: cat lai chi xu ly phan chua ingest. */}
              {keptItems.length > 0 ? (
                <Button type="button" onClick={() => void handleCommit()} disabled={controlsDisabled}>
                  <Scissors className="mr-2 size-4" />
                  Cat lai {keptItems.length} video
                </Button>
              ) : null}
              <Button type="button" variant="outline" onClick={resetForm} disabled={disabled}>
                Bat dau luot moi
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PrefetchReviewCard({
  item,
  sessionId,
  marked,
  disabled,
  onToggleMarked,
  onDelete,
}: {
  item: StoryPrefetchItem;
  sessionId: string;
  marked: boolean;
  disabled: boolean;
  onToggleMarked: () => void;
  onDelete: () => void;
}) {
  // Provider thumbnail when there is one (free, already on a CDN); otherwise a
  // frame cut from the staged file. Pixabay sweeps land entirely in the second
  // case, and with preload="none" a card without a poster is just a black box.
  const posterUrl = item.thumbnailUrl || storyPrefetchPosterUrl(sessionId, item.itemId);

  return (
    <Card
      className={`min-w-0 overflow-hidden bg-card/90 ${
        marked ? "border-destructive/70 opacity-60" : "border-border/70"
      }`}
    >
      <CardContent className="grid min-w-0 gap-3 p-3">
        <div className="relative aspect-video w-full min-w-0 overflow-hidden rounded-lg bg-black">
          {/* preload="none": the staged file is local, so there is no reason to
              fetch bytes before the user actually presses play — and a grid of
              these would otherwise open dozens of parallel range requests. */}
          <video
            controls
            preload="none"
            poster={posterUrl}
            src={`/media/${item.mediaPath}`}
            className="absolute inset-0 block h-full w-full max-w-full object-cover"
          />
        </div>
        <div className="grid min-w-0 gap-2">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">{item.title || item.videoId}</div>
              <div className="text-xs text-muted-foreground">
                {item.duration}s | {item.width}x{item.height} | {formatBytes(item.sizeBytes)}
              </div>
            </div>
            {item.pageUrl ? (
              <a
                href={item.pageUrl}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 rounded-md p-1 text-muted-foreground hover:text-foreground"
                title="Mo trang goc"
              >
                <ExternalLink className="size-4" />
              </a>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <label className="flex flex-1 cursor-pointer items-center gap-2 text-xs text-muted-foreground">
              <Checkbox checked={marked} disabled={disabled} onCheckedChange={onToggleMarked} />
              Danh dau xoa
            </label>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 px-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={onDelete}
              disabled={disabled}
              title="Xoa ngay"
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
