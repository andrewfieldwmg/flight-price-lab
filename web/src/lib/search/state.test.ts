import { describe, expect, it } from "vitest";
import type { SearchSnapshot } from "@/lib/api/types";
import { option, synthetic } from "@/test/fixtures";
import { initialSearchState, searchReducer } from "./state";

const baseline = option({ id: "direct" });
const first: SearchSnapshot = {
  search_id: "search-1",
  trip_id: "search-1",
  search_key: "key-1",
  status: "running",
  outbound: {
    baseline,
    nonstop_options: [baseline],
    cheapest_feasible: null,
    fastest_feasible: null,
    pareto_frontier: [],
    feasible_options: [],
  },
  return: null,
  errors: [],
  diagnostics: { trip_id: "search-1", search_key: "key-1", local_cache_hit: false, backend_cache_hits: 0, backend_cache_misses: 0, provider_calls_this_invocation: 0, provider_calls_avoided_this_invocation: 0, original_provider_calls: null, original_search_completed_at: null },
};

it("accepts partial baseline snapshots before search completion", () => {
  const state = searchReducer(initialSearchState, { type: "snapshot", snapshot: first });
  expect(state.snapshot?.outbound.baseline?.id).toBe("direct");
  expect(state.selectedOutboundId).toBe("direct");
});

it("applies SSE-driven result re-ranking snapshots", () => {
  const started = searchReducer(initialSearchState, { type: "snapshot", snapshot: first });
  const cheaper = synthetic();
  const update: SearchSnapshot = {
    ...first,
    outbound: {
      baseline,
      nonstop_options: [baseline],
      cheapest_feasible: cheaper,
      fastest_feasible: cheaper,
      pareto_frontier: [cheaper],
      feasible_options: [cheaper],
    },
  };
  const state = searchReducer(started, { type: "snapshot", snapshot: update });
  expect(state.snapshot?.outbound.cheapest_feasible?.id).toBe(cheaper.id);
  expect(state.selectedOutboundId).toBe(cheaper.id);
});

it("restores completed cached progress and its consistent selection", () => {
  const cheaper = synthetic();
  const snapshot: SearchSnapshot = { ...first, status: "completed", outbound: { ...first.outbound, cheapest_feasible: cheaper, fastest_feasible: cheaper, pareto_frontier: [cheaper], feasible_options: [cheaper] } };
  const state = searchReducer(initialSearchState, { type: "restore_cached", snapshot, selectedOutboundId: cheaper.id, selectedReturnId: null, directionProgress: { OUTBOUND: { started: 8, completed: 8 }, RETURN: { started: 0, completed: 0 } } });
  expect(state.selectedOutboundId).toBe(cheaper.id);
  expect(state.directionProgress.OUTBOUND).toEqual({ started: 8, completed: 8 });
});

describe("hub progress", () => {
  it("tracks partial hub completion", () => {
    let state = searchReducer(initialSearchState, { type: "event", event: "hub_started", data: { direction: "OUTBOUND" } });
    state = searchReducer(state, { type: "event", event: "hub_completed", data: { direction: "OUTBOUND" } });
    expect(state.hubsStarted).toBe(1);
    expect(state.hubsCompleted).toBe(1);
    expect(state.directionProgress.OUTBOUND).toEqual({ started: 1, completed: 1 });
  });
});
