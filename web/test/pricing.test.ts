import { describe, expect, it } from "vitest";
import { addTier, describePrice, newPrice, removeTier, samePrices, validatePrices } from "@/lib/pricing";
import type { Meter, PriceSpec } from "@/lib/types";

const meters: Meter[] = [
  { id: "m1", slug: "tokens", name: "LLM tokens", event_name: "llm_usage", aggregation: "sum", property: "total_tokens" },
];

describe("pricing editor logic", () => {
  it("describes prices the way a pricing page would", () => {
    expect(describePrice({ kind: "per_unit", meter_id: "m1", unit_amount: "3", per: 1_000_000 }, meters)).toBe(
      "$3 per 1M llm tokens",
    );
    expect(
      describePrice(
        { kind: "credits", meter_id: "m1", amount: "99", included_units: "40000000", overage_unit_amount: "2.5", per: 1_000_000 },
        meters,
      ),
    ).toBe("$99 includes 40M llm tokens, then $2.5 per 1M");
  });

  it("adds a tier before the open-ended one", () => {
    const tiers = addTier([{ up_to: null, unit_amount: "2", flat_amount: "0" }]);
    expect(tiers.map((t) => t.up_to)).toEqual(["1000000", null]);
    expect(addTier(tiers).map((t) => t.up_to)).toEqual(["1000000", "2000000", null]);
  });

  it("never removes the open-ended tier", () => {
    const tiers = addTier([{ up_to: null, unit_amount: "2", flat_amount: "0" }]);
    expect(removeTier(tiers, 1)).toBe(tiers);
    expect(removeTier(tiers, 0)).toHaveLength(1);
  });

  it("validates what the API would reject", () => {
    const tiered = newPrice("tiered", "m1");
    expect(validatePrices([tiered])).toEqual([]);
    const bad: PriceSpec = {
      ...tiered,
      kind: "tiered",
      tiers: [
        { up_to: "100", unit_amount: "1", flat_amount: "0" },
        { up_to: "50", unit_amount: "1", flat_amount: "0" },
        { up_to: null, unit_amount: "-1", flat_amount: "0" },
      ],
    } as PriceSpec;
    expect(validatePrices([bad])).toEqual([
      "price 1: tier bounds must increase",
      "price 1: amounts must be non-negative numbers",
    ]);
    expect(validatePrices([])).toEqual(["add at least one price"]);
  });

  it("treats 2.5 and 2.50 as the same price", () => {
    const a = newPrice("per_unit", "m1");
    expect(samePrices([a], [{ ...a, unit_amount: "1.00" } as PriceSpec])).toBe(true);
    expect(samePrices([a], [{ ...a, unit_amount: "1.01" } as PriceSpec])).toBe(false);
  });
});
