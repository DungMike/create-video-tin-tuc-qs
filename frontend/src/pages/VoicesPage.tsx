import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, cloneVoice, getVoices } from "@/lib/api";
import type { VoiceRecord } from "@/types/api";

export function VoicesPage() {
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [defaultVoiceId, setDefaultVoiceId] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const loadData = () => {
    setIsLoading(true);
    setErrorMessage(null);
    getVoices()
      .then((response) => {
        setVoices(response.voices);
        setDefaultVoiceId(response.defaultVoiceId);
      })
      .catch((error) => {
        setErrorMessage(error instanceof ApiError ? error.message : "Khong the tai danh sach voice.");
      })
      .finally(() => setIsLoading(false));
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorMessage(null);
    setSuccessMessage(null);

    try {
      const formData = new FormData(event.currentTarget);
      const response = await cloneVoice(formData);
      setSuccessMessage(`Da clone voice ${response.voice.voiceName}: ${response.voice.voiceId}`);
      event.currentTarget.reset();
      loadData();
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Clone voice that bai.");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai voices..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Voice Library"
        title="Clone va quan ly voice TTS"
        description="Danh sach voice duoc luu local trong storage/voices/index.json. API provider hien chi tra voiceId khi clone, nen app dung index local de hien thi lai."
        stats={[
          { label: "Voices cloned", value: voices.length },
          { label: "Default voice", value: defaultVoiceId || "Not set" },
          { label: "Provider", value: "Minimax" },
          { label: "Storage", value: "storage/voices" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}
      {successMessage ? <StatusAlert title="Thanh cong" message={successMessage} /> : null}

      <PageSection>
        <form className="grid gap-5" onSubmit={handleSubmit}>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="voiceName">Ten voice moi</Label>
              <Input id="voiceName" name="voiceName" placeholder="QS narrator" required />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="audio">Audio mau</Label>
              <Input id="audio" name="audio" type="file" accept=".mp3,.wav,.m4a,.aac,.flac,.ogg" required />
            </div>
          </div>
          <div className="flex justify-end">
            <Button type="submit" size="lg" disabled={isSubmitting}>
              {isSubmitting ? "Dang clone voice..." : "Clone voice"}
            </Button>
          </div>
        </form>
      </PageSection>

      {voices.length ? (
        <section className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {voices.map((voice) => (
            <PageSection key={voice.voiceId}>
              <div className="space-y-3">
                <div>
                  <div className="text-lg font-semibold">{voice.voiceName}</div>
                  <div className="break-all text-sm text-muted-foreground">{voice.voiceId}</div>
                </div>
                <div className="text-sm text-muted-foreground">Source: {voice.sourceFileName}</div>
                <div className="text-sm text-muted-foreground">Created: {voice.createdAt}</div>
              </div>
            </PageSection>
          ))}
        </section>
      ) : (
        <EmptyCard title="Chua co voice nao" description="Upload audio mau va dat ten voice de clone voice moi." />
      )}
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
        <Button asChild variant="ghost">
          <Link to="/docs-to-audio">Docs to Audio</Link>
        </Button>
        <Button asChild variant="secondary">
          <Link to="/voices">Voices</Link>
        </Button>
        <Button asChild variant="ghost">
          <Link to="/effects-library">Effects Library</Link>
        </Button>
      </div>
    </nav>
  );
}
