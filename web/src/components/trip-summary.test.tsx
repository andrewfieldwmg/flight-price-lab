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
    act(() => observerCallback?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(screen.queryByLabelText("Compact selected trip summary")).not.toBeInTheDocument();
  });

  it("places one primary Prepare booking action in the top summary", () => {
    render(<TripSummary outbound={synthetic()} inbound={null} outboundBaseline={option({ id: "baseline" })} inboundBaseline={null} searchId="search" outboundDate="2026-12-18" />);
    const action = screen.getByRole("button", { name: "Prepare booking" });
    expect(action).toHaveClass("primary-booking-cta");
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
