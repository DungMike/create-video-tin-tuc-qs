import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const HEX_COLOR_RE = /^#?[0-9a-fA-F]{6}$/;

/**
 * Ô chọn màu: swatch native + ô hex.
 *
 * Mặc định `value` rỗng nghĩa là "để preset quyết định": swatch vẫn phải hiện
 * một màu nào đó nên nó mượn `fallback`, còn ô hex để trống để phân biệt rõ với
 * màu đã chọn. Nơi màu luôn có giá trị (form kiểu dựng) truyền `resetValue` là
 * màu mặc định, nút quay lại đưa về màu đó thay vì về rỗng.
 */
export function ColorInput({
  id,
  label,
  value,
  fallback,
  onChange,
  placeholder = "Theo preset",
  resetValue = "",
  resetTitle = "Dùng lại màu của preset",
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  fallback: string;
  onChange: (next: string) => void;
  placeholder?: string;
  resetValue?: string;
  resetTitle?: string;
  disabled?: boolean;
}) {
  const invalid = value !== "" && !HEX_COLOR_RE.test(value.trim());
  return (
    <div className="grid content-start gap-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="color"
          disabled={disabled}
          className="h-10 w-12 shrink-0 cursor-pointer rounded-md border border-input bg-background disabled:opacity-50"
          value={HEX_COLOR_RE.test(value.trim()) ? `#${value.trim().replace(/^#/, "")}` : fallback}
          onChange={(event) => onChange(event.currentTarget.value.toUpperCase())}
        />
        <Input
          value={value}
          placeholder={placeholder}
          aria-invalid={invalid}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value)}
        />
        {value.toUpperCase() !== resetValue.toUpperCase() ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            title={resetTitle}
            disabled={disabled}
            onClick={() => onChange(resetValue)}
          >
            <RotateCcw className="size-4" />
          </Button>
        ) : null}
      </div>
      {invalid ? <p className="text-xs text-destructive">Mã màu phải dạng #RRGGBB.</p> : null}
    </div>
  );
}
