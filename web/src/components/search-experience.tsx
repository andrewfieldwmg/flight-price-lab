"use client";

import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { getCalendarPrices, getSearch, getSearchKey, streamSearch } from "@/lib/api/client";
import type { SearchSnapshot, TripOption, TripSearchRequest } from "@/lib/api/types";
import { uniqueOptions } from "@/lib/search/calculations";
import { initialSearchState, searchReducer } from "@/lib/search/state";
import { shouldRefreshProviderUsage } from "@/lib/search/telemetry";
import { cachedAgeMinutes, currentInvocationFromLocalCache, loadLocalSearch, logFrontendCacheEvent, saveLocalSearch, shouldUseBrowserSearchCache } from "@/lib/search/local-cache";
import { DirectionResults } from "./direction-results";
import { SearchForm } from "./search-form";
import { SearchStatusPanel } from "./search-status-panel";
import { TripSummary } from "./trip-summary";
import { calendarPrefetchKey, prefetchSearchCalendar } from "@/lib/search/calendar-prefetch";

const emptyDirectionResults = {
  baseline: null,
  nonstop_options: [],
  cheapest_feasible: null,
  fastest_feasible: null,
  pareto_frontier: [],
  feasible_options: [],
};

function selected(options: TripOption[], id: string | null): TripOption | null {
  return options.find((option) => option.id === id) ?? null;
}

const terminal = new Set(["completed", "partial_failure", "failed"]);

export function SearchExperience() {
  const [state, dispatch] = useReducer(searchReducer, initialSearchState);
  const [startError, setStartError] = useState<string | null>(null);
  const [cachedMinutes, setCachedMinutes] = useState<number | null>(null);
  const [excludeBaggage, setExcludeBaggage] = useState(true);
  const savedTrips = useRef(new Set<string>());
  const searchClickedAt = useRef<number | null>(null);
  const firstRenderedResultRecorded = useRef(false);
  const calendarPrefetchStarted = useRef(new Set<string>());

  async function submit(request: TripSearchRequest) {
    searchClickedAt.current = performance.now();
    firstRenderedResultRecorded.current = false;
    setStartError(null);
    dispatch({ type: "started", request });
    let activeSearchId: string | null = null;
    try {
      const key = await getSearchKey(request);
      logFrontendCacheEvent("SEARCH_RECEIVED", { search_key: key, search_key_short: key.slice(0, 12), cache_bypass: request.refresh_prices });
      if (shouldUseBrowserSearchCache(request)) {
        const cached = loadLocalSearch(key);
        if (cached) {
          try {
            const rehydrated = await getSearch(cached.trip_id);
            const restored = currentInvocationFromLocalCache({ ...cached, results: rehydrated });
            dispatch({ type: "restore_cached", snapshot: restored, selectedOutboundId: cached.ui_state.selected_outbound_id, selectedReturnId: cached.ui_state.selected_return_id, directionProgress: cached.ui_state.direction_progress });
            logFrontendCacheEvent("RESULT_RESTORED_FROM_CACHE", { trip_id: cached.trip_id, search_key: key, search_key_short: key.slice(0, 12) });
            setCachedMinutes(cachedAgeMinutes(cached));
            return;
          } catch {
            logFrontendCacheEvent("FRONTEND_LOCAL_CACHE_MISS", { trip_id: cached.trip_id, search_key: key, search_key_short: key.slice(0, 12), reason: "server_snapshot_unavailable" });
          }
        }
      }
      setCachedMinutes(null);
      await streamSearch(request, (event) => {
        const eventSearchId = event.data.search_id;
        if (typeof eventSearchId === "string") {
          activeSearchId = eventSearchId;
        }
        const snapshot = event.data.snapshot;
        if (snapshot && typeof snapshot === "object") {
          dispatch({ type: "snapshot", snapshot: snapshot as SearchSnapshot });
        }
        dispatch({ type: "event", event: event.type, data: event.data });
      }, (phase, elapsedMs) => {
        console.info("SEARCH_CLIENT_TIMING", { phase, elapsed_ms: Math.round(elapsedMs) });
      });
    } catch (error) {
      if (activeSearchId) {
        try {
          dispatch({ type: "snapshot", snapshot: await getSearch(activeSearchId) });
          dispatch({ type: "disconnect" });
          return;
        } catch {
          dispatch({ type: "disconnect" });
        }
      }
      setStartError(error instanceof Error ? error.message : "Search could not start");
    }
  }

  const outboundOptions = uniqueOptions(state.snapshot?.outbound ?? null);
  const returnOptions = uniqueOptions(state.snapshot?.return ?? null);
  const outbound = selected(outboundOptions, state.selectedOutboundId);
  const inbound = selected(returnOptions, state.selectedReturnId);
  const isTerminal = state.snapshot ? terminal.has(state.snapshot.status) : false;
  const refreshProviderUsage = shouldRefreshProviderUsage(state.snapshot, isTerminal);
  const isSearching = Boolean(state.request) && !isTerminal && !startError;

  useEffect(() => {
    if (
      firstRenderedResultRecorded.current
      || searchClickedAt.current === null
      || outboundOptions.length === 0
    ) return;
    firstRenderedResultRecorded.current = true;
    console.info("SEARCH_CLIENT_TIMING", {
      phase: "first_rendered_result",
      elapsed_ms: Math.round(performance.now() - searchClickedAt.current),
    });
  }, [outboundOptions.length]);
  const outboundTransfers = state.request?.self_transfer_policy === "OUTBOUND_ONLY" || state.request?.self_transfer_policy === "BOTH";
  const returnTransfers = state.request?.self_transfer_policy === "RETURN_ONLY" || state.request?.self_transfer_policy === "BOTH";
  const missingCachedRoutes = state.request?.cache_only && (outboundTransfers || returnTransfers)
    ? state.snapshot?.diagnostics.backend_cache_misses ?? 0
    : 0;
  const noFeasibleConnection = state.snapshot?.errors.some((error) => error.code === "no_feasible_self_transfer") ?? false;
  const progressText = useMemo(() => {
    if (!state.snapshot?.outbound.baseline) return state.request?.cache_only ? "Loading the latest stored results…" : "Searching direct flights…";
    if (!isTerminal && state.routeProgress.total > 0) {
      return `Searching best connections… ${state.routeProgress.completed} / ${state.routeProgress.total} routes checked · ${state.routeProgress.optionsFound} options found`;
    }
    if (!isTerminal) return "Searching best connections…";
    return state.snapshot.status === "partial_failure"
      ? "Search complete with some unavailable routes"
      : "Search complete";
  }, [state.snapshot, state.routeProgress, state.request?.cache_only, isTerminal]);

  useEffect(() => {
    if (refreshProviderUsage) window.dispatchEvent(new Event("provider-usage-refresh"));
  }, [refreshProviderUsage]);

  useEffect(() => {
    if (!state.request || state.request.cache_only || !state.snapshot || !isTerminal || searchClickedAt.current === null) return;
    const key = calendarPrefetchKey(state.request);
    if (calendarPrefetchStarted.current.has(key) || localStorage.getItem(key)) return;
    calendarPrefetchStarted.current.add(key);
    localStorage.setItem(key, "pending");
    let cancelled = false;
    const run = async () => {
      const result = await prefetchSearchCalendar(state.request!, getCalendarPrices);
      localStorage.setItem(key, "complete");
      console.info("CALENDAR_BACKGROUND_PREFETCH", result);
      if (!cancelled) dispatch({ type: "snapshot", snapshot: { ...state.snapshot!, diagnostics: {
        ...state.snapshot!.diagnostics,
        calendar_background_prefetch_calls: result.calls,
        calendar_background_prefetch_avoided: result.avoided,
        calendar_background_prefetch_failures: result.failures,
      } } });
    };
    const idleWindow = window as Window & { requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number; cancelIdleCallback?: (id: number) => void };
    const idleId = idleWindow.requestIdleCallback?.(() => void run(), { timeout: 2_000 });
    const timerId = idleId === undefined ? window.setTimeout(() => void run(), 0) : null;
    return () => {
      cancelled = true;
      if (idleId !== undefined) idleWindow.cancelIdleCallback?.(idleId);
      if (timerId !== null) window.clearTimeout(timerId);
    };
  }, [isTerminal, state.request, state.snapshot]);

  useEffect(() => {
    const snapshot = state.snapshot;
    if (!snapshot || !state.request || !isTerminal || cachedMinutes !== null) return;
    const tripId = snapshot.trip_id || snapshot.search_id;
    if (savedTrips.current.has(tripId)) return;
    savedTrips.current.add(tripId);
    saveLocalSearch(snapshot.search_key, state.request, snapshot, new Date(), {
      selected_outbound_id: state.selectedOutboundId,
      selected_return_id: state.selectedReturnId,
      direction_progress: state.directionProgress,
    });
  }, [state.snapshot, state.request, state.selectedOutboundId, state.selectedReturnId, state.directionProgress, isTerminal, cachedMinutes]);

  return (
    <div className="workspace">
      <SearchForm onSearch={submit} disabled={isSearching} hasSearched={Boolean(state.request)} onExcludeBaggageChange={setExcludeBaggage} />
      {(state.request || startError) && <SearchStatusPanel snapshot={state.snapshot} cachedMinutes={cachedMinutes} directionProgress={state.directionProgress} pendingText={startError ?? progressText} />}
      {(state.snapshot?.errors.length || state.sseDisconnected || missingCachedRoutes > 0) && (
        <div className="notices">
          {missingCachedRoutes > 0 && (
            <div className="notice cache-coverage-notice">
              Cached connection coverage is incomplete: {missingCachedRoutes} route {missingCachedRoutes === 1 ? "search was" : "searches were"} not stored for this date. The connection profile can only filter flights already in the cache. No live API calls were made.
            </div>
          )}
          {noFeasibleConnection && missingCachedRoutes === 0 && (
            <div className="notice">No stored connection matched the selected connection profile.</div>
          )}
          {state.snapshot?.errors
            .filter((error) => error.code !== "no_feasible_self_transfer")
            .map((error, index) => (
              <div className="notice" key={`${error.code}-${error.hub}-${index}`}>
                {error.hub ? `${error.hub}: ` : ""}
                {error.code === "provider_timeout"
                  ? "A provider search timed out; other routes continued."
                  : "Part of the search was unavailable; completed results are still shown."}
              </div>
            ))}
          {state.sseDisconnected && !isTerminal && (
            <div className="notice">Live updates disconnected. Start a new search to reconnect.</div>
          )}
        </div>
      )}
      {(state.snapshot || state.request) && (
        <>
          {state.snapshot && <TripSummary
            outbound={outbound}
            inbound={state.snapshot.return ? inbound : null}
            outboundBaseline={state.snapshot.outbound.baseline}
            inboundBaseline={state.snapshot.return?.baseline ?? null}
            outboundComparisonEnabled={outboundTransfers}
            inboundComparisonEnabled={returnTransfers}
            excludeBaggage={excludeBaggage}
            searchId={state.snapshot.search_id}
            outboundDate={state.request?.outbound_date}
            returnDate={state.request?.return_date ?? undefined}
            complete={isTerminal || (state.directionCompleted.OUTBOUND && (!state.request?.return_date || state.directionCompleted.RETURN))}
            cacheOnly={state.request?.cache_only ?? false}
          />}
          <DirectionResults
            title="Outbound"
            results={state.snapshot?.outbound ?? emptyDirectionResults}
            selectedId={state.selectedOutboundId}
            onSelect={(id) => dispatch({ type: "select_outbound", id })}
            complete={isTerminal || state.directionCompleted.OUTBOUND}
            connectionProfile={state.request?.connection_profile ?? "CONSERVATIVE"}
            selfTransferEnabled={outboundTransfers}
            date={state.request?.outbound_date ?? ""}
          />
          {state.request?.return_date && (
            <DirectionResults
              title="Return"
              results={state.snapshot?.return ?? emptyDirectionResults}
              selectedId={state.selectedReturnId}
              onSelect={(id) => dispatch({ type: "select_return", id })}
              complete={isTerminal || state.directionCompleted.RETURN}
              connectionProfile={state.request?.connection_profile ?? "CONSERVATIVE"}
              selfTransferEnabled={returnTransfers}
              date={state.request.return_date}
            />
          )}
        </>
      )}
    </div>
  );
}
