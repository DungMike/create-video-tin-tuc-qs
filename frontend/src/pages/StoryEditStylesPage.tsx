import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Copy,
  Eye,
  ImagePlus,
  Loader2,
  Plus,
  RotateCcw,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { ChapterFileHelp } from "@/components/chapter-file-help";
import { ColorInput } from "@/components/color-input";
import { EditStyleFrameEditor, type FrameItem } from "@/components/EditStyleFrameEditor";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { TopNav } from "@/components/top-nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useActiveStoryLibrary } from "@/hooks/useActiveStoryLibrary";
import { useStoredFlag } from "@/hooks/useStoredFlag";
import {
  ApiError,
  createEditStyle,
  deleteEditStyle,
  deleteEditStyleImage,
  getCtaOverlays,
  getDecorImages,
  getEditStyles,
  getSubtitleFonts,
  getWaveformOverlays,
  renderEditStylePreview,
  updateEditStyle,
  uploadEditStyleImage,
} from "@/lib/api";
import { boxOrigin } from "@/lib/overlayPlacement";
import type {
  CtaOverlay,
  EditStyleField,
  EditStyleGroup,
  EditStyleParamValue,
  EditStylePoint,
  EditStyleRecord,
  EditStyleRect,
  EditStyleType,
  StoryDecorImage,
  SubtitleFontInfo,
  WaveformOverlay,
} from "@/types/api";

type Params = Record<string, EditStyleParamValue>;

const GROUP_TITLES: Record<EditStyleGroup, { title: string; hint: string }> = {
  layout: {
    title: "Bố cục",
    hint: "Mỗi video trong batch bốc đúng một bố cục, xoay vòng để các video khác nhau.",
  },
  modifier: {
    title: "Hiệu ứng bổ trợ",
    hint: "Tick ở batch là áp cho mọi video, chồng lên bố cục đã bốc.",
  },
};

const GROUP_ORDER = ["layout", "modifier"] as const;

/** Nhớ theo trình duyệt: nhóm "Không sử dụng" đang mở hay gập. */
const SHOW_UNUSED_STORAGE_KEY = "story-edit-styles-show-unused";

const FRAME_ITEM_CLASSES = [
  "border-sky-300 bg-sky-400/25",
  "border-amber-300 bg-amber-400/30",
  "border-emerald-300 bg-emerald-400/30",
  "border-fuchsia-300 bg-fuchsia-400/30",
];

function errorText(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : fallback;
}

function defaultParams(type: EditStyleType): Params {
  const out: Params = {};
  for (const field of type.fields) out[field.key] = structuredClone(field.default) as EditStyleParamValue;
  return out;
}

function sameJson(a: unknown, b: unknown) {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Nhóm trường theo `group`, giữ thứ tự xuất hiện trong spec. */
function groupFields(fields: EditStyleField[]) {
  const groups: { name: string; fields: EditStyleField[] }[] = [];
  for (const field of fields) {
    const name = field.group || "Chung";
    const found = groups.find((group) => group.name === name);
    if (found) found.fields.push(field);
    else groups.push({ name, fields: [field] });
  }
  return groups;
}

function ratioLabel(ratio: number | null) {
  if (!ratio) return null;
  return `Render ×${ratio.toFixed(2)}`;
}

function TypeBadges({ type }: { type: EditStyleType }) {
  return (
    <span className="flex flex-wrap gap-1">
      {type.requiresDecor ? (
        <Badge variant="secondary" className="rounded-full text-[10px]">
          Cần decor
        </Badge>
      ) : null}
      {type.shrinksFrame ? (
        <Badge variant="secondary" className="rounded-full text-[10px]" title="Không dùng với thư viện đã bake sẵn sóng âm/CTA">
          Thu khung
        </Badge>
      ) : null}
      {type.needsChapters ? (
        <Badge variant="secondary" className="rounded-full text-[10px]">
          Dùng chương
        </Badge>
      ) : null}
      {ratioLabel(type.labRatio) ? (
        <Badge
          variant="outline"
          className="rounded-full text-[10px]"
          title="Thời gian render so với bố cục không khung, đo trên audio mẫu 10 phút"
        >
          {ratioLabel(type.labRatio)}
        </Badge>
      ) : null}
    </span>
  );
}

/** Một kiểu ở cột trái: tên, badge và các bản (tick = bật). */
function EditStyleTypeCard({
  type,
  records,
  selectedId,
  busyIds,
  unused,
  flash,
  cardRef,
  onAdd,
  onSelect,
  onToggle,
}: {
  type: EditStyleType;
  records: EditStyleRecord[];
  selectedId: string | null;
  busyIds: Set<string>;
  /** Không bản nào đang bật: vẽ nhạt hơn để tách khỏi các kiểu đang dùng. */
  unused: boolean;
  /** Vừa đổi nhóm (bật/tắt bản cuối): nháy viền để mắt theo kịp. */
  flash: boolean;
  cardRef: (node: HTMLDivElement | null) => void;
  onAdd: () => void;
  onSelect: (record: EditStyleRecord) => void;
  onToggle: (record: EditStyleRecord, enabled: boolean) => void;
}) {
  return (
    <div
      ref={cardRef}
      className={`grid gap-1.5 rounded-lg border p-2.5 transition-shadow ${
        unused ? "border-dashed border-border bg-muted/20" : "border-border/70 bg-background/60"
      } ${flash ? "ring-2 ring-primary/50" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className={`text-sm font-medium ${unused ? "text-muted-foreground" : "text-foreground"}`}>{type.name}</p>
          <TypeBadges type={type} />
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 shrink-0 px-2"
          title="Thêm một bản mới với thông số mặc định"
          onClick={onAdd}
        >
          <Plus className="size-4" />
        </Button>
      </div>
      {records.map((record) => (
        <div
          key={record.id}
          className={`flex items-center gap-2 rounded-md border px-2 py-1.5 ${
            record.id === selectedId ? "border-primary bg-primary/10" : "border-transparent"
          }`}
        >
          <Checkbox
            checked={record.enabled}
            disabled={busyIds.has(record.id)}
            title={record.enabled ? "Đang bật — bấm để tắt" : "Đang tắt — bấm để bật"}
            onCheckedChange={(checked) => onToggle(record, checked === true)}
          />
          <button
            type="button"
            onClick={() => onSelect(record)}
            className={`min-w-0 flex-1 truncate text-left text-sm ${
              record.enabled ? "text-foreground" : "text-muted-foreground line-through"
            }`}
          >
            {record.name}
          </button>
        </div>
      ))}
      {!records.length ? <p className="px-2 text-xs text-muted-foreground">Chưa có bản nào — bấm + để tạo.</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Một trường tham số, vẽ theo kiểu khai báo trong spec.
// ---------------------------------------------------------------------------
function ImageSlot({
  src,
  label,
  onRemove,
}: {
  src: string;
  label: string;
  onRemove: () => void;
}) {
  return (
    <div className="relative">
      <img
        src={src}
        alt={label}
        className="h-20 w-32 rounded border border-border/70 bg-[linear-gradient(45deg,#8883_25%,transparent_25%,transparent_75%,#8883_75%),linear-gradient(45deg,#8883_25%,transparent_25%,transparent_75%,#8883_75%)] bg-[length:12px_12px] bg-[position:0_0,6px_6px] object-contain"
      />
      <button
        type="button"
        title="Bỏ ảnh"
        onClick={onRemove}
        className="absolute -right-2 -top-2 rounded-full border border-border bg-background p-1 text-destructive shadow"
      >
        <X className="size-3" />
      </button>
    </div>
  );
}

function EditStyleParamField({
  field,
  value,
  fonts,
  active,
  imageBase,
  busy,
  onChange,
  onActivate,
  onUploadImage,
  onRemoveImage,
}: {
  field: EditStyleField;
  value: EditStyleParamValue;
  fonts: SubtitleFontInfo[];
  active: boolean;
  imageBase: string;
  busy: boolean;
  onChange: (next: EditStyleParamValue) => void;
  onActivate: () => void;
  onUploadImage: (key: string, file: File) => void;
  onRemoveImage: (key: string, ref?: string) => void;
}) {
  const id = `es-${field.key}`;
  const mediaUrl = (ref: string) => `/media/${imageBase}/${ref}`;
  switch (field.type) {
    case "number":
    case "int": {
      const num = typeof value === "number" ? value : field.default;
      return (
        <div className="grid content-start gap-2">
          <Label htmlFor={id} className="flex items-center justify-between gap-2">
            <span>{field.label}</span>
            <span className="font-mono text-xs text-muted-foreground">{num}</span>
          </Label>
          <div className="flex items-center gap-2">
            <input
              type="range"
              min={field.min}
              max={field.max}
              step={field.step}
              value={num}
              onChange={(event) => onChange(Number(event.currentTarget.value))}
              className="w-full accent-primary"
            />
            <Input
              id={id}
              type="number"
              min={field.min}
              max={field.max}
              step={field.step}
              value={num}
              onChange={(event) => {
                const next = Number(event.currentTarget.value);
                if (Number.isFinite(next)) onChange(next);
              }}
              className="w-24"
            />
          </div>
        </div>
      );
    }
    case "color":
      return (
        <ColorInput
          id={id}
          label={field.label}
          value={typeof value === "string" ? value : field.default}
          fallback={field.default}
          placeholder={field.default}
          resetValue={field.default}
          resetTitle="Về màu mặc định"
          onChange={onChange}
        />
      );
    case "bool":
      return (
        <div className="flex items-center gap-2 self-end pb-2">
          <Checkbox id={id} checked={value === true} onCheckedChange={(checked) => onChange(checked === true)} />
          <Label htmlFor={id} className="cursor-pointer">
            {field.label}
          </Label>
        </div>
      );
    case "select":
      return (
        <div className="grid content-start gap-2">
          <Label htmlFor={id}>{field.label}</Label>
          <select
            id={id}
            value={typeof value === "string" ? value : field.default}
            onChange={(event) => onChange(event.currentTarget.value)}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
          >
            {field.options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      );
    case "text":
      return (
        <div className="grid content-start gap-2">
          <Label htmlFor={id}>{field.label}</Label>
          <Input
            id={id}
            maxLength={field.maxLength}
            value={typeof value === "string" ? value : ""}
            placeholder={field.default}
            onChange={(event) => onChange(event.currentTarget.value)}
          />
        </div>
      );
    case "font": {
      const current = typeof value === "string" ? value : field.default;
      const known = fonts.some((item) => item.family === current);
      return (
        <div className="grid content-start gap-2">
          <Label htmlFor={id}>{field.label}</Label>
          <select
            id={id}
            value={current}
            onChange={(event) => onChange(event.currentTarget.value)}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
          >
            {!known ? <option value={current}>{current} (chưa cài)</option> : null}
            {fonts.map((item) => (
              <option key={item.family} value={item.family}>
                {item.family}
                {item.supportsThai ? " · TH" : ""}
                {item.supportsVietnamese ? " · VI" : ""}
              </option>
            ))}
          </select>
        </div>
      );
    }
    case "list": {
      const items = Array.isArray(value) ? value : field.default;
      return (
        <div className="grid content-start gap-2 md:col-span-2">
          <Label htmlFor={id}>{field.label}</Label>
          <Textarea
            id={id}
            rows={Math.min(8, Math.max(3, items.length + 1))}
            value={items.join("\n")}
            onChange={(event) => onChange(event.currentTarget.value.split("\n").slice(0, field.maxItems))}
          />
          <p className="text-xs text-muted-foreground">Mỗi dòng một mục, tối đa {field.maxItems} mục.</p>
        </div>
      );
    }
    case "image": {
      const ref = typeof value === "string" ? value : "";
      return (
        <div className="grid content-start gap-2">
          <Label htmlFor={id}>{field.label}</Label>
          <div className="flex items-end gap-3">
            {ref ? <ImageSlot src={mediaUrl(ref)} label={field.label} onRemove={() => onRemoveImage(field.key)} /> : null}
            <div className="grid gap-1">
              <Input
                id={id}
                type="file"
                accept=".png,.jpg,.jpeg,.webp"
                disabled={busy}
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  event.currentTarget.value = "";
                  if (file) onUploadImage(field.key, file);
                }}
              />
              <p className="text-xs text-muted-foreground">
                {ref ? "Chọn ảnh khác để thay." : "Trống = dùng hình tự sinh."}
              </p>
            </div>
          </div>
        </div>
      );
    }
    case "imageList": {
      const refs = Array.isArray(value) ? value : [];
      const full = refs.length >= field.maxItems;
      return (
        <div className="grid content-start gap-2 md:col-span-2">
          <Label htmlFor={id}>{field.label}</Label>
          <div className="flex flex-wrap items-end gap-3">
            {refs.map((ref) => (
              <ImageSlot key={ref} src={mediaUrl(ref)} label={field.label} onRemove={() => onRemoveImage(field.key, ref)} />
            ))}
            <label className={`flex h-20 w-32 cursor-pointer items-center justify-center gap-2 rounded border border-dashed border-border/70 text-xs text-muted-foreground ${
              full || busy ? "pointer-events-none opacity-50" : "hover:border-primary"
            }`}>
              <ImagePlus className="size-4" />
              Thêm ảnh
              <input
                id={id}
                type="file"
                accept=".png,.jpg,.jpeg,.webp"
                className="hidden"
                disabled={full || busy}
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  event.currentTarget.value = "";
                  if (file) onUploadImage(field.key, file);
                }}
              />
            </label>
          </div>
          <p className="text-xs text-muted-foreground">
            Tối đa {field.maxItems} ảnh; nên dùng PNG nền trong. Ảnh lưu ngay khi tải lên.
          </p>
        </div>
      );
    }
    case "rect": {
      const rect = (value && typeof value === "object" && "w" in value ? value : field.default) as EditStyleRect;
      const set = (key: keyof EditStyleRect, raw: string) => {
        const next = Number(raw);
        if (!Number.isFinite(next)) return;
        const updated = { ...rect, [key]: next };
        if (field.aspect && key === "w") updated.h = Math.round(next / field.aspect);
        if (field.aspect && key === "h") updated.w = Math.round(next * field.aspect);
        onChange(updated);
      };
      return (
        <div
          className={`grid content-start gap-2 rounded-lg border p-3 md:col-span-2 ${
            active ? "border-primary/60 bg-primary/5" : "border-border/70"
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <Label>{field.label}</Label>
            <Button type="button" variant={active ? "secondary" : "outline"} size="sm" onClick={onActivate}>
              Chỉnh trên khung
            </Button>
          </div>
          <div className="grid grid-cols-4 gap-2">
            {(["x", "y", "w", "h"] as const).map((key) => (
              <div key={key} className="grid gap-1">
                <span className="text-xs text-muted-foreground">{key.toUpperCase()}</span>
                <Input type="number" step={2} value={rect[key]} onChange={(event) => set(key, event.currentTarget.value)} />
              </div>
            ))}
          </div>
          {field.aspect ? <p className="text-xs text-muted-foreground">Giữ tỉ lệ 16:9 theo khung hình.</p> : null}
        </div>
      );
    }
    case "point": {
      const point = value && typeof value === "object" && "x" in value ? (value as EditStylePoint) : null;
      const set = (key: "x" | "y", raw: string) => {
        const next = Number(raw);
        if (!Number.isFinite(next)) return;
        onChange({ x: point?.x ?? 0, y: point?.y ?? 0, [key]: next });
      };
      return (
        <div
          className={`grid content-start gap-2 rounded-lg border p-3 ${
            active ? "border-primary/60 bg-primary/5" : "border-border/70"
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Label>{field.label}</Label>
            <div className="flex gap-1">
              <Button type="button" variant={active ? "secondary" : "outline"} size="sm" onClick={onActivate}>
                Chỉnh trên khung
              </Button>
              {point ? (
                <Button type="button" variant="ghost" size="sm" title="Về vị trí tự động" onClick={() => onChange(null)}>
                  <RotateCcw className="size-4" />
                </Button>
              ) : null}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {(["x", "y"] as const).map((key) => (
              <div key={key} className="grid gap-1">
                <span className="text-xs text-muted-foreground">{key.toUpperCase()} (góc trên-trái)</span>
                <Input
                  type="number"
                  step={2}
                  placeholder="Tự động"
                  value={point ? point[key] : ""}
                  onChange={(event) => set(key, event.currentTarget.value)}
                />
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {point
              ? "Vị trí riêng của kiểu dựng này."
              : "Tự động: theo bố cục, hoặc theo vị trí lưu ở bản ghi sóng âm/CTA."}
          </p>
        </div>
      );
    }
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Khung kéo thả: gom các trường rect/point của kiểu đang sửa.
// ---------------------------------------------------------------------------
function overlaySize(overlay: WaveformOverlay | CtaOverlay | undefined, fallback: [number, number]) {
  const width = overlay?.processedWidth ?? overlay?.scaleWidth ?? fallback[0];
  const height = overlay?.processedHeight ?? Math.round((width * fallback[1]) / fallback[0]);
  return { width, height };
}

function autoOrigin(overlay: WaveformOverlay | CtaOverlay | undefined, size: { width: number; height: number }, corner: "bottom_right" | "top_left") {
  return boxOrigin({
    label: "",
    x: overlay?.x ?? null,
    y: overlay?.y ?? null,
    position: overlay?.position ?? corner,
    margin: overlay?.margin ?? 40,
    width: size.width,
    height: size.height,
    className: "",
  });
}

// Dải letterbox theo tỉ lệ, khớp assets.letterbox_bar_height ở backend.
const LETTERBOX_BAR: Record<string, number> = { "2.39": 138, "2.2": 104, "2.0": 60 };

/**
 * Vị trí tự động mà bố cục tự đặt cho sóng âm/CTA khi ô vị trí để trống — cùng
 * luật với EditPlan.wave_override / cta_override. null = theo bản ghi sóng âm/CTA.
 */
function layoutAutoOrigin(typeId: string, params: Params, isCta: boolean): { x: number; y: number } | null {
  const rect = params.rect as EditStyleRect | undefined;
  switch (typeId) {
    case "card":
      if (!rect) return null;
      return isCta ? { x: rect.x + 30, y: rect.y + 20 } : { x: rect.x + 60, y: rect.y + rect.h - 250 };
    case "letterbox": {
      const bar = LETTERBOX_BAR[String(params.aspect)] ?? 138;
      return isCta ? { x: 40, y: bar + 12 } : { x: 1920 - 450, y: 1080 - bar - 250 };
    }
    case "film_frame":
      if (!rect) return null;
      return isCta ? { x: rect.x + 20, y: rect.y + 20 } : { x: rect.x + rect.w - 470, y: rect.y + rect.h - 250 };
    case "osd_camcorder":
    case "osd_cctv":
      return isCta ? { x: 90, y: 210 } : { x: 60, y: 760 };
    default:
      return null;
  }
}

function buildFrameItems(
  type: EditStyleType,
  params: Params,
  waveform: WaveformOverlay | undefined,
  cta: CtaOverlay | undefined,
): FrameItem[] {
  const items: FrameItem[] = [];
  let colour = 0;
  for (const field of type.fields) {
    const className = FRAME_ITEM_CLASSES[colour % FRAME_ITEM_CLASSES.length];
    if (field.type === "rect") {
      const rect = (params[field.key] ?? field.default) as EditStyleRect;
      items.push({ key: field.key, label: field.label, kind: "rect", ...rect, aspect: field.aspect, className });
      colour += 1;
    } else if (field.type === "point") {
      const fallback: [number, number] = field.size ?? [360, 200];
      const isCta = field.key === "ctaPlacement";
      const overlay = isCta ? cta : waveform;
      let size = overlaySize(overlay, fallback);
      // Sóng âm theo giọng có kích thước riêng trong chính tham số của nó.
      if (field.key === "placement" && typeof params.width === "number" && typeof params.height === "number") {
        size = { width: params.width, height: params.height };
      }
      const point = params[field.key] as EditStylePoint | null;
      const origin =
        point ??
        (field.key !== "placement" ? layoutAutoOrigin(type.id, params, isCta) : null) ??
        autoOrigin(overlay, size, isCta ? "top_left" : "bottom_right");
      items.push({
        key: field.key,
        label: field.label.replace(/\s*\(.*\)$/, ""),
        kind: "point",
        x: origin.x,
        y: origin.y,
        w: size.width,
        h: size.height,
        unset: !point,
        className,
      });
      colour += 1;
    }
  }
  return items;
}

// ---------------------------------------------------------------------------
// Trang
// ---------------------------------------------------------------------------
export function StoryEditStylesPage() {
  const [types, setTypes] = useState<EditStyleType[]>([]);
  const [styles, setStyles] = useState<EditStyleRecord[]>([]);
  const [fonts, setFonts] = useState<SubtitleFontInfo[]>([]);
  const [decorImages, setDecorImages] = useState<StoryDecorImage[]>([]);
  const [waveforms, setWaveforms] = useState<WaveformOverlay[]>([]);
  const [ctas, setCtas] = useState<CtaOverlay[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftParams, setDraftParams] = useState<Params>({});
  const [activeFrameKey, setActiveFrameKey] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [imageBase, setImageBase] = useState("story_edit_styles");
  const [isUploadingImage, setIsUploadingImage] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [previewDecorId, setPreviewDecorId] = useState("");
  const { libraries, activeId: activeLibraryId } = useActiveStoryLibrary();
  const [previewLibraryId, setPreviewLibraryId] = useState("");
  const [showUnused, setShowUnused] = useStoredFlag(SHOW_UNUSED_STORAGE_KEY, false);
  const [flashTypeId, setFlashTypeId] = useState<string | null>(null);
  const typeCardRefs = useRef(new Map<string, HTMLDivElement>());
  const prevUsedTypeIds = useRef<Set<string> | null>(null);

  const typeById = useMemo(() => new Map(types.map((type) => [type.id, type])), [types]);
  const selected = styles.find((style) => style.id === selectedId) ?? null;
  const selectedType = selected ? typeById.get(selected.type) ?? null : null;
  const isDirty = Boolean(selected) && (draftName !== selected?.name || !sameJson(draftParams, selected?.params));
  /** Kiểu "đang dùng" = còn ít nhất một bản bật (mới hiện ở trang render batch). */
  const usedTypeIds = useMemo(
    () => new Set(styles.filter((style) => style.enabled).map((style) => style.type)),
    [styles],
  );
  const unusedTypes = types.filter((type) => !usedTypeIds.has(type.id));
  const selectedIsUnused = Boolean(selected) && !usedTypeIds.has(selected?.type ?? "");

  // Bản đang chọn nằm trong nhóm "Không sử dụng" thì mở nhóm ra, không để nó khuất.
  useEffect(() => {
    if (selectedIsUnused) setShowUnused(true);
  }, [selectedId, selectedIsUnused, setShowUnused]);

  // Bật/tắt/xoá làm một kiểu đổi nhóm: mở nhóm đích và nháy thẻ đó cho người dùng thấy nó đi đâu.
  useEffect(() => {
    if (isLoading) return;
    const prev = prevUsedTypeIds.current;
    prevUsedTypeIds.current = usedTypeIds;
    if (!prev) return;
    const moved = types.find((type) => prev.has(type.id) !== usedTypeIds.has(type.id));
    if (!moved) return;
    if (!usedTypeIds.has(moved.id)) setShowUnused(true);
    setFlashTypeId(moved.id);
  }, [isLoading, usedTypeIds, types, setShowUnused]);

  useEffect(() => {
    if (!flashTypeId) return;
    typeCardRefs.current.get(flashTypeId)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const timer = window.setTimeout(() => setFlashTypeId(null), 1600);
    return () => window.clearTimeout(timer);
  }, [flashTypeId]);

  const loadRecord = useCallback((record: EditStyleRecord | null) => {
    setSelectedId(record?.id ?? null);
    setDraftName(record?.name ?? "");
    setDraftParams(record ? structuredClone(record.params) : {});
    setActiveFrameKey(null);
    setPreviewPath(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getEditStyles().then((res) => {
        if (cancelled) return;
        setTypes(res.types);
        setStyles(res.styles);
        setImageBase(res.imageBase || "story_edit_styles");
        // Mở trang ở một bản đang bật (nhóm trên cùng), không rơi vào nhóm "Không sử dụng" đang gập.
        const first =
          res.styles.find((style) => style.enabled && style.group === "layout") ??
          res.styles.find((style) => style.enabled) ??
          res.styles[0] ??
          null;
        if (first) loadRecord(first);
      }),
      // Phần còn lại chỉ để vẽ form cho đẹp: lỗi ở đây không chặn trang.
      getSubtitleFonts()
        .then((res) => !cancelled && setFonts(res.fonts))
        .catch(() => undefined),
      getDecorImages()
        .then((res) => !cancelled && setDecorImages(res.images.filter((item) => item.enabled !== false)))
        .catch(() => undefined),
      getWaveformOverlays()
        .then((res) => !cancelled && setWaveforms(res.overlays))
        .catch(() => undefined),
      getCtaOverlays()
        .then((res) => !cancelled && setCtas(res.overlays))
        .catch(() => undefined),
    ])
      .catch((err) => !cancelled && setErrorMessage(errorText(err, "Không tải được danh sách kiểu dựng.")))
      .finally(() => !cancelled && setIsLoading(false));
    return () => {
      cancelled = true;
    };
  }, [loadRecord]);

  const replaceStyle = (record: EditStyleRecord) =>
    setStyles((current) => current.map((item) => (item.id === record.id ? record : item)));

  const markBusy = (id: string, busy: boolean) =>
    setBusyIds((current) => {
      const next = new Set(current);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });

  const confirmLeave = () => !isDirty || window.confirm("Bản đang sửa chưa lưu. Bỏ các thay đổi?");

  const selectRecord = (record: EditStyleRecord) => {
    if (record.id === selectedId || !confirmLeave()) return;
    loadRecord(record);
  };

  const toggleEnabled = async (record: EditStyleRecord, enabled: boolean) => {
    markBusy(record.id, true);
    setErrorMessage(null);
    try {
      const res = await updateEditStyle(record.id, { enabled });
      replaceStyle(res.style);
    } catch (err) {
      setErrorMessage(errorText(err, "Không đổi được trạng thái bật/tắt."));
    } finally {
      markBusy(record.id, false);
    }
  };

  const addVariant = async (type: EditStyleType) => {
    if (!confirmLeave()) return;
    setErrorMessage(null);
    try {
      const res = await createEditStyle({ type: type.id });
      setStyles((current) => [...current, res.style]);
      loadRecord(res.style);
    } catch (err) {
      setErrorMessage(errorText(err, "Không tạo được bản mới."));
    }
  };

  const duplicate = async () => {
    if (!selected || !confirmLeave()) return;
    setErrorMessage(null);
    try {
      const res = await createEditStyle({ type: selected.type, copyFrom: selected.id });
      setStyles((current) => [...current, res.style]);
      loadRecord(res.style);
    } catch (err) {
      setErrorMessage(errorText(err, "Không nhân bản được."));
    }
  };

  const remove = async () => {
    if (!selected) return;
    if (!window.confirm(`Xoá "${selected.name}"? Batch đang chạy vẫn giữ bản đã bốc.`)) return;
    setErrorMessage(null);
    try {
      await deleteEditStyle(selected.id);
      const rest = styles.filter((item) => item.id !== selected.id);
      setStyles(rest);
      loadRecord(rest.find((item) => item.type === selected.type) ?? rest[0] ?? null);
    } catch (err) {
      setErrorMessage(errorText(err, "Không xoá được."));
    }
  };

  const save = async (): Promise<EditStyleRecord | null> => {
    if (!selected) return null;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      const res = await updateEditStyle(selected.id, { name: draftName, params: draftParams });
      replaceStyle(res.style);
      // Backend kẹp giá trị theo spec: nạp lại bản đã kẹp để form khớp với thứ sẽ render.
      setDraftName(res.style.name);
      setDraftParams(structuredClone(res.style.params));
      return res.style;
    } catch (err) {
      setErrorMessage(errorText(err, "Không lưu được."));
      return null;
    } finally {
      setIsSaving(false);
    }
  };

  const preview = async () => {
    if (!selected) return;
    if (isDirty && !(await save())) return;
    setIsPreviewing(true);
    setErrorMessage(null);
    try {
      const res = await renderEditStylePreview(selected.id, {
        libraryId: previewLibraryId || activeLibraryId || undefined,
        decorImageId: selectedType?.requiresDecor ? previewDecorId || undefined : undefined,
      });
      setPreviewPath(`${res.previewPath}?v=${Date.now()}`);
    } catch (err) {
      setErrorMessage(errorText(err, "Không render được bản xem thử."));
    } finally {
      setIsPreviewing(false);
    }
  };

  const setParam = (key: string, value: EditStyleParamValue) =>
    setDraftParams((current) => ({ ...current, [key]: value }));

  /** Pictures are stored the moment they are picked (the file itself has to go to
   *  the server), so only that one field is refreshed — the rest of the form keeps
   *  whatever is still unsaved. */
  const applyImageResult = (record: EditStyleRecord, key: string) => {
    replaceStyle(record);
    setDraftParams((current) => ({ ...current, [key]: record.params[key] }));
  };

  const uploadImage = async (key: string, file: File) => {
    if (!selected) return;
    setIsUploadingImage(true);
    setErrorMessage(null);
    try {
      const res = await uploadEditStyleImage(selected.id, key, file);
      applyImageResult(res.style, key);
    } catch (err) {
      setErrorMessage(errorText(err, "Không tải được ảnh lên."));
    } finally {
      setIsUploadingImage(false);
    }
  };

  const removeImage = async (key: string, ref?: string) => {
    if (!selected) return;
    setIsUploadingImage(true);
    setErrorMessage(null);
    try {
      const res = await deleteEditStyleImage(selected.id, key, ref);
      applyImageResult(res.style, key);
    } catch (err) {
      setErrorMessage(errorText(err, "Không xoá được ảnh."));
    } finally {
      setIsUploadingImage(false);
    }
  };

  const defaultWaveform = waveforms.find((item) => item.isDefault) ?? waveforms[0];
  const defaultCta = ctas.find((item) => item.isDefault) ?? ctas.find((item) => item.enabled !== false) ?? ctas[0];
  const frameItems = selectedType ? buildFrameItems(selectedType, draftParams, defaultWaveform, defaultCta) : [];

  const enabledCount = (group: EditStyleGroup) =>
    styles.filter((style) => style.group === group && style.enabled).length;

  const renderTypeCard = (type: EditStyleType) => (
    <EditStyleTypeCard
      key={type.id}
      type={type}
      records={styles.filter((style) => style.type === type.id)}
      selectedId={selectedId}
      busyIds={busyIds}
      unused={!usedTypeIds.has(type.id)}
      flash={flashTypeId === type.id}
      cardRef={(node) => {
        if (node) typeCardRefs.current.set(type.id, node);
        else typeCardRefs.current.delete(type.id);
      }}
      onAdd={() => void addVariant(type)}
      onSelect={selectRecord}
      onToggle={(record, enabled) => void toggleEnabled(record, enabled)}
    />
  );

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Đang tải kiểu dựng..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Story Video"
        title="Kiểu dựng"
        description="Bố cục và hiệu ứng cho video kể chuyện. Mỗi kiểu có thể có nhiều bản với thông số khác nhau; bản đang bật mới hiện ở trang render batch."
        stats={[
          { label: "Bố cục đang bật", value: enabledCount("layout") },
          { label: "Hiệu ứng bổ trợ đang bật", value: enabledCount("modifier") },
        ]}
      />

      {errorMessage ? <StatusAlert title="Có lỗi xảy ra" message={errorMessage} variant="destructive" /> : null}

      <div className="flex flex-wrap gap-2">
        <Button asChild variant="outline">
          <Link to="/story-video">
            <ArrowLeft className="mr-2 size-4" />
            Về trang render
          </Link>
        </Button>
        <Button asChild variant="outline">
          <Link to="/story-video/settings">Cấu hình overlay / decor</Link>
        </Button>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
        <PageSection className="lg:sticky lg:top-4">
          {/* Danh sách dài hơn màn hình: cuộn riêng trong cột, còn cột vẫn dính (sticky) cạnh form. */}
          <div className="-mr-2 grid max-h-[60vh] gap-6 overflow-y-auto overscroll-contain pr-2 lg:max-h-[calc(100vh-6rem)]">
            {GROUP_ORDER.map((group) => {
              const groupTypes = types.filter((type) => type.group === group && usedTypeIds.has(type.id));
              return (
                <div key={group} className="grid gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-foreground">{GROUP_TITLES[group].title}</h2>
                    <p className="text-xs text-muted-foreground">{GROUP_TITLES[group].hint}</p>
                  </div>
                  {groupTypes.map(renderTypeCard)}
                  {!groupTypes.length ? (
                    <p className="rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
                      Chưa bật kiểu nào. Mở nhóm "Không sử dụng" bên dưới và tick một bản để dùng.
                    </p>
                  ) : null}
                </div>
              );
            })}

            {unusedTypes.length ? (
              <div className="grid gap-3 border-t border-border/70 pt-4">
                <button
                  type="button"
                  aria-expanded={showUnused}
                  onClick={() => setShowUnused(!showUnused)}
                  className="-mx-1 flex items-start gap-2 rounded-md px-1 py-1 text-left hover:bg-muted/40"
                >
                  {showUnused ? (
                    <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 text-base font-semibold text-foreground">
                      Không sử dụng
                      <Badge variant="secondary" className="rounded-full text-[10px]">
                        {unusedTypes.length}
                      </Badge>
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      Không có bản nào đang bật nên không hiện ở trang render. Tick một bản để đưa kiểu về nhóm của nó.
                    </span>
                  </span>
                </button>
                {showUnused
                  ? GROUP_ORDER.map((group) => {
                      const groupTypes = unusedTypes.filter((type) => type.group === group);
                      if (!groupTypes.length) return null;
                      return (
                        <div key={group} className="grid gap-2">
                          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                            {GROUP_TITLES[group].title}
                          </p>
                          {groupTypes.map(renderTypeCard)}
                        </div>
                      );
                    })
                  : null}
              </div>
            ) : null}
          </div>
        </PageSection>

        <PageSection>
          {!selected || !selectedType ? (
            <p className="py-8 text-center text-sm text-muted-foreground">Chọn một bản ở cột trái để chỉnh.</p>
          ) : (
            <div className="grid gap-6">
              <div className="grid gap-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">
                      {GROUP_TITLES[selectedType.group].title} · {selectedType.name}
                    </p>
                    <p className="text-sm text-muted-foreground">{selectedType.description}</p>
                    <TypeBadges type={selectedType} />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" onClick={() => void save()} disabled={!isDirty || isSaving}>
                      {isSaving ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
                      Lưu
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={!isDirty || isSaving}
                      onClick={() => loadRecord(selected)}
                    >
                      Hoàn tác
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      title="Đưa mọi thông số về mặc định (chưa lưu)"
                      onClick={() => setDraftParams(defaultParams(selectedType))}
                    >
                      <RotateCcw className="mr-2 size-4" />
                      Mặc định
                    </Button>
                    <Button type="button" variant="outline" onClick={() => void duplicate()}>
                      <Copy className="mr-2 size-4" />
                      Nhân bản
                    </Button>
                    <Button type="button" variant="ghost" className="text-destructive" onClick={() => void remove()}>
                      <Trash2 className="mr-2 size-4" />
                      Xoá
                    </Button>
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
                  <div className="grid gap-2">
                    <Label htmlFor="es-name">Tên bản</Label>
                    <Input id="es-name" value={draftName} maxLength={80} onChange={(event) => setDraftName(event.currentTarget.value)} />
                  </div>
                  <div className="flex items-center gap-2 pb-2">
                    <Checkbox
                      id="es-enabled"
                      checked={selected.enabled}
                      disabled={busyIds.has(selected.id)}
                      onCheckedChange={(checked) => void toggleEnabled(selected, checked === true)}
                    />
                    <Label htmlFor="es-enabled" className="cursor-pointer">
                      Bật (hiện ở trang render)
                    </Label>
                  </div>
                </div>
              </div>

              {selectedType.needsChapters ? (
                <div className="grid gap-2">
                  <p className="text-sm text-muted-foreground">
                    Kiểu này in chữ chương lên video. Chữ chỉ lấy từ file chương đi kèm audio — không có file thì
                    phần chữ chương tắt, chương chỉ còn là mốc thời gian.
                  </p>
                  <ChapterFileHelp />
                </div>
              ) : null}

              {frameItems.length ? (
                <EditStyleFrameEditor
                  items={frameItems}
                  activeKey={activeFrameKey}
                  onActivate={setActiveFrameKey}
                  onChange={(key, next) => {
                    const field = selectedType.fields.find((item) => item.key === key);
                    if (!field) return;
                    setParam(key, field.type === "rect" ? { x: next.x, y: next.y, w: next.w ?? 0, h: next.h ?? 0 } : { x: next.x, y: next.y });
                  }}
                />
              ) : null}

              {selectedType.fields.length === 0 ? (
                <p className="text-sm text-muted-foreground">Kiểu này không có thông số riêng.</p>
              ) : (
                groupFields(selectedType.fields).map((group) => (
                  <fieldset key={group.name} className="grid gap-3">
                    <legend className="mb-2 text-sm font-semibold text-foreground">{group.name}</legend>
                    <div className="grid gap-4 md:grid-cols-2">
                      {group.fields.map((field) => (
                        <EditStyleParamField
                          key={field.key}
                          field={field}
                          value={draftParams[field.key] ?? null}
                          fonts={fonts}
                          active={activeFrameKey === field.key}
                          imageBase={imageBase}
                          busy={isUploadingImage}
                          onActivate={() => setActiveFrameKey(field.key)}
                          onChange={(next) => setParam(field.key, next)}
                          onUploadImage={(key, file) => void uploadImage(key, file)}
                          onRemoveImage={(key, ref) => void removeImage(key, ref)}
                        />
                      ))}
                    </div>
                  </fieldset>
                ))
              )}

              <div className="grid gap-3 rounded-lg border border-border/70 bg-background/60 p-4">
                <div className="flex flex-wrap items-end gap-3">
                  <div className="grid gap-2">
                    <Label htmlFor="es-preview-lib">Clip mẫu từ thư viện</Label>
                    <select
                      id="es-preview-lib"
                      value={previewLibraryId || activeLibraryId}
                      onChange={(event) => setPreviewLibraryId(event.currentTarget.value)}
                      className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                    >
                      {libraries.map((lib) => (
                        <option key={lib.id} value={lib.id}>
                          {lib.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  {selectedType.requiresDecor ? (
                    <div className="grid gap-2">
                      <Label htmlFor="es-preview-decor">Ảnh decor</Label>
                      <select
                        id="es-preview-decor"
                        value={previewDecorId}
                        onChange={(event) => setPreviewDecorId(event.currentTarget.value)}
                        className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                      >
                        <option value="">Ảnh đang bật đầu tiên</option>
                        {decorImages.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.group ? `${item.group} · ` : ""}
                            {item.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : null}
                  <Button type="button" onClick={() => void preview()} disabled={isPreviewing || isSaving}>
                    {isPreviewing ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Eye className="mr-2 size-4" />}
                    {isDirty ? "Lưu & xem thử" : "Xem thử"}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Render 10 giây bằng đúng bước ghép overlay của batch (clip mẫu lặp lại, phụ đề mẫu, sóng âm/CTA
                  mặc định)
                  {selectedType.group === "modifier" ? ", trên bố cục không khung" : ""}. Mất vài giây tới vài chục giây.
                </p>
                {previewPath ? (
                  <video
                    key={previewPath}
                    src={`/media/${previewPath}`}
                    controls
                    autoPlay
                    loop
                    muted
                    playsInline
                    className="w-full rounded-lg border border-border/70 bg-black"
                  />
                ) : null}
              </div>
            </div>
          )}
        </PageSection>
      </div>
    </AppShell>
  );
}
