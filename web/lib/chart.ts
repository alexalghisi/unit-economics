/** Small, dependency-free helpers for the SVG charts. */

export interface Scale {
  (value: number): number;
  domain: [number, number];
}

export function linear(domain: [number, number], range: [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  const scale = ((v: number) => r0 + ((v - d0) / span) * (r1 - r0)) as Scale;
  scale.domain = domain;
  return scale;
}

/** "Nice" ticks: 1, 2 or 5 times a power of ten, covering [min, max]. */
export function niceTicks(min: number, max: number, target = 5): number[] {
  if (min === max) return [min];
  const raw = (max - min) / target;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? power * 10;
  const start = Math.floor(min / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
  if ((ticks[ticks.length - 1] ?? min) < max) ticks.push(Number((start + ticks.length * step).toFixed(10)));
  return ticks;
}

/** Domain for values that may go negative, always including zero (the baseline). */
export function extentWithZero(values: number[]): [number, number] {
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  return [lo, hi === lo ? lo + 1 : hi];
}

/** Index of the point nearest to x (pixel) for evenly spaced points. */
export function nearestIndex(x: number, left: number, width: number, count: number): number {
  if (count <= 1) return 0;
  const step = width / (count - 1);
  return Math.max(0, Math.min(count - 1, Math.round((x - left) / step)));
}

export function linePath(points: [number, number][]): string {
  return points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
}

/** Y positions for direct labels at the line ends, pushed apart so they never overlap. */
export function endLabels(ys: number[], gap = 13): number[] {
  const order = ys.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y);
  for (let k = 1; k < order.length; k++) {
    const prev = order[k - 1];
    const cur = order[k];
    if (prev && cur && cur.y - prev.y < gap) cur.y = prev.y + gap;
  }
  const out = new Array<number>(ys.length);
  for (const { y, i } of order) out[i] = y;
  return out;
}
