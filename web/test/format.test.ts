import { describe, expect, it } from "vitest";
import { money, percent, shortDate, signedMoney, toNumber } from "@/lib/format";

describe("format", () => {
  it("shows cents for normal amounts and significant digits for tiny ones", () => {
    expect(money("1234.5")).toBe("$1,234.50");
    expect(money("0.000437")).toBe("$0.00044");
    expect(money("-0.004")).toBe("−$0.0040");
    expect(money("0.0312")).toBe("$0.031");
    expect(money("0.5")).toBe("$0.50");
    expect(money("-12")).toBe("−$12.00");
    expect(money(null)).toBe("$0.00");
  });

  it("signs deltas with a real minus", () => {
    expect(signedMoney("12")).toBe("+$12.00");
    expect(signedMoney("-3.5")).toBe("−$3.50");
    expect(signedMoney("0")).toBe("$0.00");
  });

  it("handles missing and malformed values", () => {
    expect(percent(null)).toBe("–");
    expect(percent("86.99")).toBe("87.0%");
    expect(toNumber("abc")).toBe(0);
    expect(shortDate("2026-10-05")).toBe("Oct 5");
  });
});
