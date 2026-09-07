import { useRef, useState } from "react";
import type { CSSProperties } from "react";

import {
  FRAME_H,
  FRAME_W,
  boxOrigin,
  clampPlacement,
  cornerToPlacement,
  type OverlayCorner,
  type PlacementBox,
} from "@/lib/overlayPlacement";

/**
 * Drag an overlay (waveform, CTA) anywhere on the output frame.
 *
 * Coordinates are always the output frame's own pixels, never screen pixels, so
 * what the editor stores is exactly what the render receives. The geometry lives
 * in `@/lib/overlayPlacement`, shared with the settings page's number inputs.
 */
function boxStyle(box: PlacementBox): CSSProperties {
  const { x, y } = boxOrigin(box);
  return {
    left: `${(x / FRAME_W) * 100}%`,
    top: `${(y / FRAME_H) * 100}%`,
    width: `${(box.width / FRAME_W) * 100}%`,
    height: `${(box.height / FRAME_H) * 100}%`,
  };
}

const CORNER_BUTTONS: { corner: OverlayCorner; className: string; title: string }[] = [
  { corner: "top_left", className: "left-1 top-1", title: "Góc trên-trái" },
  { corner: "top_right", className: "right-1 top-1", title: "Góc trên-phải" },
  { corner: "bottom_left", className: "bottom-1 left-1", title: "Góc dưới-trái" },
  { corner: "bottom_right", className: "bottom-1 right-1", title: "Góc dưới-phải" },
];

interface StoryOverlayPlacementEditorProps {
  boxes: PlacementBox[];
  /** Called with frame-space coordinates for the active overlay. */
  onMove: (position: { x: number; y: number }) => void;
}

export function StoryOverlayPlacementEditor({ boxes, onMove }: StoryOverlayPlacementEditorProps) {
  const frameRef = useRef<HTMLDivElement>(null);
  // Grab offset inside the box, so the overlay does not jump its own top-left
  // under the cursor the moment a drag starts.
  const dragRef = useRef<{ offsetX: number; offsetY: number } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [brokenPreviews, setBrokenPreviews] = useState<Record<string, boolean>>({});

  const active = boxes.find((box) => box.active);

  const toFrameCoords = (event: { clientX: number; clientY: number }) => {
    const rect = frameRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return { x: 0, y: 0 };
    return {
      x: (event.clientX - rect.left) * (FRAME_W / rect.width),
      y: (event.clientY - rect.top) * (FRAME_H / rect.height),
    };
  };

  const startDrag = (event: React.MouseEvent) => {
    if (!active) return;
    event.preventDefault();
    const point = toFrameCoords(event);
    const origin = boxOrigin(active);
    dragRef.current = { offsetX: point.x - origin.x, offsetY: point.y - origin.y };
    setIsDragging(true);
  };

  const handleMouseMove = (event: React.MouseEvent) => {
    const drag = dragRef.current;
    if (!drag || !active) return;
    const point = toFrameCoords(event);
    onMove(
      clampPlacement(
        point.x - drag.offsetX,
        point.y - drag.offsetY,
        active.width,
        active.height,
      ),
    );
  };

  const endDrag = () => {
    dragRef.current = null;
    setIsDragging(false);
  };

  return (
    <div className="grid gap-1">
      <div
        ref={frameRef}
        className="relative aspect-video w-full select-none overflow-hidden rounded-lg border border-border/70 bg-zinc-800"
        onMouseMove={handleMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
      >
        <div className="absolute inset-0 flex items-center justify-center text-xs text-zinc-500">
          Khung video 16:9
        </div>

        {boxes.map((box) => {
          const canPreview = Boolean(box.previewPath) && !brokenPreviews[box.label];
          return (
            <div
              key={box.label}
              style={{ ...boxStyle(box), cursor: box.active ? (isDragging ? "grabbing" : "grab") : "default" }}
              onMouseDown={box.active ? startDrag : undefined}
              className={`absolute flex items-center justify-center overflow-hidden rounded border text-[10px] font-medium text-white/90 ${box.className} ${
                box.active ? "z-10 ring-1 ring-white/60" : "opacity-40"
              }`}
            >
              {canPreview ? (
                <video
                  src={`/media/${box.previewPath}`}
                  autoPlay
                  loop
                  muted
                  playsInline
                  className="pointer-events-none h-full w-full object-contain"
                  onError={() => setBrokenPreviews((current) => ({ ...current, [box.label]: true }))}
                />
              ) : (
                <span className="pointer-events-none truncate px-1">{box.label}</span>
              )}
            </div>
          );
        })}

        {CORNER_BUTTONS.map(({ corner, className, title }) => (
          <button
            key={corner}
            type="button"
            title={`Đưa "${active?.label ?? "overlay"}" về ${title.toLowerCase()}`}
            onClick={() =>
              active && onMove(cornerToPlacement(corner, active.margin, active.width, active.height))
            }
            className={`absolute z-20 size-5 rounded border border-dashed border-white/40 bg-white/10 ${className} hover:bg-white/30`}
          />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        Kéo overlay sáng để đặt vào bất kỳ chỗ nào trong khung 1920×1080; overlay mờ là lớp còn lại,
        hiện ra để canh cho khỏi chồng nhau. Bốn ô vuông ở góc là lối tắt về góc theo Margin đang nhập.
      </p>
    </div>
  );
}
