import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { SearchSnapshot } from "@/lib/api/types";
import { option } from "@/test/fixtures";
import { SearchStatusPanel } from "./search-status-panel";

const originalDiagnostics = process.env.NEXT_PUBLIC_DIAGNOSTICS;
afterEach(() => { process.env.NEXT_PUBLIC_DIAGNOSTICS = originalDiagnostics; });
const progress = { OUTBOUND: { started: 8, completed: 8 }, RETURN: { started: 8, completed: 8 } };

function snapshot(updates: Partial<SearchSnapshot["diagnostics"]> = {}, status: SearchSnapshot["status"] = "completed", errorCount = 0): SearchSnapshot {
  const outbound = option({ id: "outbound", base_price: "849" });
  const inbound = option({ id: "return", direction: "RETURN", base_price: "788" });
  return {
    search_id: "trip", trip_id: "trip", search_key: "eb72a37d1a7ffff", status,
    outbound: { baseline: outbound, nonstop_options: [outbound], cheapest_feasible: null, fastest_feasible: null, pareto_frontier: [], feasible_options: [] },
    return: { baseline: inbound, nonstop_options: [inbound], cheapest_feasible: null, fastest_feasible: null, pareto_frontier: [], feasible_options: [] },
    errors: Array.from({ length: errorCount }, (_, index) => ({ code: "provider_error", message: `failure ${index}`, direction: "OUTBOUND", hub: null })),
    diagnostics: { trip_id: "trip", search_key: "eb72a37d1a7ffff", local_cache_hit: false, backend_cache_hits: 0, backend_cache_misses: 0, provider_calls_this_invocation: 17, provider_calls_avoided_this_invocation: 0, original_provider_calls: 17, original_search_completed_at: "2026-08-24T19:17:00Z", ...updates },
  };
}

describe("search status panel", () => {
  it("summarizes a local-cache search without implementation counters", () => {
    render(<SearchStatusPanel snapshot={snapshot({ local_cache_hit: true, provider_calls_this_invocation: 0, provider_calls_avoided_this_invocation: 17 })} cachedMinutes={25} directionProgress={progress} />);
    expect(screen.getByText("Search complete · Cached · 25m ago · refreshes after 04:00 · 0 provider calls · 17 calls avoided")).toBeInTheDocument();
    expect(screen.queryByText(/local cache hits|backend cache hits|cache misses/i)).not.toBeInTheDocument();
  });

  it("summarizes a backend-cache search", () => {
    render(<SearchStatusPanel snapshot={snapshot({ backend_cache_hits: 1, provider_calls_this_invocation: 0, provider_calls_avoided_this_invocation: 1 })} cachedMinutes={null} directionProgress={progress} />);
    expect(screen.getByText("Search complete · Cached · refreshes after 04:00 · 0 provider calls · 1 call avoided")).toBeInTheDocument();
  });

  it("summarizes live and partial searches", () => {
    const { rerender } = render(<SearchStatusPanel snapshot={snapshot()} cachedMinutes={null} directionProgress={progress} />);
    expect(screen.getByText("Search complete · Fresh today · 17 provider calls · 0 failures")).toBeInTheDocument();
    rerender(<SearchStatusPanel snapshot={snapshot({ provider_calls_this_invocation: 15 }, "partial_failure", 2)} cachedMinutes={null} directionProgress={progress} />);
    expect(screen.getByText("Search complete · Fresh today · 15 provider calls · 2 failed")).toBeInTheDocument();
  });

  it("expands and collapses details with consolidated fields", () => {
    render(<SearchStatusPanel snapshot={snapshot({ local_cache_hit: true, provider_calls_this_invocation: 0, provider_calls_avoided_this_invocation: 17 })} cachedMinutes={25} directionProgress={progress} />);
    expect(screen.queryByText("Direct outbound")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Details/ }));
    expect(screen.getByText("Direct outbound")).toBeInTheDocument();
    expect(screen.getByText("Local cache")).toBeInTheDocument();
    expect(screen.getByText("Original live calls")).toBeInTheDocument();
    expect(screen.getAllByText("8/8")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: /Details/ }));
    expect(screen.queryByText("Direct outbound")).not.toBeInTheDocument();
  });

  it("hides the technical search key outside diagnostics mode", () => {
    process.env.NEXT_PUBLIC_DIAGNOSTICS = "false";
    render(<SearchStatusPanel snapshot={snapshot()} cachedMinutes={null} directionProgress={progress} />);
    fireEvent.click(screen.getByRole("button", { name: /Details/ }));
    expect(screen.queryByText("Search key")).not.toBeInTheDocument();
  });
});
