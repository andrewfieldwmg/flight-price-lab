import { describe, expect, it, vi } from "vitest";
import type { CalendarResponse, TripSearchRequest } from "@/lib/api/types";
import { calendarPrefetchKey, prefetchSearchCalendar } from "./calendar-prefetch";

const request = { origins: ["LGW"], destinations: ["CAG"], outbound_date: "2026-08-27", return_date: "2026-09-03", adults: 2, children: 0, currency: "GBP" } as TripSearchRequest;
const response = (calls: number, avoided: number): CalendarResponse => ({ dates: [], calendar_provider_calls_this_invocation: calls, calendar_calls_avoided: avoided, failures: 0, request_timings: [], calendar_calls_total: calls, calendar_calls_concurrent_peak: Math.min(calls, 4), calendar_provider_median_ms: 0, calendar_provider_p95_ms: 0, calendar_provider_slowest_ms: 0, calendar_total_duration_ms: 0, calendar_postgres_total_ms: 0 });

describe("idle calendar prefetch", () => {
  it("requests only the two selected-date windows and reports backend cache avoidance", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(2, 5)).mockResolvedValueOnce(response(0, 7));
    await expect(prefetchSearchCalendar(request, fetcher)).resolves.toEqual({ calls: 2, avoided: 12, failures: 0 });
    expect(fetcher).toHaveBeenNthCalledWith(1, expect.objectContaining({ direction: "OUTBOUND", dateFrom: "2026-08-24", dateTo: "2026-08-30", origins: ["LGW"], destinations: ["CAG"] }));
    expect(fetcher).toHaveBeenNthCalledWith(2, expect.objectContaining({ direction: "RETURN", dateFrom: "2026-08-31", dateTo: "2026-09-06", origins: ["CAG"], destinations: ["LGW"] }));
  });

  it("uses one epoch-scoped marker for repeat suppression", () => {
    const now = new Date("2026-08-27T12:00:00Z");
    expect(calendarPrefetchKey(request, now)).toBe(calendarPrefetchKey(request, now));
    expect(calendarPrefetchKey(request, now)).not.toBe(calendarPrefetchKey({ ...request, outbound_date: "2026-08-28" }, now));
  });
});
