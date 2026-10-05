"use client";

import { useState } from "react";
import { endLabels, linePath, linear, nearestIndex, niceTicks } from "@/lib/chart";
import { shortDate } from "@/lib/format";
import { useWidth } from "./use-width";

export interface Series {
  key: string;
  label: string;
  color: string;
  values: number[];
}

interface Props {
  days: string[];
  series: Series[];
  height?: number;
  format: (value: number) => string;
  axisFormat: (value: number) => string;
  footer?: (index: number) => React.ReactNode;
}

const M = { top: 12, right: 76, bottom: 26, left: 52 };

export function LineChart({ days, series, height = 240, format, axisFormat, footer }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const innerW = width - M.left - M.right;
  const innerH = height - M.top - M.bottom;
  const all = series.flatMap((s) => s.values);
  const ticks = niceTicks(0, Math.max(1, ...all), 4);
  const y = linear([0, ticks[ticks.length - 1] ?? 1], [M.top + innerH, M.top]);
  const x = (i: number) => M.left + (days.length <= 1 ? 0 : (i / (days.length - 1)) * innerW);
  const labelEvery = Math.max(1, Math.ceil(days.length / Math.max(2, Math.floor(innerW / 80))));

  return (
    <div ref={ref} className="relative w-full">
      <ul className="mb-2 flex gap-4 text-xs text-ink-2" aria-label="Legend">
        {series.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4 rounded" style={{ background: s.color }} />
            {s.label}
          </li>
        ))}
      </ul>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={series.map((s) => s.label).join(" and ") + " per day"}
        onPointerMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          setHover(nearestIndex(e.clientX - box.left, M.left, innerW, days.length));
        }}
        onPointerLeave={() => setHover(null)}
        className="block touch-none select-none"
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={M.left + innerW} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--axis)" : "var(--grid)"} />
            <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tabular fill-muted text-[11px]">
              {axisFormat(t)}
            </text>
          </g>
        ))}
        {days.map((d, i) =>
          i % labelEvery === 0 ? (
            <text key={d} x={x(i)} y={height - 6} textAnchor="middle" className="fill-muted text-[11px]">
              {shortDate(d)}
            </text>
          ) : null,
        )}
        {series.map((s) => (
          <path
            key={s.key}
            d={linePath(s.values.map((v, i) => [x(i), y(v)]))}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {endLabels(series.map((s) => y(s.values[s.values.length - 1] ?? 0))).map((ly, i) => (
          <text
            key={series[i]?.key}
            x={x(days.length - 1) + 8}
            y={ly}
            dy="0.32em"
            className="fill-ink-2 text-[11px] font-medium"
          >
            {series[i]?.label}
          </text>
        ))}
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={M.top + innerH} stroke="var(--axis)" />
            {series.map((s) => (
              <circle
                key={s.key}
                cx={x(hover)}
                cy={y(s.values[hover] ?? 0)}
                r={4}
                fill={s.color}
                stroke="var(--surface)"
                strokeWidth={2}
              />
            ))}
          </g>
        )}
        <rect x={M.left} y={M.top} width={innerW} height={innerH} fill="transparent" />
      </svg>
      {hover !== null && days[hover] && (
        <div
          role="status"
          className="pointer-events-none absolute top-8 z-10 min-w-40 rounded-md border border-border bg-surface px-3 py-2 text-xs shadow-lg"
          style={{
            left: Math.min(x(hover) + 12, width - 180),
          }}
        >
          <div className="mb-1 font-medium text-ink">{shortDate(days[hover])}</div>
          {series.map((s) => (
            <div key={s.key} className="flex items-center justify-between gap-4 text-ink-2">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: s.color }} />
                {s.label}
              </span>
              <span className="tabular text-ink">{format(s.values[hover] ?? 0)}</span>
            </div>
          ))}
          {footer?.(hover)}
        </div>
      )}
    </div>
  );
}
