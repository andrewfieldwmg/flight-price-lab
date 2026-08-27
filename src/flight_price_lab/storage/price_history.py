"""Observation-based price history capture and comparison enrichment."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from flight_price_lab.api.models import (
    HistoryStatus,
    PriceHistoryComparison,
    SearchSnapshot,
    TripOption,
    TripSearchRequest,
)
from flight_price_lab.models.flight import FlightOffer
from flight_price_lab.storage.database import (
    FlightObservation,
    SearchObservationRun,
    TripOptionObservation,
)


def _comparison(
    current_price: Decimal,
    previous_price: Decimal | None,
    previous_at: datetime | None,
    previous_run_id: str | None,
    now: datetime,
) -> PriceHistoryComparison:
    if previous_price is None or previous_at is None:
        return PriceHistoryComparison(history_status=HistoryStatus.FIRST_SEEN)
    change = current_price - previous_price
    percent = (
        (change / previous_price * 100).quantize(Decimal("0.01"))
        if previous_price
        else None
    )
    return PriceHistoryComparison(
        history_status=HistoryStatus.PREVIOUS_FOUND,
        previous_price=previous_price,
        price_change_amount=change,
        price_change_percent=percent,
        previous_observed_at=previous_at,
        elapsed_seconds=max(0, int((now - previous_at).total_seconds())),
        previous_observation_run_id=previous_run_id,
    )


def _all_options(snapshot: SearchSnapshot) -> list[TripOption]:
    options: dict[tuple[str, str], TripOption] = {}
    directions = [snapshot.outbound]
    if snapshot.return_ is not None:
        directions.append(snapshot.return_)
    for results in directions:
        for option in (*results.nonstop_options, *results.feasible_options):
            options[(option.direction.value, option.id)] = option
    return list(options.values())


class PriceHistoryStore:
    """Persist actual live observations and enrich options from prior observations."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def capture_and_enrich(
        self,
        snapshot: SearchSnapshot,
        request: TripSearchRequest,
        live_offers: tuple[FlightOffer, ...],
        *,
        write_observation: bool,
        observed_at: datetime | None = None,
    ) -> SearchSnapshot:
        now = observed_at or datetime.now(UTC)
        options = _all_options(snapshot)
        run_id = uuid4().hex if write_observation else None
        with Session(self.engine) as session:
            option_history = self._previous_trips(session, options, request)
            constituent_fingerprints = {
                fingerprint
                for option in options
                for fingerprint in option.constituent_fingerprints
            }
            offer_history = self._previous_offers(
                session, constituent_fingerprints, request
            )
            enriched = {
                (option.direction.value, option.id): self._enrich_option(
                    option,
                    option_history[(option.direction.value, option.id)],
                    offer_history,
                    now,
                )
                for option in options
            }
            self._replace_snapshot_options(snapshot, enriched)
            if write_observation and run_id is not None:
                self._insert_run(session, run_id, snapshot, request, now)
                session.flush()
                self._insert_offers(
                    session, run_id, snapshot, request, live_offers, now
                )
                self._insert_options(session, run_id, request, options, now)
                session.commit()
        return snapshot

    @staticmethod
    def _previous_trips(
        session: Session, options: list[TripOption], request: TripSearchRequest
    ) -> dict[tuple[str, str], TripOptionObservation | None]:
        keys = {(option.direction.value, option.id) for option in options}
        if not keys:
            return {}
        rows = session.scalars(
            select(TripOptionObservation)
            .where(
                TripOptionObservation.trip_option_fingerprint.in_(
                    {option.id for option in options}
                ),
                TripOptionObservation.direction.in_(
                    {option.direction.value for option in options}
                ),
                TripOptionObservation.passenger_count
                == request.adults + request.children,
                TripOptionObservation.adults == request.adults,
                TripOptionObservation.children == request.children,
                TripOptionObservation.currency == request.currency,
            )
            .order_by(desc(TripOptionObservation.observed_at))
        ).all()
        latest: dict[tuple[str, str], TripOptionObservation | None] = {
            key: None for key in keys
        }
        for row in rows:
            key = (row.direction, row.trip_option_fingerprint)
            if key in latest and latest[key] is None:
                latest[key] = row
        return latest

    @staticmethod
    def _previous_offers(
        session: Session, fingerprints: set[str], request: TripSearchRequest
    ) -> dict[str, FlightObservation | None]:
        if not fingerprints:
            return {}
        rows = session.scalars(
            select(FlightObservation)
            .where(
                FlightObservation.offer_fingerprint.in_(fingerprints),
                FlightObservation.passenger_count == request.adults + request.children,
                FlightObservation.adults == request.adults,
                FlightObservation.children == request.children,
                FlightObservation.currency == request.currency,
            )
            .order_by(desc(FlightObservation.observed_at))
        ).all()
        latest: dict[str, FlightObservation | None] = {
            fingerprint: None for fingerprint in fingerprints
        }
        for row in rows:
            fingerprint = row.offer_fingerprint
            if fingerprint is not None and latest[fingerprint] is None:
                latest[fingerprint] = row
        return latest

    @staticmethod
    def _enrich_option(
        option: TripOption,
        prior: TripOptionObservation | None,
        offer_history: dict[str, FlightObservation | None],
        now: datetime,
    ) -> TripOption:
        option_comparison = _comparison(
            option.base_price,
            prior.base_price if prior else None,
            prior.observed_at if prior else None,
            prior.observation_run_id if prior else None,
            now,
        )
        legs = []
        for leg in option.legs:
            previous = (
                offer_history.get(leg.constituent_fingerprint)
                if leg.constituent_fingerprint
                else None
            )
            legs.append(
                leg.model_copy(
                    update={
                        "history": _comparison(
                            leg.constituent_price or Decimal(),
                            previous.total_price if previous else None,
                            previous.observed_at if previous else None,
                            previous.observation_run_id if previous else None,
                            now,
                        )
                    }
                )
            )
        return option.model_copy(update={"history": option_comparison, "legs": legs})

    @staticmethod
    def _replace_snapshot_options(
        snapshot: SearchSnapshot, enriched: dict[tuple[str, str], TripOption]
    ) -> None:
        for results in (snapshot.outbound, snapshot.return_):
            if results is None:
                continue

            def replace(option: TripOption | None) -> TripOption | None:
                if option is None:
                    return None
                return enriched.get((option.direction.value, option.id), option)

            results.baseline = replace(results.baseline)
            results.nonstop_options = [
                replace(item) for item in results.nonstop_options
            ]  # type: ignore[list-item]
            results.feasible_options = [
                replace(item) for item in results.feasible_options
            ]  # type: ignore[list-item]
            results.pareto_frontier = [
                replace(item) for item in results.pareto_frontier
            ]  # type: ignore[list-item]
            results.cheapest_feasible = replace(results.cheapest_feasible)
            results.fastest_feasible = replace(results.fastest_feasible)

    @staticmethod
    def _insert_run(
        session: Session,
        run_id: str,
        snapshot: SearchSnapshot,
        request: TripSearchRequest,
        observed_at: datetime,
    ) -> None:
        session.add(
            SearchObservationRun(
                id=run_id,
                search_id=snapshot.search_id,
                search_key=snapshot.search_key,
                observed_at=observed_at,
                source="LIVE_SEARCH",
                adults=request.adults,
                children=request.children,
                passenger_count=request.adults + request.children,
                currency=request.currency,
            )
        )

    @staticmethod
    def _insert_offers(
        session: Session,
        run_id: str,
        snapshot: SearchSnapshot,
        request: TripSearchRequest,
        offers: tuple[FlightOffer, ...],
        observed_at: datetime,
    ) -> None:
        unique = {offer.fingerprint: offer for offer in offers}
        for offer in unique.values():
            first = offer.legs[0]
            last = offer.legs[-1]
            session.add(
                FlightObservation(
                    observation_run_id=run_id,
                    offer_fingerprint=offer.fingerprint,
                    flight_fingerprint=offer.fingerprint,
                    observed_at=observed_at,
                    carrier=first.flight_number.split()[0].upper(),
                    flight_number=first.flight_number,
                    origin=first.origin,
                    destination=last.destination,
                    departure_at=first.departure,
                    arrival_at=last.arrival,
                    price=offer.total_price,
                    total_price=offer.total_price,
                    currency=offer.currency,
                    search_id=snapshot.search_id,
                    adults=request.adults,
                    children=request.children,
                    passenger_count=request.adults + request.children,
                )
            )

    @staticmethod
    def _insert_options(
        session: Session,
        run_id: str,
        request: TripSearchRequest,
        options: list[TripOption],
        observed_at: datetime,
    ) -> None:
        for option in options:
            session.add(
                TripOptionObservation(
                    observation_run_id=run_id,
                    trip_option_fingerprint=option.id,
                    direction=option.direction.value,
                    observed_at=observed_at,
                    base_price=option.base_price,
                    currency=option.currency,
                    adults=request.adults,
                    children=request.children,
                    passenger_count=request.adults + request.children,
                    is_nonstop=option.is_nonstop,
                    is_self_transfer=option.is_self_transfer,
                    constituent_fingerprints_json=json.dumps(
                        option.constituent_fingerprints, separators=(",", ":")
                    ),
                )
            )
