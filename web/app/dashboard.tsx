"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { LineChart } from "@/components/charts/line-chart";
import { MarginBars } from "@/components/charts/margin-bars";
import { CustomerTable } from "@/components/customer-table";
import { Kpi, KpiSkeleton } from "@/components/kpi";
import { ErrorState, Loading } from "@/components/state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, Td, Th, Tr } from "@/components/ui/table";
import { WindowSelect } from "@/components/window-select";
import { api } from "@/lib/api";
import { compactCount, money, moneyCompact, percent, shortDate, signedMoney, toNumber } from "@/lib/format";
import { useDays } from "@/lib/use-days";

export function Dashboard() {
  const [days, setDays] = useDays();
  const opts = { placeholderData: keepPreviousData };
  const summary = useQuery({ queryKey: ["summary", days], queryFn: () => api.summary(days), ...opts });
  const series = useQuery({ queryKey: ["timeseries", days], queryFn: () => api.timeseries(days), ...opts });
  const features = useQuery({ queryKey: ["features", days], queryFn: () => api.features(days), ...opts });
  const customers = useQuery({ queryKey: ["customers", days], queryFn: () => api.customers(days), ...opts });
  const products = useQuery({ queryKey: ["products"], queryFn: api.products });
  const plans = new Map((products.data ?? []).map((p) => [p.id, p.name]));
  const s = summary.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Overview</h1>
          <p className="text-sm text-ink-2">
            Revenue against LLM cost, last {days} days{s ? ` (${shortDate(s.start)} – ${shortDate(s.end)})` : ""}
          </p>
        </div>
        <WindowSelect value={days} onChange={setDays} />
      </div>

      {summary.error ? <ErrorState error={summary.error} /> : null}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {s ? (
          <>
            <Kpi label="Revenue" value={money(s.revenue)} hint={`${s.customers} customers`} />
            <Kpi label="LLM cost" value={money(s.cost)} hint={`${compactCount(s.llm_requests)} requests`} />
            <Kpi label="Gross margin" value={percent(s.margin_pct)} hint={money(s.margin)} />
            <Kpi
              label="Unprofitable customers"
              value={String(s.unprofitable_customers)}
              tone={s.unprofitable_customers > 0 ? "critical" : "default"}
              hint="cost above revenue in this window"
            />
          </>
        ) : (
          Array.from({ length: 4 }, (_, i) => <KpiSkeleton key={i} />)
        )}
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Revenue vs LLM cost</CardTitle>
            <CardDescription>Per day. Revenue is attributed to the days its usage happened.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {series.data ? (
            <LineChart
              days={series.data.map((d) => d.day)}
              series={[
                { key: "revenue", label: "Revenue", color: "var(--series-1)", values: series.data.map((d) => toNumber(d.revenue)) },
                { key: "cost", label: "LLM cost", color: "var(--series-2)", values: series.data.map((d) => toNumber(d.cost)) },
              ]}
              format={money}
              axisFormat={moneyCompact}
              footer={(i) => (
                <div className="mt-1 flex justify-between gap-4 border-t border-border pt-1 text-ink-2">
                  <span>Margin</span>
                  <span className="tabular text-ink">{signedMoney(series.data[i]?.margin ?? "0")}</span>
                </div>
              )}
            />
          ) : (
            <Loading />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader>
            <div>
              <CardTitle>Margin by feature</CardTitle>
              <CardDescription>Revenue allocated by each feature&apos;s share of the metered usage.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {features.data ? (
              <>
                <MarginBars
                  format={(v) => signedMoney(v)}
                  rows={features.data.map((f) => ({
                    key: f.feature,
                    label: f.feature || "unattributed",
                    value: toNumber(f.margin),
                    detail: (
                      <div className="space-y-0.5 text-ink-2">
                        <div>
                          Revenue <span className="tabular text-ink">{money(f.revenue)}</span>
                        </div>
                        <div>
                          LLM cost <span className="tabular text-ink">{money(f.cost)}</span>
                        </div>
                      </div>
                    ),
                  }))}
                />
                <Table className="mt-4">
                  <thead>
                    <tr>
                      <Th>Feature</Th>
                      <Th className="text-right">Requests</Th>
                      <Th className="text-right">p50 cost</Th>
                      <Th className="text-right">p90</Th>
                      <Th className="text-right">p99</Th>
                    </tr>
                  </thead>
                  <tbody className="tabular">
                    {features.data
                      .filter((f) => f.llm_requests > 0)
                      .map((f) => (
                        <Tr key={f.feature}>
                          <Td>{f.feature}</Td>
                          <Td className="text-right text-ink-2">{compactCount(f.llm_requests)}</Td>
                          <Td className="text-right">{money(f.cost_per_request.p50)}</Td>
                          <Td className="text-right">{money(f.cost_per_request.p90)}</Td>
                          <Td className="text-right">{money(f.cost_per_request.p99)}</Td>
                        </Tr>
                      ))}
                  </tbody>
                </Table>
              </>
            ) : (
              <Loading />
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <div>
              <CardTitle>Least profitable customers</CardTitle>
              <CardDescription>Lowest margin first.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {customers.data ? (
              <ul className="divide-y divide-border">
                {customers.data.items.slice(0, 8).map((c) => (
                  <li key={c.customer_id}>
                    <Link
                      href={`/customers/${encodeURIComponent(c.customer_id)}?days=${days}`}
                      className="flex items-center justify-between gap-3 py-2 text-sm hover:bg-surface-2"
                    >
                      <span className="min-w-0 truncate">
                        <span className="font-medium">{c.name ?? c.customer_id}</span>
                        <span className="ml-2 text-xs text-muted">{c.product_id ? plans.get(c.product_id) : "No plan"}</span>
                      </span>
                      <span className={toNumber(c.margin) < 0 ? "tabular font-medium text-critical" : "tabular"}>
                        {signedMoney(c.margin)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <Loading />
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Customers</CardTitle>
            <CardDescription>Margin per customer over the window. Click a customer for the breakdown.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {customers.error ? <ErrorState error={customers.error} /> : null}
          {customers.data ? <CustomerTable rows={customers.data.items} plans={plans} days={days} /> : <Loading height={400} />}
        </CardContent>
      </Card>
    </div>
  );
}
