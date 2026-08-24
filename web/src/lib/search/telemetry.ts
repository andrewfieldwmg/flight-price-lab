import type { SearchSnapshot } from "@/lib/api/types";

export function shouldRefreshProviderUsage(
  snapshot: SearchSnapshot | null,
  terminal: boolean,
): boolean {
  return terminal && (snapshot?.diagnostics.provider_calls_this_invocation ?? 0) > 0;
}
