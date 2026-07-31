import { Loader2, Settings2, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, deleteStoryIntro, uploadStoryIntro } from "@/lib/api";
import type { StoryIntro } from "@/types/api";

export interface StoryIntroSelectProps {
  intros: StoryIntro[];
  /** "" = chưa chọn (buộc chọn), "none" = không có intro, còn lại = intro id. */
  value: string;
  onChange: (value: string) => void;
  /** Gọi sau khi upload/xóa để parent refetch danh sách intro. */
  onIntrosChanged?: () => void | Promise<void>;
  disabled?: boolean;
}

function introLabel(intro: StoryIntro): string {
  const seconds = Math.round(intro.duration || 0);
  return seconds > 0 ? `${intro.name} (${seconds}s)` : intro.name;
}

export function StoryIntroSelect({ intros, value, onChange, onIntrosChanged, disabled = false }: StoryIntroSelectProps) {
  const [manageOpen, setManageOpen] = useState(false);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const resetUploadForm = () => {
    setName("");
    setFile(null);
    setFileInputKey((key) => key + 1);
  };

  const handleUpload = async () => {
    if (!file) {
      setError("Hãy chọn file intro.");
      return;
    }
    const trimmed = name.trim() || file.name.replace(/\.[^/.]+$/, "");
    setBusy(true);
    setError(null);
    try {
      const res = await uploadStoryIntro(file, trimmed);
      resetUploadForm();
      await onIntrosChanged?.();
      onChange(res.intro.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không thêm được intro.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (introId: string) => {
    setDeletingId(introId);
    setError(null);
    try {
      await deleteStoryIntro(introId);
      if (value === introId) onChange("none");
      await onIntrosChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không xóa được intro.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 flex-1 rounded-md border border-input bg-background px-3 text-sm"
      >
        <option value="">— Chọn intro —</option>
        <option value="none">Không có intro</option>
        {intros.map((intro) => (
          <option key={intro.id} value={intro.id}>
            {introLabel(intro)}
          </option>
        ))}
      </select>

      <Button
        type="button"
        variant="outline"
        size="icon"
        disabled={disabled}
        title="Quản lý intro"
        onClick={() => {
          setError(null);
          resetUploadForm();
          setManageOpen(true);
        }}
      >
        <Settings2 className="size-4" />
      </Button>

      <Dialog
        open={manageOpen}
        onOpenChange={(open) => {
          if (!busy && !deletingId) setManageOpen(open);
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Quản lý intro video</DialogTitle>
            <DialogDescription>
              Intro là video ngắn gắn vào đầu mỗi video của batch. File sẽ được chuẩn hóa về đúng khung hình khi tải lên.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 rounded-lg border border-border/70 p-3">
            <Label className="text-xs font-medium">Thêm intro mới</Label>
            <Input
              key={fileInputKey}
              ref={fileRef}
              type="file"
              accept="video/mp4,video/quicktime,video/x-matroska,video/webm,.mp4,.mov,.mkv,.webm"
              disabled={busy}
              onChange={(event) => {
                const picked = event.target.files?.[0] ?? null;
                setFile(picked);
                if (picked && !name.trim()) setName(picked.name.replace(/\.[^/.]+$/, ""));
              }}
            />
            <Input
              value={name}
              disabled={busy}
              placeholder="Tên intro (ví dụ: Intro kênh A)"
              onChange={(event) => setName(event.target.value)}
            />
            <Button type="button" disabled={busy || !file} onClick={() => void handleUpload()}>
              {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Upload className="mr-2 size-4" />}
              Thêm intro
            </Button>
          </div>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          <div className="grid max-h-64 gap-2 overflow-y-auto">
            {intros.length === 0 ? (
              <p className="text-sm text-muted-foreground">Chưa có intro nào.</p>
            ) : (
              intros.map((intro) => (
                <div key={intro.id} className="flex items-center gap-3 rounded-md border border-border/60 p-2">
                  <video
                    src={`/media/${intro.relativePath}`}
                    className="h-14 w-24 shrink-0 rounded bg-black object-cover"
                    muted
                    preload="metadata"
                    controls
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{intro.name}</p>
                    <p className="text-xs text-muted-foreground">{Math.round(intro.duration || 0)}s</p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="text-destructive hover:text-destructive"
                    disabled={deletingId === intro.id}
                    title="Xóa intro"
                    onClick={() => void handleDelete(intro.id)}
                  >
                    {deletingId === intro.id ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Trash2 className="size-4" />
                    )}
                  </Button>
                </div>
              ))
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy || Boolean(deletingId)} onClick={() => setManageOpen(false)}>
              Đóng
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
