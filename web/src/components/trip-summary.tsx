"use client";

import { useEffect, useRef, useState } from "react";
import type { TripOption } from "@/lib/api/types";
import { aggregateTrip } from "@/lib/search/calculations";
import { localClock } from "./direction-results";
import { money } from "./price-display";
import { duration } from "./result-card";

function range(low: number | string | null, high: number | string | null, currency: string) {
  if (low === null) return "unavailable";
  if (high === null) return `from ${money(low, currency)}`;
  return Number(low) === Number(high) ? money(low, currency) : `${money(low, currency)}–${money(high, currency)}`;
}

function DirectionSummary({ label, option, showBaggage }: { label: string; option: TripOption; showBaggage: boolean }) {
  const baggageEstimates = option.baggage_estimates ?? [];
  return <div className="summary-direction">
    <div className="summary-direction-head"><strong>{label}</strong><b>{money(option.base_price, option.currency)}</b></div>
    <div className="summary-route">
      {option.legs.map((leg, index) => <div key={`${leg.flight_number}-${index}`}>
        <div className="summary-leg" aria-label={`${leg.origin} ${localClock(leg.departure_at)} to ${leg.destination} ${localClock(leg.arrival_at)}`}><strong>{leg.origin}</strong> {localClock(leg.departure_at)} <span>→</span> <strong>{leg.destination}</strong> {localClock(leg.arrival_at)}</div>
        {index < option.legs.length - 1 && <div className="summary-transfer">{option.connection_airport} · {duration(option.connection_minutes)} transfer</div>}
      </div>)}
    </div>
    {option.ticketing_type === "separate_tickets" && <div className="summary-ticketing">Separate tickets</div>}
    {showBaggage && <div className="summary-baggage-enrichment">
      <div><span>Estimated bags</span><strong>{range(option.ancillary_price_low, option.ancillary_price_high, option.currency)}</strong></div>
      <div><span>Indicative total</span><strong>{range(option.effective_price_low, option.effective_price_high, option.currency)}</strong></div>
      {!!baggageEstimates.length && <details><summary>Cost detail</summary>{baggageEstimates.map((estimate) => <div key={estimate.ticket_index}><span>{estimate.carrier_codes.join("/") || estimate.flight_numbers.join("/")}</span><strong>{range(estimate.price_low, estimate.price_high, option.currency)}</strong></div>)}</details>}
    </div>}
  </div>;
}

export function TripSummary({ outbound, inbound, outboundBaseline, inboundBaseline, outboundComparisonEnabled = false, inboundComparisonEnabled = false, excludeBaggage = true }: { outbound: TripOption | null; inbound: TripOption | null; outboundBaseline: TripOption | null; inboundBaseline: TripOption | null; outboundComparisonEnabled?: boolean; inboundComparisonEnabled?: boolean; excludeBaggage?: boolean }) {
  const [showBaggage, setShowBaggage] = useState(false);
  const [compactVisible, setCompactVisible] = useState(false);
  const fullSummary = useRef<HTMLElement>(null);
  const summaryHasBeenVisible = useRef(false);
  const compareOutbound = outboundComparisonEnabled && !!outbound && !outbound.is_nonstop;
  const compareInbound = inboundComparisonEnabled && !!inbound && !inbound.is_nonstop;
  const summary = aggregateTrip(outbound, inbound, outboundBaseline, inboundBaseline, { outbound: compareOutbound, inbound: compareInbound });
  useEffect(() => {
    const element = fullSummary.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        summaryHasBeenVisible.current = true;
        setCompactVisible(false);
      } else if (summaryHasBeenVisible.current) setCompactVisible(true);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  if (!summary) return null;
  const currency = outbound?.currency ?? inbound?.currency ?? "GBP";
  const comparisonEnabled = compareOutbound || compareInbound;
  const saving = summary.baseSaving;
  const percentage = summary.baseNonstopPrice ? (saving / summary.baseNonstopPrice) * 100 : 0;
  const routes = [outbound, inbound].filter(Boolean).map((option) => (option as TripOption).route.join("-")).join(" / ");
  return <>
    {compactVisible && <aside className="compact-trip-summary" aria-label="Compact selected trip summary">
      <div><span>Trip total</span><strong>{money(summary.baseAlternativePrice, currency)}</strong></div>
      {comparisonEnabled && <div><span>Save</span><strong>{money(saving, currency)} / {percentage.toFixed(0)}%</strong></div>}
      {comparisonEnabled && <div><span>Extra travel</span><strong>+{duration(summary.extraMinutes)}</strong></div>}
      <b>{routes}</b>
    </aside>}
    <section ref={fullSummary} className="summary-strip" aria-label="Selected trip summary">
      <div className="summary-primary">
        <div><span>Trip total</span><strong>{money(summary.baseAlternativePrice, currency)}</strong></div>
        {comparisonEnabled && <div><span>Save</span><strong>{money(saving, currency)} / {percentage.toFixed(0)}%</strong></div>}
        {comparisonEnabled && <div><span>Extra travel</span><strong>+{duration(summary.extraMinutes)}</strong></div>}
        <label><input type="checkbox" checked={showBaggage} onChange={(event) => setShowBaggage(event.target.checked)} /> Show estimated baggage costs</label>
      </div>
      <div className="summary-itineraries">
        {outbound && <DirectionSummary label="Outbound" option={outbound} showBaggage={showBaggage} />}
        {inbound && <DirectionSummary label="Return" option={inbound} showBaggage={showBaggage} />}
      </div>
      {excludeBaggage && <em>Prices exclude baggage.</em>}
    </section>
  </>;
}
