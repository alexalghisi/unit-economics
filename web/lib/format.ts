const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const usdCompact = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});
const integer = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export function toNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

/** Whole-dollar amounts get cents; sub-cent amounts (cost per request) keep significant digits. */
export function money(value: string | number | null | undefined): string {
  const n = toNumber(value);
  if (n !== 0 && Math.abs(n) < 1) {
    return `${n < 0 ? "−" : ""}$${Math.abs(n).toPrecision(2)}`;
  }
  return n < 0 ? `−${usd.format(-n)}` : usd.format(n);
}

export function signedMoney(value: string | number): string {
  const n = toNumber(value);
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${usd.format(Math.abs(n))}`;
}

export function moneyCompact(value: number): string {
  return usdCompact.format(value);
}

export function count(value: number): string {
  return integer.format(value);
}

export function compactCount(value: number): string {
  return compact.format(value);
}

export function percent(value: string | null): string {
  return value === null ? "–" : `${toNumber(value).toFixed(1)}%`;
}

export function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[Number(m) - 1] ?? "?"} ${Number(d)}`;
}
