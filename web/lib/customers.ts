import type { CustomerMargin } from "./types";
import { toNumber } from "./format";

export type SortKey = "margin" | "revenue" | "cost" | "margin_pct" | "llm_requests";

export function filterAndSort(
  rows: CustomerMargin[],
  query: string,
  sort: SortKey,
  descending: boolean,
): CustomerMargin[] {
  const q = query.trim().toLowerCase();
  const filtered = q
    ? rows.filter((r) => r.customer_id.toLowerCase().includes(q) || (r.name ?? "").toLowerCase().includes(q))
    : rows;
  const value = (r: CustomerMargin): number =>
    sort === "llm_requests" ? r.llm_requests : sort === "margin_pct" ? toNumber(r.margin_pct ?? "-1e9") : toNumber(r[sort]);
  return [...filtered].sort((a, b) => {
    const d = value(a) - value(b);
    return (descending ? -d : d) || a.customer_id.localeCompare(b.customer_id);
  });
}
