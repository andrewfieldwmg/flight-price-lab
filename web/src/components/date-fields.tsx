"use client";

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { getCalendarPrices } from "@/lib/api/client";
import type { CalendarPrice } from "@/lib/api/types";

interface DateFieldsProps {
  outbound: string; inbound: string; roundTrip: boolean; origins: string[]; destinations: string[];
  adults: number; childPassengers: number; currency: string; pricesByDefault?: boolean;
  cacheOnly?: boolean;
  onOutboundChange: (value: string) => void; onInboundChange: (value: string) => void;
}

export interface DateFieldsHandle { close: () => void; }

type CellState = "UNLOADED" | "LOADING" | "LOADED" | "CACHED" | "STALE_AVAILABLE" | "UNAVAILABLE" | "ERROR";
type PickerMode = "MONTH" | "PRICES";

function dateAt(value: string) { return new Date(`${value}T12:00:00`); }
function iso(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
function shift(value: string, days: number) { const result = dateAt(value); result.setDate(result.getDate() + days); return iso(result); }
function range(center: string) { return Array.from({ length: 7 }, (_, index) => shift(center, index - 3)); }
function category(value: CalendarPrice["classification"]) { return value === "LOW" ? "Likely cheaper" : value === "HIGH" ? "Higher" : "Typical"; }
function monthStart(value: string) { const result = dateAt(value); result.setDate(1); return iso(result); }
function shiftMonth(value: string, months: number) { const result = dateAt(monthStart(value)); result.setMonth(result.getMonth() + months); return iso(result); }
function monthDates(value: string): Array<string | null> {
  const first = dateAt(monthStart(value));
  const leading = (first.getDay() + 6) % 7;
  const days = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  return [...Array.from({ length: leading }, () => null), ...Array.from({ length: days }, (_, index) => iso(new Date(first.getFullYear(), first.getMonth(), index + 1, 12)))];
}
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function MonthPanel({ month, selected, onSelect }: { month: string; selected: string; onSelect: (date: string) => void }) {
  return <section className="date-month-panel">
    <h3>{new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric" }).format(dateAt(month))}</h3>
    <div className="date-month-weekdays" aria-hidden="true">{WEEKDAYS.map((day) => <span key={day}>{day}</span>)}</div>
    <div className="date-month-days">{monthDates(month).map((date, index) => date
      ? <button type="button" key={date} className={`date-month-cell ${date === selected ? "selected" : ""}`} aria-label={new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(dateAt(date))} onClick={() => onSelect(date)}>{dateAt(date).getDate()}</button>
      : <span className="date-month-blank" key={`blank-${index}`} />)}</div>
  </section>;
}

function DirectionalDatePicker({ label, value, disabled, origins, destinations, adults, childPassengers, currency, direction, open, rootRef, pricesByDefault, cacheOnly, onOpen, onClose, onChange }: {
  label: string; value: string; disabled?: boolean; origins: string[]; destinations: string[];
  adults: number; childPassengers: number; currency: string; direction: "OUTBOUND" | "RETURN";
  open: boolean; rootRef: RefObject<HTMLDivElement | null>; pricesByDefault: boolean; cacheOnly: boolean; onOpen: () => void; onClose: () => void; onChange: (value: string) => void;
}) {
  const [center, setCenter] = useState(value);
  const [month, setMonth] = useState(monthStart(value));
  const [mode, setMode] = useState<PickerMode>(pricesByDefault ? "PRICES" : "MONTH");
  const [prices, setPrices] = useState<Record<string, CalendarPrice>>({});
  const [states, setStates] = useState<Record<string, CellState>>({});
  const [cacheNotice, setCacheNotice] = useState<string | null>(null);
  const dates = useMemo(() => range(center), [center]);

  async function load(visible: string[]) {
    if (cacheOnly) return;
    const missing = visible.filter((date) => !prices[date] && states[date] !== "LOADING");
    if (!missing.length || !origins.length || !destinations.length) return;
    setStates((current) => ({ ...current, ...Object.fromEntries(missing.map((date) => [date, "LOADING"])) }));
    try {
      const response = await getCalendarPrices({ origins, destinations, dateFrom: missing[0], dateTo: missing.at(-1)!, adults, children: childPassengers, currency, direction });
      console.info("CALENDAR_DIAGNOSTICS", {
        direction,
        calendar_provider_calls_this_invocation: response.calendar_provider_calls_this_invocation,
        calendar_calls_avoided: response.calendar_calls_avoided,
        failures: response.failures,
        calendar_calls_concurrent_peak: response.calendar_calls_concurrent_peak,
        calendar_provider_median_ms: response.calendar_provider_median_ms,
        calendar_provider_p95_ms: response.calendar_provider_p95_ms,
        calendar_provider_slowest_ms: response.calendar_provider_slowest_ms,
        calendar_total_duration_ms: response.calendar_total_duration_ms,
        calendar_postgres_total_ms: response.calendar_postgres_total_ms,
        request_timings: response.request_timings,
      });
      const returned = Object.fromEntries(response.dates.map((item) => [item.date, item]));
      setPrices((current) => ({ ...current, ...returned }));
      setStates((current) => ({ ...current, ...Object.fromEntries(missing.map((date) => [date, (returned[date]?.state as CellState | undefined) ?? "ERROR"])) }));
      const calls = response.calendar_provider_calls_this_invocation;
      const avoided = response.calendar_calls_avoided;
      setCacheNotice(calls === 0
        ? `Daily cache · ${avoided} price${avoided === 1 ? "" : "s"} reused · refreshes after 04:00 London`
        : `${calls} live price lookup${calls === 1 ? "" : "s"} · saved in the daily cache until 04:00 London`);
    } catch {
      setStates((current) => ({ ...current, ...Object.fromEntries(missing.map((date) => [date, "ERROR"])) }));
      setCacheNotice("Price guidance is temporarily unavailable");
    }
  }

  function show() {
    const nextMode = pricesByDefault ? "PRICES" : "MONTH";
    onOpen();
    setCenter(value);
    setMonth(monthStart(value));
    setMode(nextMode);
    if (nextMode === "PRICES") void load(range(value));
  }
  function navigate(days: number) { const next = shift(center, days); setCenter(next); void load(range(next)); }
  function changeMode(nextMode: PickerMode) {
    if (cacheOnly && nextMode === "PRICES") return;
    setMode(nextMode);
    if (nextMode === "PRICES") {
      setCenter(value);
      void load(range(value));
    } else {
      setMonth(monthStart(value));
    }
  }
  const nextMonth = shiftMonth(month, 1);

  return <div ref={rootRef} className="directional-date compact-field">
    <button type="button" className="date-trigger" disabled={disabled} onClick={() => open ? onClose() : show()}><span>{label}</span><strong>{new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(dateAt(value))}</strong></button>
    {open && <div className={`date-popover ${direction.toLowerCase()}`} role="dialog" aria-label={`${label} directional date prices`}>
      <div className="date-picker-mode" role="group" aria-label="Date picker view"><button type="button" aria-pressed={mode === "MONTH"} onClick={() => changeMode("MONTH")}>Choose date</button><button type="button" aria-pressed={mode === "PRICES"} disabled={cacheOnly} title={cacheOnly ? "Disabled while cached-only mode is on" : undefined} onClick={() => changeMode("PRICES")}>Nearby prices</button></div>
      {mode === "MONTH" ? <>
        <div className="date-popover-nav"><button type="button" aria-label="Previous month" onClick={() => setMonth(shiftMonth(month, -1))}>‹</button><strong>{new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric" }).format(dateAt(month))}</strong><button type="button" aria-label="Next month" onClick={() => setMonth(nextMonth)}>›</button></div>
        <div className="date-months"><MonthPanel month={month} selected={value} onSelect={(date) => { onChange(date); onClose(); }} /><MonthPanel month={nextMonth} selected={value} onSelect={(date) => { onChange(date); onClose(); }} /></div>
        <p>{cacheOnly ? "Cached-only mode is on. Date selection and searching will not make live fare requests." : "Choosing a date does not fetch fares. Use Nearby prices for a cached seven-day comparison."}</p>
      </> : <>
        <div className="date-popover-nav"><button type="button" aria-label="Previous dates" onClick={() => navigate(-7)}>‹</button><strong>{new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric" }).format(dateAt(center))}</strong><button type="button" aria-label="Next dates" onClick={() => navigate(7)}>›</button></div>
        <div className="date-price-grid">{dates.map((date) => {
          const item = prices[date]; const state = states[date] ?? "UNLOADED";
          return <button type="button" key={date} className={`date-price-cell ${item?.classification?.toLowerCase() ?? ""} ${date === value ? "selected" : ""}`} onClick={() => { onChange(date); onClose(); }}>
            <span>{new Intl.DateTimeFormat("en-GB", { weekday: "short" }).format(dateAt(date))}</span><strong>{dateAt(date).getDate()}</strong>
            {state === "LOADING" ? <i role="status" aria-label="Loading date price" className="date-price-spinner" /> : state === "UNAVAILABLE" || state === "ERROR" ? <small title={state === "ERROR" ? "Price temporarily unavailable" : "No nonstop fare observed"}>—</small> : item?.price !== null && item ? <><small>{new Intl.NumberFormat("en-GB", { style: "currency", currency: item.currency, maximumFractionDigits: 0 }).format(Number(item.price))}</small><em>{state === "STALE_AVAILABLE" ? "Last observed" : category(item.classification)}</em></> : <small>Load</small>}
          </button>;
        })}</div>
        {cacheNotice && <p className="date-cache-notice" role="status">{cacheNotice}</p>}
        <p>Date guidance uses the lowest observed nonstop fare as a directional indicator. Full one-stop options are calculated after you search.</p>
      </>}
    </div>}
  </div>;
}

export const DateFields = forwardRef<DateFieldsHandle, DateFieldsProps>(function DateFields(props, ref) {
  const [openDirection, setOpenDirection] = useState<"OUTBOUND" | "RETURN" | null>(null);
  const outboundRoot = useRef<HTMLDivElement>(null);
  const returnRoot = useRef<HTMLDivElement>(null);
  useImperativeHandle(ref, () => ({ close: () => setOpenDirection(null) }), []);
  useEffect(() => {
    const outside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!outboundRoot.current?.contains(target) && !returnRoot.current?.contains(target)) setOpenDirection(null);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpenDirection(null); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, []);
  const outboundKey = ["OUTBOUND", ...props.origins, "to", ...props.destinations, props.adults, props.childPassengers, props.currency, props.cacheOnly].join(":");
  const returnKey = ["RETURN", ...props.destinations, "to", ...props.origins, props.adults, props.childPassengers, props.currency, props.cacheOnly].join(":");
  return <>
    <DirectionalDatePicker key={outboundKey} label="Out" value={props.outbound} origins={props.origins} destinations={props.destinations} adults={props.adults} childPassengers={props.childPassengers} currency={props.currency} direction="OUTBOUND" pricesByDefault={props.pricesByDefault ?? false} cacheOnly={props.cacheOnly ?? false} open={openDirection === "OUTBOUND"} rootRef={outboundRoot} onOpen={() => setOpenDirection("OUTBOUND")} onClose={() => setOpenDirection(null)} onChange={props.onOutboundChange} />
    <DirectionalDatePicker key={returnKey} label="Return" value={props.inbound} disabled={!props.roundTrip} origins={props.destinations} destinations={props.origins} adults={props.adults} childPassengers={props.childPassengers} currency={props.currency} direction="RETURN" pricesByDefault={props.pricesByDefault ?? false} cacheOnly={props.cacheOnly ?? false} open={openDirection === "RETURN"} rootRef={returnRoot} onOpen={() => setOpenDirection("RETURN")} onClose={() => setOpenDirection(null)} onChange={props.onInboundChange} />
  </>;
});
