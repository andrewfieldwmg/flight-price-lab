import type {
  CalendarPrice,
  ProviderUsage,
  SearchEvent,
  SearchSnapshot,
  TripSearchRequest,
  BookingSession,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL
  ?? (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");

export function resolveApiUrl(path: string, baseUrl = API_BASE_URL): string {
  if (!path.startsWith("/api/")) throw new Error("API paths must retain the FastAPI /api prefix");
  return `${baseUrl.replace(/\/$/, "")}${path}`;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(resolveApiUrl(path), {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? body?.detail ?? `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function streamSearch(
  request: TripSearchRequest,
  onEvent: (event: SearchEvent) => void,
  onTiming?: (phase: "response_headers" | "first_chunk" | "final_event", elapsedMs: number) => void,
): Promise<void> {
  const started = performance.now();
  const response = await fetch(resolveApiUrl("/api/search/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  onTiming?.("response_headers", performance.now() - started);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? body?.detail ?? `HTTP ${response.status}`);
  }
  if (!response.body) throw new Error("Streaming response body unavailable");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedFirstChunk = false;
  const dispatchLines = (final = false) => {
    const lines = buffer.split("\n");
    buffer = final ? "" : (lines.pop() ?? "");
    for (const line of lines) {
      if (!line.trim()) continue;
      const value = JSON.parse(line) as { event: string; data: Record<string, unknown> };
      if (value.event === "search_completed") {
        console.info("SEARCH_SERVER_TIMING", value.data.timings);
      }
      onEvent({ type: value.event, data: value.data });
    }
  };
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!receivedFirstChunk) {
      receivedFirstChunk = true;
      onTiming?.("first_chunk", performance.now() - started);
    }
    buffer += decoder.decode(value, { stream: true });
    dispatchLines();
  }
  buffer += decoder.decode();
  if (buffer.trim()) buffer += "\n";
  dispatchLines(true);
  onTiming?.("final_event", performance.now() - started);
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

export function prepareBooking(searchId: string, selectedOptionIds: string[]): Promise<BookingSession> {
  return prepareBookingRequest(searchId, selectedOptionIds);
}

async function prepareBookingRequest(searchId: string, selectedOptionIds: string[]): Promise<BookingSession> {
  const response = await fetch(resolveApiUrl("/api/booking/prepare"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ search_id: searchId, selected_option_ids: selectedOptionIds }),
  });
  const body = await response.json().catch(() => null) as BookingSession | { detail?: string } | null;
  if (process.env.NODE_ENV === "development") {
    const prepared = body as BookingSession | null;
    console.debug("BOOKING_PREPARE_RESPONSE", {
      status: response.status,
      booking_session_id: prepared?.booking_session_id,
      tickets_length: Array.isArray(prepared?.tickets) ? prepared.tickets.length : undefined,
      tickets: Array.isArray(prepared?.tickets) ? prepared.tickets.map((ticket) => ({
        ticket_id: ticket.ticket_id,
        carrier: ticket.carrier,
        capability: ticket.capability,
        original_price: ticket.original_price,
        current_price: ticket.current_price,
      })) : undefined,
    });
  }
  if (!response.ok) {
    const detail: unknown = body && "detail" in body ? body.detail : null;
    const message = typeof detail === "string"
      ? detail
      : detail && typeof detail === "object" && "message" in detail
        ? String((detail as { message: unknown }).message)
        : null;
    throw new Error(message ?? `HTTP ${response.status}`);
  }
  if (!body || !("booking_session_id" in body) || !Array.isArray(body.tickets)) {
    throw new Error("Invalid booking preparation response");
  }
  return body;
}

export function bookingHandoffUrl(sessionId: string, ticketId: string, acknowledge: boolean): string {
  const suffix = acknowledge ? "?acknowledge_material_change=true" : "";
  return resolveApiUrl(`/api/booking/${encodeURIComponent(sessionId)}/handoff/${encodeURIComponent(ticketId)}${suffix}`);
}
