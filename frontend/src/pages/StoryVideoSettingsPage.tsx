import { Loader2, Save, Star, Trash2, Upload } from "lucide-react";
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
  deleteWaveformOverlay,
  getWaveformOverlays,
  updateWaveformOverlay,
  uploadWaveformOverlay,
} from "@/lib/api";
import type { WaveformOverlay } from "@/types/api";

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

export function StoryVideoSettingsPage() {
  const [overlays, setOverlays] = useState<WaveformOverlay[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState<WaveformForm>(DEFAULT_FORM);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const selected = useMemo(
    () => overlays.find((overlay) => overlay.id === selectedId) ?? overlays.find((overlay) => overlay.isDefault),
    [overlays, selectedId],
  );

  const loadOverlays = () => {
    setErrorMessage(null);
    return getWaveformOverlays()
      .then((res) => {
        setOverlays(res.overlays);
        const defaultId = res.overlays.find((overlay) => overlay.isDefault)?.id ?? res.overlays[0]?.id ?? "";
        setSelectedId((current) => current || defaultId);
      })
      .catch((err) => setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai waveform overlay."))
      .finally(() => setIsLoading(false));
  };

  useEffect(() => {
    void loadOverlays();
  }, []);

  useEffect(() => {
    setForm(formFromOverlay(selected));
  }, [selected]);

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
        <LoadingCard message="Dang tai cau hinh waveform..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Story Video"
        title="Cau Hinh Song Am"
        description="Thiet lap waveform overlay mac dinh cho toan bo video story."
        stats={[
          { label: "Waveforms", value: overlays.length },
          { label: "Default", value: overlays.find((item) => item.isDefault)?.name ?? "None" },
          { label: "Codec", value: "MOV QTRLE" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}

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
