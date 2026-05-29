import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { ApiError, generateCRTDemo } from "@/lib/api";
import type { CRTSettings } from "@/types/api";

const VIGNETTE_OPTIONS = [
  { label: "Off", value: "0" },
  { label: "Light", value: "PI/6" },
  { label: "Medium", value: "PI/4" },
  { label: "Heavy", value: "PI/3" },
] as const;

const CRT_PRESETS: Record<string, CRTSettings> = {
  subtle: { noiseStrength: 8, scanlineOpacity: 0.03, vignetteAngle: "PI/6", colorBleed: false, flickerIntensity: 0.01 },
  light: { noiseStrength: 15, scanlineOpacity: 0.06, vignetteAngle: "PI/5", colorBleed: true, flickerIntensity: 0.02 },
  medium: { noiseStrength: 25, scanlineOpacity: 0.10, vignetteAngle: "PI/4", colorBleed: true, flickerIntensity: 0.04 },
  heavy: { noiseStrength: 40, scanlineOpacity: 0.15, vignetteAngle: "PI/3", colorBleed: true, flickerIntensity: 0.06 },
};

const PRESET_LABELS: Record<string, string> = {
  subtle: "Subtle",
  light: "Light",
  medium: "Medium",
  heavy: "Heavy",
  custom: "Custom",
};

function detectPreset(settings: CRTSettings): string {
  for (const [name, preset] of Object.entries(CRT_PRESETS)) {
    if (
      preset.noiseStrength === settings.noiseStrength &&
      preset.scanlineOpacity === settings.scanlineOpacity &&
      preset.vignetteAngle === settings.vignetteAngle &&
      preset.colorBleed === settings.colorBleed &&
      preset.flickerIntensity === settings.flickerIntensity
    ) {
      return name;
    }
  }
  return "custom";
}

export interface CRTEffectConfiguratorProps {
  value: CRTSettings;
  onChange: (settings: CRTSettings) => void;
  sampleClipPath?: string;
}

export function CRTEffectConfigurator({ value, onChange, sampleClipPath }: CRTEffectConfiguratorProps) {
  const [demoSrc, setDemoSrc] = useState<string | null>(null);
  const [isGeneratingDemo, setIsGeneratingDemo] = useState(false);
  const [demoError, setDemoError] = useState<string | null>(null);
  const [isGeneratingVideo, setIsGeneratingVideo] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activePreset = detectPreset(value);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setIsGeneratingDemo(true);
      setDemoError(null);
      generateCRTDemo(value, sampleClipPath)
        .then((res) => {
          setDemoSrc(`/media/${res.demoPath}`);
        })
        .catch((err) => {
          setDemoError(err instanceof ApiError ? err.message : "Khong the tao demo CRT.");
        })
        .finally(() => setIsGeneratingDemo(false));
    }, 500);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [value, sampleClipPath]);

  const handlePresetChange = (presetName: string) => {
    if (presetName === "custom") return;
    const preset = CRT_PRESETS[presetName];
    if (preset) onChange({ ...preset });
  };

  const updateField = <K extends keyof CRTSettings>(field: K, fieldValue: CRTSettings[K]) => {
    onChange({ ...value, [field]: fieldValue });
  };

  const handleGenerateDemoVideo = async () => {
    setIsGeneratingVideo(true);
    try {
      const res = await generateCRTDemo(value, sampleClipPath);
      setDemoSrc(`/media/${res.demoPath}`);
    } catch (err) {
      setDemoError(err instanceof ApiError ? err.message : "Khong the tao demo video.");
    } finally {
      setIsGeneratingVideo(false);
    }
  };

  return (
    <Card className="border-border/70 bg-card/90 shadow-lg">
      <CardContent className="p-4 md:p-6">
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Left: Demo preview */}
          <div className="space-y-3">
            <Label className="text-sm font-semibold">Preview CRT Effect</Label>
            <div className="relative aspect-video w-full overflow-hidden rounded-2xl border border-border/70 bg-black">
              {isGeneratingDemo ? (
                <div className="flex size-full items-center justify-center">
                  <Loader2 className="size-8 animate-spin text-primary" />
                </div>
              ) : demoSrc ? (
                <img
                  src={demoSrc}
                  alt="CRT Demo Preview"
                  className="size-full object-cover"
                />
              ) : (
                <div className="flex size-full items-center justify-center text-sm text-muted-foreground">
                  {demoError || "Dang cho cau hinh de tao demo..."}
                </div>
              )}
            </div>
            {demoError && !isGeneratingDemo ? (
              <p className="text-xs text-destructive">{demoError}</p>
            ) : null}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleGenerateDemoVideo}
              disabled={isGeneratingVideo}
              className="w-full"
            >
              {isGeneratingVideo ? (
                <>
                  <Loader2 className="mr-2 size-4 animate-spin" />
                  Dang tao demo video...
                </>
              ) : (
                "Generate Demo Video (3s)"
              )}
            </Button>
          </div>

          {/* Right: Controls */}
          <div className="space-y-5">
            {/* Preset selector */}
            <div className="space-y-2">
              <Label className="text-sm font-semibold">Preset</Label>
              <div className="flex flex-wrap gap-2">
                {Object.keys(PRESET_LABELS).map((presetName) => (
                  <label
                    key={presetName}
                    className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors ${
                      activePreset === presetName
                        ? "border-primary bg-primary/10 font-semibold text-foreground"
                        : "border-border/70 bg-background/70 text-muted-foreground hover:border-primary/50"
                    }`}
                  >
                    <input
                      type="radio"
                      name="crt-preset"
                      value={presetName}
                      checked={activePreset === presetName}
                      onChange={() => handlePresetChange(presetName)}
                      className="sr-only"
                    />
                    {PRESET_LABELS[presetName]}
                  </label>
                ))}
              </div>
            </div>

            {/* Noise slider */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-sm">Noise</Label>
                <span className="text-sm font-medium text-foreground">{value.noiseStrength}</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={value.noiseStrength}
                onChange={(e) => updateField("noiseStrength", Number(e.target.value))}
                className="h-2 w-full cursor-pointer appearance-none rounded-full bg-muted accent-primary"
              />
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>0</span>
                <span>100</span>
              </div>
            </div>

            {/* Scanline slider */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-sm">Scanline Opacity</Label>
                <span className="text-sm font-medium text-foreground">{Math.round(value.scanlineOpacity * 100)}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={30}
                step={1}
                value={Math.round(value.scanlineOpacity * 100)}
                onChange={(e) => updateField("scanlineOpacity", Number(e.target.value) / 100)}
                className="h-2 w-full cursor-pointer appearance-none rounded-full bg-muted accent-primary"
              />
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>0%</span>
                <span>30%</span>
              </div>
            </div>

            {/* Vignette selector */}
            <div className="space-y-2">
              <Label className="text-sm">Vignette</Label>
              <div className="flex flex-wrap gap-2">
                {VIGNETTE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => updateField("vignetteAngle", opt.value)}
                    className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                      value.vignetteAngle === opt.value
                        ? "border-primary bg-primary/10 font-semibold text-foreground"
                        : "border-border/70 bg-background/70 text-muted-foreground hover:border-primary/50"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Flicker slider */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-sm">Flicker Intensity</Label>
                <span className="text-sm font-medium text-foreground">{Math.round(value.flickerIntensity * 100)}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={10}
                step={1}
                value={Math.round(value.flickerIntensity * 100)}
                onChange={(e) => updateField("flickerIntensity", Number(e.target.value) / 100)}
                className="h-2 w-full cursor-pointer appearance-none rounded-full bg-muted accent-primary"
              />
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>0%</span>
                <span>10%</span>
              </div>
            </div>

            {/* Color Bleed toggle */}
            <div className="flex items-center gap-3">
              <Checkbox
                id="color-bleed"
                checked={value.colorBleed}
                onCheckedChange={(checked) => updateField("colorBleed", Boolean(checked))}
              />
              <Label htmlFor="color-bleed" className="cursor-pointer text-sm">
                Color Bleed
              </Label>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
