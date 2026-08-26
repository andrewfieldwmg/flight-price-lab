"use client";

import { useState } from "react";
import type { SearchSnapshot } from "@/lib/api/types";
import type { SearchUiState } from "@/lib/search/state";
import { money } from "./price-display";

type Source = "Local cache" | "Backend cache" | "Live";

export function searchSource(snapshot: SearchSnapshot): Source {
  if (snapshot.diagnostics.local_cache_hit) return "Local cache";
  if (snapshot.diagnostics.backend_cache_hits > 0 && snapshot.diagnostics.provider_calls_this_invocation === 0) return "Backend cache";
  return "Live";
}

function failures(snapshot: SearchSnapshot) {
  return snapshot.errors.filter((error) => error.code.includes("provider") || error.code.includes("partial")).length;
}

function cachedAt(snapshot: SearchSnapshot) {
  const value = snapshot.diagnostics.original_search_completed_at;
  return value ? new Date(value).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" }) : null;
}

const providerCallsLabel = (count: number) => `${count} provider ${count === 1 ? "call" : "calls"}`;
const avoidedCallsLabel = (count: number) => `${count} ${count === 1 ? "call" : "calls"} avoided`;
function cacheAge(minutes: number) {
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

export function SearchStatusPanel({ snapshot, cachedMinutes, directionProgress, pendingText }: { snapshot: SearchSnapshot | null; cachedMinutes: number | null; directionProgress: SearchUiState["directionProgress"]; pendingText?: string }) {
  const [expanded, setExpanded] = useState(false);
  if (!snapshot) return pendingText ? <section className="search-status-panel"><div className="search-status-row">{pendingText}</div></section> : null;
  const diagnostics = snapshot.diagnostics;
  const source = searchSource(snapshot);
  const failed = failures(snapshot);
  const partial = snapshot.status === "partial_failure" || snapshot.status === "failed";
  const status = snapshot.status === "completed" || partial ? "Search complete" : "Searching";
  const calls = diagnostics.provider_calls_this_invocation;
  const avoided = diagnostics.provider_calls_avoided_this_invocation;
  const backendCachedMinutes = diagnostics.backend_cache_age_seconds == null ? null : Math.floor(diagnostics.backend_cache_age_seconds / 60);
  const cachedLabel = cachedMinutes !== null ? `Cached · ${cacheAge(cachedMinutes)}` : source === "Backend cache" ? `Cached${backendCachedMinutes === null ? "" : ` · ${cacheAge(backendCachedMinutes)}`}` : null;
  const collapsed = source !== "Live"
    ? [status, cachedLabel, providerCallsLabel(calls), avoidedCallsLabel(avoided)].filter(Boolean).join(" · ")
    : [status, providerCallsLabel(calls), partial ? `${failed} failed` : `${failed} failures`].join(" · ");
  const technical = process.env.NODE_ENV === "development" || process.env.NEXT_PUBLIC_DIAGNOSTICS === "true";
  return <section className="search-status-panel" aria-label="Search status">
    <div className="search-status-row" role="status"><span>{collapsed}</span><button type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>Details {expanded ? "▴" : "▾"}</button></div>
    {expanded && <div className="search-status-details">
      <section><h3>Results</h3><dl>
        <div><dt>Direct outbound</dt><dd>{snapshot.outbound.baseline ? money(snapshot.outbound.baseline.base_price, snapshot.outbound.baseline.currency) : "—"}</dd></div>
        {snapshot.return && <div><dt>Direct return</dt><dd>{snapshot.return.baseline ? money(snapshot.return.baseline.base_price, snapshot.return.baseline.currency) : "—"}</dd></div>}
        <div><dt>Outbound hubs</dt><dd>{directionProgress.OUTBOUND.completed}/{directionProgress.OUTBOUND.started}</dd></div>
        {snapshot.return && <div><dt>Return hubs</dt><dd>{directionProgress.RETURN.completed}/{directionProgress.RETURN.started}</dd></div>}
        <div><dt>Provider failures</dt><dd>{failed}</dd></div>
      </dl></section>
      <section><h3>Cache / provider</h3><dl>
        <div><dt>Source</dt><dd>{source}</dd></div>
        {cachedAt(snapshot) && <div><dt>Cached at</dt><dd>{cachedAt(snapshot)}</dd></div>}
        <div><dt>Provider calls now</dt><dd>{calls}</dd></div>
        <div><dt>Calls avoided</dt><dd>{avoided}</dd></div>
        {diagnostics.original_provider_calls !== null && <div><dt>Original live calls</dt><dd>{diagnostics.original_provider_calls}</dd></div>}
      </dl></section>
      {technical && <section><h3>Technical</h3><dl><div><dt>Search key</dt><dd>{(diagnostics.search_key || snapshot.search_key).slice(0, 12)}</dd></div></dl></section>}
    </div>}
  </section>;
}
