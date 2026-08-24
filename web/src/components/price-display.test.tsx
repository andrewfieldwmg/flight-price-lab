import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { option } from "@/test/fixtures";
import { PriceDisplay } from "./price-display";

describe("baggage price completeness", () => {
  it("renders an estimated bounded range", () => {
    render(
      <PriceDisplay
        option={option({
          id: "range",
          price_completeness: "ESTIMATED",
          effective_price_low: "510",
          effective_price_high: "545",
        })}
      />,
    );
    expect(screen.getByText("£510–£545")).toBeInTheDocument();
  });

  it("labels unknown ancillaries as a base fare", () => {
    render(
      <PriceDisplay
        option={option({
          id: "unknown",
          base_price: "486",
          price_completeness: "UNKNOWN",
          effective_price_low: null,
          effective_price_high: null,
        })}
      />,
    );
    expect(screen.getByText("£486")).toBeInTheDocument();
    expect(screen.getByText(/base fare · baggage cost not fully priced/i)).toBeInTheDocument();
  });
});
