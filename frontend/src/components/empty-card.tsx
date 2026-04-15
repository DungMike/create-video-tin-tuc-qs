import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

export function EmptyCard({ title, description }: { title: string; description: string }) {
  return (
    <Card className="border-dashed border-border/70 bg-card/70">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent />
    </Card>
  );
}
