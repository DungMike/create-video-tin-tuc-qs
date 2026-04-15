import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import type { LibraryAsset } from "@/types/api";

export function LibraryAssetCard({
  asset,
  checked,
  onCheckedChange,
}: {
  asset: LibraryAsset;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <Card className="h-full border-border/70 bg-card/90 shadow-lg">
      <CardContent className="space-y-4 p-4">
        <video
          controls
          preload="metadata"
          src={`/media/${asset.relativePath}`}
          className="aspect-video w-full rounded-2xl bg-black object-cover"
        />

        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <Checkbox
              id={asset.assetId}
              checked={checked}
              onCheckedChange={(value) => onCheckedChange(Boolean(value))}
            />
            <Label htmlFor={asset.assetId} className="cursor-pointer text-sm font-semibold">
              {asset.sourceName || asset.assetId}
            </Label>
          </div>
          <p className="text-sm text-muted-foreground">
            {asset.start}s - {asset.end}s | {asset.duration}s
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          {asset.tags.length ? (
            asset.tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="rounded-full px-3 py-1">
                {tag}
              </Badge>
            ))
          ) : (
            <span className="text-xs text-muted-foreground">Asset chưa có tag.</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
