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

function useMobileSummary(): boolean {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    if (!window.matchMedia) return;
    const query = window.matchMedia("(max-width: 680px)");
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return mobile;
}

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

export interface TripTotalHistoryPoint {
  observed_at: string;
  price: string;
  history_quality: "EXACT" | "PARTIAL_CARRY_FORWARD";
}

type DailyDirectionPoint = { date: string; price: string };

function calendarDays(left: string, right: string): number {
  return (Date.parse(`${right}T12:00:00Z`) - Date.parse(`${left}T12:00:00Z`)) / 86_400_000;
}

export function reconstructTripTotalHistory(outbound: DailyDirectionPoint[], inbound: DailyDirectionPoint[] | null): TripTotalHistoryPoint[] {
  const out = new Map(outbound.map((point) => [point.date, point.price]));
  if (!inbound) return [...out].sort(([left], [right]) => left.localeCompare(right)).map(([date, price]) => ({ observed_at: `${date}T12:00:00Z`, price, history_quality: "EXACT" }));
  const back = new Map(inbound.map((point) => [point.date, point.price]));
  const dates = [...new Set([...out.keys(), ...back.keys()])].sort();
  const prior = (series: Map<string, string>, date: string) => [...series]
    .filter(([candidate]) => candidate < date)
    .sort(([left], [right]) => right.localeCompare(left))
    .find(([candidate]) => calendarDays(candidate, date) <= 3)?.[1];
  return dates.flatMap((date): TripTotalHistoryPoint[] => {
    const exactOut = out.get(date);
    const exactBack = back.get(date);
    const outPrice = exactOut ?? prior(out, date);
    const backPrice = exactBack ?? prior(back, date);
    if (outPrice === undefined || backPrice === undefined) return [];
    return [{
      observed_at: `${date}T12:00:00Z`,
      price: String(Number(outPrice) + Number(backPrice)),
      history_quality: exactOut !== undefined && exactBack !== undefined ? "EXACT" : "PARTIAL_CARRY_FORWARD",
    }];
  });
}

function combinedTripHistory(outbound: TripOption | null, inbound: TripOption | null) {
  if (!outbound) return [];
  return reconstructTripTotalHistory(outbound.history?.daily_series ?? [], inbound ? inbound.history?.daily_series ?? [] : null);
}

function DirectionSummary({ label, option, showBaggage, date, suppressTicketingBadge = false }: { label: string; option: TripOption; showBaggage: boolean; date: string; suppressTicketingBadge?: boolean }) {
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
    {!suppressTicketingBadge && option.ticketing_type === "separate_tickets" && <div className="summary-ticketing">Separate tickets</div>}
    {showBaggage && <div className="summary-baggage-enrichment">
      <div><span>Estimated bags</span><strong>{range(option.ancillary_price_low, option.ancillary_price_high, option.currency)}</strong></div>
      <div><span>Indicative total</span><strong>{range(option.effective_price_low, option.effective_price_high, option.currency)}</strong></div>
      {!!baggageEstimates.length && <details><summary>Cost detail</summary>{baggageEstimates.map((estimate) => <div key={estimate.ticket_index}><span>{estimate.carrier_codes.join("/") || estimate.flight_numbers.join("/")}</span><strong>{range(estimate.price_low, estimate.price_high, option.currency)}</strong></div>)}</details>}
    </div>}
  </div>;
}

function MobileDirectionOverview({ label, option, date }: { label: string; option: TripOption; date: string }) {
  return <div className="mobile-summary-direction">
    <div><strong>{label} · {summaryDate(date, false)}</strong><b>{money(option.base_price, option.currency)}</b></div>
    <p>{option.route[0]} {localClock(option.departure_at)} <span>→</span> {option.route.at(-1)} {localClock(option.arrival_at)}</p>
    <small>{option.is_nonstop ? `Direct · ${option.airlines.join(" / ")}` : `1 stop · ${option.connection_airport} ${duration(option.connection_minutes)}`}</small>
  </div>;
}

export function TripSummary({ outbound, inbound, outboundBaseline, inboundBaseline, outboundComparisonEnabled = false, inboundComparisonEnabled = false, excludeBaggage = true, searchId = null, outboundDate, returnDate, complete = true }: { outbound: TripOption | null; inbound: TripOption | null; outboundBaseline: TripOption | null; inboundBaseline: TripOption | null; outboundComparisonEnabled?: boolean; inboundComparisonEnabled?: boolean; excludeBaggage?: boolean; searchId?: string | null; outboundDate?: string; returnDate?: string; complete?: boolean }) {
  const [showBaggage, setShowBaggage] = useState(false);
  const [compactVisible, setCompactVisible] = useState(false);
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const mobile = useMobileSummary();
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
  const oneWayPrior = Boolean(outbound && !inbound && outboundHistory?.history_status === "PREVIOUS_FOUND");
  const previousTripTotal = combinedPriorPoint
    ? Number(combinedPriorPoint.price)
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
  const mobileTripHistory = tripHistoryState === "LOADING"
    ? "Updating…"
    : tripHistoryState === "FIRST_SEEN"
      ? "First seen"
      : tripHistoryState === "ERROR" || tripHistoryPercent === null
        ? "History unavailable"
        : `${tripHistoryPercent === 0 ? "— (0%)" : `${tripHistoryPercent > 0 ? "↑" : "↓"} ${Math.abs(tripHistoryPercent).toFixed(1)}%`}${previousTripTotal !== null ? ` · was ${money(previousTripTotal, currency)}` : ""}`;
  const optionIds = [outbound?.id, inbound?.id].filter((id): id is string => Boolean(id));
  return <>
    <div ref={summarySentinel} className="summary-sentinel" aria-hidden="true" />
    {compactVisible && <aside className="compact-trip-summary" aria-label="Compact selected trip summary">
      <div><span>Trip total</span><strong>{money(summary.baseAlternativePrice, currency)}{tripHistoryCompact && ` · ${tripHistoryCompact}`}</strong></div>
      {comparisonEnabled && <div><span>Save</span><strong>{money(saving, currency)} / {percentage.toFixed(0)}%</strong></div>}
      {comparisonEnabled && <div><span>Extra travel</span><strong>+{duration(summary.extraMinutes)}</strong></div>}
      <b>{compactRoutes || routes}</b>
    </aside>}
    {mobile ? <section ref={fullSummary} className="summary-strip mobile-selected-summary" aria-label="Selected trip summary">
      <div className="mobile-summary-total">
        <span>Trip total</span>
        <div><strong>{money(summary.baseAlternativePrice, currency)}</strong>{complete && tripTotalSeries.length >= 2 && <PriceSparkline points={tripTotalSeries} currency={currency} className="trip-total-sparkline" />}</div>
        <small aria-live="polite" data-history-quality={combinedPriorPoint?.history_quality}>{mobileTripHistory}</small>
      </div>
      {comparisonEnabled && <div className="mobile-summary-value">
        <div><span>Save</span><strong>{money(saving, currency)} / {percentage.toFixed(0)}%</strong></div>
        <div><span>Extra travel</span><strong>+{duration(summary.extraMinutes)}</strong></div>
      </div>}
      <div className="mobile-summary-itineraries">
        {outbound && <MobileDirectionOverview label="Outbound" option={outbound} date={resolvedOutboundDate} />}
        {inbound && <MobileDirectionOverview label="Return" option={inbound} date={resolvedReturnDate} />}
      </div>
      <div className="mobile-summary-booking"><BookingPreparation searchId={searchId} optionIds={optionIds} /></div>
      <button type="button" className="mobile-trip-details-toggle" aria-expanded={detailsExpanded} onClick={() => setDetailsExpanded((value) => !value)}>Trip details <span aria-hidden="true">{detailsExpanded ? "▴" : "▾"}</span></button>
      {detailsExpanded && <div className="mobile-trip-details">
        <label><input type="checkbox" checked={showBaggage} onChange={(event) => setShowBaggage(event.target.checked)} /> Show estimated baggage costs</label>
        {outbound && <DirectionSummary label="Outbound" option={outbound} showBaggage={showBaggage} date={resolvedOutboundDate} suppressTicketingBadge />}
        {inbound && <DirectionSummary label="Return" option={inbound} showBaggage={showBaggage} date={resolvedReturnDate} suppressTicketingBadge />}
        {[outbound, inbound].filter((option): option is TripOption => Boolean(option?.is_self_transfer && option.ticketing_type === "separate_tickets")).map((option) => <p className="mobile-ticket-warning" key={option.id}>{option.direction === "OUTBOUND" ? "Outbound" : "Return"}: Separate tickets · connection not protected</p>)}
      </div>}
    </section> : <section ref={fullSummary} className="summary-strip" aria-label="Selected trip summary">
      <div className="summary-primary">
        <div className="summary-top-row" data-testid="summary-top-row">
          <div className="summary-total-block"><span>Trip total</span><div className="trip-total-price-row"><strong>{money(summary.baseAlternativePrice, currency)}</strong>{complete && tripTotalSeries.length >= 2 && <PriceSparkline points={tripTotalSeries} currency={currency} className="trip-total-sparkline" />}</div>{tripHistoryDetailed && <small aria-live="polite" data-history-quality={combinedPriorPoint?.history_quality} aria-label={tripHistoryState === "LOADING" ? "Updating trip total and price history" : tripHistoryState === "FIRST_SEEN" ? "First price observation" : tripHistoryState === "ERROR" ? "Price history unavailable" : tripHistoryChange !== null && tripHistoryChange > 0 ? `Trip price increased by ${Math.abs(tripHistoryPercent ?? 0).toFixed(1)} percent since last seen` : tripHistoryChange !== null && tripHistoryChange < 0 ? `Trip price decreased by ${Math.abs(tripHistoryPercent ?? 0).toFixed(1)} percent since last seen` : "No trip price change since last seen"}>{tripHistoryDetailed}</small>}{complete && previousTripTotal !== null && <span className="summary-previous-price">was {money(previousTripTotal, currency)}</span>}</div>
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
    </section>}
  </>;
}
