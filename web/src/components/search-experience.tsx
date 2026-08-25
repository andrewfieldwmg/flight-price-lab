"use client";

import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { getSearch, getSearchKey, startSearch, subscribeToSearch } from "@/lib/api/client";
import type { TripOption, TripSearchRequest } from "@/lib/api/types";
import { uniqueOptions } from "@/lib/search/calculations";
import { initialSearchState, searchReducer } from "@/lib/search/state";
import { shouldRefreshProviderUsage } from "@/lib/search/telemetry";
import { cachedAgeMinutes, currentInvocationFromLocalCache, loadLocalSearch, logFrontendCacheEvent, saveLocalSearch } from "@/lib/search/local-cache";
import { DirectionResults } from "./direction-results";
import { SearchForm } from "./search-form";
import { SearchStatusPanel } from "./search-status-panel";
import { TripSummary } from "./trip-summary";

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
  const [searchId, setSearchId] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [cachedMinutes, setCachedMinutes] = useState<number | null>(null);
  const [excludeBaggage, setExcludeBaggage] = useState(true);
  const savedTrips = useRef(new Set<string>());
  const refreshQueue = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    if (!searchId) return;
    const refresh = () => {
      refreshQueue.current = refreshQueue.current
        .then(async () => dispatch({ type: "snapshot", snapshot: await getSearch(searchId) }))
        .catch(() => dispatch({ type: "disconnect" }));
    };
    refresh();
    return subscribeToSearch(
      searchId,
      (event) => {
        dispatch({ type: "event", event: event.type, data: event.data });
        refresh();
      },
      () => dispatch({ type: "disconnect" }),
    );
  }, [searchId]);

  async function submit(request: TripSearchRequest) {
    setStartError(null);
    setSearchId(null);
    dispatch({ type: "started", request });
    try {
      const key = await getSearchKey(request);
      logFrontendCacheEvent("SEARCH_RECEIVED", { search_key: key, search_key_short: key.slice(0, 12), cache_bypass: request.refresh_prices });
      if (!request.refresh_prices) {
        const cached = loadLocalSearch(key);
        if (cached) {
          const restored = currentInvocationFromLocalCache(cached);
          dispatch({ type: "restore_cached", snapshot: restored, selectedOutboundId: cached.ui_state.selected_outbound_id, selectedReturnId: cached.ui_state.selected_return_id, directionProgress: cached.ui_state.direction_progress });
          logFrontendCacheEvent("RESULT_RESTORED_FROM_CACHE", { trip_id: cached.trip_id, search_key: key, search_key_short: key.slice(0, 12) });
          setCachedMinutes(cachedAgeMinutes(cached));
          return;
        }
      }
      setCachedMinutes(null);
      const response = await startSearch(request);
      setSearchId(response.search_id);
    } catch (error) {
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
  const outboundTransfers = state.request?.self_transfer_policy === "OUTBOUND_ONLY" || state.request?.self_transfer_policy === "BOTH";
  const returnTransfers = state.request?.self_transfer_policy === "RETURN_ONLY" || state.request?.self_transfer_policy === "BOTH";
  const progressText = useMemo(() => {
    if (!state.snapshot?.outbound.baseline) return "Searching direct flights…";
    if (!isTerminal) return "Searching cheaper one-stop combinations…";
    return state.snapshot.status === "partial_failure"
      ? "Search complete with some unavailable routes"
      : "Search complete";
  }, [state.snapshot, isTerminal]);

  useEffect(() => {
    if (refreshProviderUsage) window.dispatchEvent(new Event("provider-usage-refresh"));
  }, [refreshProviderUsage]);

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
      <SearchForm onSearch={submit} disabled={isSearching} onExcludeBaggageChange={setExcludeBaggage} />
      {(state.request || startError) && <SearchStatusPanel snapshot={state.snapshot} cachedMinutes={cachedMinutes} directionProgress={state.directionProgress} pendingText={startError ?? progressText} />}
      {(state.snapshot?.errors.length || state.sseDisconnected) && (
        <div className="notices">
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
