import type { TripOption } from "@/lib/api/types";

const symbols: Record<string, string> = { GBP: "£", EUR: "€", USD: "$" };
export const money = (value: number | string, currency = "GBP") =>
  `${symbols[currency] ?? `${currency} `}${Number(value).toLocaleString("en-GB", {
    maximumFractionDigits: 2,
  })}`;

export function PriceDisplay({ option }: { option: TripOption }) {
  const low = option.effective_price_low;
  const high = option.effective_price_high;
  if (option.price_completeness === "COMPLETE" && low !== null) {
    return (
      <>
        <div className="card-price">{money(low, option.currency)}</div>
        <div className="price-note">Complete party price</div>
      </>
    );
  }
  if (low !== null && high !== null) {
    return (
      <>
        <div className="card-price">
          {money(low, option.currency)}–{money(high, option.currency)}
        </div>
        <div className="price-note">Estimated party-price range</div>
      </>
    );
  }
  if (low !== null) {
    return (
      <>
        <div className="card-price">From {money(low, option.currency)}</div>
        <div className="price-note">Upper baggage cost not yet known</div>
      </>
    );
  }
  return (
    <>
      <div className="card-price">{money(option.base_price, option.currency)}</div>
      <div className="price-note">Base fare · baggage cost not fully priced</div>
    </>
  );
}
