import type { PriceHistoryComparison } from "@/lib/api/types";
import { money } from "./price-display";

const WIDTH = 80;
const HEIGHT = 26;
const PAD_X = 3;
const PAD_Y = 3;

export function sparklinePoints(series: Array<{ date: string; price: string }>): string {
  if (series.length < 2) return "";
  const values = series.map((point) => Number(point.price));
  const times = series.map((point) => Date.parse(`${point.date}T12:00:00Z`));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const centre = (minValue + maxValue) / 2;
  const minimumRange = Math.max(Math.abs(centre) * 0.02, 1);
  const range = Math.max(maxValue - minValue, minimumRange);
  const low = centre - range * 0.6;
  const high = centre + range * 0.6;
  const firstTime = times[0];
  const timeSpan = Math.max(times.at(-1)! - firstTime, 1);
  return values.map((value, index) => {
    const x = PAD_X + ((times[index] - firstTime) / timeSpan) * (WIDTH - 2 * PAD_X);
    const y = PAD_Y + ((high - value) / (high - low)) * (HEIGHT - 2 * PAD_Y);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function observationLabel(history: PriceHistoryComparison, currency: string): string {
  const series = history.daily_series ?? [];
  const dates = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "Europe/London" });
  const observations = series.map((point) => `${dates.format(new Date(`${point.date}T12:00:00Z`))}  ${money(point.price, currency)}`);
  if (series.length > 1) {
    const first = Number(series[0].price);
    const last = Number(series.at(-1)!.price);
    const overall = first ? (last - first) / first * 100 : null;
    if (overall !== null) observations.push(`Overall: ${overall >= 0 ? "+" : "−"}${Math.abs(overall).toFixed(1)}%`);
  }
  return observations.join("\n");
}

export function PriceSparkline({ history, currency }: { history: PriceHistoryComparison | null | undefined; currency: string }) {
  const series = history?.daily_series ?? [];
  if (series.length < 2) return <span className="sparkline-empty" aria-label="Insufficient price history">—</span>;
  const points = sparklinePoints(series);
  const label = observationLabel(history!, currency);
  const end = points.split(" ").at(-1)!.split(",").map(Number);
  return <details className="sparkline-detail" onClick={(event) => event.stopPropagation()}>
    <summary aria-label={`Price history. ${label.replaceAll("\n", ". ")}`} title={label}>
      <svg className="price-sparkline" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-hidden="true">
        <polyline points={points} />
        <circle cx={end[0]} cy={end[1]} r="1.8" />
      </svg>
    </summary>
    <span className="sparkline-tooltip">{label.split("\n").map((line) => <span key={line}>{line}</span>)}</span>
  </details>;
}
