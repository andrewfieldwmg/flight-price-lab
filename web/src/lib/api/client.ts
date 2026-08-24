import type {
  CalendarPrice,
  ProviderUsage,
  SearchEvent,
  SearchSnapshot,
  TripSearchRequest,
} from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? body?.detail ?? `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function startSearch(
  request: TripSearchRequest,
): Promise<{ search_id: string; trip_id: string; search_key: string; status: "started" }> {
  return apiFetch("/api/search", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function getSearchKey(request: TripSearchRequest): Promise<string> {
  const response = await apiFetch<{ search_key: string }>("/api/search/key", {
    method: "POST",
    body: JSON.stringify(request),
  });
  return response.search_key;
}

export function getSearch(searchId: string): Promise<SearchSnapshot> {
  return apiFetch(`/api/search/${encodeURIComponent(searchId)}`);
}

const EVENT_NAMES = [
  "search_started",
  "baseline_found",
  "hub_started",
  "hub_completed",
  "alternative_found",
  "results_updated",
  "direction_completed",
  "search_completed",
  "search_failed",
] as const;

export function subscribeToSearch(
  searchId: string,
  onEvent: (event: SearchEvent) => void,
  onDisconnect: () => void,
): () => void {
  const source = new EventSource(
    `${API_BASE_URL}/api/search/${encodeURIComponent(searchId)}/events`,
  );
  for (const type of EVENT_NAMES) {
    source.addEventListener(type, (raw) => {
      const message = raw as MessageEvent<string>;
      onEvent({ type, data: JSON.parse(message.data) as Record<string, unknown> });
      if (type === "search_completed" || type === "search_failed") source.close();
    });
  }
  source.onerror = () => {
    source.close();
    onDisconnect();
  };
  return () => source.close();
}

export async function getCalendarPrices(input: {
  origins: string[];
  destinations: string[];
  dateFrom: string;
  dateTo: string;
  adults: number;
  children: number;
  currency: string;
}): Promise<CalendarPrice[]> {
  const params = new URLSearchParams({
    date_from: input.dateFrom,
    date_to: input.dateTo,
    adults: String(input.adults),
    children: String(input.children),
    currency: input.currency,
  });
  input.origins.forEach((value) => params.append("origins", value));
  input.destinations.forEach((value) => params.append("destinations", value));
  const response = await apiFetch<{ prices: CalendarPrice[] }>(
    `/api/calendar?${params}`,
  );
  return response.prices;
}

export function getProviderUsage(refresh = false): Promise<ProviderUsage> {
  return apiFetch(`/api/provider-usage${refresh ? "?refresh=true" : ""}`);
}
