import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SearchForm } from "./search-form";

describe("search controls", () => {
  it("does not render an obsolete Reset action", () => {
    render(<SearchForm onSearch={vi.fn()} disabled={false} />);
    expect(screen.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
  });

  it("changes selected airports in the submitted request only after Search", () => {
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} disabled={false} />);

    fireEvent.click(screen.getByRole("button", { name: /From London/ }));
    fireEvent.click(screen.getByLabelText("From STN"));
    fireEvent.click(screen.getByRole("button", { name: /To Sardinia/ }));
    fireEvent.click(screen.getByLabelText("To OLB"));
    expect(onSearch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({
      origins: ["LGW", "LTN", "LHR", "LCY"],
      destinations: ["CAG", "AHO"],
      connection_profile: "CONSERVATIVE",
      outbound_time_window: expect.objectContaining({ max_connection_minutes: 360 }),
      refresh_prices: false,
    }));
  });

  it("marks only the explicit refresh action as cache bypassing", () => {
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh live prices" }));
    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({ refresh_prices: true }));
  });

  it("closes an open calendar when Search or another popover is opened", () => {
    render(<SearchForm onSearch={vi.fn()} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: /Out18 Dec 2026/ }));
    expect(screen.getByRole("dialog", { name: /Out directional/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /From London/ }));
    expect(screen.queryByRole("dialog", { name: /Out directional/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Out18 Dec 2026/ }));
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(screen.queryByRole("dialog", { name: /Out directional/ })).not.toBeInTheDocument();
  });

  it("allows clearing a group and disables both search actions", () => {
    render(<SearchForm onSearch={vi.fn()} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: /From London/ }));
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByText("London (0)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Refresh live prices" })).toBeDisabled();
  });

  it("keeps the dropdown open for selection and closes on Escape and outside click", () => {
    render(<SearchForm onSearch={vi.fn()} disabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: /From London/ }));
    fireEvent.click(screen.getByLabelText("From LGW"));
    expect(screen.getByLabelText("From STN")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByLabelText("From STN")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /From London/ }));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByLabelText("From STN")).not.toBeInTheDocument();
  });

  it("defaults to excluding baggage while retaining internal one-cabin-bag search context", () => {
    const onSearch = vi.fn();
    const onExcludeBaggageChange = vi.fn();
    render(<SearchForm onSearch={onSearch} disabled={false} onExcludeBaggageChange={onExcludeBaggageChange} />);
    expect(screen.getByLabelText("Exclude baggage from comparison")).toBeChecked();
    expect(screen.queryByText("Prices exclude baggage.")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Exclude baggage from comparison")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Adults"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({ baggage: { cabin_bags: 1, checked_bags: 0 } }));
    fireEvent.click(screen.getByLabelText("Exclude baggage from comparison"));
    expect(onExcludeBaggageChange).toHaveBeenCalledWith(false);
    expect(onSearch).toHaveBeenCalledTimes(1);
  });
});
