import { describe, expect, it, vi } from "vitest";
import { resolveApiUrl, streamSearch } from "./client";

describe("same-origin API route composition", () => {
  it("preserves FastAPI routes exactly in production", () => {
    expect(resolveApiUrl("/api/search", "")).toBe("/api/search");
    expect(resolveApiUrl("/api/health", "")).toBe("/api/health");
  });

  it("adds only the local origin in development", () => {
    expect(resolveApiUrl("/api/search", "http://localhost:8000")).toBe("http://localhost:8000/api/search");
    expect(resolveApiUrl("/api/search", "http://localhost:8000/")).toBe("http://localhost:8000/api/search");
  });

  it("rejects a lost or duplicated API prefix", () => {
    expect(() => resolveApiUrl("/search", "")).toThrow(/\/api prefix/);
    expect(() => resolveApiUrl("/svc/api/search", "")).toThrow(/\/api prefix/);
  });
});

describe("POST search streaming", () => {
  it("parses progressive NDJSON chunks without a follow-up GET", async () => {
    const encoder = new TextEncoder();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode('{"event":"search_started","data":{"search_id":"s1"}}\n{"event":"results_'));
            controller.enqueue(encoder.encode('updated","data":{"direction":"OUTBOUND"}}\n'));
            controller.close();
          },
        }),
        { status: 200 },
      ),
    );
    const events: string[] = [];

    await streamSearch({} as never, (event) => events.push(event.type));

    expect(events).toEqual(["search_started", "results_updated"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toContain("/api/search/stream");
    fetchMock.mockRestore();
  });

  it("logs sanitized server timing on search completion", async () => {
    const consoleMock = vi.spyOn(console, "info").mockImplementation(() => undefined);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"event":"search_completed","data":{"timings":{"provider_calls_total":2}}}\n'),
    );

    await streamSearch({} as never, () => undefined);

    expect(consoleMock).toHaveBeenCalledWith("SEARCH_SERVER_TIMING", {
      provider_calls_total: 2,
    });
    vi.restoreAllMocks();
  });
});
