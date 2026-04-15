import { startTransition, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { StatusAlert } from "@/components/status-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { createJob, ApiError } from "@/lib/api";

export function UploadPage() {
  const navigate = useNavigate();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      const formData = new FormData(event.currentTarget);
      const response = await createJob(formData);
      startTransition(() => navigate(response.redirectUrl));
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Không thể tạo job mới.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <AppShell>
      <HeroCard
        eyebrow="Workflow mới"
        title="Tải audio, ảnh và video nguồn để review clip trước khi render"
        description="UI đã chuyển sang React để bạn thao tác upload, review, gắn tag và chọn tài nguyên thư viện dễ kiểm soát hơn, trong khi pipeline Python phía sau vẫn giữ nguyên."
        stats={[
          { label: "Stack UI", value: "React + Vite" },
          { label: "Styling", value: "Tailwind + shadcn/ui" },
          { label: "Nguồn media", value: "Upload + YouTube" },
          { label: "Flow", value: "Review → Tag → Render" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Tạo job thất bại" message={errorMessage} variant="destructive" /> : null}

      <PageSection>
        <form className="grid gap-6" onSubmit={handleSubmit}>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="audio">Audio</Label>
              <Input id="audio" name="audio" type="file" accept=".mp3,.wav,.m4a,.aac,.flac,.ogg" required />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="images">Ảnh nguồn</Label>
              <Input id="images" name="images" type="file" accept=".jpg,.jpeg,.png,.webp" multiple />
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="videos">Video upload</Label>
              <Input id="videos" name="videos" type="file" accept=".mp4,.mov,.mkv,.webm" multiple />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="youtube_links">Link video YouTube</Label>
              <Textarea
                id="youtube_links"
                name="youtube_links"
                rows={8}
                placeholder="Mỗi dòng một link YouTube"
                className="min-h-[160px]"
              />
            </div>
          </div>

          <Separator />

          <div className="flex flex-wrap items-center justify-between gap-4">
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
              Hệ thống sẽ tạo job mới trong storage, cắt clip review từ video nguồn, và chuyển bạn sang màn review để chọn clip hoặc gắn tag lưu thư viện.
            </p>
            <Button type="submit" size="lg" disabled={isSubmitting}>
              {isSubmitting ? "Đang xử lý..." : "Xử lý nguồn và sang màn review"}
            </Button>
          </div>
        </form>
      </PageSection>
    </AppShell>
  );
}
