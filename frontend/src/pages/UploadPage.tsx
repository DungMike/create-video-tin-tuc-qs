import { startTransition, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, createJob, getAudioLibrary } from "@/lib/api";
import type { GeneratedAudio } from "@/types/api";

export function UploadPage() {
  const navigate = useNavigate();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [generatedAudios, setGeneratedAudios] = useState<GeneratedAudio[]>([]);

  useEffect(() => {
    let cancelled = false;
    getAudioLibrary()
      .then((response) => {
        if (!cancelled) {
          setGeneratedAudios(response.audios);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setGeneratedAudios([]);
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

    try {
      const formData = new FormData(event.currentTarget);
      const response = await createJob(formData);
      startTransition(() => navigate(response.redirectUrl));
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Khong the tao job moi.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <AppShell>
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
          <Button asChild variant="ghost">
            <Link to="/voices">Voices</Link>
          </Button>
          <Button asChild variant="ghost">
            <Link to="/decor-library">Decor Library</Link>
          </Button>
          <Button asChild variant="secondary">
            <Link to="/effects-library">Effects Library</Link>
          </Button>
          {/* batch-pipeline */}
          <Button asChild variant="secondary">
            <Link to="/batch-pipeline">Batch Pipeline</Link>
          </Button>
        </div>
      </nav>

      <HeroCard
        eyebrow="Workflow moi"
        title="Tai audio, anh va video nguon de review clip truoc khi render"
        description="UI da chuyen sang React de ban thao tac upload, review, gan tag va chon tai nguyen thu vien de kiem soat hon, trong khi pipeline Python phia sau van giu nguyen."
        stats={[
          { label: "Stack UI", value: "React + Vite" },
          { label: "Styling", value: "Tailwind + shadcn/ui" },
          { label: "Nguon media", value: "Upload + YouTube" },
          { label: "Flow", value: "Review -> Tag -> Render" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Tao job that bai" message={errorMessage} variant="destructive" /> : null}

      <PageSection>
        <form className="grid gap-6" onSubmit={handleSubmit}>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="audio">Upload audio</Label>
              <Input id="audio" name="audio" type="file" accept=".mp3,.wav,.m4a,.aac,.flac,.ogg" />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="existingAudioRelativePath">Audio da tao tu Docs</Label>
              <select
                id="existingAudioRelativePath"
                name="existingAudioRelativePath"
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                defaultValue=""
              >
                <option value="">Khong chon audio co san</option>
                {generatedAudios.map((audio) => (
                  <option key={audio.relativePath} value={audio.relativePath}>
                    {audio.name} ({audio.duration}s)
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="images">Anh nguon</Label>
              <Input id="images" name="images" type="file" accept=".jpg,.jpeg,.png,.webp" multiple />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="videos">Video upload</Label>
              <Input id="videos" name="videos" type="file" accept=".mp4,.mov,.mkv,.webm" multiple />
            </div>
          </div>

          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="youtube_links">Link video YouTube</Label>
              <Textarea
                id="youtube_links"
                name="youtube_links"
                rows={8}
                placeholder="Moi dong mot link YouTube"
                className="min-h-[160px]"
              />
            </div>
          </div>

          <Separator />

          <div className="flex flex-wrap items-center justify-between gap-4">
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
              Co the upload audio moi hoac chon audio da tao tu Google Docs. Neu ca hai cung co, backend uu tien file upload.
            </p>
            <Button type="submit" size="lg" disabled={isSubmitting}>
              {isSubmitting ? "Dang xu ly..." : "Xu ly nguon va sang man review"}
            </Button>
          </div>
        </form>
      </PageSection>
    </AppShell>
  );
}
