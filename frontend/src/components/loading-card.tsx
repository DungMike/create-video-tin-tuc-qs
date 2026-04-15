import { Card, CardContent } from "@/components/ui/card";

export function LoadingCard({ message }: { message: string }) {
  return (
    <Card className="border-border/70 bg-card/90 shadow-lg">
      <CardContent className="flex items-center gap-3 p-6 text-sm text-muted-foreground">
        <div className="size-4 animate-spin rounded-full border-2 border-primary/25 border-t-primary" />
        <span>{message}</span>
      </CardContent>
    </Card>
  );
}
