import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Crosshair, Image as ImageIcon, Loader2, Save, SquareDashed, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { StoryDecorFrame, StoryDecorImage, StoryDecorMaskMode } from "@/types/api";

const FRAME_W = 1920;
const FRAME_H = 1080;
const ASPECT = FRAME_W / FRAME_H;
const HANDLE = 26; // hit radius, in frame px
const MIN_SIDE = 64;
/** Config.STORY_DECOR_KEY_COLOR, only ever drawn as a hint on the canvas. */
const CHROMA_GREEN = "0,177,64";

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

/**
 * Keep the rectangle inside the canvas, and — when locked — exactly 16:9.
 *
 * Width and height are one number under a locked ratio, so they have to be
 * clamped *together*: capping each against its own axis is what silently
 * flattens the rectangle the moment a drag reaches the edge of the frame.
 */
function clampFrame(frame: StoryDecorFrame, lockAspect = false): StoryDecorFrame {
  let w: number;
  let h: number;

  if (lockAspect) {
    w = Math.max(MIN_SIDE, Math.round(frame.w));
    h = Math.round(w / ASPECT);
    // One shrink factor for both sides preserves the ratio exactly.
    const shrink = Math.min(FRAME_W / w, FRAME_H / h, 1);
    w = Math.max(MIN_SIDE, Math.round(w * shrink));
    h = Math.round(w / ASPECT);
  } else {
    w = Math.max(MIN_SIDE, Math.min(Math.round(frame.w), FRAME_W));
    h = Math.max(MIN_SIDE, Math.min(Math.round(frame.h), FRAME_H));
  }

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
  return Math.abs(frame.w / frame.h - ASPECT) < 0.01;
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

/**
 * Mirrors `decor_fit_geometry`'s `needsCrop`: once overscan blows the video
 * past the 1920x1080 canvas the render needs `crop`, which has no CUDA
 * counterpart, so the whole overlay pass drops to the ~3x slower CPU chain.
 */
function needsCropAtOverscan(frame: StoryDecorFrame, overscan: number): boolean {
  const safe = Number.isFinite(overscan) ? Math.max(0, Math.min(0.25, overscan)) : 0;
  const wantW = Math.max(frame.w + 2, frame.w * (1 + safe));
  const wantH = Math.max(frame.h + 2, frame.h * (1 + safe));
  return Math.max(wantW / FRAME_W, wantH / FRAME_H) > 1;
}

function maskModeOf(image: StoryDecorImage): StoryDecorMaskMode {
  return image.maskMode === "manual" ? "manual" : "chroma";
}

/** Radius the rectangle can actually take — a bigger one is just a full round. */
function maxRadius(frame: StoryDecorFrame): number {
  return Math.floor(Math.min(frame.w, frame.h) / 2);
}

function traceRect(
  ctx: CanvasRenderingContext2D,
  frame: StoryDecorFrame,
  radius: number,
) {
  ctx.beginPath();
  if (radius > 0 && typeof ctx.roundRect === "function") {
    ctx.roundRect(frame.x, frame.y, frame.w, frame.h, radius);
  } else {
    ctx.rect(frame.x, frame.y, frame.w, frame.h);
  }
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

  const [maskMode, setMaskMode] = useState<StoryDecorMaskMode>(() => maskModeOf(image));
  const [frame, setFrame] = useState<StoryDecorFrame>(() => clampFrame(image.frame));
  const [cornerRadius, setCornerRadius] = useState(String(image.cornerRadius ?? 0));
  const [overscan, setOverscan] = useState(String(image.overscan ?? 0.01));
  const [keyColor, setKeyColor] = useState(image.keyColor ?? "0x00b140");
  const [similarity, setSimilarity] = useState(String(image.similarity ?? 0.15));
  const [blend, setBlend] = useState(String(image.blend ?? 0.05));
  // A self-drawn area is 16:9 by default — it is the shape the video already
  // has, so anything else only wastes picture. The user can still unlock it for
  // an off-aspect screen (an old 4:3 TV in the photo).
  const [lockAspect, setLockAspect] = useState(
    () => maskModeOf(image) === "manual" || isSourceAspect(image.frame),
  );
  const [activeHandle, setActiveHandle] = useState<Handle | null>(null);
  const [isDetecting, setIsDetecting] = useState(false);
  const [decorBitmap, setDecorBitmap] = useState<HTMLImageElement | null>(null);
  const [previewBitmap, setPreviewBitmap] = useState<HTMLImageElement | null>(null);

  const isManual = maskMode === "manual";

  // Reload every local control when the parent selects a different decor image.
  // Keyed on identity + save time only: re-syncing on every individual field
  // would fight the user mid-edit.
  useEffect(() => {
    const mode = maskModeOf(image);
    setMaskMode(mode);
    setFrame(clampFrame(image.frame));
    setCornerRadius(String(image.cornerRadius ?? 0));
    setOverscan(String(image.overscan ?? 0.01));
    setKeyColor(image.keyColor ?? "0x00b140");
    setSimilarity(String(image.similarity ?? 0.15));
    setBlend(String(image.blend ?? 0.05));
    setLockAspect(mode === "manual" || isSourceAspect(image.frame));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image.id, image.updatedAt]);

  // Chroma mode draws the keyed PNG, because its transparent hole IS the
  // alignment target. Manual mode has no hole yet — we are about to cut one —
  // so it draws the untouched photo and paints the area on top instead.
  const backdropPath = isManual ? image.relativePath : image.processedRelativePath;
  useEffect(() => {
    if (!backdropPath) {
      setDecorBitmap(null);
      return;
    }
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = `/media/${backdropPath}?t=${encodeURIComponent(image.updatedAt ?? "")}`;
    img.onload = () => setDecorBitmap(img);
    return () => {
      img.onload = null;
    };
  }, [backdropPath, image.updatedAt]);

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

  const radiusPx = Math.max(0, Math.min(Number(cornerRadius) || 0, maxRadius(frame)));

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    ctx.clearRect(0, 0, FRAME_W, FRAME_H);

    // 1. What plays under the decor. The backend still already has the decor
    // composited on it, so it doubles as the ground truth for the alignment.
    if (previewBitmap) {
      ctx.drawImage(previewBitmap, 0, 0, FRAME_W, FRAME_H);
    } else if (isManual) {
      // The untouched photo: the area is drawn on top of it, below.
      if (decorBitmap) ctx.drawImage(decorBitmap, 0, 0, FRAME_W, FRAME_H);
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
    if (!previewBitmap && !isManual && decorBitmap) {
      ctx.drawImage(decorBitmap, 0, 0, FRAME_W, FRAME_H);
    }

    // 3. The area being drawn. Painting it chroma green is only a metaphor —
    // the backend cuts the alpha directly — but it is the picture the user has
    // in their head, and it shows the rounded corners exactly as they will cut.
    if (!previewBitmap && isManual) {
      ctx.save();
      traceRect(ctx, frame, radiusPx);
      ctx.fillStyle = `rgba(${CHROMA_GREEN},0.62)`;
      ctx.fill();
      ctx.restore();
    }

    // 4. The draggable rectangle.
    ctx.save();
    ctx.strokeStyle = "#22d3ee";
    ctx.lineWidth = 4;
    ctx.setLineDash([14, 10]);
    traceRect(ctx, frame, isManual ? radiusPx : 0);
    ctx.stroke();
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
  }, [frame, decorBitmap, previewBitmap, isManual, radiusPx]);

  useEffect(() => {
    drawCanvas();
  }, [drawCanvas]);

  const toFrameCoords = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    // Pointer capture keeps events coming after the cursor leaves the canvas,
    // which is exactly what makes edge drags work — but the coordinates have to
    // be pinned to the frame or a locked rectangle would be sized off-canvas.
    return {
      x: Math.max(0, Math.min(FRAME_W, (event.clientX - rect.left) * (FRAME_W / rect.width))),
      y: Math.max(0, Math.min(FRAME_H, (event.clientY - rect.top) * (FRAME_H / rect.height))),
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

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y } = toFrameCoords(event);
    const handle = hitTest(x, y);
    if (!handle) return;
    // Without capture the drag dies the moment the cursor crosses the canvas
    // edge — which is precisely where you end up when enlarging the area.
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { handle, startX: x, startY: y, origin: frame };
    setActiveHandle(handle);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
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
      setFrame(clampFrame({ ...origin, x: origin.x + dx, y: origin.y + dy }, lockAspect));
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
      if (width / ASPECT > height) height = width / ASPECT;
      else width = height * ASPECT;
    }

    width = Math.max(MIN_SIDE, width);
    height = Math.max(MIN_SIDE, height);
    const nextX = drag.handle === "nw" || drag.handle === "sw" ? anchorX - width : anchorX;
    const nextY = drag.handle === "nw" || drag.handle === "ne" ? anchorY - height : anchorY;
    setFrame(clampFrame({ x: nextX, y: nextY, w: width, h: height }, lockAspect));
  };

  const endDrag = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (dragRef.current && event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    setActiveHandle(null);
  };

  const handleLockAspect = (next: boolean) => {
    setLockAspect(next);
    if (next) {
      // Snap to the source aspect around the current centre.
      const height = Math.round(frame.w / ASPECT);
      setFrame(
        clampFrame(
          {
            x: frame.x,
            y: frame.y + Math.round((frame.h - height) / 2),
            w: frame.w,
            h: height,
          },
          true,
        ),
      );
    }
  };

  const handleModeChange = (next: StoryDecorMaskMode) => {
    if (next === maskMode) return;
    setMaskMode(next);
    if (next === "manual") {
      setLockAspect(true);
      setFrame((current) => clampFrame(current, true));
    }
  };

  /** The same centred 16:9 rectangle the backend falls back to on upload. */
  const centreFrame = () => {
    const w = Math.round((FRAME_W * 3) / 4);
    const h = Math.round(w / ASPECT);
    setFrame(
      clampFrame(
        { x: Math.round((FRAME_W - w) / 2), y: Math.round((FRAME_H - h) / 2), w, h },
        lockAspect,
      ),
    );
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

  const setFrameField = (key: "x" | "y" | "w" | "h", raw: string) => {
    const value = Number(raw);
    if (!Number.isFinite(value)) return;
    if (lockAspect && key === "h") {
      // Under a lock the height is the width's shadow, so drive the width.
      setFrame(clampFrame({ ...frame, w: Math.round(value * ASPECT) }, true));
      return;
    }
    setFrame(clampFrame({ ...frame, [key]: value }, lockAspect));
  };

  const cursor = useMemo(() => {
    if (activeHandle === "move") return dragRef.current ? "grabbing" : "grab";
    if (activeHandle === "nw" || activeHandle === "se") return "nwse-resize";
    if (activeHandle === "ne" || activeHandle === "sw") return "nesw-resize";
    return "default";
  }, [activeHandle]);

  const onAspect = isSourceAspect(frame);
  const spill = bleed(frame);
  const willCrop = needsCropAtOverscan(frame, Number(overscan));

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">Vùng cho video:</span>
        <Button
          type="button"
          size="sm"
          variant={isManual ? "outline" : "default"}
          onClick={() => handleModeChange("chroma")}
        >
          <Wand2 className="mr-2 size-4" />
          Tách nền xanh có sẵn
        </Button>
        <Button
          type="button"
          size="sm"
          variant={isManual ? "default" : "outline"}
          onClick={() => handleModeChange("manual")}
        >
          <SquareDashed className="mr-2 size-4" />
          Tự tạo vùng nền xanh
        </Button>
      </div>

      <div
        className="relative w-full overflow-hidden rounded-lg border border-border/60 bg-black"
        style={{ aspectRatio: "16 / 9" }}
      >
        <canvas
          ref={canvasRef}
          width={FRAME_W}
          height={FRAME_H}
          className="h-full w-full touch-none"
          style={{ cursor }}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
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
        {isManual ? (
          <Badge variant="outline">Vùng tự vẽ — không cần ảnh có nền xanh</Badge>
        ) : image.autoDetected === false ? (
          <Badge variant="outline">Chưa dò được vùng xanh — căn tay</Badge>
        ) : null}
        {willCrop ? (
          <Badge variant="destructive">
            Khung quá lớn — render sẽ chạy CPU (chậm ~3×). Thu nhỏ vùng một chút.
          </Badge>
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
        💡 Kéo bên trong khung để dời, kéo 4 góc để co giãn.{" "}
        {isManual ? (
          <>
            Vùng xanh này do bạn <strong>tự vẽ</strong>: sau khi bấm Lưu, đúng hình chữ nhật
            (kể cả phần bo góc) sẽ được khoét trong suốt trên ảnh, và video chạy lọt vào đó.
            Ảnh gốc không cần có sẵn màu xanh nào. Canh vùng trùm khít mặt màn hình trong ảnh
            để trông tự nhiên nhất.
          </>
        ) : (
          <>
            Video nền <strong>luôn giữ đúng tỉ lệ 16:9</strong>, không bao giờ bị bóp méo: nó
            được phóng to tới khi phủ kín khung, phần thừa tràn ra ngoài và bị ảnh decor đè lên
            che mất. Vì vậy khung không cần đúng 16:9 — lệch tỉ lệ chỉ tốn một ít video bị che,
            không làm chậm render. Cho khung trùm ra ngoài vùng xanh một chút để chắc chắn
            không hở viền.
          </>
        )}
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
              onChange={(event) => setFrameField(key, event.currentTarget.value)}
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

        {isManual ? (
          <div className="grid gap-1 sm:col-span-2">
            <Label htmlFor="decor-radius">Bo góc ({radiusPx}px)</Label>
            <div className="flex items-center gap-2">
              <input
                id="decor-radius-range"
                type="range"
                className="h-10 flex-1 cursor-pointer"
                min={0}
                max={maxRadius(frame)}
                step={1}
                value={radiusPx}
                onChange={(event) => setCornerRadius(event.currentTarget.value)}
              />
              <Input
                id="decor-radius"
                type="number"
                className="w-24"
                min={0}
                max={maxRadius(frame)}
                value={cornerRadius}
                onChange={(event) => setCornerRadius(event.currentTarget.value)}
              />
            </div>
          </div>
        ) : (
          <>
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
          </>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        {isManual ? (
          <>
            Bo góc 0 = vuông góc. Màn hình TV/điện thoại thật thường bo nhẹ 20-60px ở độ phân
            giải 1920×1080 — bo đúng bằng màn hình trong ảnh thì mép video sẽ không lộ.
          </>
        ) : (
          <>
            Similarity + Blend càng lớn càng tách mạnh, nhưng vượt ~0.20 sẽ bắt đầu đục thủng cả
            phần phòng (gỗ, da, lá cây có màu gần xanh). Nếu viền màn hình còn sót xanh, tăng
            Similarity từng 0.02 rồi bấm Lưu để tách lại.
          </>
        )}
      </p>

      <div className="flex flex-wrap gap-2">
        <Button
          onClick={() =>
            void onSave({
              frame,
              maskMode,
              cornerRadius: radiusPx,
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
        {isManual ? (
          <Button variant="outline" onClick={centreFrame}>
            <Crosshair className="mr-2 size-4" />
            Căn giữa 16:9
          </Button>
        ) : (
          <Button variant="outline" onClick={() => void handleDetect()} disabled={isDetecting}>
            {isDetecting ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Wand2 className="mr-2 size-4" />
            )}
            Tự động dò vùng xanh
          </Button>
        )}
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
          onClick={() => setFrame(clampFrame({ x: 0, y: 0, w: FRAME_W, h: FRAME_H }, lockAspect))}
        >
          <Crosshair className="mr-2 size-4" />
          Reset toàn khung
        </Button>
      </div>
    </div>
  );
}
