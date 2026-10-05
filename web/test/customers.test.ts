import { describe, expect, it } from "vitest";
import { filterAndSort } from "@/lib/customers";
import type { CustomerMargin } from "@/lib/types";

function row(id: string, name: string, margin: string, pct: string | null): CustomerMargin {
  return {
    customer_id: id,
    name,
    product_id: null,
    revenue: "0",
    cost: "0",
    margin,
    margin_pct: pct,
    llm_requests: 1,
    tokens: 1,
    cost_per_request: null,
  };
}

const rows = [row("c1", "Atlas Labs", "10", "50"), row("c2", "Blue Bio", "-5", "-20"), row("c3", "Cedar", "0", null)];

describe("customer table", () => {
  it("sorts by margin, worst first", () => {
    expect(filterAndSort(rows, "", "margin", false).map((r) => r.customer_id)).toEqual(["c2", "c3", "c1"]);
  });

  it("puts customers without revenue last when sorting by margin % descending", () => {
    expect(filterAndSort(rows, "", "margin_pct", true).map((r) => r.customer_id)).toEqual(["c1", "c2", "c3"]);
  });

  it("filters by id or name, case-insensitively", () => {
    expect(filterAndSort(rows, "blue", "margin", false).map((r) => r.customer_id)).toEqual(["c2"]);
    expect(filterAndSort(rows, "C3", "margin", false).map((r) => r.customer_id)).toEqual(["c3"]);
  });
});
