import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { option, synthetic } from "@/test/fixtures";
import { reconstructTripTotalHistory, shouldShowCompactSummary, TripSummary } from "./trip-summary";

let resizeCallback: ResizeObserverCallback | null = null;
class MockResizeObserver {
  constructor(callback: ResizeObserverCallback) { resizeCallback = callback; }
  observe = vi.fn(); disconnect = vi.fn(); unobserve = vi.fn();
}

function installStickyGeometry(summaryBottom: { value: number }, headerBottom: number | { value: number } = 54) {
  const header = document.createElement("header");
  header.className = "site-header";
  document.body.prepend(header);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function geometry(this: Element) {
    const measuredHeaderBottom = typeof headerBottom === "number" ? headerBottom : headerBottom.value;
    const bottom = this.classList.contains("site-header") ? measuredHeaderBottom : this.classList.contains("summary-strip") ? summaryBottom.value : 0;
    return { bottom, height: bottom, top: 0, left: 0, right: 390, width: 390, x: 0, y: 0, toJSON: () => ({}) };
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  return header;
}

afterEach(() => { resizeCallback = null; document.querySelector(".site-header")?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const renderOutbound = (comparison = true) => render(<TripSummary outbound={synthetic()} inbound={null} outboundBaseline={option({ id: "baseline" })} inboundBaseline={null} outboundComparisonEnabled={comparison} />);

describe("selected trip summary", () => {
  it("uses the full-summary and sticky-header bottoms as the visibility threshold", () => {
    const summary = document.createElement("section");
    const header = document.createElement("header");
    summary.getBoundingClientRect = () => ({ bottom: 55 } as DOMRect);
    header.getBoundingClientRect = () => ({ bottom: 54 } as DOMRect);
    expect(shouldShowCompactSummary(summary, header, true)).toBe(false);
    summary.getBoundingClientRect = () => ({ bottom: 54 } as DOMRect);
    expect(shouldShowCompactSummary(summary, header, true)).toBe(true);
    expect(shouldShowCompactSummary(summary, header, false)).toBe(false);
  });

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
    expect(screen.queryByText("↑ 6.3% since 3d ago")).not.toBeInTheDocument();
    expect(screen.getByText("£258")).toBeInTheDocument();
    expect(screen.getByText("↑ 3.2% since 3d ago")).toBeInTheDocument();
    expect(screen.getByText("Loading history…", { selector: ".summary-leg em" })).toBeInTheDocument();
    expect(screen.queryByText("↓ 10.0% since 3d ago")).not.toBeInTheDocument();
    expect(screen.getByText("History unavailable", { selector: ".summary-total-block small" })).toHaveAccessibleName("Price history unavailable");
    expect(screen.queryByText("was £918")).not.toBeInTheDocument();
  });

  it("keeps unresolved summary history in a loading state", () => {
    const unresolved = option({ id: "unresolved", history: undefined });
    render(<TripSummary outbound={unresolved} inbound={null} outboundBaseline={unresolved} inboundBaseline={null} />);
    expect(screen.getByText("Updating…", { selector: ".summary-total-block small" })).toBeInTheDocument();
    expect(screen.queryByText("New")).not.toBeInTheDocument();
    expect(screen.queryByText("First seen")).not.toBeInTheDocument();
  });

  it("shows First seen only for an explicitly resolved observation", () => {
    const first = { history_status: "FIRST_SEEN" as const, previous_price: null, price_change_amount: null, price_change_percent: null, previous_observed_at: null, elapsed_seconds: null, previous_observation_run_id: null };
    const selected = option({ id: "first", history: first });
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={selected} inboundBaseline={null} />);
    expect(screen.getAllByText("First seen")).toHaveLength(1);
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
    expect(screen.queryByText(/since y\/day/)).not.toBeInTheDocument();
  });

  it("reconstructs trip history independently of observation-run pairing", () => {
    const outbound = option({ id: "out-current", base_price: "784" });
    const inbound = option({ id: "in-current", direction: "RETURN", base_price: "735" });
    outbound.history = { history_status: "PREVIOUS_FOUND", previous_price: "849", price_change_amount: "-65", price_change_percent: "-7.66", previous_observed_at: "2026-08-26T14:41:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "prior", visual_series: [
      { observed_at: "2026-08-26T14:41:00Z", price: "849", observation_run_id: "prior" },
      { observed_at: "2026-08-27T12:30:00Z", price: "784", observation_run_id: "CURRENT" },
    ], daily_series: [{ date: "2026-08-26", price: "849" }, { date: "2026-08-27", price: "784" }] };
    inbound.history = { history_status: "PREVIOUS_FOUND", previous_price: "788", price_change_amount: "-53", price_change_percent: "-6.73", previous_observed_at: "2026-08-26T14:41:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "prior", visual_series: [
      { observed_at: "2026-08-26T14:41:00Z", price: "788", observation_run_id: "prior" },
      { observed_at: "2026-08-27T10:00:00Z", price: "999", observation_run_id: "unmatched-return" },
      { observed_at: "2026-08-27T12:30:00Z", price: "735", observation_run_id: "CURRENT" },
    ], daily_series: [{ date: "2026-08-26", price: "788" }, { date: "2026-08-27", price: "735" }] };
    render(<TripSummary outbound={outbound} inbound={inbound} outboundBaseline={outbound} inboundBaseline={inbound} />);
    expect(screen.getByText("£1,519")).toBeInTheDocument();
    expect(screen.getByText("↓ 7.2% since y/day")).toBeInTheDocument();
    expect(screen.getByText("was £1,637")).toHaveClass("summary-previous-price");
    const sparkline = screen.getByRole("img", { name: /£1,637.*£1,519/ });
    expect(sparkline).toHaveClass("trip-total-sparkline");
    expect(sparkline.closest(".trip-total-price-row")).toBeInTheDocument();
    expect(screen.getByText("£1,519").closest(".trip-total-price-row")).toContainElement(sparkline);
    expect(screen.getByLabelText("Show estimated baggage costs").closest(".summary-header-actions")).toBeInTheDocument();
    expect(sparkline).not.toHaveAccessibleName(/999/);
    expect(screen.queryByText("↓ 7.7% since y/day")).not.toBeInTheDocument();
    expect(screen.queryByText("↓ 6.7% since y/day")).not.toBeInTheDocument();
  });

  it("reconstructs exact and three-day carry-forward trip totals without carrying backward", () => {
    const outbound = [{ date: "2026-08-24", price: "450" }, { date: "2026-08-25", price: "430" }, { date: "2026-08-27", price: "503" }];
    const inbound = [{ date: "2026-08-24", price: "600" }, { date: "2026-08-26", price: "549" }, { date: "2026-08-27", price: "549" }];
    expect(reconstructTripTotalHistory(outbound, inbound)).toEqual([
      { observed_at: "2026-08-24T12:00:00Z", price: "1050", history_quality: "EXACT" },
      { observed_at: "2026-08-25T12:00:00Z", price: "1030", history_quality: "PARTIAL_CARRY_FORWARD" },
      { observed_at: "2026-08-26T12:00:00Z", price: "979", history_quality: "PARTIAL_CARRY_FORWARD" },
      { observed_at: "2026-08-27T12:00:00Z", price: "1052", history_quality: "EXACT" },
    ]);
    expect(reconstructTripTotalHistory([{ date: "2026-08-20", price: "430" }], [{ date: "2026-08-24", price: "549" }])).toEqual([]);
    expect(reconstructTripTotalHistory([{ date: "2026-08-24", price: "430" }], [{ date: "2026-08-23", price: "600" }, { date: "2026-08-25", price: "549" }])).toEqual([
      { observed_at: "2026-08-24T12:00:00Z", price: "1030", history_quality: "PARTIAL_CARRY_FORWARD" },
      { observed_at: "2026-08-25T12:00:00Z", price: "979", history_quality: "PARTIAL_CARRY_FORWARD" },
    ]);
    expect(reconstructTripTotalHistory([{ date: "2026-08-24", price: "450" }], inbound)[0].price).not.toBe(reconstructTripTotalHistory([{ date: "2026-08-24", price: "400" }], inbound)[0].price);
  });

  it("calculates the screenshot-case headline change from reconstructed totals", () => {
    const outbound = option({ id: "out-503", base_price: "503", history: { history_status: "PREVIOUS_FOUND", previous_price: "430", price_change_amount: "73", price_change_percent: "16.98", previous_observed_at: "2026-08-25T12:00:00Z", elapsed_seconds: 172800, day_difference: 2, previous_observation_run_id: "out", daily_series: [{ date: "2026-08-24", price: "450" }, { date: "2026-08-25", price: "430" }, { date: "2026-08-27", price: "503" }] } });
    const inbound = option({ id: "in-549", direction: "RETURN", base_price: "549", history: { history_status: "PREVIOUS_FOUND", previous_price: "549", price_change_amount: "0", price_change_percent: "0", previous_observed_at: "2026-08-26T12:00:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "in", daily_series: [{ date: "2026-08-24", price: "600" }, { date: "2026-08-26", price: "549" }, { date: "2026-08-27", price: "549" }] } });
    render(<TripSummary outbound={outbound} inbound={inbound} outboundBaseline={outbound} inboundBaseline={inbound} />);
    expect(screen.getByText("£1,052")).toBeInTheDocument();
    expect(screen.getByText("↑ 7.5% since y/day")).toBeInTheDocument();
    expect(screen.getByText("was £979")).toBeInTheDocument();
    expect(screen.getByText("↑ 7.5% since y/day")).toHaveAttribute("data-history-quality", "PARTIAL_CARRY_FORWARD");
  });

  it("collapses unchanged nonstop history without repeating leg price", () => {
    const unchanged = { history_status: "PREVIOUS_FOUND" as const, previous_price: "849", price_change_amount: "0", price_change_percent: "0", previous_observed_at: "2026-08-20T08:00:00Z", elapsed_seconds: 3600, day_difference: 0, previous_observation_run_id: "run-1" };
    const selected = option({ id: "direct", base_price: "849", history: unchanged });
    selected.legs[0].constituent_price = "849";
    selected.legs[0].history = unchanged;
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={selected} inboundBaseline={null} />);
    expect(screen.getAllByText("No change today")).toHaveLength(1);
    expect(screen.getByText("was £849")).toHaveClass("summary-previous-price");
    expect(screen.queryByText(/Previously/)).not.toBeInTheDocument();
    expect(screen.getAllByText("£849")).toHaveLength(2);
    expect(screen.queryByText("No change", { selector: ".summary-leg em" })).not.toBeInTheDocument();
  });

  it("uses natural day-level wording for a one-day increase", () => {
    const increased = { history_status: "PREVIOUS_FOUND" as const, previous_price: "800", price_change_amount: "49", price_change_percent: "6.125", previous_observed_at: "2026-08-20T08:00:00Z", elapsed_seconds: 24 * 3600, day_difference: 1, previous_observation_run_id: "run-1" };
    const selected = option({ id: "increased", base_price: "849", history: increased });
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={selected} inboundBaseline={null} />);
    expect(screen.getAllByText("↑ 6.1% since y/day")).toHaveLength(1);
    expect(screen.getByText("was £800")).toHaveClass("summary-previous-price");
    expect(screen.queryByText(/\d+[hm] ago/)).not.toBeInTheDocument();
  });

  it("shows meaningful trend separately from an unchanged prior-day movement", () => {
    const comparison = { history_status: "PREVIOUS_FOUND" as const, previous_price: "623", price_change_amount: "0", price_change_percent: "0", previous_observed_at: "2026-08-26T14:41:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "run", trend_status: "RISING" as const, trend_start_price: "500", trend_current_price: "623", trend_change_percent: "24.6", trend_span_days: 3, observed_day_count: 4 };
    const selected = option({ id: "plateau", base_price: "623", history: comparison });
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={selected} inboundBaseline={null} />);
    expect(screen.getAllByText("No change since y/day")).toHaveLength(1);
    expect(screen.queryByText("↑ 24.6% over 4 observed days")).not.toBeInTheDocument();
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
    const summaryBottom = { value: 180 };
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    installStickyGeometry(summaryBottom);
    renderOutbound();
    expect(screen.queryByLabelText("Compact selected trip summary")).not.toBeInTheDocument();
    summaryBottom.value = 40;
    act(() => window.dispatchEvent(new Event("scroll")));
    expect(screen.getByLabelText("Compact selected trip summary")).toBeInTheDocument();
    expect(screen.getByLabelText("Compact selected trip summary")).toHaveTextContent("18 Dec LGW→CAG");
    expect(screen.getByLabelText("Compact selected trip summary")).toHaveTextContent("£486");
    expect(screen.getByLabelText("Compact selected trip summary")).not.toHaveTextContent("Updating");
    expect(screen.getByLabelText("Insufficient trip price history")).toHaveTextContent("—");
    summaryBottom.value = -2000;
    act(() => window.dispatchEvent(new Event("scroll")));
    expect(screen.getByLabelText("Compact selected trip summary")).toBeInTheDocument();
    summaryBottom.value = 180;
    act(() => window.dispatchEvent(new Event("scroll")));
    expect(screen.queryByLabelText("Compact selected trip summary")).not.toBeInTheDocument();
  });

  it("shows the trip-total sparkline instead of a numerical change in the sticky summary", () => {
    installStickyGeometry({ value: 40 });
    const selected = option({ id: "sticky-history", base_price: "784", history: { history_status: "PREVIOUS_FOUND", previous_price: "849", price_change_amount: "-65", price_change_percent: "-7.66", previous_observed_at: "2026-08-26T12:00:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "prior", daily_series: [{ date: "2026-08-26", price: "849" }, { date: "2026-08-27", price: "784" }] } });
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={selected} inboundBaseline={null} />);
    const compact = screen.getByLabelText("Compact selected trip summary");
    expect(compact).toHaveTextContent("£784");
    expect(compact).not.toHaveTextContent("7.7%");
    expect(compact.querySelector(".compact-trip-sparkline")).toBeInTheDocument();
    expect(compact.querySelector(".compact-trip-history-empty")).not.toBeInTheDocument();
  });

  it.each([320, 375, 390, 768, 1440])("keeps mandatory sticky content at a %dpx viewport", (width) => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    vi.stubGlobal("matchMedia", vi.fn((query: string) => ({ matches: query.includes("680px") && width <= 680, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    installStickyGeometry({ value: 40 });
    const selected = synthetic();
    selected.history = { history_status: "PREVIOUS_FOUND", previous_price: "500", price_change_amount: "-14", price_change_percent: "-2.8", previous_observed_at: "2026-08-26T12:00:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "prior", daily_series: [{ date: "2026-08-26", price: "500" }, { date: "2026-08-27", price: "486" }] };
    render(<TripSummary outbound={selected} inbound={null} outboundBaseline={option({ id: "baseline" })} inboundBaseline={null} outboundComparisonEnabled />);
    const compact = screen.getByLabelText("Compact selected trip summary");
    expect(compact).toHaveTextContent("£486");
    expect(compact).toHaveTextContent("Save");
    expect(compact).toHaveTextContent("£255 / 34%");
    expect(compact).toHaveTextContent("Extra travel");
    expect(compact).toHaveTextContent("+4h 45m");
    expect(compact.querySelector(".compact-trip-sparkline")).toBeInTheDocument();
  });

  it("uses geometry and reacts to summary and header resizing", () => {
    const summaryBottom = { value: 80 };
    const headerBottom = { value: 54 };
    installStickyGeometry(summaryBottom, headerBottom);
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
    renderOutbound();
    expect(screen.queryByLabelText("Compact selected trip summary")).not.toBeInTheDocument();
    headerBottom.value = 90;
    act(() => resizeCallback?.([], {} as ResizeObserver));
    expect(screen.getByLabelText("Compact selected trip summary")).toBeInTheDocument();
    expect(document.documentElement.style.getPropertyValue("--app-header-bottom")).toBe("90px");
    summaryBottom.value = 120;
    act(() => resizeCallback?.([], {} as ResizeObserver));
    expect(screen.queryByLabelText("Compact selected trip summary")).not.toBeInTheDocument();
  });

  it("remeasures and preserves compact visibility when the selected trip changes", () => {
    installStickyGeometry({ value: 40 });
    const first = option({ id: "first-selection", base_price: "700" });
    const second = option({ id: "second-selection", base_price: "650" });
    const { rerender } = render(<TripSummary outbound={first} inbound={null} outboundBaseline={first} inboundBaseline={null} />);
    expect(screen.getByLabelText("Compact selected trip summary")).toHaveTextContent("£700");
    rerender(<TripSummary outbound={second} inbound={null} outboundBaseline={second} inboundBaseline={null} />);
    expect(screen.getByLabelText("Compact selected trip summary")).toHaveTextContent("£650");
  });

  it("keeps a provisional trip total and unresolved history distinct from first seen", () => {
    render(<TripSummary outbound={synthetic()} inbound={null} outboundBaseline={option({ id: "baseline" })} inboundBaseline={null} complete={false} />);
    expect(screen.getByText("Updating…")).toBeInTheDocument();
    expect(screen.queryByText("New")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Updating trip total and price history")).toBeInTheDocument();
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
    installStickyGeometry({ value: 40 });
    const direct = option({ id: "direct" });
    render(<TripSummary outbound={direct} inbound={null} outboundBaseline={direct} inboundBaseline={null} />);
    const compact = screen.getByLabelText("Compact selected trip summary");
    expect(compact).toHaveTextContent("18 Dec LGW→CAG");
    expect(compact).not.toHaveTextContent("Save");
    expect(compact).not.toHaveTextContent("Extra travel");
  });

  it("uses the compact decision hierarchy for the mobile selected-trip summary", () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    const outbound = synthetic();
    const inbound = synthetic("RETURN");
    const history = { history_status: "PREVIOUS_FOUND" as const, previous_price: "500", price_change_amount: "-14", price_change_percent: "-2.8", previous_observed_at: "2026-08-26T10:00:00Z", elapsed_seconds: 86400, day_difference: 1, previous_observation_run_id: "prior", trend_status: "FALLING" as const, trend_start_price: "530", trend_current_price: "486", trend_change_percent: "-8.3", trend_span_days: 3, observed_day_count: 2, daily_series: [{ date: "2026-08-26", price: "500" }, { date: "2026-08-27", price: "486" }], visual_series: [
      { observed_at: "2026-08-26T10:00:00Z", price: "500", observation_run_id: "prior" },
      { observed_at: "2026-08-27T10:00:00Z", price: "486", observation_run_id: "current" },
    ] };
    outbound.history = history;
    inbound.history = history;
    const { container } = render(<TripSummary outbound={outbound} inbound={inbound} outboundBaseline={option({ id: "out-direct" })} inboundBaseline={option({ id: "in-direct", direction: "RETURN" })} outboundComparisonEnabled inboundComparisonEnabled outboundDate="2026-12-18" returnDate="2026-12-28" searchId="search" />);
    const summary = screen.getByLabelText("Selected trip summary");

    expect(summary).toHaveClass("mobile-selected-summary");
    expect(container.querySelector(".mobile-summary-total > div > strong")).toHaveTextContent("£972");
    expect(container.querySelector(".mobile-summary-total .trip-total-sparkline")).toBeInTheDocument();
    expect(summary).toHaveTextContent("↓ 2.8% · was £1,000");
    expect(summary).toHaveTextContent("£510 / 34%");
    expect(summary).toHaveTextContent("+9h 30m");
    expect(summary).toHaveTextContent("Outbound · 18 Dec");
    expect(summary).toHaveTextContent("LGW 08:00 → CAG 15:25");
    expect(summary).toHaveTextContent("1 stop · MXP 3h 50m");
    expect(summary).toHaveTextContent("Return · 28 Dec");
    expect(summary).not.toHaveTextContent("U2 8309");
    expect(summary).not.toHaveTextContent("over 2 observed days");
    expect(screen.queryByLabelText("Show estimated baggage costs")).not.toBeInTheDocument();
    expect(summary).not.toHaveTextContent("Separate tickets");
    expect(summary).not.toHaveTextContent("Prices exclude baggage");
    const booking = screen.getByRole("button", { name: "Prepare booking" });
    const details = screen.getByRole("button", { name: /Trip details/ });
    expect(booking.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(details);
    expect(screen.getByLabelText("Show estimated baggage costs")).toBeInTheDocument();
    expect(summary).toHaveTextContent("easyJet U2 8309");
    expect(summary).toHaveTextContent("Separate tickets · connection not protected");
    expect(outbound.id).toBe("synthetic-OUTBOUND");
  });
});
