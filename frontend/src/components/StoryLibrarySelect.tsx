import { FolderPlus, Loader2, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
import { ApiError, createStoryLibrary, deleteStoryLibrary, renameStoryLibrary } from "@/lib/api";
import type { StoryLibrary } from "@/types/api";

export interface StoryLibrarySelectProps {
  libraries: StoryLibrary[];
  value: string;
  onChange: (libraryId: string) => void;
  /** Called after a create/rename/delete so the parent can re-fetch the list. */
  onLibrariesChanged?: () => void | Promise<void>;
  /** Show the create/rename/delete controls (false = picker only). */
  manage?: boolean;
  disabled?: boolean;
}

export function StoryLibrarySelect({
  libraries,
  value,
  onChange,
  onLibrariesChanged,
  manage = false,
  disabled = false,
}: StoryLibrarySelectProps) {
  const [createOpen, setCreateOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = libraries.find((lib) => lib.id === value);

  const handleCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Tên thư viện không được để trống.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await createStoryLibrary({ name: trimmed });
      setCreateOpen(false);
      setName("");
      // Refresh the library list BEFORE selecting the new id so the shared
      // active-library hook already knows about it. Otherwise there is an async
      // window where the persisted id points at a library not yet in the list,
      // and the hook silently falls back to (and re-persists) the default.
      await onLibrariesChanged?.();
      onChange(res.library.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không tạo được thư viện.");
    } finally {
      setBusy(false);
    }
  };

  const handleRename = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Tên thư viện không được để trống.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await renameStoryLibrary(value, { name: trimmed });
      setRenameOpen(false);
      onLibrariesChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không đổi tên được thư viện.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await deleteStoryLibrary(value);
      setDeleteOpen(false);
      // New selection after removing the current one: the newly-promoted default (when
      // we deleted the default), else the existing default, else any remaining library,
      // else none (last library deleted -> empty picker).
      const remaining = libraries.filter((lib) => lib.id !== value);
      const next =
        res.newDefaultLibraryId ||
        remaining.find((lib) => lib.isDefault)?.id ||
        remaining[0]?.id ||
        "";
      onChange(next);
      onLibrariesChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Không xóa được thư viện.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <Label className="whitespace-nowrap text-xs text-muted-foreground">Thư viện</Label>
      <select
        value={value}
        disabled={disabled || libraries.length === 0}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 rounded-md border border-input bg-background px-3 text-sm"
      >
        {libraries.map((lib) => (
          <option key={lib.id} value={lib.id}>
            {lib.styled ? "🎞 " : ""}
            {lib.name}
            {lib.isDefault ? " (mặc định)" : ""}
            {lib.styled && lib.styleLabel ? ` [${lib.styleLabel}]` : ""} · {lib.clipCount}
          </option>
        ))}
      </select>

      {manage ? (
        <>
          <Button
            type="button"
            variant="outline"
            size="icon"
            disabled={disabled}
            title="Tạo thư viện mới"
            onClick={() => {
              setName("");
              setError(null);
              setCreateOpen(true);
            }}
          >
            <FolderPlus className="size-4" />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            disabled={disabled || !active}
            title="Đổi tên thư viện"
            onClick={() => {
              setName(active?.name ?? "");
              setError(null);
              setRenameOpen(true);
            }}
          >
            <Pencil className="size-4" />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="text-destructive hover:text-destructive"
            disabled={disabled || !active}
            title="Xóa thư viện"
            onClick={() => {
              setError(null);
              setDeleteOpen(true);
            }}
          >
            <Trash2 className="size-4" />
          </Button>
        </>
      ) : null}

      {/* Create */}
      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          if (!busy) setCreateOpen(open);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Tạo thư viện mới</DialogTitle>
            <DialogDescription>Đặt tên cho thư viện video theo chủ đề (ví dụ: Thành phố, Đồng quê).</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="new-library-name" className="text-xs">
              Tên thư viện
            </Label>
            <Input
              id="new-library-name"
              value={name}
              autoFocus
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleCreate();
              }}
              placeholder="Thành phố"
            />
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={() => setCreateOpen(false)}>
              Hủy
            </Button>
            <Button type="button" disabled={busy} onClick={() => void handleCreate()}>
              {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
              Tạo
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Rename */}
      <Dialog
        open={renameOpen}
        onOpenChange={(open) => {
          if (!busy) setRenameOpen(open);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Đổi tên thư viện</DialogTitle>
            <DialogDescription>Chỉ đổi nhãn hiển thị, không ảnh hưởng tới clip bên trong.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="rename-library-name" className="text-xs">
              Tên thư viện
            </Label>
            <Input
              id="rename-library-name"
              value={name}
              autoFocus
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleRename();
              }}
            />
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={() => setRenameOpen(false)}>
              Hủy
            </Button>
            <Button type="button" disabled={busy} onClick={() => void handleRename()}>
              {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
              Lưu
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete */}
      <AlertDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          if (!busy) setDeleteOpen(open);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Xóa thư viện "{active?.name}"?</AlertDialogTitle>
            <AlertDialogDescription>
              {active && active.clipCount > 0
                ? `Thư viện này có ${active.clipCount} clip — xóa sẽ xóa toàn bộ clip bên trong và không thể hoàn tác.`
                : "Thao tác này không thể hoàn tác."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Hủy</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={busy}
              onClick={(event) => {
                event.preventDefault();
                void handleDelete();
              }}
            >
              {busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Trash2 className="mr-2 size-4" />}
              Xóa thư viện
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
