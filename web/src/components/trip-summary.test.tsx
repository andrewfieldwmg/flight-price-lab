import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { option, synthetic } from "@/test/fixtures";
import { TripSummary } from "./trip-summary";

let observerCallback: IntersectionObserverCallback | null = null;
class MockIntersectionObserver {
  constructor(callback: IntersectionObserverCallback) { observerCallback = callback; }
  observe = vi.fn(); disconnect = vi.fn(); unobserve = vi.fn(); takeRecords = vi.fn(() => []);
  root = null; rootMargin = "0px"; thresholds = [];
}

afterEach(() => { observerCallback = null; vi.unstubAllGlobals(); });

const renderOutbound = (comparison = true) => render(<TripSummary outbound={synthetic()} inbound={null} outboundBaseline={option({ id: "baseline" })} inboundBaseline={null} outboundComparisonEnabled={comparison} />);

describe("selected trip summary", () => {
  it("puts the trip total and base-fare saving before itinerary detail", () => {
    renderOutbound();
    const total = screen.getByText("Trip total");
    const outbound = screen.getByText(/^Outbound ·/);
    expect(total.compareDocumentPosition(outbound) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByText("£486").length).toBeGreaterThan(0);
    expect(screen.getByText("£255 / 34%")).toBeInTheDocument();
    expect(screen.getByLabelText(/LGW 08:00 to MXP 10:00.*U2 8309/)).toBeInTheDocument();
    expect(screen.getByText("(easyJet U2 8309)")).toBeInTheDocument();
    expect(screen.getByText("Separate tickets")).toBeInTheDocument();
  });

  it("shows round-trip base totals and both component itineraries", () => {
    render(<TripSummary outbound={synthetic()} inbound={synthetic("RETURN")} outboundBaseline={option({ id: "out-direct" })} inboundBaseline={option({ id: "in-direct", direction: "RETURN" })} outboundComparisonEnabled inboundComparisonEnabled outboundDate="2026-12-18" returnDate="2026-12-28" />);
    expect(screen.getByText("£972")).toBeInTheDocument();
    expect(screen.getByText("£510 / 34%")).toBeInTheDocument();
    expect(screen.getByText(/^Return ·/)).toBeInTheDocument();
    expect(screen.getByLabelText(/CAG 08:00 to MXP 10:00/)).toBeInTheDocument();
    expect(screen.getByText("Outbound · Fri 18 Dec 2026")).toBeInTheDocument();
    expect(screen.getByText("Return · Mon 28 Dec 2026")).toBeInTheDocument();
  });

  it("shows composite and constituent history without manufacturing unmatched trip totals", () => {
    const outbound = synthetic();
    const inbound = synthetic("RETURN");
    const comparison = { history_status: "PREVIOUS_FOUND" as const, previous_price: "378", price_change_amount: "24", price_change_percent: "6.35", previous_observed_at: "2026-08-20T08:00:00Z", elapsed_seconds: 3 * 86400, day_difference: 3, previous_observation_run_id: "common-run" };
    outbound.history = comparison;
    inbound.history = { ...comparison, previous_price: "540", price_change_amount: "-54", price_change_percent: "-10" };
    outbound.legs[0].constituent_price = "258";
    outbound.legs[0].history = { ...comparison, previous_price: "250", price_change_amount: "8", price_change_percent: "3.2" };
    outbound.legs[1].constituent_price = "228";
    render(<TripSummary outbound={outbound} inbound={inbound} outboundBaseline={option({ id: "out" })} inboundBaseline={option({ id: "in", direction: "RETURN" })} outboundComparisonEnabled inboundComparisonEnabled />);
    expect(screen.getByText("↑ 6.3% since 3d ago")).toBeInTheDocument();
    expect(screen.getByText("£258")).toBeInTheDocument();
    expect(screen.getByText("↑ 3.2% since 3d ago")).toBeInTheDocument();
    expect(screen.getByText("New", { selector: ".summary-leg em" })).toBeInTheDocument();
    expect(screen.getByText("↓ 10.0% since 3d ago")).toBeInTheDocument();
    expect(screen.getByText("↑ 5.9% since 3d ago")).toHaveAccessibleName("Trip price increased by 5.9 percent since last seen");
    expect(screen.getByText("was £918")).toHaveClass("summary-previous-price");
  });

  it("keeps the legacy summary fallback separate from table loading presentation", () => {
    const unresolved = option({ id: "unresolved", history: undefined });
    render(<TripSummary outbound={unresolved} inbound={null} outboundBaseline={unresolved} inboundBaseline={null} />);
    expect(screen.getByText("New", { selector: ".summary-total-block small" })).toBeInTheDocument();
    expect(screen.queryByText("First seen")).not.toBeInTheDocument();
  });

  it("shows First seen only for an explicitly resolved observation", () => {
    const first = { history_status: "FIRST_SEEN" as const, previous_price: null, price_change_amount: null, price_change_percent: null, previous_observed_at: null, elapsed_seconds: null, previous_observation_run_id: null };
    const selected = option({ id: "first", history: first });
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={selected} inboundBaseline={null} />);
    expect(screen.getAllByText("First seen")).toHaveLength(2);
    expect(screen.queryByLabelText("Loading price history")).not.toBeInTheDocument();
  });

  it("omits an exact combined history total when prior runs differ", () => {
    const outbound = synthetic();
    const inbound = synthetic("RETURN");
    const comparison = { history_status: "PREVIOUS_FOUND" as const, previous_price: "400", price_change_amount: "86", price_change_percent: "21.5", previous_observed_at: "2026-08-20T08:00:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "out-run" };
    outbound.history = comparison;
    inbound.history = { ...comparison, previous_observation_run_id: "return-run" };
    render(<TripSummary outbound={outbound} inbound={inbound} outboundBaseline={option({ id: "out" })} inboundBaseline={option({ id: "in", direction: "RETURN" })} />);
    expect(screen.queryByText(/Previously/)).not.toBeInTheDocument();
    expect(screen.getByText("History unavailable")).toBeInTheDocument();
    expect(screen.getAllByText(/since yesterday/).length).toBeGreaterThan(0);
  });

  it("combines only common-run direction history for trip change and sparkline", () => {
    const outbound = option({ id: "out-current", base_price: "784" });
    const inbound = option({ id: "in-current", direction: "RETURN", base_price: "735" });
    outbound.history = { history_status: "PREVIOUS_FOUND", previous_price: "849", price_change_amount: "-65", price_change_percent: "-7.66", previous_observed_at: "2026-08-26T14:41:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "prior", visual_series: [
      { observed_at: "2026-08-26T14:41:00Z", price: "849", observation_run_id: "prior" },
      { observed_at: "2026-08-27T12:30:00Z", price: "784", observation_run_id: "CURRENT" },
    ] };
    inbound.history = { history_status: "PREVIOUS_FOUND", previous_price: "788", price_change_amount: "-53", price_change_percent: "-6.73", previous_observed_at: "2026-08-26T14:41:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "prior", visual_series: [
      { observed_at: "2026-08-26T14:41:00Z", price: "788", observation_run_id: "prior" },
      { observed_at: "2026-08-27T10:00:00Z", price: "999", observation_run_id: "unmatched-return" },
      { observed_at: "2026-08-27T12:30:00Z", price: "735", observation_run_id: "CURRENT" },
    ] };
    render(<TripSummary outbound={outbound} inbound={inbound} outboundBaseline={outbound} inboundBaseline={inbound} />);
    expect(screen.getByText("£1,519")).toBeInTheDocument();
    expect(screen.getByText("↓ 7.2% since yesterday")).toBeInTheDocument();
    expect(screen.getByText("was £1,637")).toHaveClass("summary-previous-price");
    const sparkline = screen.getByRole("img", { name: /£1,637.*£1,519/ });
    expect(sparkline).toHaveClass("trip-total-sparkline");
    expect(sparkline.closest(".trip-total-price-row")).toBeInTheDocument();
    expect(screen.getByText("£1,519").closest(".trip-total-price-row")).toContainElement(sparkline);
    expect(screen.getByLabelText("Show estimated baggage costs").closest(".summary-header-actions")).toBeInTheDocument();
    expect(sparkline).not.toHaveAccessibleName(/999/);
    expect(screen.getByText("↓ 7.7% since yesterday")).toBeInTheDocument();
    expect(screen.getByText("↓ 6.7% since yesterday")).toBeInTheDocument();
  });

  it("collapses unchanged nonstop history without repeating leg price", () => {
    const unchanged = { history_status: "PREVIOUS_FOUND" as const, previous_price: "849", price_change_amount: "0", price_change_percent: "0", previous_observed_at: "2026-08-20T08:00:00Z", elapsed_seconds: 3600, day_difference: 0, previous_observation_run_id: "run-1" };
    const selected = option({ id: "direct", base_price: "849", history: unchanged });
    selected.legs[0].constituent_price = "849";
    selected.legs[0].history = unchanged;
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={selected} inboundBaseline={null} />);
    expect(screen.getAllByText("No change today")).toHaveLength(2);
    expect(screen.getByText("was £849")).toHaveClass("summary-previous-price");
    expect(screen.queryByText(/Previously/)).not.toBeInTheDocument();
    expect(screen.getAllByText("£849")).toHaveLength(2);
    expect(screen.queryByText("No change", { selector: ".summary-leg em" })).not.toBeInTheDocument();
  });

  it("uses natural day-level wording for a one-day increase", () => {
    const increased = { history_status: "PREVIOUS_FOUND" as const, previous_price: "800", price_change_amount: "49", price_change_percent: "6.125", previous_observed_at: "2026-08-20T08:00:00Z", elapsed_seconds: 24 * 3600, day_difference: 1, previous_observation_run_id: "run-1" };
    const selected = option({ id: "increased", base_price: "849", history: increased });
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={selected} inboundBaseline={null} />);
    expect(screen.getAllByText("↑ 6.1% since yesterday")).toHaveLength(2);
    expect(screen.getByText("was £800")).toHaveClass("summary-previous-price");
    expect(screen.queryByText(/\d+[hm] ago/)).not.toBeInTheDocument();
  });

  it("shows meaningful trend separately from an unchanged prior-day movement", () => {
    const comparison = { history_status: "PREVIOUS_FOUND" as const, previous_price: "623", price_change_amount: "0", price_change_percent: "0", previous_observed_at: "2026-08-26T14:41:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "run", trend_status: "RISING" as const, trend_start_price: "500", trend_current_price: "623", trend_change_percent: "24.6", trend_span_days: 3, observed_day_count: 4 };
    const selected = option({ id: "plateau", base_price: "623", history: comparison });
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={selected} inboundBaseline={null} />);
    expect(screen.getAllByText("No change since yesterday").length).toBeGreaterThan(0);
    expect(screen.getByText("↑ 24.6% over 4 observed days")).toBeInTheDocument();
  });

  it("keeps baggage hidden until requested and leaves the base headline unchanged", () => {
    const selected = synthetic();
    selected.ancillary_price_low = "11.99"; selected.ancillary_price_high = null;
    selected.effective_price_low = "497.99"; selected.effective_price_high = null;
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={option({ id: "baseline" })} inboundBaseline={null} outboundComparisonEnabled />);
    expect(screen.queryByText("Estimated bags")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Show estimated baggage costs"));
    expect(screen.getByText("Estimated bags")).toBeInTheDocument();
    expect(screen.getByText("from £11.99")).toBeInTheDocument();
    expect(screen.getAllByText("£486").length).toBeGreaterThan(0);
  });

  it("uses base fares even when baggage pricing is unknown", () => {
    const selected = synthetic();
    selected.ancillary_price_low = null; selected.ancillary_price_high = null;
    selected.effective_price_low = null; selected.effective_price_high = null;
    selected.price_completeness = "UNKNOWN";
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={option({ id: "baseline" })} inboundBaseline={null} outboundComparisonEnabled />);
    expect(screen.getAllByText("£486").length).toBeGreaterThan(0);
    expect(screen.getByText("£255 / 34%")).toBeInTheDocument();
  });

  it("shows compact summary only after the full summary leaves the viewport", () => {
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
    renderOutbound();
    expect(screen.queryByLabelText("Compact selected trip summary")).not.toBeInTheDocument();
    act(() => observerCallback?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    act(() => observerCallback?.([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(screen.getByLabelText("Compact selected trip summary")).toBeInTheDocument();
    expect(screen.getByLabelText("Compact selected trip summary")).toHaveTextContent("18 Dec LGW→CAG");
    expect(screen.getByLabelText("Compact selected trip summary")).toHaveTextContent("£486 · New");
    act(() => observerCallback?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(screen.queryByLabelText("Compact selected trip summary")).not.toBeInTheDocument();
  });

  it("places one primary Prepare booking action in the top summary", () => {
    render(<TripSummary outbound={synthetic()} inbound={null} outboundBaseline={option({ id: "baseline" })} inboundBaseline={null} searchId="search" outboundDate="2026-12-18" />);
    const action = screen.getByRole("button", { name: "Prepare booking" });
    expect(action).toHaveClass("primary-booking-cta");
    expect(screen.getByTestId("summary-top-row")).toContainElement(action);
    expect(screen.getByTestId("summary-top-row")).toContainElement(screen.getByText("Trip total"));
    expect(action).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "Prepare booking" })).toHaveLength(1);
  });

  it("hides saving and extra travel in a nonstop-only compact summary", () => {
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
    const direct = option({ id: "direct" });
    render(<TripSummary outbound={direct} inbound={null} outboundBaseline={direct} inboundBaseline={null} />);
    act(() => observerCallback?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    act(() => observerCallback?.([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver));
    const compact = screen.getByLabelText("Compact selected trip summary");
    expect(compact).toHaveTextContent("18 Dec LGW→CAG");
    expect(compact).not.toHaveTextContent("Save");
    expect(compact).not.toHaveTextContent("Extra travel");
  });
});
