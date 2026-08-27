"use client";

import { useEffect, useRef, useState } from "react";
import type { TripSearchRequest } from "@/lib/api/types";
import { mapSelfTransferPolicy } from "@/lib/search/policy";
import { DateFields } from "./date-fields";
import type { DateFieldsHandle } from "./date-fields";

const LONDON = { LGW: "Gatwick", STN: "Stansted", LTN: "Luton", LHR: "Heathrow", LCY: "London City" };
const SARDINIA = { CAG: "Cagliari", OLB: "Olbia", AHO: "Alghero" };

function NumberField({ label, value, min, onChange }: { label: string; value: number; min: number; onChange: (value: number) => void }) {
  return <label className="compact-field number-field"><span>{label}</span><input type="number" min={min} value={value} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

function AirportMultiSelect({ label, group, airports, selected, onChange, onOpen }: { label: string; group: string; airports: Record<string, string>; selected: string[]; onChange: (value: string[]) => void; onOpen: () => void }) {
  const codes = Object.keys(airports);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const outside = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, []);
  return (
    <div ref={root} className="airport-select compact-field">
      <button type="button" className="airport-select-trigger" aria-label={`${label} ${group} (${selected.length})`} aria-expanded={open} onClick={() => setOpen((value) => { if (!value) onOpen(); return !value; })}><span>{label}</span><strong>{group} ({selected.length})</strong></button>
      {open && <div className="airport-menu">
        <div className="airport-menu-actions"><button type="button" onClick={() => onChange(codes)}>Select all</button><button type="button" onClick={() => onChange([])}>Clear</button></div>
        {Object.entries(airports).map(([code, name]) => <label className="airport-option" key={code}><input aria-label={`${label} ${code}`} type="checkbox" checked={selected.includes(code)} onChange={() => onChange(selected.includes(code) ? selected.filter((item) => item !== code) : [...selected, code])} /><strong>{code}</strong><span>{name}</span></label>)}
      </div>}
    </div>
  );
}

function DirectionControls({ title, allowStop, onAllowStop, disabled = false }: { title: string; allowStop: boolean; onAllowStop: (value: boolean) => void; disabled?: boolean }) {
  return <div className={`constraint-row ${disabled ? "disabled" : ""}`}><strong>{title}</strong><div className="micro-toggle"><button type="button" className={!allowStop ? "active" : ""} onClick={() => onAllowStop(false)}>Nonstop</button><button type="button" className={allowStop ? "active" : ""} onClick={() => onAllowStop(true)}>Allow stop</button></div></div>;
}

export function SearchForm({ onSearch, disabled, onExcludeBaggageChange }: { onSearch: (request: TripSearchRequest) => void; disabled: boolean; onExcludeBaggageChange?: (excluded: boolean) => void }) {
  const [origins, setOrigins] = useState(Object.keys(LONDON));
  const [destinations, setDestinations] = useState(Object.keys(SARDINIA));
  const [outboundDate, setOutboundDate] = useState("2026-12-18");
  const [returnDate, setReturnDate] = useState("2026-12-28");
  const [roundTrip, setRoundTrip] = useState(true);
  const [adults, setAdults] = useState(2);
  const [children, setChildren] = useState(2);
  const [cabinBags] = useState(1);
  const [checkedBags] = useState(0);
  const [outboundTransfer, setOutboundTransfer] = useState(false);
  const [returnTransfer, setReturnTransfer] = useState(false);
  const [excludeBaggage, setExcludeBaggage] = useState(true);
  const calendarRef = useRef<DateFieldsHandle>(null);
  const closeCalendars = () => calendarRef.current?.close();

  return (
    <form className="search-panel" onSubmit={(event) => {
      event.preventDefault();
      closeCalendars();
      const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
      onSearch({
        origins,
        destinations,
        outbound_date: outboundDate,
        return_date: roundTrip ? returnDate : null,
        adults,
        children,
        baggage: { cabin_bags: cabinBags, checked_bags: checkedBags },
        outbound_time_window: { earliest_departure_time: null, latest_arrival_time: null, max_connection_minutes: 360 },
        return_time_window: { earliest_departure_time: null, latest_arrival_time: null, max_connection_minutes: 360 },
        max_extra_journey_minutes: null,
        self_transfer_policy: mapSelfTransferPolicy(outboundTransfer, roundTrip && returnTransfer),
        connection_profile: "CONSERVATIVE",
        currency: "GBP",
        refresh_prices: submitter?.value === "refresh",
      });
    }}>
      <div className="primary-controls">
        <AirportMultiSelect label="From" group="London" airports={LONDON} selected={origins} onChange={setOrigins} onOpen={closeCalendars} />
        <AirportMultiSelect label="To" group="Sardinia" airports={SARDINIA} selected={destinations} onChange={setDestinations} onOpen={closeCalendars} />
        <DateFields ref={calendarRef} outbound={outboundDate} inbound={returnDate} roundTrip={roundTrip} origins={origins} destinations={destinations} adults={adults} childPassengers={children} currency="GBP" onOutboundChange={setOutboundDate} onInboundChange={setReturnDate} />
        <NumberField label="Adults" value={adults} min={1} onChange={setAdults} />
        <NumberField label="Children" value={children} min={0} onChange={setChildren} />
        <button className="search-button" value="search" disabled={disabled || !origins.length || !destinations.length}>{disabled ? "Searching…" : "Search"}</button>
        <button className="refresh-button" value="refresh" disabled={disabled || !origins.length || !destinations.length}>Refresh live prices</button>
      </div>
      <div className="constraint-grid">
        <DirectionControls title="Outbound" allowStop={outboundTransfer} onAllowStop={setOutboundTransfer} />
        <DirectionControls title="Return" allowStop={returnTransfer} onAllowStop={setReturnTransfer} disabled={!roundTrip} />
      </div>
      <div className="search-foot"><label><input type="checkbox" checked={roundTrip} onChange={(event) => setRoundTrip(event.target.checked)} /> Return trip</label><label><input type="checkbox" checked={excludeBaggage} onChange={(event) => { setExcludeBaggage(event.target.checked); onExcludeBaggageChange?.(event.target.checked); }} /> Exclude baggage from comparison</label>{excludeBaggage && <span>Prices exclude baggage.</span>}</div>
    </form>
  );
}
