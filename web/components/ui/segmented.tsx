"use client";

import { cn } from "@/lib/utils";

interface Props<T extends string | number> {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}

export function Segmented<T extends string | number>({ label, value, options, onChange }: Props<T>) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border border-border bg-surface p-0.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            "h-7 rounded px-2.5 text-xs font-medium text-ink-2 transition-colors hover:text-ink",
            o.value === value && "bg-surface-2 text-ink shadow-sm",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
