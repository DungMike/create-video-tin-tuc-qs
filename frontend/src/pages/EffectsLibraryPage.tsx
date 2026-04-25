import { useEffect, useState } from "react";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { TopNav } from "@/components/top-nav";
import { EffectPresetCard } from "@/components/effect-preset-card";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, getEffectsLibrary, updateEffectsLibraryConfig } from "@/lib/api";
import type {
  EffectAnimationPreset,
  EffectTransitionPreset,
  EffectsLibraryResponse,
} from "@/types/api";

function toIdSet(items: { id: string; active: boolean }[]) {
  return new Set(items.filter((item) => item.active).map((item) => item.id));
}

function toggleId(current: Set<string>, id: string, checked: boolean) {
  const next = new Set(current);
  if (checked) {
    next.add(id);
  } else {
    next.delete(id);
  }
  return next;
}

export function EffectsLibraryPage() {
  const [data, setData] = useState<EffectsLibraryResponse | null>(null);
  const [activeAnimationIds, setActiveAnimationIds] = useState<Set<string>>(new Set());
  const [activeTransitionIds, setActiveTransitionIds] = useState<Set<string>>(new Set());
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);

    getEffectsLibrary()
      .then((response) => {
        if (cancelled) {
          return;
        }
        setData(response);
        setActiveAnimationIds(toIdSet(response.animations));
        setActiveTransitionIds(toIdSet(response.transitions));
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorMessage(error instanceof ApiError ? error.message : "Khong the tai thu vien hieu ung.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async () => {
    setIsSubmitting(true);
    setErrorMessage(null);
    setSuccessMessage(null);

    try {
      const response = await updateEffectsLibraryConfig({
        activeAnimationIds: Array.from(activeAnimationIds),
        activeTransitionIds: Array.from(activeTransitionIds),
      });
      setData(response);
      setActiveAnimationIds(toIdSet(response.animations));
      setActiveTransitionIds(toIdSet(response.transitions));
      setSuccessMessage("Da cap nhat cau hinh hieu ung thanh cong.");
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Khong the luu cau hinh hieu ung.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderAnimationMeta = (preset: EffectAnimationPreset) => (
    <div className="space-y-1">
      <div>Duration: {preset.durationSeconds}s</div>
      <div>Source image: {preset.sourceImageRelativePath}</div>
      <div className="break-all">Filter: {preset.ffmpegFilter}</div>
    </div>
  );

  const renderTransitionMeta = (preset: EffectTransitionPreset) => (
    <div className="space-y-1">
      <div>Clip duration: {preset.clipDurationSeconds}s</div>
      <div>Transition duration: {preset.transitionDurationSeconds}s</div>
      <div>Source A: {preset.sourceImageARelativePath}</div>
      <div>Source B: {preset.sourceImageBRelativePath}</div>
      <div>xfade: {preset.xfadeTransition}</div>
    </div>
  );

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai thu vien hieu ung..." />
      </AppShell>
    );
  }

  if (!data) {
    return (
      <AppShell>
        <StatusAlert
          title="Khong the mo thu vien hieu ung"
          message={errorMessage || "Du lieu hieu ung khong kha dung."}
          variant="destructive"
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Global Effects Library"
        title="Cau hinh animation va transition cho image clip"
        description="Preview cac preset FFmpeg da generate san tu raw_images, chon bo mau dang active va backend se random deu trong tap duoc bat khi tao image clip va ghep cac doan anh."
        stats={[
          { label: "Animation presets", value: data.animations.length },
          { label: "Transition presets", value: data.transitions.length },
          { label: "Animation active", value: activeAnimationIds.size },
          { label: "Transition active", value: activeTransitionIds.size },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}
      {successMessage ? <StatusAlert title="Da luu cau hinh" message={successMessage} /> : null}

      <PageSection className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            onClick={() => {
              setActiveAnimationIds(new Set(data.animations.map((preset) => preset.id)));
              setActiveTransitionIds(new Set(data.transitions.map((preset) => preset.id)));
            }}
          >
            Bat tat ca preset
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              setActiveAnimationIds(new Set());
              setActiveTransitionIds(new Set());
            }}
          >
            Tat tat ca preset
          </Button>
          <div className="text-sm text-muted-foreground">
            Cau hinh nay la global, khong gan voi job cu the.
          </div>
        </div>
      </PageSection>

      <Tabs defaultValue="animations" className="space-y-4">
        <TabsList>
          <TabsTrigger value="animations">Animations</TabsTrigger>
          <TabsTrigger value="transitions">Transitions</TabsTrigger>
        </TabsList>

        <TabsContent value="animations">
          {data.animations.length ? (
            <section className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
              {data.animations.map((preset) => (
                <EffectPresetCard
                  key={preset.id}
                  preset={preset}
                  checked={activeAnimationIds.has(preset.id)}
                  meta={renderAnimationMeta(preset)}
                  onCheckedChange={(checked) => {
                    setActiveAnimationIds((current) => toggleId(current, preset.id, checked));
                  }}
                />
              ))}
            </section>
          ) : (
            <EmptyCard title="Chua co animation preset" description="Hay generate thu vien hieu ung tu raw_images truoc." />
          )}
        </TabsContent>

        <TabsContent value="transitions">
          {data.transitions.length ? (
            <section className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
              {data.transitions.map((preset) => (
                <EffectPresetCard
                  key={preset.id}
                  preset={preset}
                  checked={activeTransitionIds.has(preset.id)}
                  meta={renderTransitionMeta(preset)}
                  onCheckedChange={(checked) => {
                    setActiveTransitionIds((current) => toggleId(current, preset.id, checked));
                  }}
                />
              ))}
            </section>
          ) : (
            <EmptyCard title="Chua co transition preset" description="Hay generate thu vien hieu ung tu raw_images truoc." />
          )}
        </TabsContent>
      </Tabs>

      <PageSection className="border-none bg-transparent p-0 shadow-none">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="text-sm text-muted-foreground">
            Animation active: <span className="font-semibold text-foreground">{activeAnimationIds.size}</span> | Transition active:{" "}
            <span className="font-semibold text-foreground">{activeTransitionIds.size}</span>
          </div>
          <Button size="lg" onClick={handleSubmit} disabled={isSubmitting}>
            {isSubmitting ? "Dang luu cau hinh..." : "Luu cau hinh hieu ung"}
          </Button>
        </div>
      </PageSection>
    </AppShell>
  );
}
