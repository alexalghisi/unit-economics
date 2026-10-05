import type { Meter, PriceSpec, Tier } from "./types";

const DECIMAL = /^\d+(\.\d{1,10})?$/;

export function isDecimal(value: string): boolean {
  return DECIMAL.test(value.trim());
}

export function perLabel(per: number): string {
  if (per === 1) return "unit";
  if (per === 1_000_000) return "1M";
  if (per === 1_000) return "1K";
  return per.toLocaleString("en-US");
}

function unitName(meterId: string, meters: Meter[]): string {
  return meters.find((m) => m.id === meterId)?.name.toLowerCase() ?? "units";
}

/** One-line, human description of a price, as shown on a pricing page. */
export function describePrice(price: PriceSpec, meters: Meter[]): string {
  switch (price.kind) {
    case "flat":
      return `$${price.amount} per month`;
    case "per_unit":
      return `$${price.unit_amount} per ${perLabel(price.per)} ${unitName(price.meter_id, meters)}`;
    case "credits":
      return `$${price.amount} includes ${formatUnits(price.included_units, price.per)} ${unitName(price.meter_id, meters)}, then $${price.overage_unit_amount} per ${perLabel(price.per)}`;
    case "tiered":
      return `${price.tiers.length} ${price.mode} tiers on ${unitName(price.meter_id, meters)}`;
  }
}

function formatUnits(units: string, per: number): string {
  const n = Number(units);
  if (per === 1_000_000) return `${n / 1_000_000}M`;
  return n.toLocaleString("en-US");
}

/** Problems that would make the API reject the candidate, so the UI can say so before asking. */
export function validatePrices(prices: PriceSpec[]): string[] {
  const errors: string[] = [];
  if (prices.length === 0) errors.push("add at least one price");
  prices.forEach((price, i) => {
    const at = `price ${i + 1}`;
    const amounts: string[] = [];
    if (price.kind === "flat") amounts.push(price.amount);
    if (price.kind === "per_unit") amounts.push(price.unit_amount);
    if (price.kind === "credits") amounts.push(price.amount, price.included_units, price.overage_unit_amount);
    if (price.kind === "tiered") {
      price.tiers.forEach((t) => amounts.push(t.unit_amount, t.flat_amount));
      const bounds = price.tiers.map((t) => t.up_to);
      if (bounds[bounds.length - 1] !== null) errors.push(`${at}: the last tier must be open-ended`);
      const finite = bounds.slice(0, -1);
      if (finite.some((b) => b === null || !isDecimal(b))) errors.push(`${at}: every tier but the last needs a bound`);
      const nums = finite.map(Number);
      if (nums.some((n, j) => j > 0 && n <= (nums[j - 1] ?? 0))) errors.push(`${at}: tier bounds must increase`);
    }
    if (amounts.some((a) => !isDecimal(a))) errors.push(`${at}: amounts must be non-negative numbers`);
  });
  return errors;
}

/** Adds a tier just before the open-ended one, doubling the previous bound. */
export function addTier(tiers: Tier[]): Tier[] {
  const finite = tiers.slice(0, -1);
  const last = tiers[tiers.length - 1] ?? { up_to: null, unit_amount: "0", flat_amount: "0" };
  const previous = Number(finite[finite.length - 1]?.up_to ?? 0);
  const bound = previous > 0 ? previous * 2 : 1_000_000;
  return [...finite, { up_to: String(bound), unit_amount: last.unit_amount, flat_amount: "0" }, last];
}

export function removeTier(tiers: Tier[], index: number): Tier[] {
  if (tiers.length <= 1 || index === tiers.length - 1) return tiers;
  return tiers.filter((_, i) => i !== index);
}

export function newPrice(kind: PriceSpec["kind"], meterId: string): PriceSpec {
  switch (kind) {
    case "flat":
      return { kind, amount: "0" };
    case "per_unit":
      return { kind, meter_id: meterId, unit_amount: "1", per: 1_000_000 };
    case "credits":
      return { kind, meter_id: meterId, amount: "50", included_units: "10000000", overage_unit_amount: "2", per: 1_000_000 };
    case "tiered":
      return {
        kind,
        meter_id: meterId,
        mode: "graduated",
        per: 1_000_000,
        tiers: [
          { up_to: "50000000", unit_amount: "2", flat_amount: "0" },
          { up_to: null, unit_amount: "1.5", flat_amount: "0" },
        ],
      };
  }
}

export function samePrices(a: PriceSpec[], b: PriceSpec[]): boolean {
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b));
}

function normalize(prices: PriceSpec[]): unknown {
  // "2.5" and "2.50" are the same price.
  return JSON.parse(JSON.stringify(prices), (_k, v: unknown) =>
    typeof v === "string" && isDecimal(v) ? Number(v) : v,
  );
}
