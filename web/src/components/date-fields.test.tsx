import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DateFields } from "./date-fields";

const getCalendarPrices = vi.fn();
vi.mock("@/lib/api/client", () => ({
  getCalendarPrices: (...args: unknown[]) => getCalendarPrices(...args),
}));

function response(start = 15) {
  return {
    dates: Array.from({ length: 7 }, (_, index) => ({
      date: `2026-12-${String(start + index).padStart(2, "0")}`,
      price: String([849, 944, 700, 596, 456, 800, 900][index]),
      currency: "GBP",
      state: "LOADED",
      classification: index < 2 ? "LOW" : index > 4 ? "HIGH" : "TYPICAL",
      observed_at: "2026-08-26T12:00:00Z",
    })),
    calendar_provider_calls_this_invocation: 7,
    calendar_calls_avoided: 0,
    failures: 0,
  };
}

function renderFields(overrides = {}) {
  const props = {
    outbound: "2026-12-18",
    inbound: "2026-12-28",
    roundTrip: true,
    origins: ["LGW", "STN"],
    destinations: ["CAG", "OLB"],
    adults: 2,
    childPassengers: 2,
    currency: "GBP",
    onOutboundChange: vi.fn(),
    onInboundChange: vi.fn(),
    ...overrides,
  };
  render(<DateFields {...props} />);
  return props;
}

describe("directional date pricing", () => {
  beforeEach(() => {
    getCalendarPrices.mockReset();
    getCalendarPrices.mockResolvedValue(response());
  });

  it("loads only the selected date plus or minus three days", async () => {
    renderFields();
    fireEvent.click(screen.getByRole("button", { name: /Out18 Dec 2026/ }));
    await waitFor(() => expect(getCalendarPrices).toHaveBeenCalledWith(expect.objectContaining({
      dateFrom: "2026-12-15", dateTo: "2026-12-21", direction: "OUTBOUND",
    })));
    expect(await screen.findByText("£849")).toBeInTheDocument();
    expect(screen.getAllByText("Likely cheaper").length).toBeGreaterThan(0);
    expect(screen.getByText(/lowest observed nonstop fare/)).toBeInTheDocument();
    expect(screen.queryByText(/synthetic price|one-stop price/i)).not.toBeInTheDocument();
  });

  it("loads a new visible range lazily and does not refetch known dates", async () => {
    getCalendarPrices.mockResolvedValueOnce(response()).mockResolvedValueOnce(response(22));
    renderFields();
    fireEvent.click(screen.getByRole("button", { name: /Out18 Dec 2026/ }));
    await screen.findByText("£849");
    fireEvent.click(screen.getByRole("button", { name: "Next dates" }));
    await waitFor(() => expect(getCalendarPrices).toHaveBeenCalledTimes(2));
    expect(getCalendarPrices.mock.calls[1][0]).toEqual(expect.objectContaining({ dateFrom: "2026-12-22", dateTo: "2026-12-28" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous dates" }));
    await waitFor(() => expect(getCalendarPrices).toHaveBeenCalledTimes(2));
  });

  it("keeps outbound and return markets independent and selection makes no search", async () => {
    const props = renderFields();
    fireEvent.click(screen.getByRole("button", { name: /Return28 Dec 2026/ }));
    await waitFor(() => expect(getCalendarPrices).toHaveBeenCalledWith(expect.objectContaining({
      origins: ["CAG", "OLB"], destinations: ["LGW", "STN"], direction: "RETURN",
    })));
    const dialog = screen.getByRole("dialog", { name: /Return directional/ });
    fireEvent.click(within(dialog).getAllByRole("button").find((button) => button.classList.contains("date-price-cell") && button.textContent?.includes("28"))!);
    expect(props.onInboundChange).toHaveBeenCalled();
    expect(getCalendarPrices).toHaveBeenCalledTimes(1);
  });

  it("shows explicit loading and unavailable states without zero prices", async () => {
    let resolve!: (value: ReturnType<typeof response>) => void;
    getCalendarPrices.mockReturnValue(new Promise((done) => { resolve = done; }));
    renderFields();
    fireEvent.click(screen.getByRole("button", { name: /Out18 Dec 2026/ }));
    expect(screen.getAllByLabelText("Loading date price")).toHaveLength(7);
    const unavailable = response(); unavailable.dates[0].price = null as unknown as string;
    unavailable.dates[0].state = "ERROR";
    resolve(unavailable);
    await waitFor(() => expect(screen.queryAllByLabelText("Loading date price")).toHaveLength(0));
    expect(screen.queryByText("£0")).not.toBeInTheDocument();
  });

  it("renders successful dates when one date has a provider error", async () => {
    const partial = response();
    partial.failures = 1;
    partial.dates[3] = { ...partial.dates[3], price: null as unknown as string, state: "ERROR", classification: null as unknown as string };
    getCalendarPrices.mockResolvedValue(partial);
    renderFields();

    fireEvent.click(screen.getByRole("button", { name: /Out18 Dec 2026/ }));

    expect(await screen.findByText("£849")).toBeInTheDocument();
    expect(screen.getByTitle("Price temporarily unavailable")).toHaveTextContent("—");
    expect(screen.queryByText("£0")).not.toBeInTheDocument();
  });
});
