import { Plus, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ReviewClip } from "@/types/api";

export function ReviewClipCard({
  clip,
  checked,
  activeTags,
  suggestedTags,
  onCheckedChange,
  onToggleTag,
  onAddTag,
  onDelete,
}: {
  clip: ReviewClip;
  checked: boolean;
  activeTags: string[];
  suggestedTags: string[];
  onCheckedChange: (checked: boolean) => void;
  onToggleTag: (tag: string) => void;
  onAddTag: (tag: string) => void;
  onDelete?: () => void;
}) {
  return (
    <Card className="relative h-full border-border/70 bg-card/90 shadow-lg">
      {onDelete ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="absolute right-2 top-2 z-10 h-8 w-8 rounded-full bg-destructive/80 p-0 text-white shadow-md hover:bg-destructive hover:text-white"
          onClick={onDelete}
          title="Xoá clip"
        >
          <Trash2 className="size-4" />
        </Button>
      ) : null}
      <CardContent className="space-y-4 p-4">
        <video
          controls
          preload="metadata"
          src={`/media/${clip.relativePath}`}
          className="aspect-video w-full rounded-2xl bg-black object-cover"
        />

        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <Checkbox
              id={clip.id}
              checked={checked}
              onCheckedChange={(value) => onCheckedChange(Boolean(value))}
            />
            <Label htmlFor={clip.id} className="cursor-pointer text-sm font-semibold">
              {clip.sourceName || clip.id}
            </Label>
          </div>
          <p className="text-sm text-muted-foreground">
            {clip.start}s - {clip.end}s | {clip.duration}s
          </p>
        </div>

        <div className="space-y-3 rounded-2xl border border-border/70 bg-background/60 p-3">
          <div className="space-y-1">
            <div className="text-sm font-semibold">Tags tài nguyên</div>
            <p className="text-xs leading-5 text-muted-foreground">
              Clip có tag sẽ được lưu vào thư viện, kể cả khi không dùng ở lần render này.
            </p>
          </div>

          <div className="flex min-h-9 flex-wrap gap-2">
            {activeTags.length ? (
              activeTags.map((tag) => (
                <Button
                  key={tag}
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="rounded-full"
                  onClick={() => onToggleTag(tag)}
                >
                  {tag}
                </Button>
              ))
            ) : (
              <span className="text-xs text-muted-foreground">Chưa gắn tag.</span>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            {suggestedTags.length ? (
              suggestedTags.map((tag) => {
                const isActive = activeTags.includes(tag);
                return (
                  <Badge
                    key={tag}
                    variant={isActive ? "default" : "secondary"}
                    className="cursor-pointer rounded-full px-3 py-1"
                    onClick={() => onToggleTag(tag)}
                  >
                    {tag}
                  </Badge>
                );
              })
            ) : (
              <span className="text-xs text-muted-foreground">Chưa có tag gợi ý từ thư viện.</span>
            )}
          </div>

          <TagInput onAddTag={onAddTag} />
        </div>
      </CardContent>
    </Card>
  );
}

function TagInput({ onAddTag }: { onAddTag: (tag: string) => void }) {
  return (
    <form
      className="flex flex-col gap-2 sm:flex-row"
      onSubmit={(event) => {
        event.preventDefault();
        const formData = new FormData(event.currentTarget);
        const nextTag = String(formData.get("tag") ?? "");
        onAddTag(nextTag);
        event.currentTarget.reset();
      }}
    >
      <Input name="tag" placeholder="Tạo tag mới cho clip này" className="flex-1" />
      <Button type="submit" variant="outline" className="sm:w-auto">
        <Plus className="mr-2 size-4" />
        Thêm tag
      </Button>
    </form>
  );
}
