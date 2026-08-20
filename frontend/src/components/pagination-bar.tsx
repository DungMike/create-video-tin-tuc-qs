import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function PaginationBar({
  page,
  totalPages,
  onPrevious,
  onNext,
  onPageChange,
  className,
}: {
  page: number;
  totalPages: number;
  onPrevious: () => void;
  onNext: () => void;
  onPageChange?: (page: number) => void;
  className?: string;
}) {
  const [inputValue, setInputValue] = useState(String(page));

  useEffect(() => {
    setInputValue(String(page));
  }, [page]);

  const handlePageSubmit = () => {
    const parsed = Number.parseInt(inputValue, 10);
    if (!Number.isFinite(parsed)) {
      setInputValue(String(page));
      return;
    }

    const nextPage = Math.min(totalPages, Math.max(1, parsed));
    if (onPageChange) {
      if (nextPage !== page) {
        onPageChange(nextPage);
      } else {
        setInputValue(String(nextPage));
      }
      return;
    }

    setInputValue(String(nextPage));
  };

  if (totalPages <= 1) {
    return null;
  }

  return (
    <div className={`flex flex-wrap items-center justify-center gap-3 ${className ?? ""}`.trim()}>
      <Button type="button" variant="outline" onClick={onPrevious} disabled={page <= 1}>
        <ChevronLeft className="mr-2 size-4" />
        Previous
      </Button>
      <div className="flex items-center gap-2 rounded-md border border-input bg-background px-2 py-1">
        <span className="text-sm font-medium text-muted-foreground">Page</span>
        <Input
          type="number"
          min={1}
          max={totalPages}
          value={inputValue}
          onChange={(event) => setInputValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              handlePageSubmit();
            }
          }}
          onBlur={handlePageSubmit}
          className="h-8 w-16 border-0 bg-transparent px-0 text-center shadow-none focus-visible:ring-0"
        />
        <span className="text-sm font-medium text-muted-foreground">/ {totalPages}</span>
      </div>
      <Button type="button" variant="outline" onClick={onNext} disabled={page >= totalPages}>
        Next
        <ChevronRight className="ml-2 size-4" />
      </Button>
    </div>
  );
}
