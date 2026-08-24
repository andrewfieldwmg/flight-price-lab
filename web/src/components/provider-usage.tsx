"use client";

import { useCallback, useEffect, useState } from "react";
import { getProviderUsage } from "@/lib/api/client";
import type { ProviderUsage } from "@/lib/api/types";

export function ProviderUsageIndicator({ refreshKey = 0 }: { refreshKey?: number }) {
  const [usage, setUsage] = useState<ProviderUsage | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const refresh = useCallback(async (force = false) => {
    try {
      setUsage(await getProviderUsage(force));
      setUnavailable(false);
    } catch {
      setUnavailable(true);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(false), 0);
    return () => window.clearTimeout(timer);
  }, [refresh, refreshKey]);
  useEffect(() => {
    const listener = () => void refresh(true);
    window.addEventListener("provider-usage-refresh", listener);
    return () => window.removeEventListener("provider-usage-refresh", listener);
  }, [refresh]);

  return (
    <button className="usage" type="button" onClick={() => void refresh(true)}>
      <span>SearchAPI</span>
      {usage ? (
        <strong>
          {usage.current_month_usage.toLocaleString()} / {usage.monthly_allowance.toLocaleString()}
          <small>{usage.remaining_credits.toLocaleString()} remaining</small>
        </strong>
      ) : (
        <strong>{unavailable ? "usage unavailable" : "checking…"}</strong>
      )}
    </button>
  );
}
