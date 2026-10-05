"use client";

import { AlertTriangle, ArrowDown, ArrowUp } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { type SortKey, filterAndSort } from "@/lib/customers";
import { compactCount, money, percent, toNumber } from "@/lib/format";
import type { CustomerMargin } from "@/lib/types";
import { cn } from "@/lib/utils";

const COLUMNS: { key: SortKey | null; label: string; align?: "right" }[] = [
  { key: null, label: "Customer" },
  { key: null, label: "Plan" },
  { key: "revenue", label: "Revenue", align: "right" },
  { key: "cost", label: "LLM cost", align: "right" },
  { key: "margin", label: "Margin", align: "right" },
  { key: "margin_pct", label: "Margin %", align: "right" },
  { key: "llm_requests", label: "Requests", align: "right" },
  { key: null, label: "Cost / request", align: "right" },
];

const PAGE = 15;

export function CustomerTable({ rows, plans, days }: { rows: CustomerMargin[]; plans: Map<string, string>; days: number }) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("margin");
  const [descending, setDescending] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const sorted = useMemo(() => filterAndSort(rows, query, sort, descending), [rows, query, sort, descending]);

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <Input
          type="search"
          placeholder="Search customers"
          aria-label="Search customers"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setShown(PAGE);
          }}
          className="w-64 max-w-full"
        />
        <span className="text-xs text-muted">{sorted.length} customers</span>
      </div>
      <Table>
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <Th key={c.label} className={cn(c.align === "right" && "text-right")}>
                {c.key ? (
                  <button
                    className="inline-flex items-center gap-1 hover:text-ink"
                    onClick={() => {
                      if (sort === c.key) setDescending(!descending);
                      else {
                        setSort(c.key as SortKey);
                        setDescending(c.key !== "margin" && c.key !== "margin_pct");
                      }
                    }}
                    aria-sort={sort === c.key ? (descending ? "descending" : "ascending") : undefined}
                  >
                    {c.label}
                    {sort === c.key && (descending ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
                  </button>
                ) : (
                  c.label
                )}
              </Th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular">
          {sorted.slice(0, shown).map((r) => {
            const negative = toNumber(r.margin) < 0;
            return (
              <Tr key={r.customer_id}>
                <Td>
                  <Link href={`/customers/${encodeURIComponent(r.customer_id)}?days=${days}`} className="font-medium hover:underline">
                    {r.name ?? r.customer_id}
                  </Link>
                  <span className="ml-2 text-xs text-muted">{r.customer_id}</span>
                </Td>
                <Td className="text-ink-2">{r.product_id ? plans.get(r.product_id) ?? "–" : "No plan"}</Td>
                <Td className="text-right">{money(r.revenue)}</Td>
                <Td className="text-right">{money(r.cost)}</Td>
                <Td className="text-right">
                  {negative ? (
                    <Badge variant="critical">
                      <AlertTriangle size={12} aria-hidden="true" />
                      {money(r.margin)}
                    </Badge>
                  ) : (
                    money(r.margin)
                  )}
                </Td>
                <Td className="text-right text-ink-2">{percent(r.margin_pct)}</Td>
                <Td className="text-right text-ink-2">{compactCount(r.llm_requests)}</Td>
                <Td className="text-right text-ink-2">{r.cost_per_request ? money(r.cost_per_request) : "–"}</Td>
              </Tr>
            );
          })}
        </tbody>
      </Table>
      {shown < sorted.length && (
        <div className="mt-3 flex justify-center">
          <Button variant="outline" size="sm" onClick={() => setShown(shown + PAGE * 2)}>
            Show more
          </Button>
        </div>
      )}
    </div>
  );
}
