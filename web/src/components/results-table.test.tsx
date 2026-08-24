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
  <DirectionResults title="Outbound" results={results} selectedId={null} onSelect={onSelect} complete connectionProfile="CONSERVATIVE" selfTransferEnabled={selfTransferEnabled} />,
);

describe("dense flight results", () => {
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
    render(<DirectionResults title="Outbound" results={results} selectedId="dominated" onSelect={vi.fn()} complete connectionProfile="CONSERVATIVE" selfTransferEnabled />);
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

  it("flags early departures and late arrivals", () => {
    expect(isEarlyDeparture("2026-12-18T05:59:00+00:00")).toBe(true);
    expect(isEarlyDeparture("2026-12-18T06:00:00+00:00")).toBe(false);
    expect(isLateArrival("2026-12-18T23:01:00+01:00")).toBe(true);
    expect(isLateArrival("2026-12-18T23:00:00+01:00")).toBe(false);
  });
});
