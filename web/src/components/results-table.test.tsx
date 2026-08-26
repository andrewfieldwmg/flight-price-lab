import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { option, synthetic } from "@/test/fixtures";
import { DirectionResults, isEarlyDeparture, isLateArrival, sortOptions } from "./direction-results";

const direct = option({ id: "direct" });
const cheaper = synthetic();
const dominated = option({
  id: "dominated",
  is_nonstop: false,
  is_self_transfer: true,
  route: ["LGW", "FCO", "CAG"],
  departure_at: "2026-12-18T09:00:00+00:00",
  arrival_at: "2026-12-18T18:00:00+01:00",
  saving_vs_nonstop_amount: "40",
  saving_vs_nonstop_percent: "5.4",
  extra_minutes_vs_nonstop: 400,
  connection_airport: "FCO",
  connection_minutes: 260,
  total_journey_minutes: 560,
});
const results = {
  baseline: direct,
  nonstop_options: [direct],
  cheapest_feasible: cheaper,
  fastest_feasible: cheaper,
  pareto_frontier: [cheaper],
  feasible_options: [cheaper, dominated],
};

const renderResults = (selfTransferEnabled = true, onSelect = vi.fn()) => render(
  <DirectionResults title="Outbound" date="2026-12-18" results={results} selectedId={null} onSelect={onSelect} complete connectionProfile="CONSERVATIVE" selfTransferEnabled={selfTransferEnabled} />,
);

describe("dense flight results", () => {
  const manyOptions = Array.from({ length: 31 }, (_, index) => option({
    id: `direct-${index + 1}`,
    route: [`O${index + 1}`, "CAG"],
    base_price: String(900 - index),
    flight_numbers: [`FR ${1000 + index}`],
    legs: [{
      ...option({ id: "leg" }).legs[0],
      origin: `O${index + 1}`,
      flight_number: `FR ${1000 + index}`,
    }],
  }));
  const pagedResults = { ...results, baseline: manyOptions[0], nonstop_options: manyOptions, feasible_options: [] };

  it("paginates 15 rows after sorting and retains selection across pages", () => {
    const { rerender } = render(<DirectionResults title="Outbound" date="2026-12-18" results={pagedResults} selectedId="direct-20" onSelect={vi.fn()} complete connectionProfile="CONSERVATIVE" selfTransferEnabled={false} />);
    expect(screen.getAllByRole("radio")).toHaveLength(15);
    expect(screen.getByText("1–15 of 31")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    expect(screen.getByLabelText("Select O20-CAG")).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Price" }));
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("O31–CAG")).toBeInTheDocument();
    rerender(<DirectionResults title="Outbound" date="2026-12-18" results={pagedResults} selectedId="direct-20" onSelect={vi.fn()} complete connectionProfile="CONSERVATIVE" selfTransferEnabled={false} />);
    expect(screen.getByLabelText("Select O20-CAG")).toBeChecked();
  });

  it("keeps outbound and return pagination independent", () => {
    render(<><DirectionResults title="Outbound" date="2026-12-18" results={pagedResults} selectedId={null} onSelect={vi.fn()} complete connectionProfile="CONSERVATIVE" selfTransferEnabled={false} /><DirectionResults title="Return" date="2026-12-28" results={pagedResults} selectedId={null} onSelect={vi.fn()} complete connectionProfile="CONSERVATIVE" selfTransferEnabled={false} /></>);
    const outboundPagination = screen.getByRole("navigation", { name: "Outbound pagination" });
    const returnPagination = screen.getByRole("navigation", { name: "Return pagination" });
    fireEvent.click(within(outboundPagination).getByRole("button", { name: "Next" }));
    expect(outboundPagination).toHaveTextContent("Page 2 of 3");
    expect(returnPagination).toHaveTextContent("Page 1 of 3");
  });

  it("shows the search date and direction loading states", () => {
    const { rerender } = render(<DirectionResults title="Outbound" date="2026-12-18" results={{ ...results, baseline: null, nonstop_options: [], feasible_options: [] }} selectedId={null} onSelect={vi.fn()} complete={false} connectionProfile="CONSERVATIVE" selfTransferEnabled />);
    expect(screen.getByText("Friday 18 December 2026")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading outbound options…");
    rerender(<DirectionResults title="Outbound" date="2026-12-18" results={results} selectedId={null} onSelect={vi.fn()} complete={false} connectionProfile="CONSERVATIVE" selfTransferEnabled />);
    expect(screen.getByRole("status")).toHaveTextContent("Still loading more options…");
    rerender(<DirectionResults title="Outbound" date="2026-12-18" results={results} selectedId={null} onSelect={vi.fn()} complete connectionProfile="CONSERVATIVE" selfTransferEnabled />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows all feasible options with the nonstop reference pinned initially", () => {
    renderResults();
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("£741")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Reference")).toBeInTheDocument();
    expect(screen.queryByText("LGW–FCO–CAG")).not.toBeInTheDocument();
    expect(screen.getByText("LGW–CAG")).toBeInTheDocument();
  });

  it("defaults to £100 minimum saving while retaining the nonstop reference", () => {
    const onSelect = vi.fn();
    renderResults(true, onSelect);
    expect(screen.getByLabelText("Outbound minimum saving")).toHaveValue("100");
    expect(screen.getByText("LGW–CAG")).toBeInTheDocument();
    expect(screen.queryByText("LGW–FCO–CAG")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Outbound minimum saving"), { target: { value: "0" } });
    expect(screen.getByText("LGW–FCO–CAG")).toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("retains a selected alternative below the saving threshold", () => {
    render(<DirectionResults title="Outbound" date="2026-12-18" results={results} selectedId="dominated" onSelect={vi.fn()} complete connectionProfile="CONSERVATIVE" selfTransferEnabled />);
    expect(screen.getByText("LGW–FCO–CAG")).toBeInTheDocument();
  });

  it("respects an explicit price sort instead of pinning nonstop", () => {
    renderResults();
    fireEvent.click(screen.getByRole("button", { name: "Price" }));
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("£486")).toBeInTheDocument();
  });

  it("allows the nonstop reference row to be selected", () => {
    const onSelect = vi.fn();
    renderResults(true, onSelect);
    fireEvent.click(screen.getByLabelText("Select LGW-CAG"));
    expect(onSelect).toHaveBeenCalledWith("direct");
  });

  it.each([
    ["price", [cheaper, direct], cheaper.id],
    ["saving", [dominated, cheaper], dominated.id],
    ["departure", [dominated, cheaper], cheaper.id],
    ["arrival", [dominated, cheaper], cheaper.id],
    ["transfer", [dominated, cheaper], cheaper.id],
    ["journey", [dominated, cheaper], cheaper.id],
    ["extra", [dominated, cheaper], cheaper.id],
  ] as const)("sorts %s ascending", (key, options, firstId) => {
    expect(sortOptions([...options], key, false)[0].id).toBe(firstId);
    expect(sortOptions([...options], key, true)[1].id).toBe(firstId);
  });

  it("can restrict the table to efficient options", () => {
    renderResults();
    fireEvent.click(screen.getByRole("button", { name: "Efficient only" }));
    expect(screen.queryByText("LGW–FCO–CAG")).not.toBeInTheDocument();
    expect(screen.getByText("LGW–MXP–CAG")).toBeInTheDocument();
  });

  it("hides comparison fields and alternatives in nonstop mode", () => {
    renderResults(false);
    expect(screen.queryByRole("button", { name: /Saving vs nonstop/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Extra vs nonstop/ })).not.toBeInTheDocument();
    expect(screen.queryByText("LGW–MXP–CAG")).not.toBeInTheDocument();
    expect(screen.getByText("LGW–CAG")).toBeInTheDocument();
  });

  it("expands a row inline with leg and stopover details", () => {
    renderResults();
    fireEvent.click(screen.getByText("LGW–MXP–CAG"));
    expect(screen.getByText(/LGW 08:00 → MXP 10:00/)).toBeInTheDocument();
    expect(screen.getByText("Stopover MXP · 3h 50m")).toBeInTheDocument();
  });

  it("shows composite change since the most recent comparable observation", () => {
    const historical = option({
      id: "history",
      history: {
        history_status: "PREVIOUS_FOUND",
        previous_price: "378",
        price_change_amount: "24",
        price_change_percent: "6.35",
        previous_observed_at: "2026-08-20T08:00:00Z",
        elapsed_seconds: 3 * 86400,
        previous_observation_run_id: "run-1",
      },
    });
    const historicalResults = { ...results, baseline: historical, nonstop_options: [historical] };
    render(<DirectionResults title="Outbound" date="2026-12-18" results={historicalResults} selectedId={null} onSelect={vi.fn()} complete connectionProfile="CONSERVATIVE" selfTransferEnabled={false} />);
    expect(screen.getByText("↑ 6.3%")).toBeInTheDocument();
    expect(screen.getByText("3d ago")).toBeInTheDocument();
    expect(screen.getByTitle(/Was £378.00/)).toBeInTheDocument();
  });

  it("uses decrease, neutral, and first-seen indicators in the Change column", () => {
    const comparison = {
      history_status: "PREVIOUS_FOUND" as const,
      previous_price: "400",
      price_change_amount: "-20",
      price_change_percent: "-5",
      previous_observed_at: "2026-08-20T08:00:00Z",
      elapsed_seconds: 86400,
      previous_observation_run_id: "run-1",
    };
    const decreased = option({ id: "decreased", base_price: "380", history: comparison });
    const unchanged = option({ id: "unchanged", history: { ...comparison, previous_price: "486", price_change_amount: "0", price_change_percent: "0" } });
    const first = option({ id: "first", history: { ...comparison, history_status: "FIRST_SEEN", previous_price: null, price_change_amount: null, price_change_percent: null } });
    render(<DirectionResults title="Outbound" date="2026-12-18" results={{ ...results, baseline: first, nonstop_options: [decreased, unchanged, first] }} selectedId={null} onSelect={vi.fn()} complete connectionProfile="CONSERVATIVE" selfTransferEnabled={false} />);
    expect(screen.getByText("↓ 5.0%")).toHaveAccessibleName("Price decreased by 5.0 percent since last seen");
    expect(screen.getByText("No change")).toHaveAccessibleName("No price change since last seen");
    expect(screen.getByText("New")).toHaveAccessibleName("New price observation");
  });

  it("flags early departures and late arrivals", () => {
    expect(isEarlyDeparture("2026-12-18T05:59:00+00:00")).toBe(true);
    expect(isEarlyDeparture("2026-12-18T06:00:00+00:00")).toBe(false);
    expect(isLateArrival("2026-12-18T23:01:00+01:00")).toBe(true);
    expect(isLateArrival("2026-12-18T23:00:00+01:00")).toBe(false);
  });
});
