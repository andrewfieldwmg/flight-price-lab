import { memo } from "react";
import { money } from "./price-display";

export interface SparklinePoint {
  observed_at: string;
  price: string;
  history_quality?: "EXACT" | "PARTIAL_CARRY_FORWARD";
}

export interface SparklineGeometry {
  coordinates: Array<[number, number]>;
  path: string;
}

const WIDTH = 64;
const HEIGHT = 24;
const PAD_X = 2;
const PAD_Y = 3;

export function sparklineGeometry(points: SparklinePoint[]): SparklineGeometry {
  if (points.length < 2) return { coordinates: [], path: "" };
  const values = points.map((point) => Number(point.price));
  const times = points.map((point) => Date.parse(point.observed_at));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const centre = (minValue + maxValue) / 2;
  const minimumRange = Math.max(Math.abs(centre) * 0.02, 1);
  const visualRange = Math.max(maxValue - minValue, minimumRange);
  const low = centre - visualRange / 2;
  const high = centre + visualRange / 2;
  const firstTime = times[0];
  const timeSpan = Math.max(times.at(-1)! - firstTime, 1);
  const coordinates = values.map((value, index): [number, number] => [
    PAD_X + ((times[index] - firstTime) / timeSpan) * (WIDTH - 2 * PAD_X),
    minValue === maxValue
      ? HEIGHT / 2
      : PAD_Y + ((high - value) / (high - low)) * (HEIGHT - 2 * PAD_Y),
  ]);
  if (coordinates.length === 2) {
    return {
      coordinates,
      path: `M ${coordinates[0][0].toFixed(2)} ${coordinates[0][1].toFixed(2)} L ${coordinates[1][0].toFixed(2)} ${coordinates[1][1].toFixed(2)}`,
    };
  }
  const slopes = coordinates.slice(0, -1).map((point, index) => {
    const next = coordinates[index + 1];
    return (next[1] - point[1]) / (next[0] - point[0]);
  });
  const tangents = coordinates.map((_point, index) => {
    if (index === 0) return slopes[0];
    if (index === coordinates.length - 1) return slopes.at(-1)!;
    const left = slopes[index - 1];
    const right = slopes[index];
    return left * right <= 0 ? 0 : (left + right) / 2;
  });
  slopes.forEach((slope, index) => {
    if (slope === 0) {
      tangents[index] = 0;
      tangents[index + 1] = 0;
      return;
    }
    const alpha = tangents[index] / slope;
    const beta = tangents[index + 1] / slope;
    const magnitude = Math.hypot(alpha, beta);
    if (magnitude > 3) {
      const scale = 3 / magnitude;
      tangents[index] = scale * alpha * slope;
      tangents[index + 1] = scale * beta * slope;
    }
  });
  const commands = [`M ${coordinates[0][0].toFixed(2)} ${coordinates[0][1].toFixed(2)}`];
  for (let index = 0; index < coordinates.length - 1; index += 1) {
    const [x, y] = coordinates[index];
    const [nextX, nextY] = coordinates[index + 1];
    const interval = nextX - x;
    commands.push(
      `C ${(x + interval / 3).toFixed(2)} ${(y + tangents[index] * interval / 3).toFixed(2)} ${(nextX - interval / 3).toFixed(2)} ${(nextY - tangents[index + 1] * interval / 3).toFixed(2)} ${nextX.toFixed(2)} ${nextY.toFixed(2)}`,
    );
  }
  return { coordinates, path: commands.join(" ") };
}

function observationLabel(points: SparklinePoint[], currency: string): string {
  const dates = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "Europe/London" });
  const times = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "Europe/London" });
  return points.map((point) => {
    const observed = new Date(point.observed_at);
    return `${dates.format(observed)} ${times.format(observed)}  ${money(point.price, currency)}${point.history_quality === "PARTIAL_CARRY_FORWARD" ? " · carried direction price" : ""}`;
  }).join("\n");
}

export const PriceSparkline = memo(function PriceSparkline({ points, currency, className = "" }: { points: SparklinePoint[]; currency: string; className?: string }) {
  if (points.length < 2) return <span className="sparkline-empty" aria-label="Insufficient price history">—</span>;
  const geometry = sparklineGeometry(points);
  const [endX, endY] = geometry.coordinates.at(-1)!;
  const movement = Number(points.at(-1)!.price) - Number(points[0].price);
  const movementClass = movement > 0 ? "sparkline-rising" : movement < 0 ? "sparkline-falling" : "sparkline-flat";
  const label = observationLabel(points, currency);
  const [startX] = geometry.coordinates[0];
  const areaPath = `M ${startX.toFixed(2)} ${HEIGHT} L ${geometry.path.slice(2)} L ${endX.toFixed(2)} ${HEIGHT} Z`;
  return <svg className={`price-sparkline ${movementClass} ${className}`.trim()} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`Price history. ${label.replaceAll("\n", ". ")}`}>
    <title>{label}</title>
    <path className="sparkline-area" d={areaPath} />
    <path className="sparkline-line" d={geometry.path} />
    <circle cx={endX} cy={endY} r="1.5" />
  </svg>;
});
