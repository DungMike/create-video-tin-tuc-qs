import { Check, Eye, Link as LinkIcon, Loader2, Save, Sparkles, Star, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { StoryLibraryManager } from "@/components/StoryLibraryManager";
import { StoryLibraryNormalizePanel } from "@/components/StoryLibraryNormalizePanel";
import { TopNav } from "@/components/top-nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  createSparkleOverlay,
  deleteCtaOverlay,
  deleteEffectPreviewSource,
  deleteTVNoiseOverlay,
  deleteWaveformOverlay,
  generateCustomTVEffectPreview,
  generateTVEffectStylePreview,
  generateTVNoiseDemo,
  getCtaOverlays,
  getTVEffectStyles,
  getTVNoiseOverlayJob,
  getEffectPreviewJob,
  getEffectPreviewSources,
  getTVNoiseOverlays,
  getSparklePresets,
  getWaveformOverlays,
  importTVNoiseOverlayFromYoutube,
  renderEffectPreview,
  saveCustomTVEffect,
  selectTVEffectStyle,
  updateCtaOverlay,
  updateTVNoiseOverlay,
  updateWaveformOverlay,
  uploadCtaOverlay,
  uploadEffectPreviewSource,
  uploadTVNoiseOverlay,
  uploadWaveformOverlay,
} from "@/lib/api";
import type {
  CtaOverlay,
  EffectPreviewSource,
  SparklePreset,
  TVEffectParams,
  TVEffectStyle,
  TVEffectTone,
  TVNoiseOverlay,
  WaveformOverlay,
} from "@/types/api";

const DEFAULT_EFFECT_PARAMS: TVEffectParams = {
  tone: "none",
  saturation: 1,
  contrast: 1,
  brightness: 0,
  gamma: 1,
  noise: 0,
  chromaShift: 0,
  scanlines: 0,
  vignette: 0,
  flicker: 0,
  flickerSpeed: 3,
  soften: 0,
  bloom: 0,
  bloomThreshold: 0.75,
  bloomRadius: 0.5,
};

const TONE_OPTIONS: { value: TVEffectTone; label: string }[] = [
  { value: "none", label: "Giữ nguyên" },
  { value: "warm", label: "Ấm (vàng)" },
  { value: "cool", label: "Lạnh (xanh)" },
  { value: "vintage", label: "Vintage" },
  { value: "sepia", label: "Sepia (nâu cũ)" },
  { value: "bw", label: "Đen trắng" },
  { value: "fade", label: "Fade điện ảnh" },
];

type EffectNumericKey = Exclude<keyof TVEffectParams, "tone">;

const EFFECT_PARAM_FIELDS: { key: EffectNumericKey; label: string; min: number; max: number; step: number }[] = [
  { key: "saturation", label: "Bão hòa màu", min: 0, max: 2, step: 0.05 },
  { key: "contrast", label: "Tương phản", min: 0.5, max: 1.5, step: 0.02 },
  { key: "brightness", label: "Độ sáng", min: -0.3, max: 0.3, step: 0.01 },
  { key: "gamma", label: "Gamma", min: 0.5, max: 1.5, step: 0.02 },
  { key: "noise", label: "Độ nhiễu hạt", min: 0, max: 30, step: 1 },
  { key: "chromaShift", label: "Lệch màu (px)", min: 0, max: 8, step: 1 },
  { key: "scanlines", label: "Scanline", min: 0, max: 0.3, step: 0.01 },
  { key: "vignette", label: "Viền tối", min: 0, max: 1, step: 0.05 },
  { key: "flicker", label: "Độ nháy sáng", min: 0, max: 0.08, step: 0.005 },
  { key: "flickerSpeed", label: "Tốc độ nháy (Hz)", min: 0.5, max: 15, step: 0.5 },
  { key: "soften", label: "Làm mềm", min: 0, max: 1, step: 0.05 },
  { key: "bloom", label: "Bloom (toả sáng)", min: 0, max: 1, step: 0.05 },
  { key: "bloomThreshold", label: "Ngưỡng bloom", min: 0.5, max: 0.95, step: 0.01 },
  { key: "bloomRadius", label: "Bán kính bloom", min: 0, max: 1, step: 0.05 },
];

type EffectForm = Record<EffectNumericKey, string> & { tone: TVEffectTone };

function effectFormFromParams(params: TVEffectParams): EffectForm {
  const form = { tone: params.tone ?? "none" } as EffectForm;
  for (const field of EFFECT_PARAM_FIELDS) {
    form[field.key] = String(params[field.key] ?? DEFAULT_EFFECT_PARAMS[field.key]);
  }
  return form;
}

function effectParamsFromForm(form: EffectForm): TVEffectParams {
  const params = { ...DEFAULT_EFFECT_PARAMS, tone: form.tone };
  for (const field of EFFECT_PARAM_FIELDS) {
    const value = Number(form[field.key]);
    params[field.key] = Number.isFinite(value) ? value : DEFAULT_EFFECT_PARAMS[field.key];
  }
  return params;
}

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
  blendMode: NonNullable<TVNoiseOverlay["blendMode"]>;
  opacity: string;
  tolerance: string;
  softness: string;
  lumaGain: string;
  order: string;
};

const DEFAULT_NOISE_FORM: TVNoiseForm = {
  enabled: true,
  blendMode: "alpha",
  opacity: "0.35",
  tolerance: "0.08",
  softness: "0.02",
  lumaGain: "2",
  order: "1",
};

const BLEND_MODE_HINTS: Record<NonNullable<TVNoiseOverlay["blendMode"]>, string> = {
  alpha:
    "Alpha: key nen den thanh trong suot bang lumakey. Hop nhieu TV, bui film. Tolerance/Softness dieu khien nguong key.",
  screen:
    "Screen: cong sang luc render, khong can alpha. Hop light leak, bokeh. Luu y: che do nay ep pass overlay ve CPU.",
  luma:
    "Luma: alpha lay tu chinh do sang cua nguon, mau day ve trang. Hop lop lap lanh — quang sang tan dan thay vi bi bet.",
};

const SPARKLE_PARAM_LABELS: Record<string, string> = {
  density: "Mật độ hạt",
  twinkle: "Nhấp nháy chậm",
  size: "Kích thước hạt",
  gain: "Độ sáng",
  spike: "Độ dài tia",
  sweepSpeed: "Tốc độ quét (px/s)",
  sweepAngle: "Góc nghiêng (rad)",
  sweepWidth: "Bề rộng vệt",
  sweepGain: "Độ sáng vệt",
  tintR: "Ám màu · Đỏ",
  tintG: "Ám màu · Lục",
  tintB: "Ám màu · Lam",
  loopSeconds: "Độ dài loop (giây)",
};

function paramsToForm(preset: SparklePreset): Record<string, string> {
  const form: Record<string, string> = {};
  for (const key of preset.paramsUsed) form[key] = String(preset.params[key] ?? 0);
  return form;
}

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

type CtaForm = {
  enabled: boolean;
  keyColor: string;
  similarity: string;
  blend: string;
  scaleWidth: string;
  position: NonNullable<CtaOverlay["position"]>;
  margin: string;
};

const DEFAULT_CTA_FORM: CtaForm = {
  enabled: true,
  keyColor: "0x1abe26",
  similarity: "0.2",
  blend: "0.1",
  scaleWidth: "360",
  position: "top_left",
  margin: "24",
};

function ctaFormFromOverlay(overlay?: CtaOverlay): CtaForm {
  if (!overlay) return DEFAULT_CTA_FORM;
  return {
    enabled: overlay.enabled ?? DEFAULT_CTA_FORM.enabled,
    keyColor: overlay.keyColor ?? DEFAULT_CTA_FORM.keyColor,
    similarity: String(overlay.similarity ?? DEFAULT_CTA_FORM.similarity),
    blend: String(overlay.blend ?? DEFAULT_CTA_FORM.blend),
    scaleWidth: String(overlay.scaleWidth ?? DEFAULT_CTA_FORM.scaleWidth),
    position: overlay.position ?? DEFAULT_CTA_FORM.position,
    margin: String(overlay.margin ?? DEFAULT_CTA_FORM.margin),
  };
}

// ---------------------------------------------------------------------------
// Visual position preview: mirrors backend _position_expr (margin px on a
// 1920x1080 frame; overlay height is an estimate since ffmpeg scales it W:-2).
// ---------------------------------------------------------------------------
const PREVIEW_FRAME_W = 1920;
const PREVIEW_FRAME_H = 1080;

type OverlayCorner = NonNullable<WaveformOverlay["position"]>;

interface OverlayPreviewBox {
  label: string;
  position: OverlayCorner;
  margin: number;
  scaleWidth: number;
  heightRatio: number;
  className: string;
  active?: boolean;
}

function overlayPreviewBoxStyle(box: OverlayPreviewBox): CSSProperties {
  const widthPct = Math.max(2, Math.min(90, (box.scaleWidth / PREVIEW_FRAME_W) * 100));
  const heightPct = Math.max(2, Math.min(90, ((box.scaleWidth * box.heightRatio) / PREVIEW_FRAME_H) * 100));
  const xPct = Math.min(90, (box.margin / PREVIEW_FRAME_W) * 100);
  const yPct = Math.min(90, (box.margin / PREVIEW_FRAME_H) * 100);
  const style: CSSProperties = { width: `${widthPct}%`, height: `${heightPct}%` };
  if (box.position === "top_left" || box.position === "top_right") style.top = `${yPct}%`;
  else style.bottom = `${yPct}%`;
  if (box.position === "top_left" || box.position === "bottom_left") style.left = `${xPct}%`;
  else style.right = `${xPct}%`;
  return style;
}

const OVERLAY_PREVIEW_CORNERS: { corner: OverlayCorner; className: string }[] = [
  { corner: "top_left", className: "left-1 top-1" },
  { corner: "top_right", className: "right-1 top-1" },
  { corner: "bottom_left", className: "bottom-1 left-1" },
  { corner: "bottom_right", className: "bottom-1 right-1" },
];

function OverlayPositionPreview({
  boxes,
  onPickCorner,
}: {
  boxes: OverlayPreviewBox[];
  onPickCorner: (corner: OverlayCorner) => void;
}) {
  const active = boxes.find((box) => box.active);
  return (
    <div className="grid gap-1">
      <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-border/70 bg-zinc-800">
        <div className="absolute inset-0 flex items-center justify-center text-xs text-zinc-500">Khung video 16:9</div>
        {boxes.map((box) => (
          <div
            key={box.label}
            style={overlayPreviewBoxStyle(box)}
            className={`absolute flex items-center justify-center overflow-hidden rounded border px-1 text-[10px] font-medium text-white/90 ${box.className} ${
              box.active ? "z-10" : "opacity-50"
            }`}
          >
            <span className="truncate">{box.label}</span>
          </div>
        ))}
        {OVERLAY_PREVIEW_CORNERS.map(({ corner, className }) => (
          <button
            key={corner}
            type="button"
            title={`Chuyển "${active?.label ?? "overlay"}" về góc này`}
            onClick={() => onPickCorner(corner)}
            className={`absolute z-20 size-5 rounded border border-dashed ${className} ${
              active?.position === corner ? "border-primary bg-primary/50" : "border-white/40 bg-white/10 hover:bg-white/30"
            }`}
          />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        Mô phỏng vị trí theo Position / Margin / Width đang nhập (khung 1920×1080). Bấm ô vuông ở góc để đổi vị trí.
      </p>
    </div>
  );
}

function formFromNoiseOverlay(overlay?: TVNoiseOverlay): TVNoiseForm {
  if (!overlay) return DEFAULT_NOISE_FORM;
  return {
    enabled: overlay.enabled ?? DEFAULT_NOISE_FORM.enabled,
    blendMode: overlay.blendMode ?? DEFAULT_NOISE_FORM.blendMode,
    opacity: String(overlay.opacity ?? DEFAULT_NOISE_FORM.opacity),
    tolerance: String(overlay.tolerance ?? DEFAULT_NOISE_FORM.tolerance),
    softness: String(overlay.softness ?? DEFAULT_NOISE_FORM.softness),
    lumaGain: String(overlay.lumaGain ?? DEFAULT_NOISE_FORM.lumaGain),
    order: String(overlay.order ?? DEFAULT_NOISE_FORM.order),
  };
}

export function StoryVideoSettingsPage() {
  const [overlays, setOverlays] = useState<WaveformOverlay[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState<WaveformForm>(DEFAULT_FORM);
  const [ctaOverlays, setCtaOverlays] = useState<CtaOverlay[]>([]);
  const [selectedCtaId, setSelectedCtaId] = useState("");
  const [ctaForm, setCtaForm] = useState<CtaForm>(DEFAULT_CTA_FORM);
  const [isCtaUploading, setIsCtaUploading] = useState(false);
  const [isCtaSaving, setIsCtaSaving] = useState(false);
  const [ctaPreviewBust, setCtaPreviewBust] = useState(0);
  const [tvNoiseOverlays, setTvNoiseOverlays] = useState<TVNoiseOverlay[]>([]);
  const [selectedNoiseId, setSelectedNoiseId] = useState("");
  const [noiseForm, setNoiseForm] = useState<TVNoiseForm>(DEFAULT_NOISE_FORM);
  const [youtubeNoiseUrl, setYoutubeNoiseUrl] = useState("");
  const [noiseJobId, setNoiseJobId] = useState<string | null>(null);
  const [noiseJobMessage, setNoiseJobMessage] = useState<string | null>(null);
  const [noiseDemoSrc, setNoiseDemoSrc] = useState<string | null>(null);
  const [sparklePresets, setSparklePresets] = useState<SparklePreset[]>([]);
  const [sparklePresetId, setSparklePresetId] = useState("");
  const [sparkleParams, setSparkleParams] = useState<Record<string, string>>({});
  // Ten rieng cho tung bien the: khong co no thi moi lop tao tu cung mot preset
  // deu mang dung mot ten, khong the phan biet cac mau da luu.
  const [sparkleName, setSparkleName] = useState("");
  const [isSparkleCreating, setIsSparkleCreating] = useState(false);
  const [previewSources, setPreviewSources] = useState<EffectPreviewSource[]>([]);
  const [selectedPreviewId, setSelectedPreviewId] = useState("");
  const [isPreviewUploading, setIsPreviewUploading] = useState(false);
  const [isPreviewRendering, setIsPreviewRendering] = useState(false);
  const [previewJobId, setPreviewJobId] = useState<string | null>(null);
  const [previewMessage, setPreviewMessage] = useState<string | null>(null);
  const [previewOptions, setPreviewOptions] = useState({
    includeStyle: true,
    includeOverlays: true,
    compare: true,
    maxSeconds: "15",
  });
  const [tvEffectStyles, setTvEffectStyles] = useState<TVEffectStyle[]>([]);
  const [selectedEffectId, setSelectedEffectId] = useState("none");
  const [previewingEffectId, setPreviewingEffectId] = useState<string | null>(null);
  const [isSelectingEffect, setIsSelectingEffect] = useState(false);
  const [effectPreviewBust, setEffectPreviewBust] = useState<Record<string, number>>({});
  const [effectForm, setEffectForm] = useState<EffectForm>(effectFormFromParams(DEFAULT_EFFECT_PARAMS));
  const [customPreviewPath, setCustomPreviewPath] = useState<string | null>(null);
  const [customPreviewBust, setCustomPreviewBust] = useState(0);
  const [isCustomPreviewing, setIsCustomPreviewing] = useState(false);
  const [isCustomSaving, setIsCustomSaving] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [isNoiseUploading, setIsNoiseUploading] = useState(false);
  const [isNoiseImporting, setIsNoiseImporting] = useState(false);
  const [isNoiseSaving, setIsNoiseSaving] = useState(false);
  const [isGeneratingNoiseDemo, setIsGeneratingNoiseDemo] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Bumped after a normalize run so the clip grid remounts and re-reads the
  // re-encoded files instead of showing browser-cached copies.
  const [libraryRefreshKey, setLibraryRefreshKey] = useState(0);

  const selected = useMemo(
    () => overlays.find((overlay) => overlay.id === selectedId) ?? overlays.find((overlay) => overlay.isDefault),
    [overlays, selectedId],
  );
  const selectedCta = useMemo(
    () => ctaOverlays.find((overlay) => overlay.id === selectedCtaId) ?? ctaOverlays.find((overlay) => overlay.isDefault) ?? ctaOverlays[0],
    [ctaOverlays, selectedCtaId],
  );
  const selectedPreviewSource = useMemo(
    () => previewSources.find((item) => item.id === selectedPreviewId) ?? previewSources[0],
    [previewSources, selectedPreviewId],
  );

  const selectedSparklePreset = useMemo(
    () => sparklePresets.find((preset) => preset.id === sparklePresetId),
    [sparklePresets, sparklePresetId],
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

  const loadCtaOverlays = () => {
    return getCtaOverlays()
      .then((res) => {
        setCtaOverlays(res.overlays);
        const defaultId = res.overlays.find((overlay) => overlay.isDefault)?.id ?? res.overlays[0]?.id ?? "";
        setSelectedCtaId((current) => current || defaultId);
      })
      .catch((err) => setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai CTA overlay."));
  };

  const loadTVNoiseOverlays = () => {
    return getTVNoiseOverlays()
      .then((res) => {
        setTvNoiseOverlays(res.overlays);
        setSelectedNoiseId((current) => current || (res.overlays[0]?.id ?? ""));
      })
      .catch((err) => setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai TV noise overlay."));
  };

  const loadPreviewSources = () => {
    return getEffectPreviewSources()
      .then((res) => {
        setPreviewSources(res.sources);
        setSelectedPreviewId((current) => current || (res.sources[0]?.id ?? ""));
      })
      .catch(() => {
        /* Optional feature: never block the settings page on it. */
      });
  };

  const loadSparklePresets = () => {
    return getSparklePresets()
      .then((res) => {
        setSparklePresets(res.presets);
        const first = res.presets[0];
        if (first) {
          setSparklePresetId((current) => current || first.id);
          setSparkleParams((current) =>
            Object.keys(current).length ? current : paramsToForm(first),
          );
        }
      })
      .catch(() => {
        /* Sparkle is optional: a failure here must not block the settings page. */
      });
  };

  const loadTVEffectStyles = () => {
    return getTVEffectStyles()
      .then((res) => {
        setTvEffectStyles(res.styles);
        setSelectedEffectId(res.selectedId);
        setEffectForm(effectFormFromParams(res.customParams ?? DEFAULT_EFFECT_PARAMS));
        setCustomPreviewPath(res.customPreviewPath ?? null);
      })
      .catch((err) => setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai hieu ung TV."));
  };

  useEffect(() => {
    setIsLoading(true);
    Promise.all([
      loadOverlays(),
      loadCtaOverlays(),
      loadTVNoiseOverlays(),
      loadTVEffectStyles(),
      loadSparklePresets(),
      loadPreviewSources(),
    ]).finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    if (!previewJobId) return;
    const interval = window.setInterval(() => {
      getEffectPreviewJob(previewJobId)
        .then((job) => {
          setPreviewMessage(job.message);
          if (job.status === "completed" || job.status === "failed") {
            setPreviewJobId(null);
            setIsPreviewRendering(false);
            if (job.status === "failed") setErrorMessage(job.error ?? job.message);
            void loadPreviewSources();
          }
        })
        .catch(() => {
          setPreviewJobId(null);
          setIsPreviewRendering(false);
        });
    }, 2000);
    return () => window.clearInterval(interval);
  }, [previewJobId]);

  const handlePreviewUpload = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setIsPreviewUploading(true);
    setErrorMessage(null);
    setPreviewMessage("Dang chuan hoa va ghep clip...");
    try {
      const res = await uploadEffectPreviewSource(Array.from(files));
      await loadPreviewSources();
      setSelectedPreviewId(res.source.id);
      setPreviewMessage(`Da san sang: ${res.source.clipCount} clip / ${res.source.durationSeconds}s`);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai bo clip preview.");
      setPreviewMessage(null);
    } finally {
      setIsPreviewUploading(false);
    }
  };

  const handlePreviewRender = async () => {
    if (!selectedPreviewSource) return;
    setIsPreviewRendering(true);
    setErrorMessage(null);
    setPreviewMessage("Dang render preview...");
    try {
      const maxSeconds = Number(previewOptions.maxSeconds);
      const res = await renderEffectPreview({
        sourceId: selectedPreviewSource.id,
        includeStyle: previewOptions.includeStyle,
        includeOverlays: previewOptions.includeOverlays,
        compare: previewOptions.compare,
        maxSeconds: Number.isFinite(maxSeconds) && maxSeconds > 0 ? maxSeconds : undefined,
      });
      setPreviewJobId(res.sessionId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the render preview.");
      setIsPreviewRendering(false);
    }
  };

  const handlePreviewDelete = async (sourceId: string) => {
    setErrorMessage(null);
    try {
      await deleteEffectPreviewSource(sourceId);
      setSelectedPreviewId("");
      await loadPreviewSources();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the xoa bo clip preview.");
    }
  };

  const handleSparkleCreate = async () => {
    const preset = sparklePresets.find((item) => item.id === sparklePresetId);
    if (!preset) return;
    setIsSparkleCreating(true);
    setErrorMessage(null);
    setNoiseJobMessage("Dang dung lop lap lanh (25-90 giay)...");
    try {
      const params: Record<string, number> = {};
      for (const key of preset.paramsUsed) {
        const value = Number(sparkleParams[key]);
        params[key] = Number.isFinite(value) ? value : preset.params[key];
      }
      const res = await createSparkleOverlay({
        presetId: preset.id,
        params,
        name: sparkleName.trim() || preset.name,
      });
      setNoiseJobId(res.sessionId);
      await loadTVNoiseOverlays();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tao lop lap lanh.");
      setIsSparkleCreating(false);
    }
  };

  const handleSelectEffect = async (styleId: string) => {
    setIsSelectingEffect(true);
    setErrorMessage(null);
    try {
      const res = await selectTVEffectStyle(styleId);
      setSelectedEffectId(res.selectedId);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the chon hieu ung TV.");
    } finally {
      setIsSelectingEffect(false);
    }
  };

  const handleEffectPreview = async (styleId: string) => {
    setPreviewingEffectId(styleId);
    setErrorMessage(null);
    try {
      const res = await generateTVEffectStylePreview(styleId, undefined, 4);
      setTvEffectStyles((current) =>
        current.map((style) => (style.id === styleId ? { ...style, previewPath: res.previewPath } : style)),
      );
      setEffectPreviewBust((current) => ({ ...current, [styleId]: Date.now() }));
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the render preview hieu ung.");
    } finally {
      setPreviewingEffectId(null);
    }
  };

  const handleCustomPreview = async () => {
    setIsCustomPreviewing(true);
    setErrorMessage(null);
    try {
      const res = await generateCustomTVEffectPreview(effectParamsFromForm(effectForm), undefined, 4);
      setCustomPreviewPath(res.previewPath);
      setCustomPreviewBust(Date.now());
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the render preview custom.");
    } finally {
      setIsCustomPreviewing(false);
    }
  };

  const handleCustomSave = async () => {
    setIsCustomSaving(true);
    setErrorMessage(null);
    try {
      const res = await saveCustomTVEffect(effectParamsFromForm(effectForm));
      setSelectedEffectId(res.selectedId);
      setEffectForm(effectFormFromParams(res.customParams));
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the luu cau hinh custom.");
    } finally {
      setIsCustomSaving(false);
    }
  };

  const handleLoadParamsFromPreset = (styleId: string) => {
    const style = tvEffectStyles.find((item) => item.id === styleId);
    if (style?.params) {
      setEffectForm(effectFormFromParams(style.params));
    }
  };

  useEffect(() => {
    setForm(formFromOverlay(selected));
  }, [selected]);

  useEffect(() => {
    setCtaForm(ctaFormFromOverlay(selectedCta));
  }, [selectedCta]);

  // Chi nap lai form khi DOI lop dang chon. Bam vao chinh object `selectedNoise`
  // thi moi lan poll 3s (luc dang dung lop lap lanh, danh sach tai lai lien tuc va
  // tra ve object moi) se ghi de nhung gi nguoi dung vua go -> khong luu duoc.
  const selectedNoiseRef = useRef(selectedNoise);
  selectedNoiseRef.current = selectedNoise;
  const selectedNoiseKey = selectedNoise?.id ?? "";
  useEffect(() => {
    setNoiseForm(formFromNoiseOverlay(selectedNoiseRef.current));
    setNoiseDemoSrc(null);
  }, [selectedNoiseKey]);

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
              setIsSparkleCreating(false);
              // Chon luon lop vua tao: no la lop dang dung khi render, va day la
              // cho nguoi dung xem lai thong so / bam Demo 3s.
              if (job.overlayId) setSelectedNoiseId(job.overlayId);
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
        blendMode: noiseForm.blendMode,
        opacity: Number(noiseForm.opacity),
        tolerance: Number(noiseForm.tolerance),
        softness: Number(noiseForm.softness),
        lumaGain: Number(noiseForm.lumaGain),
        order: Number(noiseForm.order),
      };
      const res = await updateTVNoiseOverlay(selectedNoise.id, payload);
      setTvNoiseOverlays((current) => current.map((item) => (item.id === selectedNoise.id ? res.overlay : item)));
      if (res.overlay.status === "processing") {
        setNoiseJobMessage("Dang tao lai alpha MOV...");
      }
      // Bật một lớp sẽ tắt các lớp còn lại ở server (chỉ 1 hiệu ứng khi render),
      // nên phải tải lại cả danh sách thay vì chỉ vá bản ghi vừa lưu.
      if (payload.enabled) {
        await loadTVNoiseOverlays();
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

  const handleCtaUpload = async (file: File | null) => {
    if (!file) return;
    setIsCtaUploading(true);
    setErrorMessage(null);
    try {
      const res = await uploadCtaOverlay(file);
      await loadCtaOverlays();
      setSelectedCtaId(res.overlay.id);
      setCtaPreviewBust(Date.now());
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the upload CTA overlay.");
    } finally {
      setIsCtaUploading(false);
    }
  };

  const handleCtaSave = async () => {
    if (!selectedCta) return;
    setIsCtaSaving(true);
    setErrorMessage(null);
    try {
      const payload: Partial<CtaOverlay> = {
        isDefault: true,
        enabled: ctaForm.enabled,
        keyColor: ctaForm.keyColor.trim() || DEFAULT_CTA_FORM.keyColor,
        similarity: Number(ctaForm.similarity),
        blend: Number(ctaForm.blend),
        scaleWidth: Number(ctaForm.scaleWidth),
        position: ctaForm.position,
        margin: Number(ctaForm.margin),
      };
      const res = await updateCtaOverlay(selectedCta.id, payload);
      setCtaOverlays((current) =>
        current.map((item) => (item.id === selectedCta.id ? res.overlay : { ...item, isDefault: false })),
      );
      setCtaPreviewBust(Date.now());
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the luu cau hinh CTA.");
    } finally {
      setIsCtaSaving(false);
    }
  };

  const handleCtaToggle = async (enabled: boolean) => {
    if (!selectedCta) return;
    setCtaForm((current) => ({ ...current, enabled }));
    setErrorMessage(null);
    try {
      const res = await updateCtaOverlay(selectedCta.id, { enabled });
      setCtaOverlays((current) => current.map((item) => (item.id === selectedCta.id ? res.overlay : item)));
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the cap nhat trang thai CTA.");
    }
  };

  const handleCtaDelete = async (overlayId: string) => {
    setErrorMessage(null);
    try {
      await deleteCtaOverlay(overlayId);
      setCtaOverlays((current) => current.filter((item) => item.id !== overlayId));
      setSelectedCtaId("");
      void loadCtaOverlays();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the xoa CTA overlay.");
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
        <div className="mb-4 space-y-1">
          <h2 className="flex items-center gap-2 text-base font-semibold text-foreground">
            <Sparkles className="size-4 text-primary" />
            Hieu ung TV 1990s
          </h2>
          <p className="text-sm text-muted-foreground">
            Cac hieu ung dung filter co san cua FFmpeg (mau sac, chroma bleed, scanline, flicker, vignette) — render GPU
            (NVDEC + NVENC) trong cung 1 pass voi overlay. Bam Preview de render thu 4 giay tu clip mau trong thu vien.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {tvEffectStyles.map((style) => {
            const isSelected = style.id === selectedEffectId;
            const isPreviewing = previewingEffectId === style.id;
            const bust = effectPreviewBust[style.id];
            const previewSrc = style.previewPath ? `/media/${style.previewPath}${bust ? `?t=${bust}` : ""}` : null;
            return (
              <div
                key={style.id}
                className={`flex flex-col gap-3 rounded-lg border p-4 transition-colors ${
                  isSelected ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-semibold text-foreground">{style.name}</span>
                  {isSelected ? (
                    <Badge className="rounded-full">
                      <Check className="mr-1 size-3" />
                      Dang dung
                    </Badge>
                  ) : null}
                </div>
                <p className="min-h-10 text-xs text-muted-foreground">{style.description}</p>

                {previewSrc ? (
                  <video src={previewSrc} controls loop muted className="aspect-video w-full rounded-md bg-black object-contain" />
                ) : (
                  <div className="flex aspect-video w-full items-center justify-center rounded-md border border-dashed border-border bg-background/50 text-xs text-muted-foreground">
                    Chua co preview
                  </div>
                )}

                <div className="mt-auto flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => void handleEffectPreview(style.id)}
                    disabled={previewingEffectId !== null}
                  >
                    {isPreviewing ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Eye className="mr-2 size-4" />}
                    {isPreviewing ? "Dang render..." : "Preview 4s"}
                  </Button>
                  {!isSelected ? (
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => void handleSelectEffect(style.id)}
                      disabled={isSelectingEffect}
                    >
                      {isSelectingEffect ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Check className="mr-2 size-4" />}
                      Dung hieu ung nay
                    </Button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>

        <div
          className={`mt-6 rounded-lg border p-4 ${
            selectedEffectId === "custom" ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
          }`}
        >
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="space-y-1">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                Tuy chinh hieu ung
                {selectedEffectId === "custom" ? (
                  <Badge className="rounded-full">
                    <Check className="mr-1 size-3" />
                    Dang dung
                  </Badge>
                ) : null}
              </h3>
              <p className="text-xs text-muted-foreground">
                Chinh tung thong so (do nhieu, vien toi, tan suat nhay...) roi render preview truoc khi ap dung. De nhay
                de chiu: do nhay &le; 0.02 va toc do 2-5 Hz.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <select
                defaultValue=""
                onChange={(event) => {
                  if (event.target.value) handleLoadParamsFromPreset(event.target.value);
                  event.target.value = "";
                }}
                className="h-9 rounded-md border border-input bg-background px-3 text-xs"
              >
                <option value="">Nap thong so tu preset...</option>
                {tvEffectStyles
                  .filter((style) => style.id !== "none")
                  .map((style) => (
                    <option key={style.id} value={style.id}>
                      {style.name}
                    </option>
                  ))}
              </select>
            </div>
          </div>

          <div className="grid gap-5 lg:grid-cols-[1fr_minmax(280px,420px)]">
            <div className="grid content-start gap-3 sm:grid-cols-3 md:grid-cols-4">
              <div className="grid gap-1.5">
                <Label className="text-xs">Tong mau</Label>
                <select
                  value={effectForm.tone}
                  onChange={(event) => setEffectForm((current) => ({ ...current, tone: event.target.value as TVEffectTone }))}
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                >
                  {TONE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              {EFFECT_PARAM_FIELDS.map((field) => (
                <div key={field.key} className="grid gap-1.5">
                  <Label className="text-xs">
                    {field.label} <span className="text-muted-foreground">({field.min}–{field.max})</span>
                  </Label>
                  <Input
                    type="number"
                    min={field.min}
                    max={field.max}
                    step={field.step}
                    value={effectForm[field.key]}
                    onChange={(event) =>
                      setEffectForm((current) => ({ ...current, [field.key]: event.target.value }))
                    }
                    className="h-9"
                  />
                </div>
              ))}
              <div className="col-span-full flex flex-wrap gap-3 pt-1">
                <Button type="button" variant="outline" onClick={() => void handleCustomPreview()} disabled={isCustomPreviewing}>
                  {isCustomPreviewing ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Eye className="mr-2 size-4" />}
                  {isCustomPreviewing ? "Dang render..." : "Render preview 4s"}
                </Button>
                <Button type="button" onClick={() => void handleCustomSave()} disabled={isCustomSaving}>
                  {isCustomSaving ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
                  Luu & dung cau hinh nay
                </Button>
              </div>
            </div>

            {customPreviewPath ? (
              <video
                src={`/media/${customPreviewPath}${customPreviewBust ? `?t=${customPreviewBust}` : ""}`}
                controls
                loop
                muted
                className="aspect-video w-full self-start rounded-md bg-black object-contain"
              />
            ) : (
              <div className="flex aspect-video w-full items-center justify-center self-start rounded-md border border-dashed border-border bg-background/50 text-xs text-muted-foreground">
                Chua co preview custom — chinh thong so roi bam Render preview
              </div>
            )}
          </div>
        </div>
      </PageSection>

      <PageSection>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-base font-semibold text-foreground">TV Noise Overlays</h2>
            <p className="text-sm text-muted-foreground">Quan ly cac lop nhieu nen den, preprocess thanh alpha MOV va ap dung global cho Story Video.</p>
          </div>
          {noiseJobMessage ? <Badge variant="secondary" className="rounded-full">{noiseJobMessage}</Badge> : null}
        </div>

        {/* min-w-0 tren cot trai: mac dinh grid item la min-width:auto, nen mot ten
            file dai (khong xuong dong duoc) keo cot rong hon track 380px va de len
            panel cau hinh ben phai. */}
        <div className="grid gap-5 lg:grid-cols-[minmax(280px,380px)_1fr]">
          <div className="min-w-0 space-y-4">
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

            {sparklePresets.length ? (
              <div className="grid gap-2 rounded-lg border border-border/70 bg-background/70 p-3">
                <Label>Tạo lớp lấp lánh</Label>
                <select
                  value={sparklePresetId}
                  onChange={(event) => {
                    const next = sparklePresets.find((item) => item.id === event.target.value);
                    setSparklePresetId(event.target.value);
                    if (next) setSparkleParams(paramsToForm(next));
                  }}
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                >
                  {sparklePresets.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.name}
                    </option>
                  ))}
                </select>
                {selectedSparklePreset ? (
                  <>
                    <p className="text-xs text-muted-foreground">{selectedSparklePreset.description}</p>
                    <div className="grid gap-1">
                      <Label className="text-xs font-normal text-muted-foreground">
                        Tên lớp (để phân biệt các mẫu)
                      </Label>
                      <Input
                        value={sparkleName}
                        placeholder={selectedSparklePreset.name}
                        onChange={(event) => setSparkleName(event.target.value)}
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      {selectedSparklePreset.paramsUsed.map((key) => (
                        <div key={key} className="grid gap-1">
                          <Label className="text-xs font-normal text-muted-foreground">
                            {SPARKLE_PARAM_LABELS[key] ?? key}
                          </Label>
                          <Input
                            type="number"
                            step="any"
                            value={sparkleParams[key] ?? ""}
                            onChange={(event) =>
                              setSparkleParams((current) => ({ ...current, [key]: event.target.value }))
                            }
                          />
                        </div>
                      ))}
                    </div>
                  </>
                ) : null}
                <Button type="button" variant="secondary" onClick={() => void handleSparkleCreate()} disabled={isSparkleCreating}>
                  {isSparkleCreating ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
                  {isSparkleCreating ? "Dang dung lop lap lanh..." : "Tao lop lap lanh"}
                </Button>
                <p className="text-xs text-muted-foreground">
                  Dung mot lan roi cache lai (25-90 giay). Lop tao ra nam trong danh sach ben duoi: bat/tat,
                  chinh opacity va thu tu nhu moi overlay khac. Lớp vừa tạo sẽ tự thành lớp đang dùng.
                </p>
              </div>
            ) : null}

            {tvNoiseOverlays.length ? (
              <p className="text-xs text-muted-foreground">
                Mỗi lần render chỉ áp <span className="font-semibold text-foreground">một</span> lớp hiệu ứng. Bật một lớp
                sẽ tự tắt các lớp còn lại — chúng vẫn nằm trong danh sách để bật lại sau.
              </p>
            ) : null}

            {tvNoiseOverlays.length ? (
              <div className="grid gap-2">
                {tvNoiseOverlays.map((overlay) => (
                  <button
                    key={overlay.id}
                    type="button"
                    onClick={() => setSelectedNoiseId(overlay.id)}
                    className={`min-w-0 rounded-lg border p-3 text-left transition-colors ${
                      selectedNoise?.id === overlay.id ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
                    }`}
                  >
                    <div className="flex min-w-0 items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-foreground">{overlay.name}</span>
                      <Badge
                        variant={overlay.status === "failed" ? "destructive" : overlay.status === "ready" ? "secondary" : "outline"}
                        className="rounded-full"
                      >
                        {overlay.status}
                      </Badge>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      #{overlay.order} | opacity {Math.round((overlay.opacity ?? 0) * 100)}% |{" "}
                      {overlay.enabled ? (
                        <span className="font-semibold text-primary">đang dùng khi render</span>
                      ) : (
                        "tắt"
                      )}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyCard title="Chua co TV noise" description="Upload hoac import video noise nen den de bat dau." />
            )}
          </div>

          {selectedNoise ? (
            <div className="grid min-w-0 gap-5">
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

              {/* Thong so cua lop lap lanh da tao: khong hien thi thi hai bien the
                  cung preset trong y het nhau va khong the dung lai de tinh chinh. */}
              {selectedNoise.kind === "sparkle" && selectedNoise.meta?.params ? (
                <div className="grid gap-2 rounded-lg border border-border/70 bg-background/70 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Label className="text-xs font-normal text-muted-foreground">
                      Thông số đã lưu ·{" "}
                      {sparklePresets.find((preset) => preset.id === selectedNoise.meta?.presetId)?.name ??
                        selectedNoise.meta?.presetId ??
                        "sparkle"}
                    </Label>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        const presetId = selectedNoise.meta?.presetId ?? "";
                        const preset = sparklePresets.find((item) => item.id === presetId);
                        if (!preset) return;
                        setSparklePresetId(presetId);
                        setSparkleParams(
                          Object.fromEntries(
                            preset.paramsUsed.map((key) => [
                              key,
                              String(selectedNoise.meta?.params?.[key] ?? preset.params[key] ?? 0),
                            ]),
                          ),
                        );
                        setSparkleName(`${selectedNoise.name} (copy)`);
                      }}
                    >
                      Nạp vào form tạo lớp
                    </Button>
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-3">
                    {Object.entries(selectedNoise.meta.params).map(([key, value]) => (
                      <div key={key} className="flex min-w-0 justify-between gap-2">
                        <span className="truncate">{SPARKLE_PARAM_LABELS[key] ?? key}</span>
                        <span className="font-medium text-foreground">{value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                <div className="grid min-w-0 gap-2">
                  <Label>Trang thai</Label>
                  <label className="flex h-10 items-center gap-2 rounded-md border border-input px-3 text-sm">
                    <input
                      type="checkbox"
                      checked={noiseForm.enabled}
                      onChange={(event) => setNoiseForm((current) => ({ ...current, enabled: event.target.checked }))}
                    />
                    Dùng lớp này khi render
                  </label>
                </div>

                <div className="grid min-w-0 gap-2">
                  <Label>Blend</Label>
                  <select
                    value={noiseForm.blendMode}
                    onChange={(event) =>
                      setNoiseForm((current) => ({ ...current, blendMode: event.target.value as TVNoiseForm["blendMode"] }))
                    }
                    className="h-10 w-full min-w-0 truncate rounded-md border border-input bg-background px-3 text-sm"
                  >
                    <option value="alpha">Alpha &mdash; key nen den</option>
                    <option value="screen">Screen &mdash; cong sang</option>
                    <option value="luma">Luma &mdash; lop lap lanh</option>
                  </select>
                </div>

                <div className="grid min-w-0 gap-2">
                  <Label>Opacity</Label>
                  <Input
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={noiseForm.opacity}
                    onChange={(event) => setNoiseForm((current) => ({ ...current, opacity: event.target.value }))}
                  />
                </div>

                {/* Cac o duoi day chi co nghia voi dung mot blend mode, nen an han
                    thay vi disable: form 7 cot truoc day bi vo o man hinh hep. */}
                {noiseForm.blendMode === "luma" ? (
                  <div className="grid min-w-0 gap-2">
                    <Label>Luma gain</Label>
                    <Input
                      type="number"
                      min="1"
                      max="8"
                      step="0.1"
                      value={noiseForm.lumaGain}
                      onChange={(event) => setNoiseForm((current) => ({ ...current, lumaGain: event.target.value }))}
                    />
                  </div>
                ) : null}

                {noiseForm.blendMode === "alpha" ? (
                  <>
                    <div className="grid min-w-0 gap-2">
                      <Label>Tolerance</Label>
                      <Input
                        type="number"
                        min="0"
                        max="1"
                        step="0.01"
                        value={noiseForm.tolerance}
                        onChange={(event) => setNoiseForm((current) => ({ ...current, tolerance: event.target.value }))}
                      />
                    </div>
                    <div className="grid min-w-0 gap-2">
                      <Label>Softness</Label>
                      <Input
                        type="number"
                        min="0"
                        max="1"
                        step="0.01"
                        value={noiseForm.softness}
                        onChange={(event) => setNoiseForm((current) => ({ ...current, softness: event.target.value }))}
                      />
                    </div>
                  </>
                ) : null}

                <div className="grid min-w-0 gap-2">
                  <Label>Order</Label>
                  <Input
                    type="number"
                    min="0"
                    step="1"
                    value={noiseForm.order}
                    onChange={(event) => setNoiseForm((current) => ({ ...current, order: event.target.value }))}
                  />
                </div>
              </div>

              <p className="text-xs text-muted-foreground">{BLEND_MODE_HINTS[noiseForm.blendMode]}</p>

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
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-base font-semibold text-foreground">Preview hieu ung tren video that</h2>
            <p className="text-sm text-muted-foreground">
              Tai len vai clip ngan 3-5s giong trong thu vien, roi render de xem ca chong hieu ung
              (style + cac lop overlay dang bat + song am + CTA) tren dung loai canh ban se dung.
            </p>
          </div>
          {previewMessage ? <Badge variant="secondary" className="rounded-full">{previewMessage}</Badge> : null}
        </div>

        {/* min-w-0 tren cot trai: mac dinh grid item la min-width:auto, nen mot ten
            file dai (khong xuong dong duoc) keo cot rong hon track 380px va de len
            panel cau hinh ben phai. */}
        <div className="grid gap-5 lg:grid-cols-[minmax(280px,380px)_1fr]">
          <div className="min-w-0 space-y-4">
            <div className="grid gap-2">
              <Label>Tai len bo clip</Label>
              <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-background/70 p-4 text-center text-sm text-muted-foreground">
                {isPreviewUploading ? <Loader2 className="size-5 animate-spin text-primary" /> : <Upload className="size-5 text-primary" />}
                <span>{isPreviewUploading ? "Dang chuan hoa va ghep..." : "Chon nhieu clip cung luc (toi da 24)"}</span>
                <Input
                  type="file"
                  multiple
                  accept=".mp4,.mov,.mkv,.webm"
                  className="hidden"
                  disabled={isPreviewUploading}
                  onChange={(event) => void handlePreviewUpload(event.currentTarget.files)}
                />
              </label>
              <p className="text-xs text-muted-foreground">
                Clip duoc chuan hoa ve dung dinh dang thu vien roi ghep lai mot lan. Sau do doi thong so
                bao nhieu lan cung duoc, chi phai render lai buoc hieu ung.
              </p>
            </div>

            {previewSources.length ? (
              <div className="grid gap-2">
                {previewSources.map((source) => (
                  <button
                    key={source.id}
                    type="button"
                    onClick={() => setSelectedPreviewId(source.id)}
                    className={`min-w-0 rounded-lg border p-3 text-left transition-colors ${
                      selectedPreviewSource?.id === source.id ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
                    }`}
                  >
                    <div className="truncate text-sm font-semibold text-foreground">{source.name}</div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {source.clipCount} clip | {source.durationSeconds}s
                      {source.previewPath ? " | da co preview" : " | chua render"}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyCard title="Chua co bo clip nao" description="Tai len vai clip ngan de xem thu hieu ung." />
            )}
          </div>

          {selectedPreviewSource ? (
            <div className="grid gap-5">
              {selectedPreviewSource.previewPath ? (
                <video
                  key={selectedPreviewSource.previewPath}
                  src={`/media/${selectedPreviewSource.previewPath}`}
                  controls
                  className="aspect-video w-full rounded-lg bg-black object-contain"
                />
              ) : (
                <div className="flex aspect-video w-full items-center justify-center rounded-lg border border-dashed border-border bg-background/70 text-sm text-muted-foreground">
                  Chua render preview cho bo clip nay.
                </div>
              )}

              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <label className="flex h-10 items-center gap-2 rounded-md border border-input px-3 text-sm">
                  <input
                    type="checkbox"
                    checked={previewOptions.includeStyle}
                    onChange={(event) => setPreviewOptions((current) => ({ ...current, includeStyle: event.target.checked }))}
                  />
                  Ap style mau
                </label>
                <label className="flex h-10 items-center gap-2 rounded-md border border-input px-3 text-sm">
                  <input
                    type="checkbox"
                    checked={previewOptions.includeOverlays}
                    onChange={(event) => setPreviewOptions((current) => ({ ...current, includeOverlays: event.target.checked }))}
                  />
                  Ap cac lop overlay
                </label>
                <label className="flex h-10 items-center gap-2 rounded-md border border-input px-3 text-sm">
                  <input
                    type="checkbox"
                    checked={previewOptions.compare}
                    onChange={(event) => setPreviewOptions((current) => ({ ...current, compare: event.target.checked }))}
                  />
                  So sanh canh nhau
                </label>
                <div className="grid min-w-0 gap-2">
                  <Label>Gioi han (giay)</Label>
                  <Input
                    type="number"
                    min="1"
                    max="90"
                    step="1"
                    value={previewOptions.maxSeconds}
                    onChange={(event) => setPreviewOptions((current) => ({ ...current, maxSeconds: event.target.value }))}
                  />
                </div>
              </div>

              {selectedPreviewSource.appliedLayers?.length ? (
                <div className="flex flex-wrap gap-2">
                  {selectedPreviewSource.appliedLayers.map((layer, index) => (
                    <Badge key={layer.id ?? index} variant="outline" className="rounded-full">
                      {layer.name} · {layer.blendMode}
                    </Badge>
                  ))}
                </div>
              ) : null}

              <div className="flex flex-wrap gap-3">
                <Button type="button" onClick={() => void handlePreviewRender()} disabled={isPreviewRendering}>
                  {isPreviewRendering ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Eye className="mr-2 size-4" />}
                  {isPreviewRendering ? "Dang render..." : "Render preview"}
                </Button>
                <Button type="button" variant="destructive" onClick={() => void handlePreviewDelete(selectedPreviewSource.id)}>
                  <Trash2 className="mr-2 size-4" />
                  Xoa bo clip
                </Button>
              </div>

              <p className="text-xs text-muted-foreground">
                Preview dung dung chuoi filter ma buoc render that dung, nen ket qua khop nhau. Chay tren CPU
                va o 1080p, nen cho khoang 3-4 giay xu ly cho moi giay video.
              </p>
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

              <OverlayPositionPreview
                boxes={[
                  {
                    label: "Sóng âm",
                    position: form.position,
                    margin: Number(form.margin) || 0,
                    scaleWidth: Number(form.scaleWidth) || 420,
                    heightRatio: 0.3,
                    className: "border-sky-300 bg-sky-500/70",
                    active: true,
                  },
                  {
                    label: ctaForm.enabled ? "CTA" : "CTA (tắt)",
                    position: ctaForm.position,
                    margin: Number(ctaForm.margin) || 0,
                    scaleWidth: Number(ctaForm.scaleWidth) || 360,
                    heightRatio: 0.6,
                    className: "border-orange-300 bg-orange-500/60",
                  },
                ]}
                onPickCorner={(corner) => setForm((current) => ({ ...current, position: corner }))}
              />

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
          <h2 className="text-base font-semibold text-foreground">CTA overlay (Like / Subscribe / Thông báo)</h2>
          <p className="text-sm text-muted-foreground">
            Video nút kêu gọi được tách nền xanh và chèn vào một góc của mọi video Story Video. Mặc định bật sẵn ở góc trên-trái, loop hết thời lượng và không có tiếng.
          </p>
        </div>
        <div className="grid gap-5 lg:grid-cols-[minmax(260px,360px)_1fr]">
          <div className="space-y-4">
            <div className="grid gap-2">
              <Label>Upload video CTA (nền xanh)</Label>
              <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-background/70 p-4 text-sm text-muted-foreground">
                {isCtaUploading ? <Loader2 className="size-5 animate-spin text-primary" /> : <Upload className="size-5 text-primary" />}
                <span>{isCtaUploading ? "Dang tao alpha MOV..." : "Chon video CTA"}</span>
                <Input
                  type="file"
                  accept=".mp4,.mov,.mkv,.webm"
                  className="hidden"
                  disabled={isCtaUploading}
                  onChange={(event) => void handleCtaUpload(event.currentTarget.files?.[0] ?? null)}
                />
              </label>
            </div>

            {ctaOverlays.length ? (
              <div className="grid gap-2">
                {ctaOverlays.map((overlay) => (
                  <button
                    key={overlay.id}
                    type="button"
                    onClick={() => setSelectedCtaId(overlay.id)}
                    className={`rounded-lg border p-3 text-left transition-colors ${
                      selectedCta?.id === overlay.id ? "border-primary bg-primary/10" : "border-border/70 bg-background/70"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-foreground">{overlay.name}</span>
                      <div className="flex items-center gap-1">
                        {overlay.enabled === false ? (
                          <Badge variant="secondary" className="rounded-full">Tắt</Badge>
                        ) : null}
                        {overlay.isDefault ? (
                          <Badge className="rounded-full">
                            <Star className="mr-1 size-3" />
                            Default
                          </Badge>
                        ) : null}
                      </div>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {overlay.durationSeconds}s | {overlay.scaleWidth ?? 360}px | {overlay.position ?? "top_left"}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyCard title="Chua co CTA overlay" description="Upload video nut nen xanh de tao overlay alpha." />
            )}
          </div>

          {selectedCta ? (
            <div className="grid gap-5">
              {selectedCta.processedRelativePath ? (
                <div
                  className="w-full overflow-hidden rounded-lg"
                  style={{
                    backgroundColor: "#3a3a3a",
                    backgroundImage:
                      "linear-gradient(45deg, #555 25%, transparent 25%), linear-gradient(-45deg, #555 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #555 75%), linear-gradient(-45deg, transparent 75%, #555 75%)",
                    backgroundSize: "24px 24px",
                    backgroundPosition: "0 0, 0 12px, 12px -12px, -12px 0",
                  }}
                >
                  <video
                    key={ctaPreviewBust}
                    src={`/media/${selectedCta.processedRelativePath}?t=${ctaPreviewBust}`}
                    autoPlay
                    loop
                    muted
                    playsInline
                    className="aspect-video w-full object-contain"
                  />
                </div>
              ) : null}

              <label className="flex items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={ctaForm.enabled}
                  onChange={(event) => void handleCtaToggle(event.currentTarget.checked)}
                  className="size-4"
                />
                Bật CTA overlay cho mọi video Story Video
              </label>

              <OverlayPositionPreview
                boxes={[
                  {
                    label: ctaForm.enabled ? "CTA" : "CTA (tắt)",
                    position: ctaForm.position,
                    margin: Number(ctaForm.margin) || 0,
                    scaleWidth: Number(ctaForm.scaleWidth) || 360,
                    heightRatio: 0.6,
                    className: "border-orange-300 bg-orange-500/60",
                    active: true,
                  },
                  {
                    label: "Sóng âm",
                    position: form.position,
                    margin: Number(form.margin) || 0,
                    scaleWidth: Number(form.scaleWidth) || 420,
                    heightRatio: 0.3,
                    className: "border-sky-300 bg-sky-500/70",
                  },
                ]}
                onPickCorner={(corner) => setCtaForm((current) => ({ ...current, position: corner }))}
              />

              <div className="grid gap-4 md:grid-cols-3">
                <div className="grid gap-2">
                  <Label>Key color</Label>
                  <Input value={ctaForm.keyColor} onChange={(event) => setCtaForm((current) => ({ ...current, keyColor: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Similarity</Label>
                  <Input type="number" min="0" max="1" step="0.01" value={ctaForm.similarity} onChange={(event) => setCtaForm((current) => ({ ...current, similarity: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Blend</Label>
                  <Input type="number" min="0" max="1" step="0.01" value={ctaForm.blend} onChange={(event) => setCtaForm((current) => ({ ...current, blend: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Width</Label>
                  <Input type="number" min="64" step="2" value={ctaForm.scaleWidth} onChange={(event) => setCtaForm((current) => ({ ...current, scaleWidth: event.target.value }))} />
                </div>
                <div className="grid gap-2">
                  <Label>Position</Label>
                  <select
                    value={ctaForm.position}
                    onChange={(event) => setCtaForm((current) => ({ ...current, position: event.target.value as CtaForm["position"] }))}
                    className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  >
                    <option value="top_left">Top left</option>
                    <option value="top_right">Top right</option>
                    <option value="bottom_left">Bottom left</option>
                    <option value="bottom_right">Bottom right</option>
                  </select>
                </div>
                <div className="grid gap-2">
                  <Label>Margin</Label>
                  <Input type="number" min="0" step="1" value={ctaForm.margin} onChange={(event) => setCtaForm((current) => ({ ...current, margin: event.target.value }))} />
                </div>
              </div>

              <div className="flex flex-wrap gap-3">
                <Button type="button" onClick={handleCtaSave} disabled={isCtaSaving}>
                  {isCtaSaving ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
                  Luu va dat mac dinh
                </Button>
                <Button type="button" variant="destructive" onClick={() => void handleCtaDelete(selectedCta.id)}>
                  <Trash2 className="mr-2 size-4" />
                  Xoa
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </PageSection>

      <PageSection>
        <StoryLibraryNormalizePanel onNormalized={() => setLibraryRefreshKey((current) => current + 1)} />
      </PageSection>

      <PageSection>
        <div className="mb-4 space-y-1">
          <h2 className="text-base font-semibold text-foreground">Thu vien clip</h2>
          <p className="text-sm text-muted-foreground">
            Upload video nguon hoac nhap link Pixabay/Pexels de he thong tai ve, cat thanh clip ngan va chuan hoa ve
            dung dinh dang render dung chung cho Story Video.
          </p>
        </div>
        <StoryLibraryManager key={libraryRefreshKey} showBulkDeleteActions />
      </PageSection>
    </AppShell>
  );
}
