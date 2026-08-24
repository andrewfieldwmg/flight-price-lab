import type { TripOption } from "@/lib/api/types";
import { money, PriceDisplay } from "./price-display";

export function duration(minutes: number | null): string {
  if (minutes === null) return "—";
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `${hours ? `${hours}h ` : ""}${remainder}m`;
}

function localTime(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(value));
}

export function ResultCard({
  option,
  label,
  selected,
  onSelect,
}: {
  option: TripOption;
  label: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`result-card ${selected ? "selected" : ""}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <span className="card-tag">{label}</span>
      <PriceDisplay option={option} />
      <div className="route">
        <span>{option.route[0]}</span>
        <span className="route-line" />
        {option.connection_airport && <span>{option.connection_airport}</span>}
        <span className="route-line" />
        <span>{option.route.at(-1)}</span>
      </div>
      <div className="flight-meta">
        {option.airlines.join(" + ")}<br />
        {option.flight_numbers.join(" · ")}
      </div>
      <div className="times">
        <div><span>Departs</span>{localTime(option.departure_at)}</div>
        <div><span>Arrives</span>{localTime(option.arrival_at)}</div>
      </div>
      <div className="metrics">
        <div className="metric">
          <span>Total journey</span><strong>{duration(option.total_journey_minutes)}</strong>
        </div>
        {option.connection_minutes !== null && (
          <div className="metric">
            <span>Transfer at {option.connection_airport}</span>
            <strong>{duration(option.connection_minutes)}</strong>
          </div>
        )}
        {option.extra_minutes_vs_nonstop !== null && (
          <div className="metric">
            <span>Extra travel</span><strong>+{duration(option.extra_minutes_vs_nonstop)}</strong>
          </div>
        )}
        {option.saving_vs_nonstop_amount !== null && (
          <div className="metric saving-line">
            <span>Base-fare saving</span>
            <strong>{money(option.saving_vs_nonstop_amount, option.currency)}</strong>
          </div>
        )}
      </div>
      {option.ticketing_type === "separate_tickets" && (
        <div className="risk">Separate tickets · allow time to reconnect independently</div>
      )}
    </button>
  );
}
