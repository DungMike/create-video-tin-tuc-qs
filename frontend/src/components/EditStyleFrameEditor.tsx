import { useRef, useState } from "react";

import { FRAME_H, FRAME_W } from "@/lib/overlayPlacement";

/**
 * Kéo vùng (`rect`) và vị trí (`point`) của một kiểu dựng trên khung 1920×1080.
 *
 * Toạ độ luôn là pixel của khung ra, không phải pixel màn hình, và được làm tròn
 * xuống số chẵn như `spec._clean_rect` ở backend làm, để thứ kéo được ở đây đúng
 * là thứ render nhận. Vùng có `aspect` giữ tỉ lệ khi kéo góc.
 */
export interface FrameItem {
  key: string;
  label: string;
  kind: "rect" | "point";
  x: number;
  y: number;
  w: number;
  h: number;
  /** Chỉ vùng: tỉ lệ w/h cố định. */
  aspect?: number | null;
  /** Vị trí đang để trống (tự động theo bố cục/bản ghi): vẽ nét đứt, chỉ để canh. */
  unset?: boolean;
  className: string;
}

const MIN_SIZE = 64;

function even(value: number): number {
  const rounded = Math.round(value);
  return rounded - (rounded % 2);
}

function clampMove(item: FrameItem, x: number, y: number) {
  return {
    x: even(Math.max(0, Math.min(FRAME_W - item.w, x))),
    y: even(Math.max(0, Math.min(FRAME_H - item.h, y))),
  };
}

function clampResize(item: FrameItem, w: number, h: number) {
  let nw = Math.max(MIN_SIZE, Math.min(FRAME_W - item.x, w));
  let nh = Math.max(MIN_SIZE, Math.min(FRAME_H - item.y, h));
  if (item.aspect) {
    nh = nw / item.aspect;
    if (item.y + nh > FRAME_H) {
      nh = FRAME_H - item.y;
      nw = nh * item.aspect;
    }
  }
  return { w: even(nw), h: even(nh) };
}

type Drag =
  | { mode: "move"; key: string; offsetX: number; offsetY: number }
  | { mode: "resize"; key: string };

export function EditStyleFrameEditor({
  items,
  activeKey,
  onActivate,
  onChange,
}: {
  items: FrameItem[];
  activeKey: string | null;
  onActivate: (key: string) => void;
  /** Toạ độ mới của item: vùng nhận x/y/w/h, vị trí chỉ nhận x/y. */
  onChange: (key: string, next: { x: number; y: number; w?: number; h?: number }) => void;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const [dragging, setDragging] = useState(false);

  const toFrame = (event: { clientX: number; clientY: number }) => {
    const rect = frameRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return { x: 0, y: 0 };
    return {
      x: (event.clientX - rect.left) * (FRAME_W / rect.width),
      y: (event.clientY - rect.top) * (FRAME_H / rect.height),
    };
  };

  const startMove = (item: FrameItem) => (event: React.MouseEvent) => {
    event.preventDefault();
    onActivate(item.key);
    const p = toFrame(event);
    dragRef.current = { mode: "move", key: item.key, offsetX: p.x - item.x, offsetY: p.y - item.y };
    setDragging(true);
  };

  const startResize = (item: FrameItem) => (event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    onActivate(item.key);
    dragRef.current = { mode: "resize", key: item.key };
    setDragging(true);
  };

  const handleMove = (event: React.MouseEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const item = items.find((entry) => entry.key === drag.key);
    if (!item) return;
    const p = toFrame(event);
    if (drag.mode === "move") {
      const next = clampMove(item, p.x - drag.offsetX, p.y - drag.offsetY);
      onChange(item.key, item.kind === "rect" ? { ...next, w: item.w, h: item.h } : next);
    } else {
      onChange(item.key, { x: item.x, y: item.y, ...clampResize(item, p.x - item.x, p.y - item.y) });
    }
  };

  const endDrag = () => {
    dragRef.current = null;
    setDragging(false);
  };

  // Vùng lớn vẽ trước để vị trí nhỏ (sóng âm, CTA) luôn nằm trên, bấm trúng được.
  const ordered = [...items].sort((a, b) => b.w * b.h - a.w * a.h);

  return (
    <div className="grid gap-1">
      <div
        ref={frameRef}
        className="relative aspect-video w-full select-none overflow-hidden rounded-lg border border-border/70 bg-zinc-800"
        onMouseMove={handleMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
      >
        <div className="absolute inset-0 flex items-center justify-center text-xs text-zinc-500">
          Khung video 1920×1080
        </div>
        {ordered.map((item) => {
          const active = item.key === activeKey;
          return (
            <div
              key={item.key}
              onMouseDown={startMove(item)}
              style={{
                left: `${(item.x / FRAME_W) * 100}%`,
                top: `${(item.y / FRAME_H) * 100}%`,
                width: `${(item.w / FRAME_W) * 100}%`,
                height: `${(item.h / FRAME_H) * 100}%`,
                cursor: active && dragging ? "grabbing" : "grab",
              }}
              className={`absolute flex items-center justify-center rounded border text-[10px] font-medium text-white/90 ${
                item.className
              } ${item.unset ? "border-dashed" : ""} ${active ? "z-10 ring-1 ring-white/70" : "opacity-60"}`}
            >
              <span className="pointer-events-none truncate px-1">
                {item.label}
                {item.unset ? " (tự động)" : ""}
              </span>
              {item.kind === "rect" && active ? (
                <span
                  onMouseDown={startResize(item)}
                  className="absolute -bottom-1 -right-1 size-3 cursor-nwse-resize rounded-sm border border-white bg-white/80"
                />
              ) : null}
            </div>
          );
        })}
      </div>
      <p className="text-xs text-muted-foreground">
        Bấm để chọn, kéo để di chuyển; vùng đang chọn có nút ở góc dưới-phải để đổi kích thước
        {items.some((item) => item.aspect) ? " (vùng có tỉ lệ cố định giữ đúng 16:9)" : ""}. Ô nét đứt là vị
        trí tự động (theo bố cục hoặc bản ghi sóng âm/CTA) — kéo nó để đặt vị trí riêng cho kiểu dựng này.
      </p>
    </div>
  );
}
