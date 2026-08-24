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
