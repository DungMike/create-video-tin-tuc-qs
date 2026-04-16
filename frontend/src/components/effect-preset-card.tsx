import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

type BasePreset = {
  id: string;
  name: string;
  description: string;
  previewRelativePath: string;
  active: boolean;
};

export function EffectPresetCard({
  preset,
  checked,
  meta,
  onCheckedChange,
}: {
  preset: BasePreset;
  checked: boolean;
  meta: React.ReactNode;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <Card className="h-full border-border/70 bg-card/90 shadow-lg">
      <CardContent className="space-y-4 p-4">
        <video
          controls
          preload="metadata"
          src={`/media/${preset.previewRelativePath}`}
          className="aspect-video w-full rounded-2xl bg-black object-cover"
        />

        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <Checkbox
              id={preset.id}
              checked={checked}
              onCheckedChange={(value) => onCheckedChange(Boolean(value))}
            />
            <Label htmlFor={preset.id} className="cursor-pointer text-sm font-semibold">
              {preset.name}
            </Label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={checked ? "default" : "secondary"} className="rounded-full">
              {checked ? "Active" : "Inactive"}
            </Badge>
            <Badge variant="outline" className="rounded-full">
              {preset.id}
            </Badge>
          </div>
          <p className="text-sm leading-6 text-muted-foreground">{preset.description}</p>
        </div>

        <div className="rounded-2xl border border-border/70 bg-background/70 p-3 text-xs leading-5 text-muted-foreground">
          {meta}
        </div>
      </CardContent>
    </Card>
  );
}
