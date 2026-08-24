import type { PriceCompleteness, TripOption } from "@/lib/api/types";

const number = (value: string | null): number | null =>
  value === null ? null : Number(value);

export interface CombinedTripSummary {
  baseAlternativePrice: number;
  baseNonstopPrice: number;
  baseSaving: number;
  alternativePrice: number | null;
  nonstopPrice: number | null;
  saving: number | null;
  savingPercent: number | null;
  ancillaryLow: number | null;
  ancillaryHigh: number | null;
  effectivePriceLow: number | null;
  effectivePriceHigh: number | null;
  nonstopEffectiveLow: number | null;
  nonstopEffectiveHigh: number | null;
  savingLow: number | null;
  savingHigh: number | null;
  extraMinutes: number;
  savingPerExtraHour: number | null;
  completeness: PriceCompleteness;
}

const completenessRank: Record<PriceCompleteness, number> = {
  COMPLETE: 3,
  ESTIMATED: 2,
  PARTIAL: 1,
  UNKNOWN: 0,
};

export function aggregateTrip(
  outbound: TripOption | null,
  inbound: TripOption | null,
  outboundBaseline: TripOption | null,
  inboundBaseline: TripOption | null,
  comparisons: { outbound: boolean; inbound: boolean } = { outbound: true, inbound: true },
): CombinedTripSummary | null {
  const selected = [outbound, inbound].filter(Boolean) as TripOption[];
  const pairs = [
    comparisons.outbound && outbound && outboundBaseline ? [outbound, outboundBaseline] : null,
    comparisons.inbound && inbound && inboundBaseline ? [inbound, inboundBaseline] : null,
  ].filter(Boolean) as [TripOption, TripOption][];
  const comparedSelected = pairs.map(([option]) => option);
  const baselines = pairs.map(([, baseline]) => baseline);
  if (!selected.length) return null;

  const selectedPrices = selected.map((option) => number(option.effective_price_low));
  const comparedPrices = comparedSelected.map((option) => number(option.effective_price_low));
  const baselinePrices = baselines.map((option) => number(option.effective_price_low));
  const alternativePrice = selectedPrices.every((price) => price !== null)
    ? (selectedPrices as number[]).reduce((sum, price) => sum + price, 0)
    : null;
  const nonstopPrice = baselinePrices.length && baselinePrices.every((price) => price !== null)
    ? (baselinePrices as number[]).reduce((sum, price) => sum + price, 0)
    : null;
  const saving =
    comparedPrices.length && comparedPrices.every((price) => price !== null) && nonstopPrice !== null
      ? nonstopPrice - (comparedPrices as number[]).reduce((sum, price) => sum + price, 0)
      : null;
  const sumBound = (options: TripOption[], field: "ancillary_price_low" | "ancillary_price_high" | "effective_price_low" | "effective_price_high") => {
    const values = options.map((option) => number(option[field]));
    return values.every((value) => value !== null) ? (values as number[]).reduce((sum, value) => sum + value, 0) : null;
  };
  const ancillaryLow = sumBound(selected, "ancillary_price_low");
  const ancillaryHigh = sumBound(selected, "ancillary_price_high");
  const effectivePriceLow = sumBound(selected, "effective_price_low");
  const effectivePriceHigh = sumBound(selected, "effective_price_high");
  const comparedEffectiveLow = sumBound(comparedSelected, "effective_price_low");
  const comparedEffectiveHigh = sumBound(comparedSelected, "effective_price_high");
  const nonstopEffectiveLow = sumBound(baselines, "effective_price_low");
  const nonstopEffectiveHigh = sumBound(baselines, "effective_price_high");
  const savingLow = nonstopEffectiveLow !== null && comparedEffectiveHigh !== null ? nonstopEffectiveLow - comparedEffectiveHigh : null;
  const savingHigh = nonstopEffectiveHigh !== null && comparedEffectiveLow !== null ? nonstopEffectiveHigh - comparedEffectiveLow : null;
  const extraMinutes = comparedSelected.reduce(
    (sum, option) => sum + (option.extra_minutes_vs_nonstop ?? 0),
    0,
  );
  const completeness = selected.reduce<PriceCompleteness>(
    (worst, option) =>
      completenessRank[option.price_completeness] < completenessRank[worst]
        ? option.price_completeness
        : worst,
    "COMPLETE",
  );
  return {
    baseAlternativePrice: selected.reduce(
      (sum, option) => sum + Number(option.base_price),
      0,
    ),
    baseNonstopPrice: baselines.reduce(
      (sum, option) => sum + Number(option.base_price),
      0,
    ),
    baseSaving:
      baselines.reduce((sum, option) => sum + Number(option.base_price), 0) -
      comparedSelected.reduce((sum, option) => sum + Number(option.base_price), 0),
    alternativePrice,
    nonstopPrice,
    saving,
    savingPercent:
      saving !== null && nonstopPrice ? (saving / nonstopPrice) * 100 : null,
    ancillaryLow,
    ancillaryHigh,
    effectivePriceLow,
    effectivePriceHigh,
    nonstopEffectiveLow,
    nonstopEffectiveHigh,
    savingLow,
    savingHigh,
    extraMinutes,
    savingPerExtraHour:
      saving !== null && extraMinutes > 0 ? saving / (extraMinutes / 60) : null,
    completeness,
  };
}

export function uniqueOptions(
  results: {
    baseline: TripOption | null;
    nonstop_options?: TripOption[];
    cheapest_feasible: TripOption | null;
    fastest_feasible: TripOption | null;
    pareto_frontier: TripOption[];
    feasible_options?: TripOption[];
  } | null,
): TripOption[] {
  if (!results) return [];
  const options = [
    results.cheapest_feasible,
    results.fastest_feasible,
    results.baseline,
    ...(results.nonstop_options ?? []),
    ...(results.feasible_options ?? []),
    ...results.pareto_frontier,
  ].filter(Boolean) as TripOption[];
  return [...new Map(options.map((option) => [option.id, option])).values()];
}
