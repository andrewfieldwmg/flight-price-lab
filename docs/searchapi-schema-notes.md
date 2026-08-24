# SearchAPI capture schema notes

Source capture: `20260824T103652Z_LGW_MXP_2026-12-18.json`. These notes describe
only fields observed in that response; opaque token values are intentionally omitted.

## Top-level shape

- `search_metadata`: object with `id`, status, UTC `created_at`, timing values, and
  request/HTML/JSON URLs. The search ID and URLs are volatile.
- `search_parameters`: strings for engine, route, currency, locale, date, flight type,
  travel class, stops, sorting, adults, children, both infant counts, and the cheapest
  flights flag. The captured counts are 2 adults, 2 children, and zero infants.
- `other_flights`: 11 result groups. `best_flights` is absent.
- `airports`: one object containing `departure` and `arrival` lists. Each entry has an
  airport `{id, name}`, city, country, country code, image, and thumbnail.
- `airlines`: an object containing lists of `{code, name}` objects under `alliances`,
  `airlines`, and `hubs`.
- `baggage_allowance_links`: four `{airline_code, airline, url}` objects.
- `passenger_assistance_links`: four `{airline_code, airline, url}` objects.

`price_insights`, `best_flights`, and `departure_token` were not observed.

## Result-group shape

Every group has `flights`, `total_duration` (minutes), `carbon_emissions`, integer
`price`, `type` (`One way`), `extensions`, `airline_logo`, and an opaque
`booking_token`. Four connected groups additionally have a one-item `layovers` list.
Each layover has duration in minutes, airport name, and IATA ID; the overnight Zurich
layover also has `is_overnight: true`.

No explicit separate-ticket or self-transfer indicator was observed. Result extensions
only state that bag and fare conditions depend on the return flight. This is not a
quantified baggage allowance. The top-level baggage links are airline reference links.

## Flight-leg shape

Every leg contains:

- `departure_airport` and `arrival_airport`, each with airport name, IATA ID, local
  date, and local time
- `duration` in minutes
- `airplane`, `airline`, `airline_logo`, `travel_class`, and `flight_number`
- free-text `extensions` and structured `detected_extensions`

Some legs have `is_often_delayed`; Vueling legs have `ticket_also_sold_by`. No leg has
`is_overnight` in this capture (the field occurs on one layover instead). Enrichment
observed but deliberately not modeled includes aircraft, logos, carbon emissions,
seat pitch/type, Wi-Fi, power, streaming entertainment, delay flags, and codeshare sales.

## Nullability and optionality

No explicit JSON nulls occur in the result groups. Optional-by-absence fields include
result `layovers`, leg `is_often_delayed`, leg `ticket_also_sold_by`, and layover
`is_overnight`. `best_flights`, `price_insights`, and all departure tokens are absent.

## Price semantics

SearchAPI Google Flights result-level `price` is treated as
**`SEARCH_PARTY_TOTAL`**. This provider assumption was empirically validated on
2026-08-24 by a controlled comparison of identical LGW to MXP flight fingerprints from
one-adult and two-adult-plus-two-child searches. All 11 offers matched; the median party
price ratio was 3.9787. Ten results were approximately four times the one-adult price,
while one SWISS result was 3.5267 times.

The mapper assigns the result-level value directly to `FlightOffer.total_price`, records
the searched `passenger_count`, and does not multiply or convert it. The SWISS exception
also demonstrates why a multi-passenger total cannot safely be derived by multiplying a
single-passenger fare: individual passenger pricing can differ.

## Validation and open questions

The mapper records a two-leg group as journey structure `CONNECTION` with ticketing type
`UNKNOWN`; co-location in one provider result is not evidence of ticket protection. It
verifies airport continuity, strict chronological order, layover airport agreement, and
duration agreement within one minute. The tolerance covers minute-precision displayed
timestamps.

Booking and departure tokens are volatile provider-action metadata only. They never
participate in schedule fingerprints, deduplication, or stable provider identifiers.
Changing a token therefore cannot change canonical offer identity.

Airport-local timestamps are accepted only when they map to exactly one instant in the
airport's IANA timezone. Nonexistent spring-forward wall times and ambiguous fall-back
wall times are rejected; the mapper does not silently select a `fold` value.

Before expanding the integration, identify the exact separate-ticket schema, learn whether
`best_flights` differs structurally, decide whether booking tokens are stable enough for
any purpose, and establish behavior for ambiguous/nonexistent local times around DST.
