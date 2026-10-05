import { describe, expect, it } from "vitest";
import { endLabels, extentWithZero, linear, linePath, nearestIndex, niceTicks } from "@/lib/chart";

describe("chart helpers", () => {
  it("maps a domain onto a range, inverted for SVG y", () => {
    const y = linear([0, 100], [200, 0]);
    expect(y(0)).toBe(200);
    expect(y(50)).toBe(100);
  });

  it("produces nice ticks that cover the data", () => {
    expect(niceTicks(0, 1340)).toEqual([0, 500, 1000, 1500]);
    expect(niceTicks(-20, 80)).toEqual([-20, 0, 20, 40, 60, 80]);
    const ticks = niceTicks(0, 0.0007);
    expect(ticks[0]).toBe(0);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(0.0007);
  });

  it("keeps zero in the domain so bars start at the baseline", () => {
    expect(extentWithZero([5, 9])).toEqual([0, 9]);
    expect(extentWithZero([-4, 9])).toEqual([-4, 9]);
    expect(extentWithZero([0, 0])).toEqual([0, 1]);
  });

  it("finds the nearest point for the crosshair", () => {
    expect(nearestIndex(0, 10, 100, 11)).toBe(0);
    expect(nearestIndex(54, 10, 100, 11)).toBe(4);
    expect(nearestIndex(500, 10, 100, 11)).toBe(10);
  });

  it("pushes colliding end labels apart and keeps distant ones in place", () => {
    expect(endLabels([200, 196])).toEqual([209, 196]);
    expect(endLabels([50, 200])).toEqual([50, 200]);
  });

  it("builds an SVG path", () => {
    expect(linePath([[0, 1], [2.25, 3]])).toBe("M0.0,1.0L2.3,3.0");
  });
});
