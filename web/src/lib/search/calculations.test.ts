import { describe, expect, it } from "vitest";
import { option, synthetic } from "@/test/fixtures";
import { aggregateTrip } from "./calculations";

describe("round-trip calculations", () => {
  it("aggregates outbound synthetic with inbound nonstop", () => {
    const outbound = synthetic();
    const outboundBaseline = option({ id: "out-direct" });
    const inbound = option({
      id: "in-direct",
      direction: "RETURN",
      base_price: "300",
      effective_price_low: "300",
      effective_price_high: "300",
    });

    const summary = aggregateTrip(outbound, inbound, outboundBaseline, inbound);

    expect(summary?.alternativePrice).toBe(786);
    expect(summary?.nonstopPrice).toBe(1041);
    expect(summary?.saving).toBe(255);
    expect(summary?.extraMinutes).toBe(285);
    expect(summary?.savingPerExtraHour).toBeCloseTo(53.68, 2);
  });

  it("propagates incomplete effective pricing without treating it as zero", () => {
    const incomplete = synthetic();
    incomplete.effective_price_low = null;
    incomplete.effective_price_high = null;
    incomplete.price_completeness = "UNKNOWN";

    const summary = aggregateTrip(incomplete, null, option({ id: "direct" }), null);

    expect(summary?.alternativePrice).toBeNull();
    expect(summary?.saving).toBeNull();
    expect(summary?.baseSaving).toBe(255);
    expect(summary?.completeness).toBe("UNKNOWN");
  });

  it("calculates effective saving bounds from baggage-adjusted alternatives and baselines", () => {
    const alternative = synthetic();
    alternative.effective_price_low = "500";
    alternative.effective_price_high = "540";
    alternative.ancillary_price_low = "14";
    alternative.ancillary_price_high = "54";
    const baseline = option({
      id: "direct",
      effective_price_low: "750",
      effective_price_high: "780",
      ancillary_price_low: "9",
      ancillary_price_high: "39",
    });

    const summary = aggregateTrip(alternative, null, baseline, null);

    expect(summary?.savingLow).toBe(210);
    expect(summary?.savingHigh).toBe(280);
    expect(summary?.ancillaryLow).toBe(14);
    expect(summary?.ancillaryHigh).toBe(54);
  });
});
