import { describe, expect, it } from "vitest";
import { resolveApiUrl } from "./client";

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
