import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SearchForm } from "./search-form";
import { LAST_SEARCH_FORM_KEY } from "@/lib/search/form-persistence";

describe("search controls", () => {
  beforeEach(() => window.localStorage.clear());

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
      destinations: ["AHO", "CAG"],
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

  it("offers a persisted cache-only switch that cannot trigger a live refresh", () => {
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} disabled={false} />);

    const cacheOnly = screen.getByRole("switch", { name: /Use cached results only/i });
    fireEvent.click(cacheOnly);

    expect(cacheOnly).toBeChecked();
    expect(screen.getByRole("button", { name: "Refresh live prices" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Use cached results" }));
    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({
      cache_only: true,
      refresh_prices: false,
    }));
    expect(JSON.parse(window.localStorage.getItem(LAST_SEARCH_FORM_KEY) ?? "{}")).toEqual(
      expect.objectContaining({ cacheOnly: true }),
    );
  });

  it("normalizes passenger fields immediately without leading zeroes", () => {
    render(<SearchForm onSearch={vi.fn()} disabled={false} />);
    const adults = screen.getByLabelText("Adults");
    const children = screen.getByLabelText("Children");

    fireEvent.change(adults, { target: { value: "01" } });
    fireEvent.change(children, { target: { value: "002" } });

    expect(adults).toHaveValue(1);
    expect(children).toHaveValue(2);
  });

  it("offers an explicit aggressive profile for tight self-transfers", () => {
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} disabled={false} />);

    expect(screen.queryByLabelText("Connection buffer")).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Allow stop" })[0]);
    fireEvent.change(screen.getByLabelText("Connection buffer"), {
      target: { value: "AGGRESSIVE" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({
      self_transfer_policy: "OUTBOUND_ONLY",
      connection_profile: "AGGRESSIVE",
    }));
  });

  it("reruns an existing live search as soon as the connection profile changes", () => {
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} disabled={false} hasSearched />);

    fireEvent.click(screen.getAllByRole("button", { name: "Allow stop" })[0]);
    fireEvent.change(screen.getByLabelText("Connection buffer"), {
      target: { value: "STANDARD" },
    });

    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({
      cache_only: false,
      refresh_prices: false,
      connection_profile: "STANDARD",
      self_transfer_policy: "OUTBOUND_ONLY",
    }));
    expect(screen.getByText("Applies immediately")).toBeInTheDocument();
  });

  it("also reruns cached-only results without enabling a live refresh", () => {
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} disabled={false} hasSearched />);

    fireEvent.click(screen.getByRole("switch", { name: /Use cached results only/i }));
    fireEvent.click(screen.getAllByRole("button", { name: "Allow stop" })[0]);
    fireEvent.change(screen.getByLabelText("Connection buffer"), {
      target: { value: "AGGRESSIVE" },
    });

    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({
      cache_only: true,
      refresh_prices: false,
      connection_profile: "AGGRESSIVE",
    }));
  });

  it("restores every submitted search parameter after a page load", async () => {
    window.localStorage.setItem(LAST_SEARCH_FORM_KEY, JSON.stringify({
      version: 1,
      origins: ["STN"],
      destinations: ["OLB"],
      outboundDate: "2026-10-25",
      returnDate: "2026-11-01",
      roundTrip: false,
      adults: 3,
      children: 1,
      cabinBags: 2,
      checkedBags: 1,
      outboundTransfer: true,
      returnTransfer: false,
      connectionProfile: "AGGRESSIVE",
      excludeBaggage: false,
      cacheOnly: true,
    }));
    const onSearch = vi.fn();
    const onExcludeBaggageChange = vi.fn();
    render(<SearchForm onSearch={onSearch} disabled={false} onExcludeBaggageChange={onExcludeBaggageChange} />);

    expect(await screen.findByRole("button", { name: "From London (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "To Sardinia (1)" })).toBeInTheDocument();
    expect(screen.getByLabelText("Adults")).toHaveValue(3);
    expect(screen.getByLabelText("Children")).toHaveValue(1);
    expect(screen.getByLabelText("Return trip")).not.toBeChecked();
    expect(screen.getByLabelText("Exclude baggage from comparison")).not.toBeChecked();
    expect(screen.getByRole("switch", { name: /Use cached results only/i })).toBeChecked();
    expect(screen.getByLabelText("Connection buffer")).toHaveValue("AGGRESSIVE");
    expect(onExcludeBaggageChange).toHaveBeenCalledWith(false);

    fireEvent.click(screen.getByRole("button", { name: "Use cached results" }));
    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({
      origins: ["STN"],
      destinations: ["OLB"],
      outbound_date: "2026-10-25",
      return_date: null,
      adults: 3,
      children: 1,
      baggage: { cabin_bags: 2, checked_bags: 1 },
      self_transfer_policy: "OUTBOUND_ONLY",
      connection_profile: "AGGRESSIVE",
      cache_only: true,
    }));
  });

  it("persists only when a search is submitted and discards corrupt saved state", () => {
    window.localStorage.setItem(LAST_SEARCH_FORM_KEY, "not-json");
    const onSearch = vi.fn();
    render(<SearchForm onSearch={onSearch} disabled={false} />);
    expect(window.localStorage.getItem(LAST_SEARCH_FORM_KEY)).toBeNull();

    fireEvent.change(screen.getByLabelText("Adults"), { target: { value: "3" } });
    expect(window.localStorage.getItem(LAST_SEARCH_FORM_KEY)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(JSON.parse(window.localStorage.getItem(LAST_SEARCH_FORM_KEY) ?? "{}")).toEqual(expect.objectContaining({
      version: 1,
      adults: 3,
      origins: ["STN", "LGW", "LTN", "LHR", "LCY"],
      destinations: ["AHO", "CAG", "OLB"],
      connectionProfile: "CONSERVATIVE",
    }));
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
