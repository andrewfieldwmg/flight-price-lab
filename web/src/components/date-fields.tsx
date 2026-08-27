"use client";

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { getCalendarPrices } from "@/lib/api/client";
import type { CalendarPrice } from "@/lib/api/types";

interface DateFieldsProps {
  outbound: string; inbound: string; roundTrip: boolean; origins: string[]; destinations: string[];
  adults: number; childPassengers: number; currency: string;
  onOutboundChange: (value: string) => void; onInboundChange: (value: string) => void;
}

export interface DateFieldsHandle { close: () => void; }

type CellState = "UNLOADED" | "LOADING" | "LOADED" | "CACHED" | "STALE_AVAILABLE" | "UNAVAILABLE" | "ERROR";

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

function DirectionalDatePicker({ label, value, disabled, origins, destinations, adults, childPassengers, currency, direction, open, rootRef, onOpen, onClose, onChange }: {
  label: string; value: string; disabled?: boolean; origins: string[]; destinations: string[];
  adults: number; childPassengers: number; currency: string; direction: "OUTBOUND" | "RETURN";
  open: boolean; rootRef: RefObject<HTMLDivElement | null>; onOpen: () => void; onClose: () => void; onChange: (value: string) => void;
}) {
  const [center, setCenter] = useState(value);
  const [prices, setPrices] = useState<Record<string, CalendarPrice>>({});
  const [states, setStates] = useState<Record<string, CellState>>({});
  const dates = useMemo(() => range(center), [center]);

  async function load(visible: string[]) {
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
    } catch {
      setStates((current) => ({ ...current, ...Object.fromEntries(missing.map((date) => [date, "ERROR"])) }));
    }
  }

  function show() { onOpen(); setCenter(value); void load(range(value)); }
  function navigate(days: number) { const next = shift(center, days); setCenter(next); void load(range(next)); }

  return <div ref={rootRef} className="directional-date compact-field">
    <button type="button" className="date-trigger" disabled={disabled} onClick={() => open ? onClose() : show()}><span>{label}</span><strong>{new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(dateAt(value))}</strong></button>
    {open && <div className="date-popover" role="dialog" aria-label={`${label} directional date prices`}>
      <div className="date-popover-nav"><button type="button" aria-label="Previous dates" onClick={() => navigate(-7)}>‹</button><strong>{new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric" }).format(dateAt(center))}</strong><button type="button" aria-label="Next dates" onClick={() => navigate(7)}>›</button></div>
      <div className="date-price-grid">{dates.map((date) => {
        const item = prices[date]; const state = states[date] ?? "UNLOADED";
        return <button type="button" key={date} className={`date-price-cell ${item?.classification?.toLowerCase() ?? ""} ${date === value ? "selected" : ""}`} onClick={() => { onChange(date); onClose(); }}>
          <span>{new Intl.DateTimeFormat("en-GB", { weekday: "short" }).format(dateAt(date))}</span><strong>{dateAt(date).getDate()}</strong>
          {state === "LOADING" ? <i role="status" aria-label="Loading date price" className="date-price-spinner" /> : state === "UNAVAILABLE" || state === "ERROR" ? <small title={state === "ERROR" ? "Price temporarily unavailable" : "No nonstop fare observed"}>—</small> : item?.price !== null && item ? <><small>{new Intl.NumberFormat("en-GB", { style: "currency", currency: item.currency, maximumFractionDigits: 0 }).format(Number(item.price))}</small><em>{state === "STALE_AVAILABLE" ? "Last observed" : category(item.classification)}</em></> : <small>Load</small>}
        </button>;
      })}</div>
      <p>Date guidance uses the lowest observed nonstop fare as a directional indicator. Full one-stop options are calculated after you search.</p>
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
  return <>
    <DirectionalDatePicker label="Out" value={props.outbound} origins={props.origins} destinations={props.destinations} adults={props.adults} childPassengers={props.childPassengers} currency={props.currency} direction="OUTBOUND" open={openDirection === "OUTBOUND"} rootRef={outboundRoot} onOpen={() => setOpenDirection("OUTBOUND")} onClose={() => setOpenDirection(null)} onChange={props.onOutboundChange} />
    <DirectionalDatePicker label="Return" value={props.inbound} disabled={!props.roundTrip} origins={props.destinations} destinations={props.origins} adults={props.adults} childPassengers={props.childPassengers} currency={props.currency} direction="RETURN" open={openDirection === "RETURN"} rootRef={returnRoot} onOpen={() => setOpenDirection("RETURN")} onClose={() => setOpenDirection(null)} onChange={props.onInboundChange} />
  </>;
});
