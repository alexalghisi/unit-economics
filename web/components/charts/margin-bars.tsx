"use client";

import { useState } from "react";
import { extentWithZero, linear } from "@/lib/chart";
import { useWidth } from "./use-width";

export interface MarginRow {
  key: string;
  label: string;
  value: number;
  detail: React.ReactNode;
}

interface Props {
  rows: MarginRow[];
  format: (value: number) => string;
}

const ROW = 30;
const LABEL_W = 108;
const VALUE_W = 84;

/** Horizontal bars from a zero baseline: positive margins in blue, losses in red. */
export function MarginBars({ rows, format }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [lo, hi] = extentWithZero(rows.map((r) => r.value));
  const x = linear([lo, hi], [LABEL_W + VALUE_W * (lo < 0 ? 1 : 0), width - VALUE_W]);
  const height = rows.length * ROW + 4;

  return (
    <div ref={ref} className="relative w-full">
      <svg width={width} height={height} role="img" aria-label="Margin by feature" className="block">
        <line x1={x(0)} x2={x(0)} y1={0} y2={height} stroke="var(--axis)" />
        {rows.map((r, i) => {
          const y = i * ROW + 6;
          const x0 = Math.min(x(0), x(r.value));
          const w = Math.max(2, Math.abs(x(r.value) - x(0)));
          const negative = r.value < 0;
          return (
            <g key={r.key} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              <rect x={0} y={i * ROW} width={width} height={ROW} fill={hover === i ? "var(--surface-2)" : "transparent"} />
              <text x={0} y={y + 9} dy="0.32em" className="fill-ink text-[12px]">
                {r.label}
              </text>
              <rect
                x={x0}
                y={y}
                width={w}
                height={18}
                rx={3}
                fill={negative ? "var(--negative)" : "var(--positive)"}
              />
              <text
                x={negative ? x0 - 6 : x0 + w + 6}
                y={y + 9}
                dy="0.32em"
                textAnchor={negative ? "end" : "start"}
                className="tabular fill-ink-2 text-[11px]"
              >
                {format(r.value)}
              </text>
            </g>
          );
        })}
      </svg>
      {hover !== null && rows[hover] && (
        <div
          role="status"
          className="pointer-events-none absolute z-10 rounded-md border border-border bg-surface px-3 py-2 text-xs shadow-lg"
          style={{ top: hover * ROW + ROW, left: LABEL_W }}
        >
          {rows[hover].detail}
        </div>
      )}
    </div>
  );
}
