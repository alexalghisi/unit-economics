import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface Props {
  label: string;
  value: string;
  hint?: React.ReactNode;
  tone?: "default" | "critical";
}

export function Kpi({ label, value, hint, tone = "default" }: Props) {
  return (
    <Card className="px-5 py-4">
      <div className="text-xs font-medium text-ink-2">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold tracking-tight", tone === "critical" && "text-critical")}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

export function KpiSkeleton() {
  return <Card className="h-[92px] animate-pulse bg-surface-2" />;
}
