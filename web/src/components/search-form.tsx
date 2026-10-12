"use client";

import { useEffect, useRef, useState } from "react";
import type { ConnectionProfile, TripSearchRequest } from "@/lib/api/types";
import { loadLastSearchForm, saveLastSearchForm } from "@/lib/search/form-persistence";
import { mapSelfTransferPolicy } from "@/lib/search/policy";
import { DateFields } from "./date-fields";
import type { DateFieldsHandle } from "./date-fields";

const LONDON = { STN: "Stansted", LGW: "Gatwick", LTN: "Luton", LHR: "Heathrow", LCY: "London City" };
const SARDINIA = { AHO: "Alghero", CAG: "Cagliari", OLB: "Olbia" };

function selectedInPriorityOrder(selected: string[], airports: Record<string, string>): string[] {
  return Object.keys(airports).filter((airport) => selected.includes(airport));
}

function NumberField({ label, value, min, onChange }: { label: string; value: number; min: number; onChange: (value: number) => void }) {
  return <label className="compact-field number-field"><span>{label}</span><input type="number" inputMode="numeric" step={1} min={min} value={value} onFocus={(event) => event.currentTarget.select()} onChange={(event) => {
    const parsed = Number.parseInt(event.currentTarget.value, 10);
    const next = Number.isFinite(parsed) ? Math.max(min, parsed) : min;
    event.currentTarget.value = String(next);
    onChange(next);
  }} /></label>;
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

export function SearchForm({ onSearch, disabled, onExcludeBaggageChange, hasSearched = false }: { onSearch: (request: TripSearchRequest) => void; disabled: boolean; onExcludeBaggageChange?: (excluded: boolean) => void; hasSearched?: boolean }) {
  const [origins, setOrigins] = useState(Object.keys(LONDON));
  const [destinations, setDestinations] = useState(Object.keys(SARDINIA));
  const [outboundDate, setOutboundDate] = useState("2026-12-18");
  const [returnDate, setReturnDate] = useState("2026-12-28");
  const [roundTrip, setRoundTrip] = useState(true);
  const [adults, setAdults] = useState(2);
  const [children, setChildren] = useState(2);
  const [cabinBags, setCabinBags] = useState(1);
  const [checkedBags, setCheckedBags] = useState(0);
  const [outboundTransfer, setOutboundTransfer] = useState(false);
  const [returnTransfer, setReturnTransfer] = useState(false);
  const [connectionProfile, setConnectionProfile] = useState<ConnectionProfile>("CONSERVATIVE");
  const [excludeBaggage, setExcludeBaggage] = useState(true);
  const [cacheOnly, setCacheOnly] = useState(false);
  const calendarRef = useRef<DateFieldsHandle>(null);
  const closeCalendars = () => calendarRef.current?.close();

  function runSearch(profile: ConnectionProfile, refreshPrices = false) {
    const prioritizedOrigins = selectedInPriorityOrder(origins, LONDON);
    const prioritizedDestinations = selectedInPriorityOrder(destinations, SARDINIA);
    const request: TripSearchRequest = {
      origins: prioritizedOrigins,
      destinations: prioritizedDestinations,
      outbound_date: outboundDate,
      return_date: roundTrip ? returnDate : null,
      adults,
      children,
      baggage: { cabin_bags: cabinBags, checked_bags: checkedBags },
      outbound_time_window: { earliest_departure_time: null, latest_arrival_time: null, max_connection_minutes: 360 },
      return_time_window: { earliest_departure_time: null, latest_arrival_time: null, max_connection_minutes: 360 },
      max_extra_journey_minutes: null,
      self_transfer_policy: mapSelfTransferPolicy(outboundTransfer, roundTrip && returnTransfer),
      connection_profile: profile,
      currency: "GBP",
      refresh_prices: !cacheOnly && refreshPrices,
      cache_only: cacheOnly,
    };
    saveLastSearchForm({ origins: prioritizedOrigins, destinations: prioritizedDestinations, outboundDate, returnDate, roundTrip, adults, children, cabinBags, checkedBags, outboundTransfer, returnTransfer, connectionProfile: profile, excludeBaggage, cacheOnly });
    onSearch(request);
  }

  useEffect(() => {
    const saved = loadLastSearchForm();
    if (!saved) return;
    const restore = window.setTimeout(() => {
      setOrigins(saved.origins);
      setDestinations(saved.destinations);
      setOutboundDate(saved.outboundDate);
      setReturnDate(saved.returnDate);
      setRoundTrip(saved.roundTrip);
      setAdults(saved.adults);
      setChildren(saved.children);
      setCabinBags(saved.cabinBags);
      setCheckedBags(saved.checkedBags);
      setOutboundTransfer(saved.outboundTransfer);
      setReturnTransfer(saved.returnTransfer);
      setConnectionProfile(saved.connectionProfile);
      setExcludeBaggage(saved.excludeBaggage);
      setCacheOnly(saved.cacheOnly ?? false);
      onExcludeBaggageChange?.(saved.excludeBaggage);
    }, 0);
    return () => window.clearTimeout(restore);
  }, [onExcludeBaggageChange]);

  return (
    <form className="search-panel" onSubmit={(event) => {
      event.preventDefault();
      closeCalendars();
      const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
      runSearch(connectionProfile, submitter?.value === "refresh");
    }}>
      <div className="primary-controls">
        <AirportMultiSelect label="From" group="London" airports={LONDON} selected={selectedInPriorityOrder(origins, LONDON)} onChange={setOrigins} onOpen={closeCalendars} />
        <AirportMultiSelect label="To" group="Sardinia" airports={SARDINIA} selected={selectedInPriorityOrder(destinations, SARDINIA)} onChange={setDestinations} onOpen={closeCalendars} />
        <DateFields ref={calendarRef} outbound={outboundDate} inbound={returnDate} roundTrip={roundTrip} origins={selectedInPriorityOrder(origins, LONDON)} destinations={selectedInPriorityOrder(destinations, SARDINIA)} adults={adults} childPassengers={children} currency="GBP" pricesByDefault={hasSearched && !cacheOnly} cacheOnly={cacheOnly} onOutboundChange={setOutboundDate} onInboundChange={setReturnDate} />
        <NumberField label="Adults" value={adults} min={1} onChange={setAdults} />
        <NumberField label="Children" value={children} min={0} onChange={setChildren} />
        <button className="search-button" value="search" disabled={disabled || !origins.length || !destinations.length}>{disabled ? "Searching…" : cacheOnly ? "Use cached results" : "Search"}</button>
        <button className="refresh-button" value="refresh" title={cacheOnly ? "Turn off cached-only mode to fetch live prices" : undefined} disabled={cacheOnly || disabled || !origins.length || !destinations.length}>Refresh live prices</button>
      </div>
      <div className="constraint-grid">
        <DirectionControls title="Outbound" allowStop={outboundTransfer} onAllowStop={setOutboundTransfer} />
        <DirectionControls title="Return" allowStop={returnTransfer} onAllowStop={setReturnTransfer} disabled={!roundTrip} />
      </div>
      <div className="search-foot"><label><input type="checkbox" checked={roundTrip} onChange={(event) => setRoundTrip(event.target.checked)} /> Return trip</label><label><input type="checkbox" checked={excludeBaggage} onChange={(event) => { setExcludeBaggage(event.target.checked); onExcludeBaggageChange?.(event.target.checked); }} /> Exclude baggage from comparison</label><label className={`cache-only-switch ${cacheOnly ? "active" : ""}`}><input role="switch" type="checkbox" checked={cacheOnly} onChange={(event) => setCacheOnly(event.target.checked)} /><span><strong>Use cached results only</strong><small>No live API calls</small></span></label>{(outboundTransfer || (roundTrip && returnTransfer)) && <label className="connection-buffer-control">Connection buffer <select aria-label="Connection buffer" value={connectionProfile} onChange={(event) => {
        const profile = event.target.value as ConnectionProfile;
        setConnectionProfile(profile);
        if (hasSearched && !disabled) runSearch(profile);
      }}><option value="CONSERVATIVE">Conservative (2h+)</option><option value="STANDARD">Standard (90m+)</option><option value="AGGRESSIVE">Aggressive (60m+)</option></select>{hasSearched && <small>Applies immediately</small>}</label>}</div>
    </form>
  );
}
