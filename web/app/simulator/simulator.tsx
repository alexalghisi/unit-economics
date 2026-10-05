"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Kpi, KpiSkeleton } from "@/components/kpi";
import { PriceEditor } from "@/components/price-editor";
import { ErrorState, Loading } from "@/components/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { WindowSelect } from "@/components/window-select";
import { api } from "@/lib/api";
import { money, signedMoney, toNumber } from "@/lib/format";
import { samePrices, validatePrices } from "@/lib/pricing";
import type { PriceSpec } from "@/lib/types";
import { useDebounced } from "@/lib/use-debounced";
import { useDays } from "@/lib/use-days";
import { cn } from "@/lib/utils";

export function Simulator() {
  const [days, setDays] = useDays();
  const products = useQuery({ queryKey: ["products"], queryFn: api.products });
  const meters = useQuery({ queryKey: ["meters"], queryFn: api.meters });
  const [productId, setProductId] = useState<string | null>(null);
  const [prices, setPrices] = useState<PriceSpec[]>([]);

  const product = products.data?.find((p) => p.id === productId);
  useEffect(() => {
    // Default to the plan with metered prices: that is where pricing questions live.
    if (!productId && products.data?.length) {
      const first = products.data.find((p) => p.prices.some((x) => x.kind !== "flat")) ?? products.data[0];
      if (first) {
        setProductId(first.id);
        setPrices(first.prices);
      }
    }
  }, [productId, products.data]);

  const errors = validatePrices(prices);
  const candidate = useDebounced(prices, 250);
  const dirty = product ? !samePrices(product.prices, prices) : false;
  const sim = useQuery({
    queryKey: ["simulate", productId, days, candidate],
    queryFn: ({ signal }) => api.simulate({ product_id: productId ?? "", days, prices: candidate }, signal),
    enabled: productId !== null && validatePrices(candidate).length === 0,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
  const r = sim.data;
  const changed = useMemo(() => r?.items.filter((i) => i.change !== null) ?? [], [r]);
  const deltaPct = r && toNumber(r.revenue_before) > 0 ? (toNumber(r.revenue_delta) / toNumber(r.revenue_before)) * 100 : 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Pricing simulator</h1>
          <p className="text-sm text-ink-2">
            Replays the last {days} days of real usage under the prices on the left. Nothing is saved.
          </p>
        </div>
        <WindowSelect value={days} onChange={setDays} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="space-y-4">
          <Card>
            <CardContent className="space-y-4 pt-4">
              <div className="flex items-end gap-2">
                <Label className="flex-1">
                  Plan
                  <Select
                    value={productId ?? ""}
                    onChange={(e) => {
                      const next = products.data?.find((p) => p.id === e.target.value);
                      setProductId(e.target.value);
                      setPrices(next?.prices ?? []);
                    }}
                  >
                    {products.data?.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </Select>
                </Label>
                <Button variant="outline" disabled={!dirty} onClick={() => product && setPrices(product.prices)}>
                  <RotateCcw size={14} /> Reset
                </Button>
              </div>
              {meters.data ? <PriceEditor prices={prices} meters={meters.data} onChange={setPrices} /> : <Loading height={160} />}
              {errors.length > 0 && (
                <ul role="alert" className="space-y-1 text-xs text-critical">
                  {errors.map((e) => (
                    <li key={e} className="flex items-center gap-1">
                      <AlertTriangle size={12} aria-hidden="true" /> {e}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          {sim.error ? <ErrorState error={sim.error} /> : null}
          <div className={cn("grid grid-cols-2 gap-3 transition-opacity", sim.isFetching && "opacity-60")}>
            {r ? (
              <>
                <Kpi
                  label="Revenue change"
                  value={signedMoney(r.revenue_delta)}
                  hint={`${deltaPct >= 0 ? "+" : ""}${deltaPct.toFixed(1)}% · ${money(r.revenue_before)} → ${money(r.revenue_after)}`}
                />
                <Kpi
                  label="Unprofitable customers"
                  value={`${r.unprofitable_before} → ${r.unprofitable_after}`}
                  tone={r.unprofitable_after > 0 ? "critical" : "default"}
                  hint={`of ${r.customers} on this plan`}
                />
                <Kpi
                  label="Become profitable"
                  value={String(r.became_profitable)}
                  hint={`${r.became_unprofitable} become unprofitable`}
                />
                <Kpi
                  label="Margin"
                  value={signedMoney(r.margin_after)}
                  hint={`was ${signedMoney(r.margin_before)} · computed in ${Math.round(r.timings.total_ms)} ms`}
                />
              </>
            ) : (
              Array.from({ length: 4 }, (_, i) => <KpiSkeleton key={i} />)
            )}
          </div>

          <Card>
            <CardHeader>
              <div>
                <CardTitle>Customers on this plan</CardTitle>
                <CardDescription>
                  Lowest margin under the candidate prices first
                  {changed.length > 0 ? ` · ${changed.length} change sides` : ""}
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {r ? (
                <Table>
                  <thead>
                    <tr>
                      <Th>Customer</Th>
                      <Th className="text-right">LLM cost</Th>
                      <Th className="text-right">Revenue now</Th>
                      <Th className="text-right">Candidate</Th>
                      <Th className="text-right">Change</Th>
                      <Th className="text-right">Margin</Th>
                    </tr>
                  </thead>
                  <tbody className={cn("tabular", sim.isFetching && "opacity-60")}>
                    {r.items.slice(0, 20).map((i) => (
                      <Tr key={i.customer_id}>
                        <Td>
                          <Link href={`/customers/${encodeURIComponent(i.customer_id)}?days=${days}`} className="font-medium hover:underline">
                            {i.name ?? i.customer_id}
                          </Link>
                          {i.change === "became_profitable" && (
                            <Badge variant="good" className="ml-2">
                              <CheckCircle2 size={12} aria-hidden="true" /> profitable
                            </Badge>
                          )}
                          {i.change === "became_unprofitable" && (
                            <Badge variant="critical" className="ml-2">
                              <AlertTriangle size={12} aria-hidden="true" /> unprofitable
                            </Badge>
                          )}
                        </Td>
                        <Td className="text-right text-ink-2">{money(i.cost)}</Td>
                        <Td className="text-right text-ink-2">{money(i.revenue_before)}</Td>
                        <Td className="text-right">{money(i.revenue_after)}</Td>
                        <Td className="text-right text-ink-2">{toNumber(i.revenue_delta) === 0 ? "–" : signedMoney(i.revenue_delta)}</Td>
                        <Td className={cn("text-right", toNumber(i.margin_after) < 0 && "font-medium text-critical")}>
                          {signedMoney(i.margin_after)}
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <Loading height={400} />
              )}
              {r && r.items.length > 20 && (
                <p className="mt-2 text-xs text-muted">Showing the 20 lowest margins of {r.items.length}.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
