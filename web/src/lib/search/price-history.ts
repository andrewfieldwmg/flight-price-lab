import type { PriceHistoryComparison } from "@/lib/api/types";

export function elapsedShort(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 48 * 3600) return `${Math.max(1, Math.floor(seconds / 3600))}h`;
  if (seconds < 14 * 86400) return `${Math.floor(seconds / 86400)}d`;
  return `${Math.floor(seconds / (7 * 86400))}w`;
}

export function elapsedDetailed(seconds: number | null): string {
  if (seconds === null) return "unknown interval";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days) return `${days} day${days === 1 ? "" : "s"}${hours ? ` ${hours} hour${hours === 1 ? "" : "s"}` : ""}`;
  return `${Math.max(1, hours)} hour${hours === 1 ? "" : "s"}`;
}

export function historySignal(history: PriceHistoryComparison | null | undefined): string {
  if (!history || history.history_status === "FIRST_SEEN") return "New";
  const percent = Number(history.price_change_percent ?? 0);
  const arrow = percent > 0 ? "↑" : percent < 0 ? "↓" : "→";
  return `${arrow} ${Math.abs(percent).toFixed(1)}%`;
}

export function historyAccessibleLabel(history: PriceHistoryComparison | null | undefined): string {
  if (!history || history.history_status === "FIRST_SEEN") return "New price observation";
  const percent = Number(history.price_change_percent ?? 0);
  if (percent > 0) return `Price increased by ${Math.abs(percent).toFixed(1)} percent since last seen`;
  if (percent < 0) return `Price decreased by ${Math.abs(percent).toFixed(1)} percent since last seen`;
  return "No price change since last seen";
}

export function percentageChangeSignal(percent: number): string {
  const arrow = percent > 0 ? "↑" : percent < 0 ? "↓" : "→";
  return `${arrow} ${Math.abs(percent).toFixed(1)}%`;
}

export function sinceLastSeen(history: PriceHistoryComparison | null | undefined): string {
  if (!history || history.history_status === "FIRST_SEEN") return "New · No previous observation";
  return `${historySignal(history)} since last seen ${elapsedShort(history.elapsed_seconds)} ago`;
}

export function historyTooltip(history: PriceHistoryComparison | null | undefined, current: string, currency: string): string {
  if (!history || history.history_status === "FIRST_SEEN" || history.previous_price === null) return "New · No previous observation";
  const format = new Intl.NumberFormat("en-GB", { style: "currency", currency });
  const amount = Number(history.price_change_amount ?? 0);
  const percent = Number(history.price_change_percent ?? 0);
  const observed = history.previous_observed_at ? new Date(history.previous_observed_at).toLocaleString("en-GB") : "unknown";
  return `Was ${format.format(Number(history.previous_price))}\nNow ${format.format(Number(current))}\n${amount >= 0 ? "+" : "−"}${format.format(Math.abs(amount))} (${amount >= 0 ? "+" : "−"}${Math.abs(percent).toFixed(1)}%)\nLast seen ${elapsedDetailed(history.elapsed_seconds)} ago · ${observed}`;
}
