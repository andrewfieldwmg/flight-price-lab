"use client";

import { useEffect, useRef, useState } from "react";
import type { PriceHistoryComparison, TripOption } from "@/lib/api/types";
import { aggregateTrip } from "@/lib/search/calculations";
import { localClock } from "./direction-results";
import { money } from "./price-display";
import { duration } from "./result-card";
import { BookingPreparation } from "./booking-preparation";
import { elapsedSummaryDay, historyAccessibleLabel, londonCalendarDayCount, priceHistoryState, summaryTrend } from "@/lib/search/price-history";
import { PriceSparkline } from "./price-sparkline";

function summaryChangeSignal(history: PriceHistoryComparison | null | undefined): string {
  const state = priceHistoryState(history);
  if (state === "LOADING") return "Loading history…";
  if (state === "FIRST_SEEN") return "First seen";
  if (state === "ERROR") return "History unavailable";
  if (state === "UNCHANGED") return "No change";
  const percent = Number(history?.price_change_percent ?? 0);
  return `${percent > 0 ? "↑" : "↓"} ${Math.abs(percent).toFixed(1)}%`;
}

function summaryDirectionHistory(history: PriceHistoryComparison | null | undefined): string {
  const signal = summaryChangeSignal(history);
  const state = priceHistoryState(history);
  if (state === "CHANGED" || state === "UNCHANGED") {
    return `${signal} ${elapsedSummaryDay(history?.elapsed_seconds ?? null, history?.previous_observed_at, undefined, history?.day_difference)}`.trim();
  }
  return signal;
}

function summaryHistoryAccessibleLabel(history: PriceHistoryComparison | null | undefined): string {
  const state = priceHistoryState(history);
  if (state === "LOADING") return "Loading price history";
  return state === "FIRST_SEEN" ? "First price observation" : historyAccessibleLabel(history);
}

function range(low: number | string | null, high: number | string | null, currency: string) {
  if (low === null) return "unavailable";
  if (high === null) return `from ${money(low, currency)}`;
  return Number(low) === Number(high) ? money(low, currency) : `${money(low, currency)}–${money(high, currency)}`;
}

function summaryDate(value: string, weekday = true) {
  return new Date(`${value}T12:00:00`).toLocaleDateString("en-GB", {
    weekday: weekday ? "short" : undefined,
    day: "numeric",
    month: "short",
    year: weekday ? "numeric" : undefined,
  }).replace(",", "");
}

function combinedTripHistory(outbound: TripOption | null, inbound: TripOption | null) {
  if (!outbound || !inbound) return [];
  const returns = new Map(
    (inbound.history?.visual_series ?? [])
      .filter((point) => point.observation_run_id)
      .map((point) => [point.observation_run_id, point]),
  );
  return (outbound.history?.visual_series ?? [])
    .filter((point) => point.observation_run_id && returns.has(point.observation_run_id))
    .map((point) => {
      const paired = returns.get(point.observation_run_id)!;
      return {
        observed_at: new Date(point.observed_at) > new Date(paired.observed_at) ? point.observed_at : paired.observed_at,
        price: String(Number(point.price) + Number(paired.price)),
        observation_run_id: point.observation_run_id,
      };
    })
    .sort((left, right) => Date.parse(left.observed_at) - Date.parse(right.observed_at));
}

function DirectionSummary({ label, option, showBaggage, date }: { label: string; option: TripOption; showBaggage: boolean; date: string }) {
  const baggageEstimates = option.baggage_estimates ?? [];
  return <div className="summary-direction">
    <div className="summary-direction-head"><strong>{label} · {summaryDate(date)}</strong><b>{money(option.base_price, option.currency)}</b></div>
    {summaryDirectionHistory(option.history) && <div className="summary-history" aria-label={summaryHistoryAccessibleLabel(option.history)}>{summaryDirectionHistory(option.history)}</div>}
    {summaryTrend(option.history) && <div className="summary-history">{summaryTrend(option.history)}</div>}
    <div className="summary-route">
      {option.legs.map((leg, index) => <div key={`${leg.flight_number}-${index}`}>
        <div className="summary-leg" aria-label={`${leg.origin} ${localClock(leg.departure_at)} to ${leg.destination} ${localClock(leg.arrival_at)} ${leg.airline} ${leg.flight_number}${option.is_self_transfer && leg.constituent_price !== null && leg.constituent_price !== undefined && summaryChangeSignal(leg.history) ? `. ${summaryHistoryAccessibleLabel(leg.history)}` : ""}`}><strong>{leg.origin}</strong> {localClock(leg.departure_at)} <span>→</span> <strong>{leg.destination}</strong> {localClock(leg.arrival_at)} <small>({leg.airline} {leg.flight_number})</small>{option.is_self_transfer && leg.constituent_price !== null && leg.constituent_price !== undefined && <b>{money(leg.constituent_price, option.currency)} {summaryChangeSignal(leg.history) && <em>{summaryDirectionHistory(leg.history)}</em>}</b>}</div>
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

export function TripSummary({ outbound, inbound, outboundBaseline, inboundBaseline, outboundComparisonEnabled = false, inboundComparisonEnabled = false, excludeBaggage = true, searchId = null, outboundDate, returnDate, complete = true }: { outbound: TripOption | null; inbound: TripOption | null; outboundBaseline: TripOption | null; inboundBaseline: TripOption | null; outboundComparisonEnabled?: boolean; inboundComparisonEnabled?: boolean; excludeBaggage?: boolean; searchId?: string | null; outboundDate?: string; returnDate?: string; complete?: boolean }) {
  const [showBaggage, setShowBaggage] = useState(false);
  const [compactVisible, setCompactVisible] = useState(false);
  const fullSummary = useRef<HTMLElement>(null);
  const summarySentinel = useRef<HTMLDivElement>(null);
  const compareOutbound = outboundComparisonEnabled && !!outbound && !outbound.is_nonstop;
  const compareInbound = inboundComparisonEnabled && !!inbound && !inbound.is_nonstop;
  const summary = aggregateTrip(outbound, inbound, outboundBaseline, inboundBaseline, { outbound: compareOutbound, inbound: compareInbound });
  useEffect(() => {
    const sentinel = summarySentinel.current;
    const header = document.querySelector<HTMLElement>(".site-header");
    if (!sentinel || typeof IntersectionObserver === "undefined") return;
    let observer: IntersectionObserver | null = null;
    let frame = 0;
    const connect = () => {
      observer?.disconnect();
      const headerHeight = Math.round(header?.getBoundingClientRect().height ?? 0);
      document.documentElement.style.setProperty("--sticky-header-height", `${headerHeight}px`);
      observer = new IntersectionObserver(([entry]) => {
        setCompactVisible(!entry.isIntersecting && entry.boundingClientRect.top <= headerHeight + 1);
      }, { rootMargin: `-${headerHeight}px 0px 0px 0px`, threshold: 0 });
      observer.observe(sentinel);
    };
    const reconnect = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(connect);
    };
    connect();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(reconnect);
    if (header) resizeObserver?.observe(header);
    if (fullSummary.current) resizeObserver?.observe(fullSummary.current);
    window.addEventListener("resize", reconnect);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      resizeObserver?.disconnect();
      window.removeEventListener("resize", reconnect);
    };
  }, []);
  if (!summary) return null;
  const currency = outbound?.currency ?? inbound?.currency ?? "GBP";
  const comparisonEnabled = compareOutbound || compareInbound;
  const saving = summary.baseSaving;
  const percentage = summary.baseNonstopPrice ? (saving / summary.baseNonstopPrice) * 100 : 0;
  const routes = [outbound, inbound].filter(Boolean).map((option) => (option as TripOption).route.join("-")).join(" / ");
  const resolvedOutboundDate = outboundDate ?? outbound?.departure_at.slice(0, 10) ?? "";
  const resolvedReturnDate = returnDate ?? inbound?.departure_at.slice(0, 10) ?? "";
  const compactRoutes = [
    outbound && `${summaryDate(resolvedOutboundDate, false)} ${outbound.route[0]}→${outbound.route.at(-1)}`,
    inbound && `${summaryDate(resolvedReturnDate, false)} ${inbound.route[0]}→${inbound.route.at(-1)}`,
  ].filter(Boolean).join(" · ");
  const outboundHistory = outbound?.history;
  const inboundHistory = inbound?.history;
  const tripTotalSeries = combinedTripHistory(outbound, inbound);
  const currentTripPoint = tripTotalSeries.at(-1) ?? null;
  const combinedPriorPoint = currentTripPoint
    ? [...tripTotalSeries].reverse().find((point) => londonCalendarDayCount(point.observed_at, new Date(currentTripPoint.observed_at))! > 0) ?? null
    : null;
  const commonPriorRun = Boolean(
    outbound
    && inbound
    && outboundHistory?.history_status === "PREVIOUS_FOUND"
    && inboundHistory?.history_status === "PREVIOUS_FOUND"
    && outboundHistory.previous_observation_run_id
    && outboundHistory.previous_observation_run_id === inboundHistory.previous_observation_run_id,
  );
  const oneWayPrior = Boolean(outbound && !inbound && outboundHistory?.history_status === "PREVIOUS_FOUND");
  const previousTripTotal = combinedPriorPoint
    ? Number(combinedPriorPoint.price)
    : commonPriorRun
    ? Number(outboundHistory?.previous_price) + Number(inboundHistory?.previous_price)
    : oneWayPrior ? Number(outboundHistory?.previous_price) : null;
  const tripHistoryChange = previousTripTotal === null ? null : summary.baseAlternativePrice - previousTripTotal;
  const tripHistoryPercent = previousTripTotal ? (tripHistoryChange ?? 0) / previousTripTotal * 100 : null;
  const combinedDayDifference = combinedPriorPoint && currentTripPoint
    ? londonCalendarDayCount(combinedPriorPoint.observed_at, new Date(currentTripPoint.observed_at))
    : null;
  const tripHistoryElapsed = outboundHistory?.elapsed_seconds ?? inboundHistory?.elapsed_seconds ?? null;
  const outboundHistoryState = priceHistoryState(outboundHistory);
  const inboundHistoryState = inbound ? priceHistoryState(inboundHistory) : null;
  const tripHistoryState = !complete ? "LOADING" : inbound
    ? outboundHistoryState === "LOADING" || inboundHistoryState === "LOADING"
      ? "LOADING"
      : outboundHistoryState === "FIRST_SEEN" && inboundHistoryState === "FIRST_SEEN"
        ? "FIRST_SEEN"
        : previousTripTotal !== null && tripHistoryPercent === 0 ? "UNCHANGED" : previousTripTotal !== null ? "CHANGED" : "ERROR"
    : outboundHistoryState;
  const tripHistoryCompact = tripHistoryState === "LOADING" ? "Updating…" : tripHistoryState === "FIRST_SEEN" ? "First seen" : tripHistoryState === "ERROR" ? "Unavailable" : tripHistoryState === "UNCHANGED" ? "No change" : `${(tripHistoryPercent ?? 0) > 0 ? "↑" : "↓"}${Math.abs(tripHistoryPercent ?? 0).toFixed(1)}%`;
  const tripHistoryDetailed = tripHistoryState === "LOADING"
    ? "Updating…"
    : tripHistoryState === "FIRST_SEEN"
      ? "First seen"
      : tripHistoryState === "ERROR"
        ? "History unavailable"
        : tripHistoryPercent === null
          ? "History unavailable"
    : tripHistoryPercent === 0
      ? `No change ${elapsedSummaryDay(tripHistoryElapsed, combinedPriorPoint?.observed_at ?? outboundHistory?.previous_observed_at, currentTripPoint ? new Date(currentTripPoint.observed_at) : undefined, combinedDayDifference ?? outboundHistory?.day_difference)}`
      : `${tripHistoryPercent > 0 ? "↑" : "↓"} ${Math.abs(tripHistoryPercent).toFixed(1)}% ${elapsedSummaryDay(tripHistoryElapsed, combinedPriorPoint?.observed_at ?? outboundHistory?.previous_observed_at, currentTripPoint ? new Date(currentTripPoint.observed_at) : undefined, combinedDayDifference ?? outboundHistory?.day_difference)}`;
  return <>
    <div ref={summarySentinel} className="summary-sentinel" aria-hidden="true" />
    {compactVisible && <aside className="compact-trip-summary" aria-label="Compact selected trip summary">
      <div><span>Trip total</span><strong>{money(summary.baseAlternativePrice, currency)}{tripHistoryCompact && ` · ${tripHistoryCompact}`}</strong></div>
      {comparisonEnabled && <div><span>Save</span><strong>{money(saving, currency)} / {percentage.toFixed(0)}%</strong></div>}
      {comparisonEnabled && <div><span>Extra travel</span><strong>+{duration(summary.extraMinutes)}</strong></div>}
      <b>{compactRoutes || routes}</b>
    </aside>}
    <section ref={fullSummary} className="summary-strip" aria-label="Selected trip summary">
      <div className="summary-primary">
        <div className="summary-top-row" data-testid="summary-top-row">
          <div className="summary-total-block"><span>Trip total</span><div className="trip-total-price-row"><strong>{money(summary.baseAlternativePrice, currency)}</strong>{complete && tripTotalSeries.length >= 2 && <PriceSparkline points={tripTotalSeries} currency={currency} className="trip-total-sparkline" />}</div>{tripHistoryDetailed && <small aria-live="polite" aria-label={tripHistoryState === "LOADING" ? "Updating trip total and price history" : tripHistoryState === "FIRST_SEEN" ? "First price observation" : tripHistoryState === "ERROR" ? "Price history unavailable" : tripHistoryChange !== null && tripHistoryChange > 0 ? `Trip price increased by ${Math.abs(tripHistoryPercent ?? 0).toFixed(1)} percent since last seen` : tripHistoryChange !== null && tripHistoryChange < 0 ? `Trip price decreased by ${Math.abs(tripHistoryPercent ?? 0).toFixed(1)} percent since last seen` : "No trip price change since last seen"}>{tripHistoryDetailed}</small>}{complete && previousTripTotal !== null && <span className="summary-previous-price">was {money(previousTripTotal, currency)}</span>}</div>
          <div className="summary-header-actions"><BookingPreparation searchId={searchId} optionIds={[outbound?.id, inbound?.id].filter((id): id is string => Boolean(id))} /><label><input type="checkbox" checked={showBaggage} onChange={(event) => setShowBaggage(event.target.checked)} /> Show estimated baggage costs</label></div>
        </div>
        {comparisonEnabled && <div className="summary-metrics">
          {comparisonEnabled && <div><span>Save</span><strong>{money(saving, currency)} / {percentage.toFixed(0)}%</strong></div>}
          {comparisonEnabled && <div><span>Extra travel</span><strong>+{duration(summary.extraMinutes)}</strong></div>}
        </div>}
      </div>
      <div className="summary-itineraries">
        {outbound && <DirectionSummary label="Outbound" option={outbound} showBaggage={showBaggage} date={resolvedOutboundDate} />}
        {inbound && <DirectionSummary label="Return" option={inbound} showBaggage={showBaggage} date={resolvedReturnDate} />}
      </div>
      {excludeBaggage && <em>Prices exclude baggage.</em>}
    </section>
  </>;
}
