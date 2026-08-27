import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PriceSparkline, sparklineGeometry } from "./price-sparkline";

function geometry(prices: string[]) {
  return sparklineGeometry(prices.map((price, index) => ({ observed_at: `2026-08-${24 + index}T12:00:00Z`, price })));
}

describe("stock-ticker price sparkline", () => {
  it("renders a smooth rising curve that visibly plateaus", () => {
    const result = geometry(["500", "550", "623", "623"]);
    expect(result.path).toContain(" C ");
    expect(result.coordinates[0][1]).toBeGreaterThan(result.coordinates[1][1]);
    expect(result.coordinates[1][1]).toBeGreaterThan(result.coordinates[2][1]);
    expect(result.coordinates[2][1]).toBe(result.coordinates[3][1]);
    const finalCurve = result.path.split(" C ").at(-1)!.split(" ").map(Number);
    expect([finalCurve[1], finalCurve[3], finalCurve[5]]).toEqual([
      result.coordinates[3][1], result.coordinates[3][1], result.coordinates[3][1],
    ]);
  });

  it("renders a smooth falling curve that visibly plateaus", () => {
    const result = geometry(["650", "600", "550", "550"]);
    expect(result.coordinates[0][1]).toBeLessThan(result.coordinates[1][1]);
    expect(result.coordinates[1][1]).toBeLessThan(result.coordinates[2][1]);
    expect(result.coordinates[2][1]).toBe(result.coordinates[3][1]);
  });

  it("keeps tiny movement visually near-flat and equal prices horizontal", () => {
    const nearFlat = geometry(["500", "501", "500"]);
    expect(Math.abs(nearFlat.coordinates[0][1] - nearFlat.coordinates[1][1])).toBeLessThan(3);
    const flat = geometry(["500", "500", "500"]);
    expect(flat.coordinates.every(([, y]) => y === 12)).toBe(true);
  });

  it("uses actual calendar spacing and supports two points", () => {
    const result = sparklineGeometry([
      { observed_at: "2026-08-01T12:00:00Z", price: "500" },
      { observed_at: "2026-08-03T12:00:00Z", price: "525" },
      { observed_at: "2026-08-07T12:00:00Z", price: "550" },
    ]);
    expect(result.coordinates[1][0] - result.coordinates[0][0]).toBeCloseTo(20);
    expect(result.coordinates[2][0] - result.coordinates[1][0]).toBeCloseTo(40);
    expect(geometry(["500", "550"]).path).toContain(" L ");
    expect(geometry(["500", "550"]).path).not.toContain(" C ");
  });

  it("keeps same-day observations close and never overshoots the plot range", () => {
    const result = sparklineGeometry([
      { observed_at: "2026-08-26T09:00:00Z", price: "623" },
      { observed_at: "2026-08-26T12:00:00Z", price: "610" },
      { observed_at: "2026-08-27T12:00:00Z", price: "549" },
      { observed_at: "2026-08-27T15:00:00Z", price: "560" },
    ]);
    expect(result.coordinates[1][0] - result.coordinates[0][0]).toBeLessThan(10);
    expect(result.coordinates[2][0] - result.coordinates[1][0]).toBeGreaterThan(40);
    const yValues = result.path.match(/-?\d+\.\d+/g)!.map(Number).filter((_value, index) => index % 2 === 1);
    expect(Math.min(...yValues)).toBeGreaterThanOrEqual(3);
    expect(Math.max(...yValues)).toBeLessThanOrEqual(21);
  });

  it("renders only a 64 by 22 inline SVG, or a dash for insufficient history", () => {
    const points = [{ observed_at: "2026-08-26T09:10:00Z", price: "500" }, { observed_at: "2026-08-27T09:50:00Z", price: "550" }];
    const { rerender } = render(<PriceSparkline points={points} currency="GBP" />);
    const svg = screen.getByRole("img", { name: /26 Aug.*£500.*27 Aug.*£550/ });
    expect(svg).toHaveAttribute("viewBox", "0 0 64 24");
    expect(svg.querySelectorAll("path")).toHaveLength(2);
    expect(svg.querySelectorAll("circle")).toHaveLength(1);
    expect(svg.querySelector("polyline, text, rect")).toBeNull();
    rerender(<PriceSparkline points={points.slice(0, 1)} currency="GBP" />);
    expect(screen.getByLabelText("Insufficient price history")).toHaveTextContent("—");
  });
});
