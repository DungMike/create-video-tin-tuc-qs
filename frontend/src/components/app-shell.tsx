import type { PropsWithChildren, ReactNode } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export function AppShell({ children }: PropsWithChildren) {
  return <main className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-6 px-4 py-6 md:px-8 md:py-8">{children}</main>;
}

export function HeroCard({
  eyebrow,
  title,
  description,
  stats,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  stats?: { label: string; value: ReactNode }[];
}) {
  return (
    <Card className="overflow-hidden border-border/70 bg-card/90 shadow-xl backdrop-blur">
      <CardContent className="space-y-6 p-6 md:p-8">
        <div className="space-y-3">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-primary">{eyebrow}</p>
          <h1 className="text-4xl font-semibold tracking-tight text-foreground md:text-5xl">{title}</h1>
          {description ? <p className="max-w-4xl text-base leading-7 text-muted-foreground">{description}</p> : null}
        </div>

        {stats?.length ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {stats.map((stat) => (
              <div
                key={stat.label}
                className="rounded-2xl border border-border/70 bg-background/70 p-4 shadow-sm"
              >
                <div className="text-sm text-muted-foreground">{stat.label}</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{stat.value}</div>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function PageSection({
  children,
  className,
}: PropsWithChildren<{ className?: string }>) {
  return (
    <Card className={cn("border-border/70 bg-card/90 shadow-lg backdrop-blur", className)}>
      <CardContent className="p-4 md:p-6">{children}</CardContent>
    </Card>
  );
}
