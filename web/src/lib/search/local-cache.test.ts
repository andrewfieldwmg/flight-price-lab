import { beforeEach, describe, expect, it } from "vitest";
import { option } from "@/test/fixtures";
import type { SearchSnapshot, TripSearchRequest } from "@/lib/api/types";
import { currentCacheEpoch, currentInvocationFromLocalCache, loadLocalSearch, nextLondonCacheCutoff, saveLocalSearch } from "./local-cache";

const request: TripSearchRequest = {
  origins: ["LGW"], destinations: ["CAG"], outbound_date: "2026-12-18", return_date: null,
  adults: 2, children: 2, baggage: { cabin_bags: 0, checked_bags: 0 },
  outbound_time_window: { earliest_departure_time: null, latest_arrival_time: null, max_connection_minutes: 360 },
  return_time_window: { earliest_departure_time: null, latest_arrival_time: null, max_connection_minutes: 360 },
  max_extra_journey_minutes: null, self_transfer_policy: "NONE", connection_profile: "CONSERVATIVE", currency: "GBP", refresh_prices: false,
};
const direct = option({ id: "FR-2687" });
const results: SearchSnapshot = {
  search_id: "trip-1", trip_id: "trip-1", search_key: "key-1", status: "completed",
  outbound: { baseline: direct, nonstop_options: [direct], cheapest_feasible: null, fastest_feasible: null, pareto_frontier: [], feasible_options: [] },
  return: null, errors: [], diagnostics: { trip_id: "trip-1", search_key: "key-1", local_cache_hit: false, backend_cache_hits: 0, backend_cache_misses: 1, provider_calls_this_invocation: 17, provider_calls_avoided_this_invocation: 0, original_provider_calls: 17, original_search_completed_at: "2026-08-24T12:00:00Z" },
};

describe("completed search localStorage cache", () => {
  beforeEach(() => window.localStorage.clear());

  it("expires at the next 04:00 Europe/London cutoff", () => {
    const saved = new Date("2026-08-26T14:00:00Z");
    saveLocalSearch("key-1", request, results, saved);
    expect(loadLocalSearch("key-1", new Date("2026-08-27T02:59:59Z"))).not.toBeNull();
    expect(loadLocalSearch("key-1", new Date("2026-08-27T03:00:00Z"))).toBeNull();
  });

  it("rejects a legacy rolling-TTL snapshot at 09:31 after the epoch changed", () => {
    const saved = new Date("2026-08-26T14:30:00Z");
    const value = saveLocalSearch("key-1", request, results, saved);
    window.localStorage.setItem("flight-price-lab:key-1", JSON.stringify({ ...value, expires_at: "2026-08-27T14:30:00Z" }));
    expect(loadLocalSearch("key-1", new Date("2026-08-27T08:31:00Z"))).toBeNull();
    expect(window.localStorage.getItem("flight-price-lab:key-1")).toBeNull();
  });

  it("keeps a 04:01 snapshot throughout its London cache day", () => {
    saveLocalSearch("key-1", request, results, new Date("2026-08-27T03:01:00Z"));
    expect(loadLocalSearch("key-1", new Date("2026-08-28T02:59:59Z"))).not.toBeNull();
  });

  it("resolves GMT and BST cutoffs without a fixed offset", () => {
    expect(currentCacheEpoch(new Date("2026-08-27T02:59:59Z")).toISOString()).toBe("2026-08-26T03:00:00.000Z");
    expect(currentCacheEpoch(new Date("2026-08-27T03:00:00Z")).toISOString()).toBe("2026-08-27T03:00:00.000Z");
    expect(nextLondonCacheCutoff(new Date("2026-08-27T01:00:00Z")).toISOString()).toBe("2026-08-27T03:00:00.000Z");
    expect(currentCacheEpoch(new Date("2026-01-15T09:31:00Z")).toISOString()).toBe("2026-01-15T04:00:00.000Z");
    expect(nextLondonCacheCutoff(new Date("2026-10-25T05:00:00Z")).toISOString()).toBe("2026-10-26T04:00:00.000Z");
  });

  it("restores the completed table snapshot and selection source", () => {
    saveLocalSearch("key-1", request, results, new Date(), {
      selected_outbound_id: "FR-2687",
      selected_return_id: null,
      direction_progress: { OUTBOUND: { started: 8, completed: 8 }, RETURN: { started: 0, completed: 0 } },
    });
    const cached = loadLocalSearch("key-1");
    expect(cached?.trip_id).toBe("trip-1");
    expect(cached?.results.outbound.nonstop_options[0].id).toBe("FR-2687");
    expect(cached?.request).toEqual(request);
    expect(cached?.ui_state.direction_progress.OUTBOUND).toEqual({ started: 8, completed: 8 });
  });

  it("does not replay original execution metrics on repeated local hits", () => {
    saveLocalSearch("key-1", request, results);
    const second = currentInvocationFromLocalCache(loadLocalSearch("key-1")!);
    const third = currentInvocationFromLocalCache(loadLocalSearch("key-1")!);

    for (const invocation of [second, third]) {
      expect(invocation.diagnostics.local_cache_hit).toBe(true);
      expect(invocation.diagnostics.backend_cache_hits).toBe(0);
      expect(invocation.diagnostics.backend_cache_misses).toBe(0);
      expect(invocation.diagnostics.provider_calls_this_invocation).toBe(0);
      expect(invocation.diagnostics.original_provider_calls).toBe(17);
    }
  });
});
