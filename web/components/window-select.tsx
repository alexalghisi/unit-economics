"use client";

import { Segmented } from "@/components/ui/segmented";

export const WINDOWS = [7, 30, 60, 90] as const;

export function WindowSelect({ value, onChange }: { value: number; onChange: (days: number) => void }) {
  return (
    <Segmented
      label="Time window"
      value={value}
      onChange={onChange}
      options={WINDOWS.map((d) => ({ value: d, label: `${d}d` }))}
    />
  );
}
