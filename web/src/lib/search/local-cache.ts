import type { SearchSnapshot, TripSearchRequest } from "@/lib/api/types";

export const SEARCH_CACHE_TTL_MS = 60 * 60 * 1000;
const PREFIX = "flight-price-lab:";

function devLog(event: string, fields: Record<string, unknown>) {
  if (process.env.NODE_ENV === "development" || process.env.NEXT_PUBLIC_DIAGNOSTICS === "true") {
    console.info(JSON.stringify({ event, timestamp: new Date().toISOString(), ...fields }));
  }
}

export function logFrontendCacheEvent(event: string, fields: Record<string, unknown>) {
  devLog(event, fields);
}

export interface LocalSearchSnapshot {
  trip_id: string;
  search_key: string;
  saved_at: string;
  expires_at: string;
  request: TripSearchRequest;
  results: SearchSnapshot;
  ui_state: {
    selected_outbound_id: string | null;
    selected_return_id: string | null;
    direction_progress: Record<"OUTBOUND" | "RETURN", { started: number; completed: number }>;
  };
}

export function saveLocalSearch(
  searchKey: string,
  request: TripSearchRequest,
  results: SearchSnapshot,
  now = new Date(),
  uiState: LocalSearchSnapshot["ui_state"] = {
    selected_outbound_id: null,
    selected_return_id: null,
    direction_progress: { OUTBOUND: { started: 0, completed: 0 }, RETURN: { started: 0, completed: 0 } },
  },
): LocalSearchSnapshot {
  const value: LocalSearchSnapshot = {
    trip_id: results.trip_id || results.search_id,
    search_key: searchKey,
    saved_at: now.toISOString(),
    expires_at: new Date(now.getTime() + SEARCH_CACHE_TTL_MS).toISOString(),
    request: { ...request, refresh_prices: false },
    results,
    ui_state: uiState,
  };
  window.localStorage.setItem(`${PREFIX}${searchKey}`, JSON.stringify(value));
  return value;
}

export function currentInvocationFromLocalCache(value: LocalSearchSnapshot): SearchSnapshot {
  const previous = value.results.diagnostics;
  return {
    ...value.results,
    diagnostics: {
      ...previous,
      local_cache_hit: true,
      backend_cache_hits: 0,
      backend_cache_misses: 0,
      provider_calls_this_invocation: 0,
      provider_calls_avoided_this_invocation: previous.original_provider_calls ?? previous.provider_calls_this_invocation,
      original_provider_calls: previous.original_provider_calls ?? previous.provider_calls_this_invocation,
      original_search_completed_at: previous.original_search_completed_at ?? value.saved_at,
    },
  };
}

export function loadLocalSearch(
  searchKey: string,
  now = new Date(),
): LocalSearchSnapshot | null {
  const raw = window.localStorage.getItem(`${PREFIX}${searchKey}`);
  if (!raw) {
    devLog("FRONTEND_LOCAL_CACHE_MISS", { search_key: searchKey, search_key_short: searchKey.slice(0, 12), reason: "not_found" });
    return null;
  }
  try {
    const value = JSON.parse(raw) as LocalSearchSnapshot;
    if (value.search_key !== searchKey || Date.parse(value.expires_at) <= now.getTime()) {
      window.localStorage.removeItem(`${PREFIX}${searchKey}`);
      devLog("FRONTEND_LOCAL_CACHE_MISS", { search_key: searchKey, search_key_short: searchKey.slice(0, 12), reason: "expired_or_mismatched" });
      return null;
    }
    if (!value.ui_state) {
      const outboundEnabled = value.request.self_transfer_policy === "OUTBOUND_ONLY" || value.request.self_transfer_policy === "BOTH";
      const returnEnabled = value.request.self_transfer_policy === "RETURN_ONLY" || value.request.self_transfer_policy === "BOTH";
      value.ui_state = {
        selected_outbound_id: null,
        selected_return_id: null,
        direction_progress: {
          OUTBOUND: outboundEnabled ? { started: 8, completed: 8 } : { started: 0, completed: 0 },
          RETURN: returnEnabled ? { started: 8, completed: 8 } : { started: 0, completed: 0 },
        },
      };
    }
    const legacy = value.results.diagnostics as unknown as Record<string, unknown>;
    value.results.diagnostics = {
      ...value.results.diagnostics,
      provider_calls_this_invocation: Number(legacy.provider_calls_this_invocation ?? legacy.provider_calls ?? 0),
      provider_calls_avoided_this_invocation: Number(legacy.provider_calls_avoided_this_invocation ?? legacy.provider_calls_avoided ?? 0),
      original_provider_calls: legacy.original_provider_calls === null ? null : Number(legacy.original_provider_calls ?? legacy.provider_calls_this_invocation ?? legacy.provider_calls ?? 0),
      original_search_completed_at: typeof legacy.original_search_completed_at === "string" ? legacy.original_search_completed_at : value.saved_at,
    };
    devLog("FRONTEND_LOCAL_CACHE_HIT", { trip_id: value.trip_id, search_key: searchKey, search_key_short: searchKey.slice(0, 12) });
    return value;
  } catch {
    window.localStorage.removeItem(`${PREFIX}${searchKey}`);
    devLog("FRONTEND_LOCAL_CACHE_MISS", { search_key: searchKey, search_key_short: searchKey.slice(0, 12), reason: "invalid_json" });
    return null;
  }
}

export function cachedAgeMinutes(value: LocalSearchSnapshot, now = new Date()): number {
  return Math.max(0, Math.floor((now.getTime() - Date.parse(value.saved_at)) / 60_000));
}
