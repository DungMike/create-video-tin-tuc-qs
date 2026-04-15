import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export function StatusAlert({
  title,
  message,
  variant = "default",
}: {
  title: string;
  message: string;
  variant?: "default" | "destructive";
}) {
  return (
    <Alert variant={variant}>
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}
