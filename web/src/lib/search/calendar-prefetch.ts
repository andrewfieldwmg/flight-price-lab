import type { CalendarResponse, TripSearchRequest } from "@/lib/api/types";
import { currentCacheEpoch } from "./local-cache";

type CalendarFetcher = (input: {
  origins: string[]; destinations: string[]; dateFrom: string; dateTo: string;
  adults: number; children: number; currency: string; direction: "OUTBOUND" | "RETURN";
}) => Promise<CalendarResponse>;

function offsetDate(value: string, days: number) {
  const date = new Date(`${value}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function calendarPrefetchKey(request: TripSearchRequest, now = new Date()) {
  return ["flight-price-lab:calendar-prefetch", currentCacheEpoch(now).toISOString(), request.origins.join(","), request.destinations.join(","), request.outbound_date, request.return_date ?? "", request.adults, request.children, request.currency].join(":");
}

export async function prefetchSearchCalendar(request: TripSearchRequest, fetchCalendar: CalendarFetcher) {
  const dates: Array<["OUTBOUND" | "RETURN", string, string[], string[]]> = [
    ["OUTBOUND", request.outbound_date, request.origins, request.destinations],
  ];
  if (request.return_date) dates.push(["RETURN", request.return_date, request.destinations, request.origins]);
  let calls = 0;
  let avoided = 0;
  let failures = 0;
  for (const [direction, date, origins, destinations] of dates) {
    try {
      const response = await fetchCalendar({ origins, destinations, dateFrom: offsetDate(date, -3), dateTo: offsetDate(date, 3), adults: request.adults, children: request.children, currency: request.currency, direction });
      calls += response.calendar_provider_calls_this_invocation;
      avoided += response.calendar_calls_avoided;
      failures += response.failures;
    } catch {
      failures += 1;
    }
  }
  return { calls, avoided, failures };
}
