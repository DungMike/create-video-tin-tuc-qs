import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";

export function PaginationBar({
  page,
  totalPages,
  onPrevious,
  onNext,
}: {
  page: number;
  totalPages: number;
  onPrevious: () => void;
  onNext: () => void;
}) {
  if (totalPages <= 1) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-center justify-center gap-3">
      <Button type="button" variant="outline" onClick={onPrevious} disabled={page <= 1}>
        <ChevronLeft className="mr-2 size-4" />
        Previous
      </Button>
      <div className="text-sm font-medium text-muted-foreground">
        Page {page} / {totalPages}
      </div>
      <Button type="button" variant="outline" onClick={onNext} disabled={page >= totalPages}>
        Next
        <ChevronRight className="ml-2 size-4" />
      </Button>
    </div>
  );
}
