import { startTransition, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, createAudioFromDocs, getAudioLibrary, getVoices } from "@/lib/api";
import type { DocsToAudioResponse, VoiceRecord } from "@/types/api";

export function DocsToAudioPage() {
  const navigate = useNavigate();
  const [defaultOutputName, setDefaultOutputName] = useState("");
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [defaultVoiceId, setDefaultVoiceId] = useState("");
  const [result, setResult] = useState<DocsToAudioResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage(null);

    Promise.all([getAudioLibrary(), getVoices()])
      .then(([audioResponse, voiceResponse]) => {
        if (cancelled) {
          return;
        }
        setDefaultOutputName(audioResponse.defaultOutputName);
        setVoices(voiceResponse.voices);
        setDefaultVoiceId(voiceResponse.defaultVoiceId);
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorMessage(error instanceof ApiError ? error.message : "Khong the tai cau hinh audio.");
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

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorMessage(null);
    setResult(null);

    const formData = new FormData(event.currentTarget);
    try {
      const response = await createAudioFromDocs({
        docUrl: String(formData.get("docUrl") ?? ""),
        outputName: String(formData.get("outputName") ?? ""),
        voiceId: String(formData.get("voiceId") ?? ""),
        speed: Number(formData.get("speed") || 1),
        volume: Number(formData.get("volume") || 1),
      });
      setResult(response);
      setDefaultOutputName(response.audio.name);
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Khong the tao audio tu Google Docs.");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai cau hinh TTS..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Docs to Audio"
        title="Chuyen Google Docs public link thanh audio"
        description="Backend se export noi dung Google Docs thanh text, chia chunk toi da 2000 ky tu, goi TTS song song toi da 15 task va ghep thanh mot file MP3 hoan chinh."
        stats={[
          { label: "Max chars/chunk", value: 2000 },
          { label: "Max concurrency", value: 15 },
          { label: "Voices", value: voices.length },
          { label: "Output", value: "storage/audio/generated" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}

      <PageSection>
        <form className="grid gap-5" onSubmit={handleSubmit}>
          <div className="grid gap-2">
            <Label htmlFor="docUrl">Google Docs public link</Label>
            <Input id="docUrl" name="docUrl" placeholder="https://docs.google.com/document/d/..." required />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="outputName">Ten file audio</Label>
              <Input id="outputName" name="outputName" defaultValue={defaultOutputName} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="voiceId">Voice</Label>
              <select
                id="voiceId"
                name="voiceId"
                defaultValue={defaultVoiceId}
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
              >
                <option value={defaultVoiceId}>
                  {defaultVoiceId ? `Default voice (${defaultVoiceId})` : "Use TTS_DEFAULT_VOICE_ID"}
                </option>
                {voices.map((voice) => (
                  <option key={voice.voiceId} value={voice.voiceId}>
                    {voice.voiceName} - {voice.voiceId}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="speed">Speed</Label>
              <Input id="speed" name="speed" type="number" step="0.1" min="0.5" max="1.5" defaultValue="1" />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="volume">Volume</Label>
              <Input id="volume" name="volume" type="number" step="0.1" min="0" defaultValue="1" />
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-4">
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
              Yeu cau nay chay sync; tai lieu cang dai thi thoi gian cho cang lau.
            </p>
            <Button type="submit" size="lg" disabled={isSubmitting}>
              {isSubmitting ? "Dang tao audio..." : "Tao audio tu Docs"}
            </Button>
          </div>
        </form>
      </PageSection>

      {result ? (
        <PageSection>
          <div className="space-y-4">
            <StatusAlert
              title="Da tao audio"
              message={`File ${result.audio.name} gom ${result.chunkCount} chunk. Text source: ${result.textRelativePath}`}
            />
            <audio controls src={`/media/${result.audio.relativePath}`} className="w-full" />
            <div className="flex flex-wrap gap-3">
              <Button
                onClick={() => {
                  startTransition(() => navigate("/"));
                }}
              >
                Dung audio nay tren UploadPage
              </Button>
              <Button asChild variant="secondary">
                <Link to="/voices">Quan ly voices</Link>
              </Button>
            </div>
          </div>
        </PageSection>
      ) : null}
    </AppShell>
  );
}

function TopNav() {
  return (
    <nav className="flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-border/70 bg-card/90 px-4 py-3 shadow-lg backdrop-blur md:px-6">
      <Link to="/" className="text-sm font-semibold tracking-tight text-foreground">
        Auto Video Review Studio
      </Link>
      <div className="flex flex-wrap items-center gap-2">
        <Button asChild variant="ghost">
          <Link to="/">Upload</Link>
        </Button>
        <Button asChild variant="secondary">
          <Link to="/docs-to-audio">Docs to Audio</Link>
        </Button>
        <Button asChild variant="ghost">
          <Link to="/voices">Voices</Link>
        </Button>
        <Button asChild variant="ghost">
          <Link to="/effects-library">Effects Library</Link>
        </Button>
      </div>
    </nav>
  );
}
