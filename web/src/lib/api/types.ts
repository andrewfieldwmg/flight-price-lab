export type SelfTransferPolicy =
  | "NONE"
  | "OUTBOUND_ONLY"
  | "RETURN_ONLY"
  | "BOTH";
export type ConnectionProfile = "CONSERVATIVE" | "STANDARD" | "AGGRESSIVE";
export type Direction = "OUTBOUND" | "RETURN";
export type PriceCompleteness = "COMPLETE" | "ESTIMATED" | "PARTIAL" | "UNKNOWN";
export type SearchStatus =
  | "started"
  | "running"
  | "completed"
  | "partial_failure"
  | "failed";

export interface TripSearchRequest {
  origins: string[];
  destinations: string[];
  outbound_date: string;
  return_date: string | null;
  adults: number;
  children: number;
  baggage: { cabin_bags: number; checked_bags: number };
  outbound_time_window: DirectionTimeWindow;
  return_time_window: DirectionTimeWindow;
  max_extra_journey_minutes: number | null;
  self_transfer_policy: SelfTransferPolicy;
  connection_profile: ConnectionProfile;
  currency: string;
  refresh_prices: boolean;
}

export interface DirectionTimeWindow {
  earliest_departure_time: string | null;
  latest_arrival_time: string | null;
  max_connection_minutes: number;
}

export interface TripLegSummary {
  origin: string;
  destination: string;
  departure_at: string;
  arrival_at: string;
  airline: string;
  flight_number: string;
  constituent_fingerprint?: string | null;
  constituent_price?: string | null;
  history?: PriceHistoryComparison | null;
}

export interface PriceHistoryComparison {
  history_status: "FIRST_SEEN" | "PREVIOUS_FOUND";
  previous_price: string | null;
  price_change_amount: string | null;
  price_change_percent: string | null;
  previous_observed_at: string | null;
  elapsed_seconds: number | null;
  previous_observation_run_id: string | null;
}

export interface BaggageEstimate {
  ticket_index: number;
  carrier_codes: string[];
  flight_numbers: string[];
  price_low: string | null;
  price_high: string | null;
  completeness: PriceCompleteness;
  confidence: string;
}

export interface TripOption {
  id: string;
  direction: Direction;
  route: string[];
  flight_numbers: string[];
  airlines: string[];
  legs: TripLegSummary[];
  base_price: string;
  ancillary_price_low: string | null;
  ancillary_price_high: string | null;
  baggage_estimates: BaggageEstimate[];
  cabin_bags: number;
  checked_bags: number;
  effective_price_low: string | null;
  effective_price_high: string | null;
  currency: string;
  price_completeness: PriceCompleteness;
  is_nonstop: boolean;
  is_self_transfer: boolean;
  connection_airport: string | null;
  connection_minutes: number | null;
  departure_at: string;
  arrival_at: string;
  total_journey_minutes: number;
  saving_vs_nonstop_amount: string | null;
  saving_vs_nonstop_percent: string | null;
  saving_vs_nonstop_low: string | null;
  saving_vs_nonstop_high: string | null;
  extra_minutes_vs_nonstop: number | null;
  ticketing_type: "single_ticket" | "separate_tickets" | "unknown";
  baggage_confidence: string;
  constituent_fingerprints?: string[];
  history?: PriceHistoryComparison | null;
}

export type BookingSessionState = "CREATED" | "REPRICING" | "READY" | "VERIFY_ON_AIRLINE" | "UNAVAILABLE" | "PRICE_CHANGED" | "HANDOFF_STARTED" | "AIRLINE_VERIFIED" | "FAILED";

export interface BookingTicket {
  ticket_id: string;
  carrier: string;
  flight_number: string;
  route: string;
  travel_date: string;
  departure_at: string;
  arrival_at: string;
  original_price: string;
  current_price: string | null;
  price_delta: string | null;
  currency: string;
  status: BookingSessionState;
  price_change_status: "PRICE_DECREASED" | "UNCHANGED" | "MINOR_INCREASE" | "MATERIAL_INCREASE" | null;
  material_change_acknowledgement_required: boolean;
  capability: "EXACT_CHECKOUT_HANDOFF" | "EXACT_FLIGHT_HANDOFF" | "PREFILLED_SEARCH" | "GENERIC_BOOKING_PAGE" | "UNAVAILABLE" | null;
  pricing_confidence: "VERIFIED" | "UNVERIFIED";
  fare_selected: boolean;
  adults: number;
  children: number;
  exact_flight_verified: boolean;
  passenger_composition_verified: boolean;
}

export interface BookingSession {
  booking_session_id: string;
  state: BookingSessionState;
  tickets: BookingTicket[];
  original_total: string;
  current_total: string | null;
  price_delta: string | null;
}

export interface DirectionResults {
  baseline: TripOption | null;
  nonstop_options: TripOption[];
  cheapest_feasible: TripOption | null;
  fastest_feasible: TripOption | null;
  pareto_frontier: TripOption[];
  feasible_options: TripOption[];
}

export interface SearchError {
  code: string;
  message: string;
  direction: Direction | null;
  hub: string | null;
}

export interface SearchSnapshot {
  search_id: string;
  trip_id: string;
  search_key: string;
  status: SearchStatus;
  outbound: DirectionResults;
  return: DirectionResults | null;
  errors: SearchError[];
  diagnostics: {
    trip_id: string;
    search_key: string;
    local_cache_hit: boolean;
    backend_cache_hits: number;
    backend_cache_misses: number;
    provider_calls_this_invocation: number;
    provider_calls_avoided_this_invocation: number;
    original_provider_calls: number | null;
    original_search_completed_at: string | null;
    search_started_at?: string | null;
    search_completed_at?: string | null;
    total_duration_ms?: number | null;
    direct_outbound_ms?: number;
    direct_return_ms?: number;
    hub_search_total_ms?: number;
    normalization_ms?: number;
    itinerary_synthesis_ms?: number;
    ranking_filtering_ms?: number;
    postgres_write_ms?: number;
    final_serialization_ms?: number;
    provider_calls_total?: number;
    provider_calls_concurrent_peak?: number;
    slowest_provider_call_ms?: number;
    median_provider_call_ms?: number;
    p95_provider_call_ms?: number;
    provider_requests?: Record<string, unknown>[];
  };
}

export interface SearchEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface CalendarPrice {
  date: string;
  price: string;
  currency: string;
}

export interface ProviderUsage {
  current_month_usage: number;
  monthly_allowance: number;
  remaining_credits: number;
  period_start: string;
  period_end: string;
}
