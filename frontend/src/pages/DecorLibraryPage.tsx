import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { EmptyCard } from "@/components/empty-card";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, deleteDecorVideoApi, getDecorVideos, uploadDecorVideo } from "@/lib/api";
import type { DecorVideo } from "@/types/api";

export function DecorLibraryPage() {
  const [decorVideos, setDecorVideos] = useState<DecorVideo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const loadData = () => {
    setIsLoading(true);
    setErrorMessage(null);
    getDecorVideos()
      .then((response) => {
        setDecorVideos(response.decorVideos);
      })
      .catch((error) => {
        setErrorMessage(error instanceof ApiError ? error.message : "Khong the tai danh sach decor video.");
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
      const response = await uploadDecorVideo(formData);
      setSuccessMessage(`Da upload decor video: ${response.decorVideo.name} (${response.decorVideo.durationSeconds}s)`);
      event.currentTarget.reset();
      loadData();
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Upload decor video that bai.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (decorId: string) => {
    setErrorMessage(null);
    setSuccessMessage(null);
    try {
      await deleteDecorVideoApi(decorId);
      setSuccessMessage("Da xoa decor video.");
      loadData();
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Xoa decor video that bai.");
    }
  };

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai decor video library..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Decor Video Library"
        title="Upload va quan ly video decor (PiP overlay)"
        description="Video decor se duoc hien thi o goc man hinh (PiP) khi render. Video decor se tu dong loop neu ngan hon audio va duoc mute tieng."
        stats={[
          { label: "Videos uploaded", value: decorVideos.length },
          { label: "Vi tri mac dinh", value: "Top right" },
          { label: "Scale", value: "25%" },
          { label: "Storage", value: "storage/decor_videos" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Co loi xay ra" message={errorMessage} variant="destructive" /> : null}
      {successMessage ? <StatusAlert title="Thanh cong" message={successMessage} /> : null}

      <PageSection>
        <form className="grid gap-5" onSubmit={handleSubmit}>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="name">Ten decor video</Label>
              <Input id="name" name="name" placeholder="MC goc khuat" required />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="video">File video</Label>
              <Input id="video" name="video" type="file" accept=".mp4,.mov,.mkv,.webm" required />
            </div>
          </div>
          <div className="flex justify-end">
            <Button type="submit" size="lg" disabled={isSubmitting}>
              {isSubmitting ? "Dang upload..." : "Upload decor video"}
            </Button>
          </div>
        </form>
      </PageSection>

      {decorVideos.length ? (
        <section className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {decorVideos.map((dv) => (
            <PageSection key={dv.id}>
              <div className="space-y-3">
                <div>
                  <div className="text-lg font-semibold">{dv.name}</div>
                  <div className="break-all text-sm text-muted-foreground">{dv.filename}</div>
                </div>
                <div className="text-sm text-muted-foreground">Duration: {dv.durationSeconds}s</div>
                <div className="text-sm text-muted-foreground">Created: {dv.createdAt}</div>
                <div className="flex gap-2">
                  <Button variant="destructive" size="sm" onClick={() => handleDelete(dv.id)}>
                    Xoa
                  </Button>
                </div>
              </div>
            </PageSection>
          ))}
        </section>
      ) : (
        <EmptyCard title="Chua co decor video" description="Upload video decor de su dung lam PiP overlay khi render." />
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
        <Button asChild variant="ghost">
          <Link to="/voices">Voices</Link>
        </Button>
        <Button asChild variant="secondary">
          <Link to="/decor-library">Decor Library</Link>
        </Button>
        <Button asChild variant="ghost">
          <Link to="/effects-library">Effects Library</Link>
        </Button>
      </div>
    </nav>
  );
}
