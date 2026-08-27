import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PriceHistoryComparison } from "@/lib/api/types";
import { PriceSparkline, sparklinePoints } from "./price-sparkline";

function coordinates(prices: string[]) {
  return sparklinePoints(prices.map((price, index) => ({ date: `2026-08-${24 + index}`, price })))
    .split(" ").map((point) => point.split(",").map(Number));
}

describe("price history sparkline", () => {
  it("rises then plateaus", () => {
    const points = coordinates(["500", "550", "623", "623"]);
    expect(points[0][1]).toBeGreaterThan(points[1][1]);
    expect(points[1][1]).toBeGreaterThan(points[2][1]);
    expect(points[2][1]).toBe(points[3][1]);
  });

  it("falls then plateaus", () => {
    const points = coordinates(["650", "600", "550", "550"]);
    expect(points[0][1]).toBeLessThan(points[1][1]);
    expect(points[1][1]).toBeLessThan(points[2][1]);
    expect(points[2][1]).toBe(points[3][1]);
  });

  it("does not exaggerate near-flat movement", () => {
    const points = coordinates(["500", "501", "500"]);
    expect(Math.abs(points[0][1] - points[1][1])).toBeLessThan(3);
  });

  it("renders two points and exposes observations on tap, with a dash for one", () => {
    const history = { daily_series: [{ date: "2026-08-26", price: "500" }, { date: "2026-08-27", price: "550" }] } as PriceHistoryComparison;
    const { rerender } = render(<PriceSparkline history={history} currency="GBP" />);
    expect(screen.getByLabelText(/26 Aug.*£500.*27 Aug.*£550/)).toBeInTheDocument();
    rerender(<PriceSparkline history={{ ...history, daily_series: history.daily_series!.slice(0, 1) }} currency="GBP" />);
    expect(screen.getByLabelText("Insufficient price history")).toHaveTextContent("—");
  });
});
