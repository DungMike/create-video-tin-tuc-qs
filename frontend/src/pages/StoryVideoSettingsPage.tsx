import { Eye, Link as LinkIcon, Loader2, Save, Star, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { StoryLibraryManager } from "@/components/StoryLibraryManager";
import { TopNav } from "@/components/top-nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  deleteTVNoiseOverlay,
  deleteWaveformOverlay,
  generateTVNoiseDemo,
  getTVNoiseOverlayJob,
  getTVNoiseOverlays,
  getWaveformOverlays,
  importTVNoiseOverlayFromYoutube,
  updateTVNoiseOverlay,
  updateWaveformOverlay,
  uploadTVNoiseOverlay,
  uploadWaveformOverlay,
} from "@/lib/api";
import type { TVNoiseOverlay, WaveformOverlay } from "@/types/api";

type WaveformForm = {
  keyColor: string;
  similarity: string;
  blend: string;
  scaleWidth: string;
  position: NonNullable<WaveformOverlay["position"]>;
  margin: string;
};

const DEFAULT_FORM: WaveformForm = {
  keyColor: "0x2baa40",
  similarity: "0.12",
  blend: "0.03",
  scaleWidth: "420",
  position: "bottom_right",
  margin: "15",
};

type TVNoiseForm = {
  enabled: boolean;
  opacity: string;
  tolerance: string;
  softness: string;
  order: string;
};

const DEFAULT_NOISE_FORM: TVNoiseForm = {
  enabled: true,
  opacity: "0.35",
  tolerance: "0.08",
  softness: "0.02",
  order: "1",
};

function formFromOverlay(overlay?: WaveformOverlay): WaveformForm {
  if (!overlay) return DEFAULT_FORM;
  return {
    keyColor: overlay.keyColor ?? DEFAULT_FORM.keyColor,
    similarity: String(overlay.similarity ?? DEFAULT_FORM.similarity),
    blend: String(overlay.blend ?? DEFAULT_FORM.blend),
    scaleWidth: String(overlay.scaleWidth ?? DEFAULT_FORM.scaleWidth),
    position: overlay.position ?? DEFAULT_FORM.position,
    margin: String(overlay.margin ?? DEFAULT_FORM.margin),
  };
}

function formFromNoiseOverlay(overlay?: TVNoiseOverlay): TVNoiseForm {
  if (!overlay) return DEFAULT_NOISE_FORM;
  return {
    enabled: overlay.enabled ?? DEFAULT_NOISE_FORM.enabled,
    opacity: String(overlay.opacity ?? DEFAULT_NOISE_FORM.opacity),
    tolerance: String(overlay.tolerance ?? DEFAULT_NOISE_FORM.tolerance),
    softness: String(overlay.softness ?? DEFAULT_NOISE_FORM.softness),
    order: String(overlay.order ?? DEFAULT_NOISE_FORM.order),
  };
}

export function StoryVideoSettingsPage() {
  const [overlays, setOverlays] = useState<WaveformOverlay[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState<WaveformForm>(DEFAULT_FORM);
  const [tvNoiseOverlays, setTvNoiseOverlays] = useState<TVNoiseOverlay[]>([]);
  const [selectedNoiseId, setSelectedNoiseId] = useState("");
  const [noiseForm, setNoiseForm] = useState<TVNoiseForm>(DEFAULT_NOISE_FORM);
  const [youtubeNoiseUrl, setYoutubeNoiseUrl] = useState("");
  const [noiseJobId, setNoiseJobId] = useState<string | null>(null);
  const [noiseJobMessage, setNoiseJobMessage] = useState<string | null>(null);
  const [noiseDemoSrc, setNoiseDemoSrc] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [isNoiseUploading, setIsNoiseUploading] = useState(false);
  const [isNoiseImporting, setIsNoiseImporting] = useState(false);
  const [isNoiseSaving, setIsNoiseSaving] = useState(false);
  const [isGeneratingNoiseDemo, setIsGeneratingNoiseDemo] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const selected = useMemo(
    () => overlays.find((overlay) => overlay.id === selectedId) ?? overlays.find((overlay) => overlay.isDefault),
    [overlays, selectedId],
  );
  const selectedNoise = useMemo(
    () => tvNoiseOverlays.find((overlay) => overlay.id === selectedNoiseId) ?? tvNoiseOverlays[0],
    [tvNoiseOverlays, selectedNoiseId],
  );

  const loadOverlays = () => {
    setErrorMessage(null);
    return getWaveformOverlays()
      .then((res) => {
        setOverlays(res.overlays);
        const defaultId = res.overlays.find((overlay) => overlay.isDefault)?.id ?? res.overlays[0]?.id ?? "";
        setSelectedId((current) => current || defaultId);
      })
      .catch((err) => setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai waveform overlay."));
  };

  const loadTVNoiseOverlays = () => {
    return getTVNoiseOverlays()
      .then((res) => {
        setTvNoiseOverlays(res.overlays);
        setSelectedNoiseId((current) => current || (res.overlays[0]?.id ?? ""));
      })
      .catch((err) => setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai TV noise overlay."));
  };

  useEffect(() => {
    setIsLoading(true);
    Promise.all([loadOverlays(), loadTVNoiseOverlays()]).finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    setForm(formFromOverlay(selected));
  }, [selected]);

  useEffect(() => {
    setNoiseForm(formFromNoiseOverlay(selectedNoise));
    setNoiseDemoSrc(null);
  }, [selectedNoise]);

  useEffect(() => {
    const hasProcessing = tvNoiseOverlays.some((overlay) => overlay.status === "processing");
    if (!hasProcessing && !noiseJobId) return;

    const interval = window.setInterval(() => {
      void loadTVNoiseOverlays();
      if (noiseJobId) {
        getTVNoiseOverlayJob(noiseJobId)
          .then((job) => {
            setNoiseJobMessage(job.message);
            if (job.status === "completed" || job.status === "failed") {
              setNoiseJobId(null);
              setIsNoiseUploading(false);
              setIsNoiseImporting(false);
              void loadTVNoiseOverlays();
            }
          })
          .catch(() => setNoiseJobId(null));
      }
    }, 3000);

    return () => window.clearInterval(interval);
  }, [tvNoiseOverlays, noiseJobId]);

  const handleUpload = async (file: File | null) => {
    if (!file) return;
    setIsUploading(true);
    setErrorMessage(null);
    try {
      const res = await uploadWaveformOverlay(file);
      await loadOverlays();
      setSelectedId(res.overlay.id);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the upload waveform overlay.");
    } finally {
      setIsUploading(false);
    }
  };

  const handleNoiseUpload = async (file: File | null) => {
    if (!file) return;
    setIsNoiseUploading(true);
    setErrorMessage(null);
    setNoiseJobMessage("Dang upload va tao alpha MOV...");
    try {
      const res = await uploadTVNoiseOverlay(file);
      setNoiseJobId(res.sessionId);
      setSelectedNoiseId(res.overlay.id);
      await loadTVNoiseOverlays();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the upload TV noise overlay.");
      setIsNoiseUploading(false);
    }
  };

  const handleNoiseYoutubeImport = async () => {
    const url = youtubeNoiseUrl.trim();
    if (!url) {
      setErrorMessage("Nhap YouTube URL cho TV noise.");
      return;
    }
    setIsNoiseImporting(true);
    setErrorMessage(null);
    setNoiseJobMessage("Dang tai TV noise tu YouTube...");
    try {
      const res = await importTVNoiseOverlayFromYoutube(url);
      setNoiseJobId(res.sessionId);
      setSelectedNoiseId(res.overlay.id);
      setYoutubeNoiseUrl("");
      await loadTVNoiseOverlays();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the import TV noise tu YouTube.");
      setIsNoiseImporting(false);
    }
  };

  const handleNoiseSave = async () => {
    if (!selectedNoise) return;
    setIsNoiseSaving(true);
    setErrorMessage(null);
    try {
      const payload: Partial<TVNoiseOverlay> = {
        enabled: noiseForm.enabled,
        opacity: Number(noiseForm.opacity),
        tolerance: Number(noiseForm.tolerance),
        softness: Number(noiseForm.softness),
        order: Number(noiseForm.order),
      };
      const res = await updateTVNoiseOverlay(selectedNoise.id, payload);
      setTvNoiseOverlays((current) => current.map((item) => (item.id === selectedNoise.id ? res.overlay : item)));
      if (res.overlay.status === "processing") {
        setNoiseJobMessage("Dang tao lai alpha MOV...");
      }
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the luu cau hinh TV noise.");
    } finally {
      setIsNoiseSaving(false);
    }
  };

  const handleNoiseDelete = async (overlayId: string) => {
    setErrorMessage(null);
    try {
      await deleteTVNoiseOverlay(overlayId);
      setTvNoiseOverlays((current) => current.filter((item) => item.id !== overlayId));
      setSelectedNoiseId("");
      setNoiseDemoSrc(null);
      void loadTVNoiseOverlays();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the xoa TV noise overlay.");
    }
  };

  const handleGenerateNoiseDemo = async () => {
    if (!selectedNoise) return;
    setIsGeneratingNoiseDemo(true);
    setErrorMessage(null);
    try {
      const res = await generateTVNoiseDemo(selectedNoise.id);
      setNoiseDemoSrc(`/media/${res.demoPath}?t=${Date.now()}`);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tao demo TV noise.");
    } finally {
      setIsGeneratingNoiseDemo(false);
    }
  };

  const handleSave = async () => {
    if (!selected) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      const payload: Partial<WaveformOverlay> = {
        isDefault: true,
        keyColor: form.keyColor.trim() || DEFAULT_FORM.keyColor,
        similarity: Number(form.similarity),
        blend: Number(form.blend),
        scaleWidth: Number(form.scaleWidth),
        position: form.position,
        margin: Number(form.margin),
      };
      const res = await updateWaveformOverlay(selected.id, payload);
      setOverlays((current) => current.map((item) => (item.id === selected.id ? res.overlay : { ...item, isDefault: false })));
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the luu cau hinh waveform.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (overlayId: string) => {
    setErrorMessage(null);
    try {
      await deleteWaveformOverlay(overlayId);
      setOverlays((current) => current.filter((item) => item.id !== overlayId));
      setSelectedId("");
      void loadOverlays();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the xoa waveform overlay.");
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai cau hinh Story Video..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Story Video"
        title="Cau Hinh Story Video"
        description="Quan ly TV noise overlay va waveform mac dinh cho video story."
        stats={[
          { label: "TV Noise", value: tvNoiseOverlays.length },
          { label: "Active Noise", value: tvNoiseOverlays.filter((item) => item.enabled && item.status === "ready").length },
          { label: "Waveforms", value: overlays.length },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}

      <PageSection>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-base font-semibold text-foreground">TV Noise Overlays</h2>
            <p className="text-sm text-muted-foreground">Quan ly cac lop nhieu nen den, preprocess thanh alpha MOV va ap dung global cho Story Video.</p>
          </div>
          {noiseJobMessage ? <Badge variant="secondary" className="rounded-full">{noiseJobMessage}</Badge> : null}
        </div>

        <div className="grid gap-5 lg:grid-cols-[minmax(280px,380px)_1fr]">
          <div className="space-y-4">
            <div className="grid gap-2">
              <Label>Upload TV noise</Label>
              <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-background/70 p-4 text-sm text-muted-foreground">
                {isNoiseUploading ? <Loader2 className="size-5 animate-spin text-primary" /> : <Upload className="size-5 text-primary" />}
                <span>{isNoiseUploading ? "Dang tao alpha MOV..." : "Chon video noise nen den"}</span>
                <Input
                  type="file"
                  accept=".mp4,.mov,.mkv,.webm"
                  className="hidden"
                  disabled={isNoiseUploading}
                  onChange={(event) => void handleNoiseUpload(event.currentTarget.files?.[0] ?? null)}
                />
              </label>
            </div>

            <div className="grid gap-2">
              <Label>YouTube TV noise URL</Label>
              <div className="flex gap-2">
                <Input
                  value={youtubeNoiseUrl}
                  onChange={(event) => setYoutubeNoiseUrl(event.target.value)}
                  placeholder="https://www.youtube.com/watch?v=..."
                  disabled={isNoiseImporting}
                />
                <Button type="button" variant="secondary" onClick={() => void handleNoiseYoutubeImport()} disabled={isNoiseImporting}>
                  {isNoiseImporting ? <Loader2 className="mr-2 size-4 animate-spin" /> : <LinkIcon className="mr-2 size-4" />}
                  Import
                </Button>
              </div>
            </div>

            {tvNoiseOverlays.length ? (
              <div className="grid gap-2">
                {tvNoiseOverlays.map((overlay) => (
                  <button
                    key={overlay.id}
                    type="button"
                    onClick={() => setSelectedNoiseId(overlay.id)}
                    className={`rounded-lg border p-3 text-left transition-colors ${
                      selectedNoise?.id === overlay.id ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-foreground">{overlay.name}</span>
                      <Badge
                        variant={overlay.status === "failed" ? "destructive" : overlay.status === "ready" ? "secondary" : "outline"}
                        className="rounded-full"
                      >
                        {overlay.status}
                      </Badge>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      #{overlay.order} | opacity {Math.round((overlay.opacity ?? 0) * 100)}% | {overlay.enabled ? "enabled" : "disabled"}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyCard title="Chua co TV noise" description="Upload hoac import video noise nen den de bat dau." />
            )}
          </div>

          {selectedNoise ? (
            <div className="grid gap-5">
              {noiseDemoSrc || selectedNoise.relativePath ? (
                <video
                  src={noiseDemoSrc || `/media/${selectedNoise.relativePath}`}
                  controls
                  className="aspect-video w-full rounded-lg bg-black object-contain"
                />
              ) : null}

              {selectedNoise.error ? (
                <StatusAlert title="TV noise preprocess failed" message={selectedNoise.error} variant="destructive" />
              ) : null}

              <div className="grid gap-4 md:grid-cols-5">
                <label className="flex h-10 items-center gap-2 rounded-md border border-input px-3 text-sm">
                  <input
                    type="checkbox"
                    checked={noiseForm.enabled}
                    onChange={(event) => setNoiseForm((current) => ({ ...current, enabled: event.target.checked }))}
                  />
                  Enabled
                </label>
                <div className="grid gap-2">
                  <Label>Opacity</Label>
                  <Input type="number" min="0" max="1" step="0.01" value={noiseForm.opacity} onChange={(event) => setNoiseForm((current) => ({ ...current, opacity: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Tolerance</Label>
                  <Input type="number" min="0" max="1" step="0.01" value={noiseForm.tolerance} onChange={(event) => setNoiseForm((current) => ({ ...current, tolerance: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Softness</Label>
                  <Input type="number" min="0" max="1" step="0.01" value={noiseForm.softness} onChange={(event) => setNoiseForm((current) => ({ ...current, softness: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Order</Label>
                  <Input type="number" min="0" step="1" value={noiseForm.order} onChange={(event) => setNoiseForm((current) => ({ ...current, order: event.target.value }))} />
                </div>
              </div>

              <div className="flex flex-wrap gap-3">
                <Button type="button" onClick={handleNoiseSave} disabled={isNoiseSaving}>
                  {isNoiseSaving ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
                  Luu TV noise
                </Button>
                <Button type="button" variant="outline" onClick={() => void handleGenerateNoiseDemo()} disabled={isGeneratingNoiseDemo || selectedNoise.status !== "ready"}>
                  {isGeneratingNoiseDemo ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Eye className="mr-2 size-4" />}
                  Demo 3s
                </Button>
                <Button type="button" variant="destructive" onClick={() => void handleNoiseDelete(selectedNoise.id)}>
                  <Trash2 className="mr-2 size-4" />
                  Xoa
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </PageSection>

      <PageSection>
        <div className="grid gap-5 lg:grid-cols-[minmax(260px,360px)_1fr]">
          <div className="space-y-4">
            <div className="grid gap-2">
              <Label>Upload waveform</Label>
              <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-background/70 p-4 text-sm text-muted-foreground">
                {isUploading ? <Loader2 className="size-5 animate-spin text-primary" /> : <Upload className="size-5 text-primary" />}
                <span>{isUploading ? "Dang tao alpha MOV..." : "Chon video waveform"}</span>
                <Input
                  type="file"
                  accept=".mp4,.mov,.mkv,.webm"
                  className="hidden"
                  disabled={isUploading}
                  onChange={(event) => void handleUpload(event.currentTarget.files?.[0] ?? null)}
                />
              </label>
            </div>

            {overlays.length ? (
              <div className="grid gap-2">
                {overlays.map((overlay) => (
                  <button
                    key={overlay.id}
                    type="button"
                    onClick={() => setSelectedId(overlay.id)}
                    className={`rounded-lg border p-3 text-left transition-colors ${
                      selected?.id === overlay.id ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-foreground">{overlay.name}</span>
                      {overlay.isDefault ? (
                        <Badge className="rounded-full">
                          <Star className="mr-1 size-3" />
                          Default
                        </Badge>
                      ) : null}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {overlay.durationSeconds}s | {overlay.scaleWidth ?? 420}px | {overlay.position ?? "bottom_right"}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyCard title="Chua co waveform" description="Upload video nen xanh de tao overlay alpha mac dinh." />
            )}
          </div>

          {selected ? (
            <div className="grid gap-5">
              {selected.relativePath ? (
                <video src={`/media/${selected.relativePath}`} controls className="aspect-video w-full rounded-lg bg-black object-contain" />
              ) : null}

              <div className="grid gap-4 md:grid-cols-3">
                <div className="grid gap-2">
                  <Label>Key color</Label>
                  <Input value={form.keyColor} onChange={(event) => setForm((current) => ({ ...current, keyColor: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Similarity</Label>
                  <Input type="number" min="0" max="1" step="0.01" value={form.similarity} onChange={(event) => setForm((current) => ({ ...current, similarity: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Blend</Label>
                  <Input type="number" min="0" max="1" step="0.01" value={form.blend} onChange={(event) => setForm((current) => ({ ...current, blend: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Width</Label>
                  <Input type="number" min="64" step="2" value={form.scaleWidth} onChange={(event) => setForm((current) => ({ ...current, scaleWidth: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Position</Label>
                  <select
                    value={form.position}
                    onChange={(event) => setForm((current) => ({ ...current, position: event.target.value as WaveformForm["position"] }))}
                    className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  >
                    <option value="bottom_right">Bottom right</option>
                    <option value="bottom_left">Bottom left</option>
                    <option value="top_right">Top right</option>
                    <option value="top_left">Top left</option>
                  </select>
                </div>
                <div className="grid gap-2">
                  <Label>Margin</Label>
                  <Input type="number" min="0" step="1" value={form.margin} onChange={(event) => setForm((current) => ({ ...current, margin: event.target.value }))} />
                </div>
              </div>

              <div className="flex flex-wrap gap-3">
                <Button type="button" onClick={handleSave} disabled={isSaving}>
                  {isSaving ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
                  Luu va dat mac dinh
                </Button>
                <Button type="button" variant="destructive" onClick={() => void handleDelete(selected.id)}>
                  <Trash2 className="mr-2 size-4" />
                  Xoa
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </PageSection>

      <PageSection>
        <div className="mb-4 space-y-1">
          <h2 className="text-base font-semibold text-foreground">Thu vien clip 5 giay</h2>
          <p className="text-sm text-muted-foreground">
            Upload video nguon hoac nhap link Pixabay/Pexels de he thong tai ve va cat thanh clip 5 giay dung chung cho Story Video.
          </p>
        </div>
        <StoryLibraryManager />
      </PageSection>
    </AppShell>
  );
}
