"use client";

import { useMemo, useState } from "react";
import type { ConnectionProfile, DirectionResults as Results, TripOption } from "@/lib/api/types";
import { money } from "./price-display";
import { duration } from "./result-card";
import { elapsedCompactDay, historyAccessibleLabel, historySignal, historyTooltip, priceHistoryState, trendSignal } from "@/lib/search/price-history";

export type SortKey = "saving" | "price" | "departure" | "arrival" | "transfer" | "journey" | "extra";
const RESULTS_PER_PAGE = 15;

export function localClock(value: string): string {
  return value.match(/T(\d{2}:\d{2})/)?.[1] ?? "—";
}

export function longSearchDate(value: string): string {
  return new Date(`${value}T12:00:00`).toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  }).replace(",", "");
}

export function isEarlyDeparture(value: string): boolean {
  return localClock(value) < "06:00";
}

export function isLateArrival(value: string): boolean {
  return localClock(value) > "23:00";
}

function price(option: TripOption): number {
  return Number(option.base_price);
}

export function filterByMinimumSaving(options: TripOption[], minimum: number, selectedId: string | null) {
  return options.filter((option) => option.is_nonstop || option.id === selectedId || Number(option.saving_vs_nonstop_amount ?? 0) >= minimum);
}

export function sortOptions(options: TripOption[], key: SortKey, descending: boolean): TripOption[] {
  const value = (option: TripOption): number | string => {
    if (key === "saving") return Number(option.saving_vs_nonstop_amount ?? 0);
    if (key === "price") return price(option);
    if (key === "departure") return option.departure_at;
    if (key === "arrival") return option.arrival_at;
    if (key === "transfer") return option.connection_minutes ?? -1;
    if (key === "extra") return option.extra_minutes_vs_nonstop ?? -1;
    return option.total_journey_minutes;
  };
  return [...options].sort((a, b) => {
    const left = value(a);
    const right = value(b);
    const compared = left < right ? -1 : left > right ? 1 : 0;
    return descending ? -compared : compared;
  });
}

function deduplicate(options: Array<TripOption | null>): TripOption[] {
  return [...new Map(options.filter(Boolean).map((item) => [(item as TripOption).id, item as TripOption])).values()];
}

function withBaseFareSavings(options: TripOption[], baseline: TripOption | null) {
  if (!baseline) return options;
  const reference = Number(baseline.base_price);
  return options.map((option) => {
    if (option.is_nonstop) return option;
    const saving = reference - Number(option.base_price);
    return { ...option, saving_vs_nonstop_amount: String(saving), saving_vs_nonstop_percent: reference ? String((saving / reference) * 100) : null };
  });
}

function compactPrice(option: TripOption) {
  return money(option.base_price, option.currency);
}

function HeaderButton({ label, sortKey, active, descending, onSort }: { label: string; sortKey: SortKey; active: boolean; descending: boolean; onSort: (key: SortKey) => void }) {
  return <button type="button" onClick={() => onSort(sortKey)}>{label}{active ? (descending ? " ↓" : " ↑") : ""}</button>;
}

export function DirectionResults({
  title,
  results,
  selectedId,
  onSelect,
  complete,
  selfTransferEnabled,
  date,
}: {
  title: string;
  results: Results;
  selectedId: string | null;
  onSelect: (id: string) => void;
  complete: boolean;
  connectionProfile: ConnectionProfile;
  selfTransferEnabled: boolean;
  date: string;
}) {
  const [sort, setSort] = useState<SortKey | null>(null);
  const [descending, setDescending] = useState(false);
  const [minimumSaving, setMinimumSaving] = useState(100);
  const [customSaving, setCustomSaving] = useState(false);
  const [page, setPage] = useState(1);
  const options = useMemo(() => {
    const nonstops = results.nonstop_options.length ? results.nonstop_options : results.baseline ? [results.baseline] : [];
    const candidates = !selfTransferEnabled
      ? nonstops
      : deduplicate([...nonstops, ...results.feasible_options]);
    const basePriced = withBaseFareSavings(candidates, results.baseline);
    const filtered = selfTransferEnabled ? filterByMinimumSaving(basePriced, minimumSaving, selectedId) : basePriced;
    return sort ? sortOptions(filtered, sort, descending) : filtered;
  }, [results, sort, descending, selfTransferEnabled, minimumSaving, selectedId]);
  const pageCount = Math.max(1, Math.ceil(options.length / RESULTS_PER_PAGE));
  const currentPage = Math.min(page, pageCount);
  const pageOptions = options.slice((currentPage - 1) * RESULTS_PER_PAGE, currentPage * RESULTS_PER_PAGE);
  const firstResult = options.length ? (currentPage - 1) * RESULTS_PER_PAGE + 1 : 0;
  const lastResult = Math.min(currentPage * RESULTS_PER_PAGE, options.length);

  function changeSort(key: SortKey) {
    setPage(1);
    if (key === sort) setDescending((value) => !value);
    else {
      setSort(key);
      setDescending(false);
    }
  }

  return (
    <section className="results-section">
      <div className="results-heading">
        <div><h2>{title}</h2><div className="direction-date">{longSearchDate(date)}</div></div>
        <div className="compromise-controls">
          {selfTransferEnabled && <label className="minimum-saving">Minimum saving <select aria-label={`${title} minimum saving`} value={customSaving ? "custom" : minimumSaving} onChange={(event) => { setPage(1); if (event.target.value === "custom") setCustomSaving(true); else { setCustomSaving(false); setMinimumSaving(Number(event.target.value)); } }}><option value="0">£0</option><option value="50">£50</option><option value="100">£100</option><option value="200">£200</option><option value="custom">custom</option></select>{customSaving && <input aria-label={`${title} custom minimum saving`} type="number" min="0" value={minimumSaving} onChange={(event) => { setMinimumSaving(Number(event.target.value)); setPage(1); }} />}</label>}
          <span>{options.length} shown</span>
        </div>
      </div>
      {!complete && <div className={`results-loading ${options.length ? "progressive" : ""}`} role="status"><span className="loading-spinner" aria-hidden="true" />{options.length ? "Still loading more options…" : `Loading ${title.toLowerCase()} options…`}</div>}
      <div className="table-scroll">
        <table className="results-table">
          <thead><tr>
            <th>Select</th>
            <th><HeaderButton label="Price" sortKey="price" active={sort === "price"} descending={descending} onSort={changeSort} /></th>
            <th>Change</th>
            {selfTransferEnabled && <th><HeaderButton label="Saving vs nonstop" sortKey="saving" active={sort === "saving"} descending={descending} onSort={changeSort} /></th>}
            <th><HeaderButton label="Depart" sortKey="departure" active={sort === "departure"} descending={descending} onSort={changeSort} /></th>
            <th><HeaderButton label="Arrive" sortKey="arrival" active={sort === "arrival"} descending={descending} onSort={changeSort} /></th>
            <th>Route</th>
            <th><HeaderButton label="Stopover" sortKey="transfer" active={sort === "transfer"} descending={descending} onSort={changeSort} /></th>
            <th><HeaderButton label="Journey" sortKey="journey" active={sort === "journey"} descending={descending} onSort={changeSort} /></th>
            {selfTransferEnabled && <th><HeaderButton label="Extra vs nonstop" sortKey="extra" active={sort === "extra"} descending={descending} onSort={changeSort} /></th>}
            <th>Airlines</th>
          </tr></thead>
          <tbody>
            {pageOptions.map((option) => <ResultRow key={option.id} option={option} selected={selectedId === option.id} onClick={() => onSelect(option.id)} showComparisons={selfTransferEnabled} />)}
          </tbody>
        </table>
      </div>
      {!options.length && <div className="compact-empty">{complete ? "No options matched this search." : "Waiting for direct-flight results…"}</div>}
      {!!options.length && <nav className="results-pagination" aria-label={`${title} pagination`}><span>{firstResult}–{lastResult} of {options.length}</span><div><button type="button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={currentPage === 1}>Previous</button><span>Page {currentPage} of {pageCount}</span><button type="button" onClick={() => setPage((value) => Math.min(pageCount, value + 1))} disabled={currentPage === pageCount}>Next</button></div></nav>}
      <div className="table-footnote">Prices exclude baggage.</div>
    </section>
  );
}

function ResultRow({ option, selected, onClick, showComparisons }: { option: TripOption; selected: boolean; onClick: () => void; showComparisons: boolean }) {
  const saving = Number(option.saving_vs_nonstop_amount ?? 0);
  const stopover = option.connection_airport && option.connection_minutes !== null
    ? `${option.connection_airport} · ${duration(option.connection_minutes)}`
    : "—";
  return (
      <tr className={selected ? "selected-row" : ""} onClick={onClick}>
        <td data-label="Select"><input type="radio" readOnly checked={selected} aria-label={`Select ${option.route.join("-")}`} /> <span className="type-pill">{option.is_nonstop ? "Direct" : "1-stop"}</span></td>
        <td data-label="Price" className="price-cell">{compactPrice(option)}</td>
        <HistoryCell option={option} />
        {showComparisons && <td data-label="Saving vs nonstop" className="saving-cell">{option.is_nonstop ? "Reference" : saving > 0 ? `${money(saving, option.currency)} / ${Number(option.saving_vs_nonstop_percent).toFixed(0)}%` : "—"}</td>}
        <td data-label="Depart" className={isEarlyDeparture(option.departure_at) ? "time-warning" : ""}>{localClock(option.departure_at)}</td>
        <td data-label="Arrive" className={isLateArrival(option.arrival_at) ? "time-warning" : ""}>{localClock(option.arrival_at)}</td>
        <td data-label="Route" className="route-cell">{option.route.join("–")}</td>
        <td data-label="Stopover" className={(option.connection_minutes ?? 0) > 240 ? "time-warning" : ""}>{stopover}</td>
        <td data-label="Journey">{duration(option.total_journey_minutes)}</td>
        {showComparisons && <td data-label="Extra vs nonstop">{option.extra_minutes_vs_nonstop ? `+${duration(option.extra_minutes_vs_nonstop)}` : "—"}</td>}
        <td data-label="Airlines">{option.airlines.join(" / ")}</td>
      </tr>
  );
}

function HistoryCell({ option }: { option: TripOption }) {
  const state = priceHistoryState(option.history);
  return <td data-label="Change" className={`history-cell ${Number(option.history?.price_change_amount ?? 0) > 0 ? "history-up" : Number(option.history?.price_change_amount ?? 0) < 0 ? "history-down" : ""}`} title={historyTooltip(option.history, option.base_price, option.currency)}>
    {state === "LOADING"
      ? <span className="loading-spinner history-loading-spinner" role="status" aria-label="Loading price history" />
      : <><strong aria-label={historyAccessibleLabel(option.history)}>{historySignal(option.history)}</strong>{option.history?.history_status === "PREVIOUS_FOUND" && <small>{elapsedCompactDay(option.history.elapsed_seconds, option.history.previous_observed_at, undefined, option.history.day_difference)}{trendSignal(option.history) && ` · ${trendSignal(option.history)}`}</small>}</>}
  </td>;
}
