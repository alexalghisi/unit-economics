import { AlertTriangle } from "lucide-react";

export function ErrorState({ error }: { error: unknown }) {
  return (
    <div role="alert" className="flex items-center gap-2 rounded-md border border-border bg-surface px-4 py-3 text-sm text-critical">
      <AlertTriangle size={16} aria-hidden="true" />
      {error instanceof Error ? error.message : "Something went wrong"}
    </div>
  );
}

export function Loading({ height = 240 }: { height?: number }) {
  return <div className="w-full animate-pulse rounded-md bg-surface-2" style={{ height }} />;
}
