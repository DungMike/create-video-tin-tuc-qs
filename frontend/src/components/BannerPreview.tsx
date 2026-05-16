import { useCallback, useEffect, useRef } from "react";
import type { DecorImage } from "@/types/api";

interface BannerPreviewProps {
  decorImage: DecorImage;
  titleText: string;
  /** Width of the container in CSS pixels, canvas scales accordingly */
  containerWidth?: number;
}

const FRAME_W = 1920;
const FRAME_H = 1080;

export function BannerPreview({ decorImage, titleText, containerWidth }: BannerPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const bannerImgRef = useRef<HTMLImageElement | null>(null);

  const bannerW = decorImage.width || FRAME_W;
  const bannerH = decorImage.height || 300;
  const bannerY = FRAME_H - bannerH;

  const titleOffsetX = decorImage.titleOffsetX;
  const titleOffsetY = decorImage.titleOffsetY;
  const titleMaxWidth = decorImage.titleMaxWidth;
  const titleFontSize = decorImage.titleFontSize || 36;
  const titleColor = decorImage.titleColor || "white";

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

    ctx.clearRect(0, 0, FRAME_W, FRAME_H);

    // Background
    const grad = ctx.createLinearGradient(0, 0, 0, FRAME_H);
    grad.addColorStop(0, "#1a1a2e");
    grad.addColorStop(0.5, "#16213e");
    grad.addColorStop(1, "#0f3460");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, FRAME_W, FRAME_H);

    // Video area label
    ctx.fillStyle = "rgba(255,255,255,0.08)";
    ctx.font = "bold 32px Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("VIDEO", FRAME_W / 2, FRAME_H / 2);
    ctx.textAlign = "start";

    // Draw banner
    if (bannerImgRef.current) {
      ctx.drawImage(bannerImgRef.current, 0, bannerY, bannerW, bannerH);
    } else {
      ctx.fillStyle = "rgba(0,0,0,0.5)";
      ctx.fillRect(0, bannerY, bannerW, bannerH);
    }

    // Draw title text
    if (titleText) {
      const absX = titleOffsetX;
      const absY = bannerY + titleOffsetY;

      ctx.font = `bold ${titleFontSize}px Arial, sans-serif`;
      ctx.textAlign = "start";
      ctx.textBaseline = "top";

      // Border
      ctx.strokeStyle = "black";
      ctx.lineWidth = 3;
      ctx.lineJoin = "round";

      // Word wrap
      const lines = wrapText(ctx, titleText, titleMaxWidth);
      const lineHeight = titleFontSize * 1.3;

      for (let i = 0; i < lines.length; i++) {
        const ly = absY + i * lineHeight;
        ctx.strokeText(lines[i], absX, ly);
        ctx.fillStyle = titleColor;
        ctx.fillText(lines[i], absX, ly);
      }
    }
  }, [titleText, titleOffsetX, titleOffsetY, titleMaxWidth, titleFontSize, titleColor, bannerY, bannerW, bannerH]);

  useEffect(() => {
    drawCanvas();
  }, [drawCanvas]);

  return (
    <div
      className="overflow-hidden rounded-lg border border-border/40"
      style={{ width: containerWidth || "100%", aspectRatio: "16 / 9" }}
    >
      <canvas
        ref={canvasRef}
        width={FRAME_W}
        height={FRAME_H}
        className="w-full h-full"
      />
    </div>
  );
}

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
