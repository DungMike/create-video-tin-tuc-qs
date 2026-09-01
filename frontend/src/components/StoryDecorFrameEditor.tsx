import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Crosshair, Image as ImageIcon, Loader2, Save, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { StoryDecorFrame, StoryDecorImage } from "@/types/api";

const FRAME_W = 1920;
const FRAME_H = 1080;
const HANDLE = 26; // hit radius, in frame px
const MIN_SIDE = 64;

type Handle = "nw" | "ne" | "sw" | "se" | "move";

interface StoryDecorFrameEditorProps {
  image: StoryDecorImage;
  /** Still composed by the backend (library frame fitted into this decor frame). */
  previewPath: string | null;
  isPreviewLoading?: boolean;
  isSaving?: boolean;
  onRequestPreview: () => void;
  onDetectFrame: () => Promise<StoryDecorFrame | null>;
  onSave: (updates: Partial<StoryDecorImage>) => Promise<void>;
}

function clampFrame(frame: StoryDecorFrame): StoryDecorFrame {
  const w = Math.max(MIN_SIDE, Math.min(Math.round(frame.w), FRAME_W));
  const h = Math.max(MIN_SIDE, Math.min(Math.round(frame.h), FRAME_H));
  return {
    w,
    h,
    x: Math.max(0, Math.min(Math.round(frame.x), FRAME_W - w)),
    y: Math.max(0, Math.min(Math.round(frame.y), FRAME_H - h)),
  };
}

/** True when the rectangle matches the output aspect, i.e. nothing is wasted. */
function isSourceAspect(frame: StoryDecorFrame): boolean {
  if (frame.h <= 0) return false;
  return Math.abs(frame.w / frame.h - FRAME_W / FRAME_H) < 0.01;
}

/**
 * How far the 16:9 video spills past the frame on each side.
 *
 * The video is never squeezed to the frame's shape — it is scaled until it
 * covers the frame and the overhang is left on the canvas, where the decor
 * photo's opaque pixels hide it. So an off-aspect frame costs a little wasted
 * video, never a distorted picture and never the slow CPU render path.
 */
function bleed(frame: StoryDecorFrame): { x: number; y: number } {
  if (frame.h <= 0 || frame.w <= 0) return { x: 0, y: 0 };
  const scale = Math.max(frame.w / FRAME_W, frame.h / FRAME_H);
  return {
    x: Math.round((FRAME_W * scale - frame.w) / 2),
    y: Math.round((FRAME_H * scale - frame.h) / 2),
  };
}

export function StoryDecorFrameEditor({
  image,
  previewPath,
  isPreviewLoading = false,
  isSaving = false,
  onRequestPreview,
  onDetectFrame,
  onSave,
}: StoryDecorFrameEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<{ handle: Handle; startX: number; startY: number; origin: StoryDecorFrame } | null>(null);

  const [frame, setFrame] = useState<StoryDecorFrame>(() => clampFrame(image.frame));
  const [overscan, setOverscan] = useState(String(image.overscan ?? 0.01));
  const [keyColor, setKeyColor] = useState(image.keyColor ?? "0x00b140");
  const [similarity, setSimilarity] = useState(String(image.similarity ?? 0.15));
  const [blend, setBlend] = useState(String(image.blend ?? 0.05));
  const [lockAspect, setLockAspect] = useState(() => isSourceAspect(image.frame));
  const [activeHandle, setActiveHandle] = useState<Handle | null>(null);
  const [isDetecting, setIsDetecting] = useState(false);
  const [decorBitmap, setDecorBitmap] = useState<HTMLImageElement | null>(null);
  const [previewBitmap, setPreviewBitmap] = useState<HTMLImageElement | null>(null);

  // Reload every local control when the parent selects a different decor image.
  // Keyed on identity + save time only: re-syncing on every individual field
  // would fight the user mid-edit.
  useEffect(() => {
    setFrame(clampFrame(image.frame));
    setOverscan(String(image.overscan ?? 0.01));
    setKeyColor(image.keyColor ?? "0x00b140");
    setSimilarity(String(image.similarity ?? 0.15));
    setBlend(String(image.blend ?? 0.05));
    setLockAspect(isSourceAspect(image.frame));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image.id, image.updatedAt]);

  // The keyed PNG is what the render actually overlays, so the editor draws that
  // one (not the original upload) — the transparent hole IS the alignment target.
  useEffect(() => {
    if (!image.processedRelativePath) return;
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = `/media/${image.processedRelativePath}?t=${encodeURIComponent(image.updatedAt ?? "")}`;
    img.onload = () => setDecorBitmap(img);
    return () => {
      img.onload = null;
    };
  }, [image.processedRelativePath, image.updatedAt]);

  useEffect(() => {
    if (!previewPath) {
      setPreviewBitmap(null);
      return;
    }
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = `/media/${previewPath}`;
    img.onload = () => setPreviewBitmap(img);
    return () => {
      img.onload = null;
    };
  }, [previewPath]);

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    ctx.clearRect(0, 0, FRAME_W, FRAME_H);

    // 1. What plays under the decor. The backend still already has the decor
    // composited on it, so it doubles as the ground truth for the alignment.
    if (previewBitmap) {
      ctx.drawImage(previewBitmap, 0, 0, FRAME_W, FRAME_H);
    } else {
      // No still yet: a checkerboard makes the keyed-out hole obvious.
      const size = 60;
      for (let y = 0; y < FRAME_H; y += size) {
        for (let x = 0; x < FRAME_W; x += size) {
          ctx.fillStyle = ((x / size + y / size) % 2 === 0) ? "#2a2a33" : "#20202a";
          ctx.fillRect(x, y, size, size);
        }
      }
      ctx.fillStyle = "rgba(56,189,248,0.18)";
      ctx.fillRect(frame.x, frame.y, frame.w, frame.h);
    }

    // 2. The decor photo on top — only drawn when there is no composed still,
    // otherwise it would be painted twice.
    if (!previewBitmap && decorBitmap) {
      ctx.drawImage(decorBitmap, 0, 0, FRAME_W, FRAME_H);
    }

    // 3. The draggable rectangle.
    ctx.save();
    ctx.strokeStyle = "#22d3ee";
    ctx.lineWidth = 4;
    ctx.setLineDash([14, 10]);
    ctx.strokeRect(frame.x, frame.y, frame.w, frame.h);
    ctx.setLineDash([]);

    for (const [hx, hy] of [
      [frame.x, frame.y],
      [frame.x + frame.w, frame.y],
      [frame.x, frame.y + frame.h],
      [frame.x + frame.w, frame.y + frame.h],
    ]) {
      ctx.fillStyle = "#22d3ee";
      ctx.fillRect(hx - 11, hy - 11, 22, 22);
      ctx.fillStyle = "#0b1220";
      ctx.fillRect(hx - 5, hy - 5, 10, 10);
    }

    const label = `${frame.w} x ${frame.h}  @  ${frame.x}, ${frame.y}`;
    ctx.font = "600 30px system-ui, sans-serif";
    const width = ctx.measureText(label).width;
    const boxY = frame.y > 60 ? frame.y - 52 : frame.y + frame.h + 12;
    ctx.fillStyle = "rgba(3,7,18,0.78)";
    ctx.fillRect(frame.x, boxY, width + 26, 42);
    ctx.fillStyle = "#e2e8f0";
    ctx.fillText(label, frame.x + 13, boxY + 30);
    ctx.restore();
  }, [frame, decorBitmap, previewBitmap]);

  useEffect(() => {
    drawCanvas();
  }, [drawCanvas]);

  const toFrameCoords = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * (FRAME_W / rect.width),
      y: (event.clientY - rect.top) * (FRAME_H / rect.height),
    };
  };

  const hitTest = (x: number, y: number): Handle | null => {
    const corners: [Handle, number, number][] = [
      ["nw", frame.x, frame.y],
      ["ne", frame.x + frame.w, frame.y],
      ["sw", frame.x, frame.y + frame.h],
      ["se", frame.x + frame.w, frame.y + frame.h],
    ];
    for (const [handle, cx, cy] of corners) {
      if (Math.abs(x - cx) <= HANDLE && Math.abs(y - cy) <= HANDLE) return handle;
    }
    const inside =
      x >= frame.x && x <= frame.x + frame.w && y >= frame.y && y <= frame.y + frame.h;
    return inside ? "move" : null;
  };

  const handleMouseDown = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const { x, y } = toFrameCoords(event);
    const handle = hitTest(x, y);
    if (!handle) return;
    dragRef.current = { handle, startX: x, startY: y, origin: frame };
    setActiveHandle(handle);
  };

  const handleMouseMove = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    const { x, y } = toFrameCoords(event);
    if (!drag) {
      setActiveHandle(hitTest(x, y));
      return;
    }

    const dx = x - drag.startX;
    const dy = y - drag.startY;
    const origin = drag.origin;

    if (drag.handle === "move") {
      setFrame(clampFrame({ ...origin, x: origin.x + dx, y: origin.y + dy }));
      return;
    }

    // Resize from the corner opposite the one being dragged, so that corner
    // stays pinned to the photo while the rectangle grows.
    const anchorX = drag.handle === "nw" || drag.handle === "sw" ? origin.x + origin.w : origin.x;
    const anchorY = drag.handle === "nw" || drag.handle === "ne" ? origin.y + origin.h : origin.y;
    let width = Math.abs(x - anchorX);
    let height = Math.abs(y - anchorY);

    if (lockAspect) {
      // Take whichever axis moved more, so the drag never feels sticky.
      if (width / (FRAME_W / FRAME_H) > height) height = width / (FRAME_W / FRAME_H);
      else width = height * (FRAME_W / FRAME_H);
    }

    width = Math.max(MIN_SIDE, width);
    height = Math.max(MIN_SIDE, height);
    const nextX = drag.handle === "nw" || drag.handle === "sw" ? anchorX - width : anchorX;
    const nextY = drag.handle === "nw" || drag.handle === "ne" ? anchorY - height : anchorY;
    setFrame(clampFrame({ x: nextX, y: nextY, w: width, h: height }));
  };

  const endDrag = () => {
    dragRef.current = null;
    setActiveHandle(null);
  };

  const handleLockAspect = (next: boolean) => {
    setLockAspect(next);
    if (next) {
      // Snap to the source aspect around the current centre.
      const height = Math.round(frame.w / (FRAME_W / FRAME_H));
      setFrame(
        clampFrame({
          x: frame.x,
          y: frame.y + Math.round((frame.h - height) / 2),
          w: frame.w,
          h: height,
        }),
      );
    }
  };

  const handleDetect = async () => {
    setIsDetecting(true);
    try {
      const detected = await onDetectFrame();
      if (detected) {
        setFrame(clampFrame(detected));
        setLockAspect(isSourceAspect(detected));
      }
    } finally {
      setIsDetecting(false);
    }
  };

  const cursor = useMemo(() => {
    if (activeHandle === "move") return dragRef.current ? "grabbing" : "grab";
    if (activeHandle === "nw" || activeHandle === "se") return "nwse-resize";
    if (activeHandle === "ne" || activeHandle === "sw") return "nesw-resize";
    return "default";
  }, [activeHandle]);

  const onAspect = isSourceAspect(frame);
  const spill = bleed(frame);

  return (
    <div className="grid gap-4">
      <div
        className="relative w-full overflow-hidden rounded-lg border border-border/60 bg-black"
        style={{ aspectRatio: "16 / 9" }}
      >
        <canvas
          ref={canvasRef}
          width={FRAME_W}
          height={FRAME_H}
          className="h-full w-full"
          style={{ cursor }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={endDrag}
          onMouseLeave={endDrag}
        />
        {isPreviewLoading ? (
          <div className="absolute inset-0 flex items-center justify-center bg-background/60">
            <Loader2 className="size-6 animate-spin text-primary" />
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">
          {onAspect
            ? "Khung đúng 16:9 — không phí phần nào"
            : `Video giữ 16:9, ảnh decor che ${spill.x}px ngang / ${spill.y}px dọc`}
        </Badge>
        {image.autoDetected === false ? (
          <Badge variant="outline">Chưa dò được vùng xanh — căn tay</Badge>
        ) : null}
        <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={lockAspect}
            onChange={(event) => handleLockAspect(event.currentTarget.checked)}
          />
          Khoá tỉ lệ 16:9 khi kéo
        </label>
      </div>

      <p className="text-xs text-muted-foreground">
        💡 Kéo bên trong khung để dời, kéo 4 góc để co giãn. Video nền <strong>luôn giữ đúng tỉ
        lệ 16:9</strong>, không bao giờ bị bóp méo: nó được phóng to tới khi phủ kín khung, phần
        thừa tràn ra ngoài và bị ảnh decor đè lên che mất. Vì vậy khung không cần đúng 16:9 —
        lệch tỉ lệ chỉ tốn một ít video bị che, không làm chậm render. Cho khung trùm ra ngoài
        vùng xanh một chút để chắc chắn không hở viền.
      </p>

      <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-4">
        {(
          [
            ["x", "X (px)", FRAME_W],
            ["y", "Y (px)", FRAME_H],
            ["w", "Rộng (px)", FRAME_W],
            ["h", "Cao (px)", FRAME_H],
          ] as const
        ).map(([key, label, max]) => (
          <div key={key} className="grid gap-1">
            <Label htmlFor={`decor-${key}`}>{label}</Label>
            <Input
              id={`decor-${key}`}
              type="number"
              min={0}
              max={max}
              value={frame[key]}
              onChange={(event) =>
                setFrame(clampFrame({ ...frame, [key]: Number(event.currentTarget.value) }))
              }
            />
          </div>
        ))}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-4">
        <div className="grid gap-1">
          <Label htmlFor="decor-overscan">Overscan (0 - 0.25)</Label>
          <Input
            id="decor-overscan"
            type="number"
            step="0.01"
            min={0}
            max={0.25}
            value={overscan}
            onChange={(event) => setOverscan(event.currentTarget.value)}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="decor-key">Màu nền xanh</Label>
          <div className="flex items-center gap-2">
            <input
              type="color"
              className="h-10 w-12 cursor-pointer rounded-md border border-input bg-background"
              value={`#${keyColor.replace(/^0x/i, "").padStart(6, "0")}`}
              onChange={(event) => setKeyColor(`0x${event.currentTarget.value.slice(1)}`)}
            />
            <Input value={keyColor} onChange={(event) => setKeyColor(event.currentTarget.value)} />
          </div>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="decor-similarity">Similarity</Label>
          <Input
            id="decor-similarity"
            type="number"
            step="0.01"
            min={0.01}
            max={0.6}
            value={similarity}
            onChange={(event) => setSimilarity(event.currentTarget.value)}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="decor-blend">Blend</Label>
          <Input
            id="decor-blend"
            type="number"
            step="0.01"
            min={0}
            max={0.4}
            value={blend}
            onChange={(event) => setBlend(event.currentTarget.value)}
          />
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        Similarity + Blend càng lớn càng tách mạnh, nhưng vượt ~0.20 sẽ bắt đầu đục thủng cả
        phần phòng (gỗ, da, lá cây có màu gần xanh). Nếu viền màn hình còn sót xanh, tăng
        Similarity từng 0.02 rồi bấm Lưu để tách lại.
      </p>

      <div className="flex flex-wrap gap-2">
        <Button
          onClick={() =>
            void onSave({
              frame,
              overscan: Number(overscan),
              keyColor,
              similarity: Number(similarity),
              blend: Number(blend),
            })
          }
          disabled={isSaving}
        >
          {isSaving ? (
            <Loader2 className="mr-2 size-4 animate-spin" />
          ) : (
            <Save className="mr-2 size-4" />
          )}
          Lưu khung
        </Button>
        <Button variant="outline" onClick={() => void handleDetect()} disabled={isDetecting}>
          {isDetecting ? (
            <Loader2 className="mr-2 size-4 animate-spin" />
          ) : (
            <Wand2 className="mr-2 size-4" />
          )}
          Tự động dò vùng xanh
        </Button>
        <Button variant="outline" onClick={onRequestPreview} disabled={isPreviewLoading}>
          {isPreviewLoading ? (
            <Loader2 className="mr-2 size-4 animate-spin" />
          ) : (
            <ImageIcon className="mr-2 size-4" />
          )}
          Xem thử khung (ảnh tĩnh)
        </Button>
        <Button
          variant="ghost"
          onClick={() => setFrame(clampFrame({ x: 0, y: 0, w: FRAME_W, h: FRAME_H }))}
        >
          <Crosshair className="mr-2 size-4" />
          Reset toàn khung
        </Button>
      </div>
    </div>
  );
}
