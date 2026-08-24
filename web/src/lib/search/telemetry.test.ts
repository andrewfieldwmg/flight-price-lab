import { expect, it } from "vitest";
import type { SearchSnapshot } from "@/lib/api/types";
import { shouldRefreshProviderUsage } from "./telemetry";

function snapshot(providerCalls: number, localCacheHit: boolean): SearchSnapshot {
  return {
    search_id: "trip", trip_id: "trip", search_key: "key", status: "completed",
    outbound: { baseline: null, nonstop_options: [], cheapest_feasible: null, fastest_feasible: null, pareto_frontier: [], feasible_options: [] },
    return: null, errors: [],
    diagnostics: { trip_id: "trip", search_key: "key", local_cache_hit: localCacheHit, backend_cache_hits: 0, backend_cache_misses: providerCalls, provider_calls_this_invocation: providerCalls, provider_calls_avoided_this_invocation: 0, original_provider_calls: 17, original_search_completed_at: "2026-08-24T12:00:00Z" },
  };
}

it("refreshes monthly usage only for the live invocation in A-A-A", () => {
  expect([
    shouldRefreshProviderUsage(snapshot(17, false), true),
    shouldRefreshProviderUsage(snapshot(0, true), true),
    shouldRefreshProviderUsage(snapshot(0, true), true),
  ]).toEqual([true, false, false]);
});
