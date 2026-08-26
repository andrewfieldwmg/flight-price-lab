"use client";

import { useState } from "react";
import { createPortal } from "react-dom";
import { bookingHandoffUrl, prepareBooking } from "@/lib/api/client";
import type { BookingSession, BookingTicket } from "@/lib/api/types";
import { localClock } from "./direction-results";

type DrawerState = "IDLE" | "PREPARING" | "READY" | "PARTIAL" | "ERROR";

function money(value: number | string, currency: string) {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value));
}
function carrierName(code: string) { return ({ FR: "Ryanair", U2: "easyJet", W4: "Wizz Air", XZ: "Aeroitalia", BA: "British Airways", AZ: "ITA Airways", VY: "Vueling", LX: "SWISS", DE: "Condor" } as Record<string, string>)[code] ?? code; }
function changeLabel(ticket: BookingTicket) {
  if (ticket.price_delta === null) return "Verify on airline";
  const delta = Number(ticket.price_delta);
  if (delta < 0) return `↓ ${money(Math.abs(delta), ticket.currency)}`;
  if (delta > 0) return `+${money(delta, ticket.currency)}`;
  return "No change";
}

export function BookingPreparation({ searchId, optionIds }: { searchId: string | null; optionIds: string[] }) {
  const [session, setSession] = useState<BookingSession | null>(null);
  const [drawerState, setDrawerState] = useState<DrawerState>("IDLE");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({});
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [preparedFor, setPreparedFor] = useState<string[] | null>(null);
  const validSelection = Boolean(searchId && optionIds.length);
  const loading = drawerState === "PREPARING";

  async function prepare(refreshBookingPrices = false) {
    if (!searchId || !optionIds.length) return;
    setDrawerOpen(true);
    setDrawerState("PREPARING");
    setError(null);
    try {
      const prepared = await prepareBooking(searchId, optionIds, refreshBookingPrices);
      setSession(prepared);
      setPreparedFor([...optionIds]);
      setAcknowledged({});
      if (prepared.tickets.length === 0) {
        setError("Booking preparation returned no tickets.");
        setDrawerState("ERROR");
      } else if (prepared.state === "FAILED" || prepared.tickets.some((ticket) => ticket.status === "FAILED")) {
        setDrawerState("PARTIAL");
      } else {
        setDrawerState("READY");
      }
    } catch {
      setSession(null);
      setError("Booking preparation failed. Please try again.");
      setDrawerState("ERROR");
    }
  }

  const tickets = session?.tickets ?? [];
  const currency = tickets[0]?.currency ?? "GBP";
  const originalTotal = tickets.length ? session?.original_total ?? tickets.reduce((sum, ticket) => sum + Number(ticket.original_price), 0) : null;
  const currentTotal = tickets.length && session ? session.current_total : null;
  const delta = session?.price_delta === null || session?.price_delta === undefined ? null : Number(session.price_delta);
  const mayRenderCards = drawerState === "READY" || drawerState === "PARTIAL";
  const stale = Boolean(session && preparedFor && (preparedFor.length !== optionIds.length || preparedFor.some((id, index) => id !== optionIds[index])));

  const drawer = drawerOpen && typeof document !== "undefined" ? createPortal(
    <div className="booking-drawer-overlay" data-testid="booking-drawer-overlay">
      <aside className="booking-drawer" role="dialog" aria-modal="true" aria-label="Prepared booking">
        <header className="booking-drawer-header">
          <div><span className="booking-eyebrow">{drawerState === "PREPARING" ? "Preparing booking" : drawerState === "ERROR" ? "Booking unavailable" : "Ready to book"}</span>{mayRenderCards && <h2>Current trip total</h2>}</div>
          <button type="button" className="booking-drawer-close" aria-label="Close prepared booking" onClick={() => setDrawerOpen(false)}>×</button>
        </header>
        {drawerState === "PREPARING" && <div className="booking-drawer-state" role="status">Preparing selected flights…</div>}
        {drawerState === "ERROR" && <div className="booking-drawer-state booking-ticket-error" role="alert">{error}</div>}
        {stale && drawerState !== "PREPARING" && <div className="booking-stale-state"><strong>Selection changed</strong><p>The prepared booking shown here belongs to your previous trip selection.</p><button type="button" onClick={() => prepare()}>Prepare new selection</button></div>}
        {mayRenderCards && session && <>
          <div className="booking-current-total">{currentTotal === null ? "Verify on airlines" : money(currentTotal, currency)}</div>
          <div className="booking-total-context">
            {originalTotal !== null && <span>Was {money(originalTotal, currency)}</span>}
            {delta !== null && delta < 0 && <strong>You save {money(Math.abs(delta), currency)} since search</strong>}
            {delta !== null && delta > 0 && <strong>Price increased by {money(delta, currency)}</strong>}
            {delta === 0 && <strong>No price change</strong>}
          </div>
          <div className="booking-provider-diagnostics">Booking provider calls now: {session.booking_provider_calls_this_invocation}</div>
          <div className="booking-cards">
            {tickets.map((ticket, index) => {
              const material = ticket.price_change_status === "MATERIAL_INCREASE" && ticket.material_change_acknowledgement_required;
              const canOpen = !stale && ticket.status !== "FAILED" && (!material || acknowledged[ticket.ticket_id]);
              return <article key={ticket.ticket_id} className="booking-ticket-card">
                <span className="booking-count">Booking {index + 1} of {tickets.length}</span>
                <h3>{carrierName(ticket.carrier)}</h3><strong className="booking-flight">{ticket.flight_number}</strong>
                <p className="booking-schedule"><strong>{ticket.route.split(" → ")[0]} {localClock(ticket.departure_at)} → {ticket.route.split(" → ").at(-1)} {localClock(ticket.arrival_at)}</strong><br />{new Date(`${ticket.travel_date}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" })}<br />{ticket.adults} adult{ticket.adults === 1 ? "" : "s"} + {ticket.children} child{ticket.children === 1 ? "" : "ren"}</p>
                <dl className="booking-prices">
                  <div><dt>Search price</dt><dd>{money(ticket.original_price, ticket.currency)}</dd></div>
                  <div><dt>{ticket.carrier === "AZ" ? "Latest booking-option price" : "Current price"}</dt><dd>{ticket.current_price === null ? "Verify on airline" : money(ticket.current_price, ticket.currency)}</dd></div>
                  {ticket.carrier === "AZ" && <div><dt>Airline price</dt><dd>Verify on ITA</dd></div>}
                  <div><dt>Price change</dt><dd className={Number(ticket.price_delta) < 0 ? "price-decrease" : ""}>{changeLabel(ticket)}</dd></div>
                </dl>
                {ticket.status === "FAILED" ? <p className="booking-ticket-error">This ticket could not be prepared. No airline handoff was started.</p> : <>
                  {ticket.capability === "EXACT_FLIGHT_HANDOFF" && <p className="booking-capability"><strong>Exact flight ready</strong><br />Passenger composition preserved<br />Fare selection happens on {carrierName(ticket.carrier)}</p>}
                  {ticket.capability === "PREFILLED_SEARCH" && <p className="booking-capability"><strong>Search prefilled — confirm {ticket.flight_number}</strong><br />Route, date and passengers are preserved. Confirm the flight and current price on {carrierName(ticket.carrier)}.</p>}
                  {ticket.carrier === "AZ" && <p className="booking-ticket-warning">ITA may reprice significantly during handoff. Confirm the fare before continuing.</p>}
                  {material && <label className="booking-acknowledgement"><input type="checkbox" checked={acknowledged[ticket.ticket_id] ?? false} onChange={(event) => setAcknowledged({ ...acknowledged, [ticket.ticket_id]: event.target.checked })} /> I acknowledge this material price increase</label>}
                  <form method="post" target="_blank" action={bookingHandoffUrl(session.booking_session_id, ticket.ticket_id, material)} onSubmit={() => setOpened({ ...opened, [ticket.ticket_id]: true })}><button className="airline-handoff-button" type="submit" disabled={!canOpen}>Continue on {carrierName(ticket.carrier)}</button></form>
                  <span className="booking-open-status">{opened[ticket.ticket_id] ? "Opened" : "Not opened"}</span>
                </>}
              </article>;
            })}
          </div>
        </>}
        {drawerState !== "PREPARING" && !stale && <button type="button" className="booking-refresh" onClick={() => prepare(true)}>Refresh booking prices</button>}
      </aside>
    </div>, document.body
  ) : null;

  return <div className="booking-preparation-action">
    <button className="primary-booking-cta" type="button" onClick={session && drawerState !== "ERROR" && !stale ? () => setDrawerOpen(true) : () => prepare()} disabled={loading || !validSelection}>{loading ? "Preparing booking…" : stale ? "Prepare selected trip" : session && drawerState !== "ERROR" ? "View prepared booking" : "Prepare booking"}</button>
    {error && !drawerOpen && <span role="alert">{error}</span>}{drawer}
  </div>;
}
