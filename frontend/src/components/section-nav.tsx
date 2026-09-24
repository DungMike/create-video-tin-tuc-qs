import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

export type SectionNavItem = { id: string; label: string };

/**
 * Sticky left-side table of contents: click to smooth-scroll to a section,
 * highlights the section currently at the top of the viewport.
 */
export function SectionNav({ items, className }: { items: SectionNavItem[]; className?: string }) {
  const [activeId, setActiveId] = useState(items[0]?.id ?? "");

  useEffect(() => {
    const updateActive = () => {
      // Near the bottom of the page the last sections can never reach the top, so pin to the last one.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
        setActiveId(items[items.length - 1]?.id ?? "");
        return;
      }
      let current = items[0]?.id ?? "";
      for (const item of items) {
        const el = document.getElementById(item.id);
        if (el && el.getBoundingClientRect().top <= 120) current = item.id;
      }
      setActiveId(current);
    };
    updateActive();
    window.addEventListener("scroll", updateActive, { passive: true });
    window.addEventListener("resize", updateActive);
    return () => {
      window.removeEventListener("scroll", updateActive);
      window.removeEventListener("resize", updateActive);
    };
  }, [items]);

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveId(id);
  };

  return (
    <nav
      aria-label="Muc luc trang"
      className={cn(
        "sticky top-0 z-20 -mx-4 overflow-x-auto border-b border-border/70 bg-background/95 px-4 py-2 backdrop-blur",
        "lg:top-4 lg:mx-0 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto lg:rounded-lg lg:border lg:bg-card/90 lg:p-3 lg:shadow-lg",
        className,
      )}
    >
      <p className="mb-2 hidden px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground lg:block">
        Muc luc
      </p>
      <ul className="flex gap-1 lg:flex-col">
        {items.map((item) => (
          <li key={item.id} className="shrink-0">
            <button
              type="button"
              onClick={() => scrollTo(item.id)}
              className={cn(
                "w-full whitespace-nowrap rounded-md px-3 py-1.5 text-left text-sm transition-colors lg:whitespace-normal",
                activeId === item.id
                  ? "bg-primary/15 font-medium text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {item.label}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
