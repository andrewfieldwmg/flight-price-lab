"""Print deduplicated SearchAPI searches for candidate-hub routing; executes none."""

import argparse
from datetime import date
from pathlib import Path

from flight_price_lab.routing.availability import load_route_availability
from flight_price_lab.routing.planning import RoutePlan, plan_provider_searches


def _airports(value: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in value.split(",") if part.strip())


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origins", type=_airports, required=True)
    parser.add_argument("--destinations", type=_airports, required=True)
    parser.add_argument("--hubs", type=_airports, required=True)
    parser.add_argument("--date", type=_date, required=True)
    parser.add_argument("--adults", type=int, required=True)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--currency", default="GBP")
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/searchapi"))
    args = parser.parse_args()

    route_plan = RoutePlan(
        origin_airports=args.origins,
        destination_airports=args.destinations,
        candidate_hubs=args.hubs,
        travel_date=args.date,
        adults=args.adults,
        children=args.children,
        currency=args.currency,
    )
    availability = load_route_availability(
        args.raw_root, travel_date=route_plan.travel_date
    )
    searches = plan_provider_searches(route_plan, availability)
    print(f"Unique API searches required: {len(searches)}")
    print(f"Estimated quota consumption: {len(searches)} requests")
    for index, search in enumerate(searches, start=1):
        parameters = search.as_searchapi_arguments()
        print(
            f"{index}. {parameters['departure_id']} -> {parameters['arrival_id']} | "
            f"{parameters['outbound_date']} | nonstop | "
            f"{parameters['adults']}A+{parameters['children']}C | "
            f"{parameters['currency']}"
        )


if __name__ == "__main__":
    main()
