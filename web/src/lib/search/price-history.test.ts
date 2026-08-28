import { describe, expect, it } from "vitest";
import { elapsedCompactDay, elapsedShort, elapsedSummaryDay, historyAccessibleLabel, historySignal, historyTooltip, percentageChangeSignal, priceHistoryState, sinceLastSeen, summaryTrend, trendSignal } from "./price-history";

const history = {
  history_status: "PREVIOUS_FOUND" as const,
  previous_price: "378",
  price_change_amount: "24",
  price_change_percent: "6.35",
  previous_observed_at: "2026-08-20T08:00:00Z",
  elapsed_seconds: 3 * 86400 + 4 * 3600,
  previous_observation_run_id: "run-1",
};

describe("observation history language", () => {
  it("reports factual change since the latest observation", () => {
    expect(historySignal(history)).toBe("↑ 6.3%");
    expect(sinceLastSeen(history)).toBe("↑ 6.3% since last seen 3d ago");
    expect(historyTooltip(history, "402", "GBP")).toContain("Previous day £378.00");
    expect(historyTooltip(history, "402", "GBP")).toContain("Daily +6.3%");
  });

  it("uses New rather than zero percent for first observations", () => {
    const first = { ...history, history_status: "FIRST_SEEN" as const, previous_price: null };
    expect(historySignal(first)).toBe("New");
    expect(historySignal(first)).not.toMatch(/[↑↓→]/);
    expect(sinceLastSeen(first)).toBe("First seen");
  });

  it("uses consistent directional arrows and accessible descriptions", () => {
    expect(historySignal(history)).toBe("↑ 6.3%");
    expect(historyAccessibleLabel(history)).toContain("increased by 6.3 percent");
    const decrease = { ...history, price_change_amount: "-24", price_change_percent: "-4.1" };
    expect(historySignal(decrease)).toBe("↓ 4.1%");
    expect(historyAccessibleLabel(decrease)).toContain("decreased by 4.1 percent");
    const unchanged = { ...history, price_change_amount: "0", price_change_percent: "0" };
    expect(historySignal(unchanged)).toBe("— 0%");
    expect(historyAccessibleLabel(unchanged)).toBe("No price change since last seen");
    expect(percentageChangeSignal(0)).toBe("— (0%)");
  });

  it("distinguishes unresolved history from explicitly first seen", () => {
    expect(priceHistoryState(undefined)).toBe("LOADING");
    expect(historySignal(undefined)).toBe("Loading…");
    expect(historyAccessibleLabel(undefined)).toBe("Loading price history");
    const first = { ...history, history_status: "FIRST_SEEN" as const, previous_price: null };
    expect(priceHistoryState(first)).toBe("FIRST_SEEN");
    expect(historySignal(first)).toBe("New");
  });

  it("uses the server's London calendar-day difference across devices", () => {
    expect(elapsedCompactDay(3600, "2026-08-27T09:50:00Z", new Date("2026-08-27T12:50:00Z"), 1)).toBe("1d ago");
    expect(elapsedSummaryDay(3600, "2026-08-27T09:50:00Z", new Date("2026-08-27T12:50:00Z"), 1)).toBe("since yesterday");
    expect(elapsedSummaryDay(3600, "2026-08-27T09:50:00Z", new Date("2026-08-27T12:50:00Z"), 3)).toBe("since 3d ago");
  });

  it("formats factual multi-day trends separately from daily movement", () => {
    const rising = { ...history, trend_status: "RISING" as const, trend_start_price: "500", trend_current_price: "623", trend_change_percent: "24.6", trend_span_days: 3, observed_day_count: 4 };
    expect(trendSignal(rising)).toBe("↑ 24.6% / 3d");
    expect(summaryTrend(rising)).toBe("↑ 24.6% over 4 observed days");
    expect(historyTooltip(rising, "623", "GBP")).toContain("£500.00 → £623.00");
    expect(trendSignal({ ...rising, trend_status: "INSUFFICIENT_HISTORY" })).toBe("");
  });

  it("formats observed elapsed intervals", () => {
    expect(elapsedShort(18 * 3600)).toBe("18h");
    expect(elapsedShort(15 * 86400)).toBe("2w");
  });

  it.each([
    [3 * 3600, "today", "today"],
    [24 * 3600 - 60, "today", "today"],
    [24 * 3600, "1d ago", "since yesterday"],
    [48 * 3600 - 60, "1d ago", "since yesterday"],
    [48 * 3600, "2d ago", "since 2d ago"],
    [72 * 3600, "3d ago", "since 3d ago"],
  ])("uses day-level table and summary wording for %s seconds", (seconds, compact, summary) => {
    expect(elapsedCompactDay(seconds)).toBe(compact);
    expect(elapsedSummaryDay(seconds)).toBe(summary);
    expect(compact).not.toMatch(/[hm]/);
    expect(summary).not.toMatch(/\d+[hm]/);
  });

  it("uses London calendar dates rather than elapsed 24-hour periods", () => {
    const now = new Date("2026-08-27T08:00:00Z");
    expect(elapsedCompactDay(15 * 3600, "2026-08-26T17:00:00Z", now)).toBe("1d ago");
    expect(elapsedSummaryDay(15 * 3600, "2026-08-26T17:00:00Z", now)).toBe("since yesterday");
    expect(elapsedSummaryDay(3600, "2026-08-27T07:00:00Z", now)).toBe("today");
    expect(elapsedCompactDay(30 * 3600, "2026-08-25T17:00:00Z", now)).toBe("2d ago");
  });
});
