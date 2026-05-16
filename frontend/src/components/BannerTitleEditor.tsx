import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { DecorImage } from "@/types/api";

interface BannerTitleEditorProps {
  decorImage: DecorImage;
  channelId: string;
  onSave: (config: Partial<DecorImage>) => Promise<void>;
  onClose: () => void;
}

const FRAME_W = 1920;
const FRAME_H = 1080;
const DEFAULT_SAMPLE_TEXT = "Ukraina tung cơn mưa UAV tấn công Nga tối mặt đối phó";

export function BannerTitleEditor({ decorImage, channelId, onSave, onClose }: BannerTitleEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const bannerImgRef = useRef<HTMLImageElement | null>(null);

  const [titleOffsetX, setTitleOffsetX] = useState(decorImage.titleOffsetX);
  const [titleOffsetY, setTitleOffsetY] = useState(decorImage.titleOffsetY);
  const [titleMaxWidth, setTitleMaxWidth] = useState(decorImage.titleMaxWidth);
  const [titleFontSize, setTitleFontSize] = useState(decorImage.titleFontSize || 36);
  const [titleColor, setTitleColor] = useState(decorImage.titleColor || "white");
  const [sampleText, setSampleText] = useState(DEFAULT_SAMPLE_TEXT);
  const [isDragging, setIsDragging] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const bannerW = decorImage.width || FRAME_W;
  const bannerH = decorImage.height || 300;
  const bannerY = FRAME_H - bannerH; // Banner position from top of frame

  // Load banner image
  useEffect(() => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = `/media/${decorImage.relativePath}`;
    img.onload = () => {
      bannerImgRef.current = img;
      drawCanvas();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decorImage.relativePath]);

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Clear
    ctx.clearRect(0, 0, FRAME_W, FRAME_H);

    // Background gradient (simulates video area)
    const grad = ctx.createLinearGradient(0, 0, 0, FRAME_H);
    grad.addColorStop(0, "#1a1a2e");
    grad.addColorStop(0.5, "#16213e");
    grad.addColorStop(1, "#0f3460");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, FRAME_W, FRAME_H);

    // "VIDEO AREA" label
    ctx.fillStyle = "rgba(255,255,255,0.15)";
    ctx.font = "bold 48px Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("VÙNG VIDEO", FRAME_W / 2, FRAME_H / 2 - 40);
    ctx.font = "24px Arial, sans-serif";
    ctx.fillText(`${FRAME_W} × ${FRAME_H}`, FRAME_W / 2, FRAME_H / 2 + 10);
    ctx.textAlign = "start";

    // Draw banner image at bottom
    if (bannerImgRef.current) {
      ctx.drawImage(bannerImgRef.current, 0, bannerY, bannerW, bannerH);
    } else {
      // Fallback: draw semi-transparent banner placeholder
      ctx.fillStyle = "rgba(0,0,0,0.6)";
      ctx.fillRect(0, bannerY, bannerW, bannerH);
      ctx.strokeStyle = "rgba(255,255,255,0.3)";
      ctx.strokeRect(0, bannerY, bannerW, bannerH);
    }

    // Draw title text on banner
    const absX = titleOffsetX;
    const absY = bannerY + titleOffsetY;

    ctx.font = `bold ${titleFontSize}px Arial, sans-serif`;
    ctx.textAlign = "start";
    ctx.textBaseline = "top";

    // Text border (stroke)
    ctx.strokeStyle = "black";
    ctx.lineWidth = 3;
    ctx.lineJoin = "round";

    // Word wrap within titleMaxWidth
    const lines = wrapText(ctx, sampleText, titleMaxWidth);
    const lineHeight = titleFontSize * 1.3;

    for (let i = 0; i < lines.length; i++) {
      const ly = absY + i * lineHeight;
      ctx.strokeText(lines[i], absX, ly);
      ctx.fillStyle = titleColor;
      ctx.fillText(lines[i], absX, ly);
    }

    // Draw drag handle indicator (dashed rect around text area)
    const textHeight = lines.length * lineHeight;
    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = "rgba(0, 200, 255, 0.7)";
    ctx.lineWidth = 2;
    ctx.strokeRect(absX - 4, absY - 4, titleMaxWidth + 8, textHeight + 8);
    ctx.setLineDash([]);

    // Draw position indicator
    ctx.fillStyle = "rgba(0, 200, 255, 0.9)";
    ctx.font = "14px monospace";
    ctx.textBaseline = "bottom";
    ctx.fillText(`X: ${titleOffsetX}  Y: ${titleOffsetY}  W: ${titleMaxWidth}`, absX, absY - 8);
    ctx.textBaseline = "top";
  }, [titleOffsetX, titleOffsetY, titleMaxWidth, titleFontSize, titleColor, sampleText, bannerY, bannerW, bannerH]);

  // Redraw when values change
  useEffect(() => {
    drawCanvas();
  }, [drawCanvas]);

  // Convert mouse coordinates to canvas coordinates
  const getCanvasCoords = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const scaleX = FRAME_W / rect.width;
    const scaleY = FRAME_H / rect.height;
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    };
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const { x, y } = getCanvasCoords(e);
    // Check if clicking near the text area
    const absY = bannerY + titleOffsetY;
    if (
      x >= titleOffsetX - 20 &&
      x <= titleOffsetX + titleMaxWidth + 20 &&
      y >= absY - 20 &&
      y <= absY + titleFontSize * 3 + 20
    ) {
      setIsDragging(true);
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDragging) return;
    const { x, y } = getCanvasCoords(e);
    // Convert to offset relative to banner top-left
    const newOffsetX = Math.max(0, Math.min(bannerW - 100, Math.round(x)));
    const newOffsetY = Math.max(0, Math.min(bannerH - titleFontSize, Math.round(y - bannerY)));
    setTitleOffsetX(newOffsetX);
    setTitleOffsetY(newOffsetY);
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await onSave({
        titleOffsetX,
        titleOffsetY,
        titleMaxWidth,
        titleFontSize,
        titleColor,
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="grid gap-4 rounded-xl border border-border bg-card p-4 shadow-lg">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-foreground">
          Cấu hình tiêu đề — {decorImage.name}
        </h3>
        <Button variant="ghost" size="sm" onClick={onClose}>
          ✕
        </Button>
      </div>

      {/* Canvas Preview */}
      <div
        ref={containerRef}
        className="relative w-full overflow-hidden rounded-lg border border-border/50 bg-black"
        style={{ aspectRatio: "16 / 9" }}
      >
        <canvas
          ref={canvasRef}
          width={FRAME_W}
          height={FRAME_H}
          className="w-full h-full"
          style={{ cursor: isDragging ? "grabbing" : "grab" }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        />
      </div>

      <p className="text-xs text-muted-foreground">
        💡 Kéo thả vùng chữ trên canvas để di chuyển vị trí tiêu đề. Các thông số sẽ được lưu làm mặc định cho banner này.
      </p>

      {/* Controls */}
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
        <div className="grid gap-1">
          <Label htmlFor="title-x">Offset X (px)</Label>
          <Input
            id="title-x"
            type="number"
            min={0}
            max={bannerW}
            value={titleOffsetX}
            onChange={(e) => setTitleOffsetX(Number(e.target.value))}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="title-y">Offset Y (px)</Label>
          <Input
            id="title-y"
            type="number"
            min={0}
            max={bannerH}
            value={titleOffsetY}
            onChange={(e) => setTitleOffsetY(Number(e.target.value))}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="title-max-w">Max Width (px)</Label>
          <Input
            id="title-max-w"
            type="number"
            min={100}
            max={bannerW}
            value={titleMaxWidth}
            onChange={(e) => setTitleMaxWidth(Number(e.target.value))}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="title-font-size">Font Size (px)</Label>
          <Input
            id="title-font-size"
            type="number"
            min={12}
            max={120}
            value={titleFontSize}
            onChange={(e) => setTitleFontSize(Number(e.target.value))}
          />
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="grid gap-1">
          <Label htmlFor="title-color">Màu chữ</Label>
          <div className="flex items-center gap-2">
            <input
              id="title-color"
              type="color"
              value={titleColor === "white" ? "#ffffff" : titleColor}
              onChange={(e) => setTitleColor(e.target.value)}
              className="h-10 w-12 cursor-pointer rounded border border-input"
            />
            <Input
              value={titleColor}
              onChange={(e) => setTitleColor(e.target.value)}
              placeholder="white, #ff0000, ..."
              className="flex-1"
            />
          </div>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="sample-text">Text mẫu</Label>
          <Input
            id="sample-text"
            value={sampleText}
            onChange={(e) => setSampleText(e.target.value)}
            placeholder="Nhập text mẫu để preview..."
          />
        </div>
      </div>

      <div className="flex gap-2 justify-end">
        <Button variant="outline" onClick={onClose}>
          Huỷ
        </Button>
        <Button onClick={handleSave} disabled={isSaving}>
          {isSaving ? "Đang lưu..." : "💾 Lưu cấu hình"}
        </Button>
      </div>
    </div>
  );
}

/** Simple word-wrap for canvas text */
function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const words = text.split(" ");
  const lines: string[] = [];
  let currentLine = "";

  for (const word of words) {
    const testLine = currentLine ? `${currentLine} ${word}` : word;
    const metrics = ctx.measureText(testLine);
    if (metrics.width > maxWidth && currentLine) {
      lines.push(currentLine);
      currentLine = word;
    } else {
      currentLine = testLine;
    }
  }
  if (currentLine) {
    lines.push(currentLine);
  }
  return lines.length > 0 ? lines : [text];
}
