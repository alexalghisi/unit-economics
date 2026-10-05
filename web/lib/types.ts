/** Mirrors the API's response models. Money and token prices arrive as decimal strings. */
export type Decimal = string;

export interface Amounts {
  revenue: Decimal;
  cost: Decimal;
  margin: Decimal;
  margin_pct: Decimal | null;
  llm_requests: number;
  tokens: number;
}

export interface Summary extends Amounts {
  start: string;
  end: string;
  customers: number;
  unprofitable_customers: number;
}

export interface CustomerMargin extends Amounts {
  customer_id: string;
  name: string | null;
  product_id: string | null;
  cost_per_request: Decimal | null;
}

export interface CustomerList {
  start: string;
  end: string;
  total: number;
  items: CustomerMargin[];
}

export interface CostPercentiles {
  p50: Decimal | null;
  p90: Decimal | null;
  p99: Decimal | null;
}

export interface FeatureMargin extends Amounts {
  feature: string;
  cost_per_request: CostPercentiles;
}

export interface ModelUsage extends Amounts {
  model: string;
}

export interface DayPoint {
  day: string;
  revenue: Decimal;
  cost: Decimal;
  margin: Decimal;
}

export interface CustomerDetail {
  start: string;
  end: string;
  customer: CustomerMargin;
  features: FeatureMargin[];
  models: ModelUsage[];
  days: DayPoint[];
  cost_per_request: CostPercentiles;
}

export interface Meter {
  id: string;
  slug: string;
  name: string;
  event_name: string;
  aggregation: "count" | "sum" | "unique";
  property: string | null;
}

export interface Tier {
  up_to: Decimal | null;
  unit_amount: Decimal;
  flat_amount: Decimal;
}

export type PriceSpec =
  | { kind: "flat"; amount: Decimal }
  | { kind: "per_unit"; meter_id: string; unit_amount: Decimal; per: number }
  | { kind: "tiered"; meter_id: string; mode: "graduated" | "volume"; tiers: Tier[]; per: number }
  | {
      kind: "credits";
      meter_id: string;
      amount: Decimal;
      included_units: Decimal;
      overage_unit_amount: Decimal;
      per: number;
    };

export interface Product {
  id: string;
  name: string;
  prices: PriceSpec[];
}

export interface SimulatedCustomer {
  customer_id: string;
  name: string | null;
  cost: Decimal;
  revenue_before: Decimal;
  revenue_after: Decimal;
  revenue_delta: Decimal;
  margin_before: Decimal;
  margin_after: Decimal;
  change: "became_unprofitable" | "became_profitable" | null;
}

export interface SimulationResult {
  product_id: string;
  start: string;
  end: string;
  customers: number;
  revenue_before: Decimal;
  revenue_after: Decimal;
  revenue_delta: Decimal;
  margin_before: Decimal;
  margin_after: Decimal;
  unprofitable_before: number;
  unprofitable_after: number;
  became_unprofitable: number;
  became_profitable: number;
  items: SimulatedCustomer[];
  timings: { query_ms: number; pricing_ms: number; total_ms: number };
}

export interface CurrentPeriod {
  customer_external_id: string;
  product_id: string;
  period_start: string;
  period_end: string;
  lines: { kind: string; meter_id: string | null; quantity: Decimal; amount: Decimal }[];
  total: Decimal;
  llm_cost: Decimal;
}
