import type { PriceHistoryComparison } from "@/lib/api/types";

export type PriceHistoryState = "LOADING" | "FIRST_SEEN" | "CHANGED" | "UNCHANGED" | "ERROR";

export function priceHistoryState(history: PriceHistoryComparison | null | undefined): PriceHistoryState {
  if (history === null || history === undefined) return "LOADING";
  if (history.history_status === "FIRST_SEEN") return "FIRST_SEEN";
  if (history.history_status !== "PREVIOUS_FOUND" || history.price_change_percent === null || history.previous_price === null) return "ERROR";
  return Number(history.price_change_percent) === 0 ? "UNCHANGED" : "CHANGED";
}

export function elapsedShort(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 48 * 3600) return `${Math.max(1, Math.floor(seconds / 3600))}h`;
  if (seconds < 14 * 86400) return `${Math.floor(seconds / 86400)}d`;
  return `${Math.floor(seconds / (7 * 86400))}w`;
}

export function elapsedDayCount(seconds: number | null): number | null {
  if (seconds === null) return null;
  return Math.max(0, Math.floor(seconds / 86400));
}

export function londonCalendarDayCount(previousObservedAt: string | null | undefined, now = new Date()): number | null {
  if (!previousObservedAt) return null;
  const formatter = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London", year: "numeric", month: "2-digit", day: "2-digit" });
  const ordinal = (value: Date) => {
    const parts = Object.fromEntries(formatter.formatToParts(value).map(({ type, value: part }) => [type, Number(part)]));
    return Date.UTC(parts.year, parts.month - 1, parts.day) / 86_400_000;
  };
  return Math.max(0, ordinal(now) - ordinal(new Date(previousObservedAt)));
}

export function elapsedCompactDay(seconds: number | null, previousObservedAt?: string | null, now = new Date(), dayDifference?: number | null): string {
  const days = dayDifference ?? (previousObservedAt ? londonCalendarDayCount(previousObservedAt, now) : elapsedDayCount(seconds));
  if (days === null) return "";
  return days === 0 ? "today" : `${days}d ago`;
}

export function elapsedSummaryDay(seconds: number | null, previousObservedAt?: string | null, now = new Date(), dayDifference?: number | null): string {
  const days = dayDifference ?? (previousObservedAt ? londonCalendarDayCount(previousObservedAt, now) : elapsedDayCount(seconds));
  if (days === null) return "";
  if (days === 0) return "today";
  if (days === 1) return "since yesterday";
  return `since ${days}d ago`;
}

export function elapsedDetailed(seconds: number | null): string {
  if (seconds === null) return "unknown interval";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days) return `${days} day${days === 1 ? "" : "s"}${hours ? ` ${hours} hour${hours === 1 ? "" : "s"}` : ""}`;
  return `${Math.max(1, hours)} hour${hours === 1 ? "" : "s"}`;
}

export function comparisonPeriod(history: PriceHistoryComparison | null | undefined): string {
  const state = priceHistoryState(history);
  if (state !== "CHANGED" && state !== "UNCHANGED") return "";
  const days = history?.day_difference ?? londonCalendarDayCount(history?.previous_observed_at);
  if (days === null) return "";
  if (days === 0) return "vs today";
  return days === 1 ? "vs yesterday" : `vs ${days}d ago`;
}

export function historySignal(history: PriceHistoryComparison | null | undefined): string {
  const state = priceHistoryState(history);
  if (state === "LOADING") return "Loading…";
  if (state === "FIRST_SEEN") return "New";
  if (state === "ERROR") return "Unavailable";
  if (state === "UNCHANGED") return "— 0%";
  const percent = Number(history?.price_change_percent ?? 0);
  const arrow = percent > 0 ? "↑" : "↓";
  return `${arrow} ${Math.abs(percent).toFixed(1)}%`;
}

export function historyAccessibleLabel(history: PriceHistoryComparison | null | undefined): string {
  const state = priceHistoryState(history);
  if (state === "LOADING") return "Loading price history";
  if (state === "FIRST_SEEN") return "First price observation";
  if (state === "ERROR") return "Price history unavailable";
  const percent = Number(history?.price_change_percent ?? 0);
  if (percent > 0) return `Price increased by ${Math.abs(percent).toFixed(1)} percent since last seen`;
  if (percent < 0) return `Price decreased by ${Math.abs(percent).toFixed(1)} percent since last seen`;
  return "No price change since last seen";
}

export function percentageChangeSignal(percent: number): string {
  if (percent === 0) return "— (0%)";
  const arrow = percent > 0 ? "↑" : "↓";
  return `${arrow} ${Math.abs(percent).toFixed(1)}%`;
}

export function sinceLastSeen(history: PriceHistoryComparison | null | undefined): string {
  const state = priceHistoryState(history);
  if (state === "LOADING") return "Loading history…";
  if (state === "FIRST_SEEN") return "First seen";
  if (state === "ERROR") return "History unavailable";
  if (state === "UNCHANGED") return `— (0%) since last seen ${elapsedShort(history?.elapsed_seconds ?? null)} ago`;
  return `${historySignal(history)} since last seen ${elapsedShort(history?.elapsed_seconds ?? null)} ago`;
}

export function directionHistory(history: PriceHistoryComparison | null | undefined): string {
  const state = priceHistoryState(history);
  if (state === "LOADING") return "Loading history…";
  if (state === "FIRST_SEEN") return "First seen";
  if (state === "ERROR") return "History unavailable";
  return `${historySignal(history)} since last seen`;
}

export function trendSignal(history: PriceHistoryComparison | null | undefined): string {
  if (!history || history.trend_status === "INSUFFICIENT_HISTORY" || !history.trend_status) return "";
  if (history.trend_status === "FLAT") return "— flat";
  const arrow = history.trend_status === "RISING" ? "↑" : "↓";
  return `${arrow} ${Math.abs(Number(history.trend_change_percent ?? 0)).toFixed(1)}% / ${history.trend_span_days ?? 0}d`;
}

export function summaryTrend(history: PriceHistoryComparison | null | undefined): string {
  if (!history || (history.trend_status !== "RISING" && history.trend_status !== "FALLING")) return "";
  const arrow = history.trend_status === "RISING" ? "↑" : "↓";
  return `${arrow} ${Math.abs(Number(history.trend_change_percent ?? 0)).toFixed(1)}% over ${history.observed_day_count ?? 0} observed days`;
}

export function historyTooltip(history: PriceHistoryComparison | null | undefined, current: string, currency: string): string {
  const state = priceHistoryState(history);
  if (state === "LOADING") return "Loading price history";
  if (state === "FIRST_SEEN") return "First seen · No previous observation";
  if (state === "ERROR" || !history || history.previous_price === null) return "Price history unavailable";
  const format = new Intl.NumberFormat("en-GB", { style: "currency", currency });
  const amount = Number(history.price_change_amount ?? 0);
  const percent = Number(history.price_change_percent ?? 0);
  const observed = history.previous_observed_at ? new Date(history.previous_observed_at).toLocaleString("en-GB") : "unknown";
  const daily = `Current ${format.format(Number(current))}\nPrevious day ${format.format(Number(history.previous_price))}\nDaily ${amount >= 0 ? "+" : "−"}${Math.abs(percent).toFixed(1)}%\nObserved ${observed}`;
  const trend = trendSignal(history);
  if (!trend || history.trend_status === "FLAT") return `${daily}${trend ? `\n\nTrend: ${trend}` : ""}`;
  return `${daily}\n\n${history.trend_span_days}-day trend:\n${format.format(Number(history.trend_start_price))} → ${format.format(Number(history.trend_current_price))}\n${Number(history.trend_change_percent) >= 0 ? "+" : "−"}${Math.abs(Number(history.trend_change_percent)).toFixed(1)}%\n${history.observed_day_count} observed days`;
}
