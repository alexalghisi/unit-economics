"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { LineChart } from "@/components/charts/line-chart";
import { Kpi, KpiSkeleton } from "@/components/kpi";
import { ErrorState, Loading } from "@/components/state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { WindowSelect } from "@/components/window-select";
import { api } from "@/lib/api";
import { compactCount, count, money, moneyCompact, percent, shortDate, signedMoney, toNumber } from "@/lib/format";
import type { Amounts } from "@/lib/types";
import { useDays } from "@/lib/use-days";

function AmountsTable({ rows, label }: { rows: (Amounts & { name: string })[]; label: string }) {
  return (
    <Table>
      <thead>
        <tr>
          <Th>{label}</Th>
          <Th className="text-right">Revenue</Th>
          <Th className="text-right">LLM cost</Th>
          <Th className="text-right">Margin</Th>
          <Th className="text-right">Requests</Th>
          <Th className="text-right">Tokens</Th>
        </tr>
      </thead>
      <tbody className="tabular">
        {rows.map((r) => (
          <Tr key={r.name}>
            <Td>{r.name || "unattributed"}</Td>
            <Td className="text-right">{money(r.revenue)}</Td>
            <Td className="text-right">{money(r.cost)}</Td>
            <Td className={toNumber(r.margin) < 0 ? "text-right font-medium text-critical" : "text-right"}>
              {signedMoney(r.margin)}
            </Td>
            <Td className="text-right text-ink-2">{compactCount(r.llm_requests)}</Td>
            <Td className="text-right text-ink-2">{compactCount(r.tokens)}</Td>
          </Tr>
        ))}
      </tbody>
    </Table>
  );
}

export function CustomerView({ id }: { id: string }) {
  const [days, setDays] = useDays();
  const detail = useQuery({
    queryKey: ["customer", id, days],
    queryFn: () => api.customer(id, days),
    placeholderData: keepPreviousData,
  });
  const period = useQuery({ queryKey: ["period", id], queryFn: () => api.currentPeriod(id), retry: false });
  const products = useQuery({ queryKey: ["products"], queryFn: api.products });
  const meters = useQuery({ queryKey: ["meters"], queryFn: api.meters });
  const d = detail.data;
  const c = d?.customer;
  const plan = products.data?.find((p) => p.id === c?.product_id)?.name;

  return (
    <div className="space-y-6">
      <Link href={`/?days=${days}`} className="inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <ArrowLeft size={14} /> Overview
      </Link>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{c?.name ?? id}</h1>
          <p className="text-sm text-ink-2">
            {id}
            {plan ? ` · ${plan} plan` : c && !c.product_id ? " · no subscription" : ""}
          </p>
        </div>
        <WindowSelect value={days} onChange={setDays} />
      </div>
      {detail.error ? <ErrorState error={detail.error} /> : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {c && d ? (
          <>
            <Kpi label="Revenue" value={money(c.revenue)} hint={`last ${days} days`} />
            <Kpi label="LLM cost" value={money(c.cost)} hint={`${count(c.llm_requests)} requests`} />
            <Kpi
              label="Margin"
              value={signedMoney(c.margin)}
              hint={percent(c.margin_pct)}
              tone={toNumber(c.margin) < 0 ? "critical" : "default"}
            />
            <Kpi
              label="Cost per request"
              value={money(d.cost_per_request.p50)}
              hint={`p90 ${money(d.cost_per_request.p90)} · p99 ${money(d.cost_per_request.p99)}`}
            />
          </>
        ) : (
          Array.from({ length: 4 }, (_, i) => <KpiSkeleton key={i} />)
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Revenue vs LLM cost</CardTitle>
        </CardHeader>
        <CardContent>
          {d ? (
            <LineChart
              days={d.days.map((p) => p.day)}
              series={[
                { key: "revenue", label: "Revenue", color: "var(--series-1)", values: d.days.map((p) => toNumber(p.revenue)) },
                { key: "cost", label: "LLM cost", color: "var(--series-2)", values: d.days.map((p) => toNumber(p.cost)) },
              ]}
              format={money}
              axisFormat={moneyCompact}
            />
          ) : (
            <Loading />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>By feature</CardTitle>
          </CardHeader>
          <CardContent>{d ? <AmountsTable label="Feature" rows={d.features.map((f) => ({ ...f, name: f.feature }))} /> : <Loading />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>By model</CardTitle>
          </CardHeader>
          <CardContent>{d ? <AmountsTable label="Model" rows={d.models.map((m) => ({ ...m, name: m.model }))} /> : <Loading />}</CardContent>
        </Card>
      </div>

      {period.data && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Current billing period</CardTitle>
              <CardDescription>
                {shortDate(period.data.period_start)} – {shortDate(period.data.period_end)}, priced as the invoice will be
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <Table>
              <thead>
                <tr>
                  <Th>Line</Th>
                  <Th className="text-right">Quantity</Th>
                  <Th className="text-right">Amount</Th>
                </tr>
              </thead>
              <tbody className="tabular">
                {period.data.lines.map((l, i) => (
                  <Tr key={i}>
                    <Td>
                      {l.kind}
                      {l.meter_id ? ` · ${meters.data?.find((m) => m.id === l.meter_id)?.name ?? "meter"}` : ""}
                    </Td>
                    <Td className="text-right text-ink-2">{l.meter_id ? count(toNumber(l.quantity)) : "–"}</Td>
                    <Td className="text-right">{money(l.amount)}</Td>
                  </Tr>
                ))}
                <Tr>
                  <Td className="font-medium">Total so far</Td>
                  <Td className="text-right text-ink-2">LLM cost {money(period.data.llm_cost)}</Td>
                  <Td className="text-right font-medium">{money(period.data.total)}</Td>
                </Tr>
              </tbody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
