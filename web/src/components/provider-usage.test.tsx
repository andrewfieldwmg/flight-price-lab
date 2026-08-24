import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { getProviderUsage } from "@/lib/api/client";
import { ProviderUsageIndicator } from "./provider-usage";

vi.mock("@/lib/api/client", () => ({ getProviderUsage: vi.fn() }));

beforeEach(() => {
  vi.mocked(getProviderUsage).mockResolvedValue({
    current_month_usage: 327,
    monthly_allowance: 10_000,
    remaining_credits: 9_673,
    period_start: "2026-08-01T00:00:00Z",
    period_end: "2026-09-01T00:00:00Z",
  });
});

it("shows normalized provider usage and remaining credits", async () => {
  render(<ProviderUsageIndicator />);

  await waitFor(() => expect(screen.getByText("327 / 10,000")).toBeInTheDocument());
  expect(screen.getByText("9,673 remaining")).toBeInTheDocument();
  expect(getProviderUsage).toHaveBeenCalledWith(false);
  window.dispatchEvent(new Event("provider-usage-refresh"));
  await waitFor(() => expect(getProviderUsage).toHaveBeenCalledWith(true));
});
