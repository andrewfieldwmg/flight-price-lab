import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BookingPreparation } from "./booking-preparation";

const prepareBooking = vi.fn();
vi.mock("@/lib/api/client", () => ({
  prepareBooking: (...args: unknown[]) => prepareBooking(...args),
  bookingHandoffUrl: (session: string, ticket: string, acknowledged: boolean) =>
    `/api/booking/${session}/handoff/${ticket}${acknowledged ? "?acknowledge_material_change=true" : ""}`,
}));

function ticket(overrides = {}) {
  return {
    ticket_id: "ticket-1", carrier: "FR", flight_number: "FR 2687", route: "STN → CAG",
    travel_date: "2026-12-18", original_price: "849", current_price: "813", price_delta: "-36",
    departure_at: "2026-12-18T14:25:00+00:00", arrival_at: "2026-12-18T17:25:00+01:00",
    currency: "GBP", status: "READY", price_change_status: "PRICE_DECREASED",
    material_change_acknowledgement_required: false, capability: "EXACT_FLIGHT_HANDOFF",
    pricing_confidence: "VERIFIED",
    fare_selected: false, adults: 2, children: 2, exact_flight_verified: true,
    passenger_composition_verified: true, ...overrides,
  };
}

function session(overrides = {}) {
  return { booking_session_id: "opaque-session", state: "READY", original_total: "849", current_total: "813", price_delta: "-36", booking_provider_calls_this_invocation: 1, tickets: [ticket()], ...overrides };
}

describe("BookingPreparation drawer", () => {
  beforeEach(() => prepareBooking.mockReset());

  it("renders a real primary action and opens the drawer only after preparation", async () => {
    prepareBooking.mockResolvedValue(session());
    render(<section aria-label="Green summary"><BookingPreparation searchId="search" optionIds={["outbound"]} /></section>);
    const action = screen.getByRole("button", { name: "Prepare booking" });
    expect(action).toHaveClass("primary-booking-cta");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(action);
    const drawer = await screen.findByRole("dialog", { name: "Prepared booking" });
    expect(within(drawer).getByText("Ready to book")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Green summary" })).queryByText("Ready to book")).not.toBeInTheDocument();
  });

  it("has disabled and loading primary states", async () => {
    let release!: (value: ReturnType<typeof session>) => void;
    prepareBooking.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const { rerender } = render(<BookingPreparation searchId={null} optionIds={[]} />);
    expect(screen.getByRole("button", { name: "Prepare booking" })).toBeDisabled();
    rerender(<BookingPreparation searchId="search" optionIds={["outbound"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    expect(screen.getByRole("button", { name: "Preparing booking…" })).toBeDisabled();
    expect(screen.getByText("Preparing selected flights…")).toBeInTheDocument();
    expect(screen.queryByText(/Was £0\.00/)).not.toBeInTheDocument();
    release(session());
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("renders an explicit error instead of empty totals for a successful empty response", async () => {
    prepareBooking.mockResolvedValue(session({ state: "FAILED", original_total: "0", current_total: null, price_delta: null, tickets: [] }));
    render(<BookingPreparation searchId="search" optionIds={["outbound"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    expect(await screen.findByText("Booking preparation returned no tickets.")).toBeInTheDocument();
    expect(screen.queryByText("Was £0.00")).not.toBeInTheDocument();
    expect(screen.queryByText("Current trip total")).not.toBeInTheDocument();
  });

  it("closes and reopens the prepared session without repricing", async () => {
    prepareBooking.mockResolvedValue(session());
    render(<BookingPreparation searchId="search" optionIds={["outbound"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "Close prepared booking" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View prepared booking" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(prepareBooking).toHaveBeenCalledTimes(1);
  });

  it("shows two independent ticket cards and buttons", async () => {
    const second = ticket({ ticket_id: "ticket-2", carrier: "W4", flight_number: "W4 6997", route: "MXP → CAG", current_price: null, price_delta: null, price_change_status: null, capability: "PREFILLED_SEARCH", exact_flight_verified: false });
    prepareBooking.mockResolvedValue(session({ current_total: null, price_delta: null, tickets: [ticket(), second] }));
    render(<BookingPreparation searchId="search" optionIds={["connection"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    expect(await screen.findByText("Booking 1 of 2")).toBeInTheDocument();
    expect(screen.getByText("Booking 2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue on Ryanair" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Continue on Wizz Air" })).toBeEnabled();
    expect(screen.getByText("Search prefilled — confirm W4 6997")).toBeInTheDocument();
    expect(screen.getAllByText("Not opened")).toHaveLength(2);
  });

  it("does not require acknowledgement for decreases and shows combined saving", async () => {
    const second = ticket({ ticket_id: "ticket-2", flight_number: "FR 2686", original_price: "788", current_price: "757", price_delta: "-31" });
    prepareBooking.mockResolvedValue(session({ original_total: "1637", current_total: "1570", price_delta: "-67", tickets: [ticket(), second] }));
    render(<BookingPreparation searchId="search" optionIds={["outbound", "return"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    expect(await screen.findByText("You save £67.00 since search")).toBeInTheDocument();
    expect(screen.getByText("↓ £36.00")).toBeInTheDocument();
    expect(screen.getByText("↓ £31.00")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("requires acknowledgement only for a material increase", async () => {
    prepareBooking.mockResolvedValue(session({ state: "PRICE_CHANGED", current_total: "900", price_delta: "51", tickets: [ticket({ current_price: "900", price_delta: "51", price_change_status: "MATERIAL_INCREASE", material_change_acknowledgement_required: true })] }));
    render(<BookingPreparation searchId="search" optionIds={["outbound"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    const handoff = await screen.findByRole("button", { name: "Continue on Ryanair" });
    expect(handoff).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(handoff).toBeEnabled();
  });

  it("reprices again only through Refresh booking prices", async () => {
    prepareBooking.mockResolvedValue(session());
    render(<BookingPreparation searchId="search" optionIds={["outbound"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    await screen.findByRole("dialog");
    expect(prepareBooking).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Refresh booking prices" }));
    await waitFor(() => expect(prepareBooking).toHaveBeenCalledTimes(2));
    expect(prepareBooking).toHaveBeenLastCalledWith("search", ["outbound"], true);
  });

  it("marks a changed selection stale and only prepares it on explicit action", async () => {
    prepareBooking.mockResolvedValue(session());
    const { rerender } = render(<BookingPreparation searchId="search" optionIds={["old"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    await screen.findByRole("dialog");
    rerender(<BookingPreparation searchId="search" optionIds={["new"]} />);
    expect(screen.getByText("Selection changed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare selected trip" })).toBeInTheDocument();
    expect(prepareBooking).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Prepare new selection" }));
    await waitFor(() => expect(prepareBooking).toHaveBeenLastCalledWith("search", ["new"], false));
    fireEvent.click(screen.getByRole("button", { name: "Close prepared booking" }));
    fireEvent.click(screen.getByRole("button", { name: "View prepared booking" }));
    expect(prepareBooking).toHaveBeenCalledTimes(2);
  });

  it("shows exact scheduled times for each constituent", async () => {
    prepareBooking.mockResolvedValue(session());
    render(<BookingPreparation searchId="search" optionIds={["outbound"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    expect(await screen.findByText("STN 14:25 → CAG 17:25")).toBeInTheDocument();
  });

  it("renders independent Aeroitalia and British Airways prefilled handoffs", async () => {
    const aeroitalia = ticket({ ticket_id: "xz", carrier: "XZ", flight_number: "XZ 2331", capability: "PREFILLED_SEARCH", exact_flight_verified: false });
    const britishAirways = ticket({ ticket_id: "ba", carrier: "BA", flight_number: "BA 534", capability: "PREFILLED_SEARCH", exact_flight_verified: false });
    prepareBooking.mockResolvedValue(session({ tickets: [aeroitalia, britishAirways] }));
    render(<BookingPreparation searchId="search" optionIds={["mixed"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    expect(await screen.findByRole("button", { name: "Continue on Aeroitalia" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Continue on British Airways" })).toBeEnabled();
    expect(screen.getByText("Search prefilled — confirm XZ 2331")).toBeInTheDocument();
    expect(screen.getByText("Search prefilled — confirm BA 534")).toBeInTheDocument();
  });

  it("renders ITA Airways as a prefilled handoff requiring flight confirmation", async () => {
    const ita = ticket({ ticket_id: "az", carrier: "AZ", flight_number: "AZ 217", route: "LCY → LIN", original_price: "236", current_price: "276", price_delta: "40", capability: "PREFILLED_SEARCH", pricing_confidence: "UNVERIFIED", exact_flight_verified: false });
    prepareBooking.mockResolvedValue(session({ original_total: "236", current_total: null, price_delta: null, tickets: [ita] }));
    render(<BookingPreparation searchId="search" optionIds={["ita"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Prepare booking" }));
    expect(await screen.findByRole("button", { name: "Continue on ITA Airways" })).toBeEnabled();
    expect(screen.getByText("Search prefilled — confirm AZ 217")).toBeInTheDocument();
    expect(screen.getByText(/Confirm the flight and current price on ITA Airways/)).toBeInTheDocument();
    expect(screen.getByText("Latest booking-option price")).toBeInTheDocument();
    expect(screen.getByText("Verify on ITA")).toBeInTheDocument();
    expect(screen.getByText("ITA may reprice significantly during handoff. Confirm the fare before continuing.")).toBeInTheDocument();
    expect(screen.getByText("Verify on airlines")).toBeInTheDocument();
  });
});
