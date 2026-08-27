"""Observation-based price history capture and comparison enrichment."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from flight_price_lab.api.models import (
    DailyPricePoint,
    HistoryStatus,
    ObservedPricePoint,
    PriceHistoryComparison,
    SearchSnapshot,
    TrendStatus,
    TripOption,
    TripSearchRequest,
)
from flight_price_lab.models.flight import FlightOffer
from flight_price_lab.storage.database import (
    FlightObservation,
    SearchObservationRun,
    TripOptionObservation,
)

LONDON = ZoneInfo("Europe/London")


@dataclass(frozen=True)
class TrendThresholds:
    max_series_days: int = 7
    classification_days: int = 5
    minimum_observed_days: int = 3
    flat_change_percent: Decimal = Decimal(2)
    minimum_direction_consistency: Decimal = Decimal("0.5")


TREND_THRESHOLDS = TrendThresholds()


@dataclass
class HydratedHistory:
    canonical: list[TripOptionObservation | FlightObservation]
    visual: list[ObservedPricePoint]


def _london_day_difference(current_at: datetime, previous_at: datetime) -> int:
    """Return the calendar-day distance in Europe/London."""
    if current_at.tzinfo is None:
        current_at = current_at.replace(tzinfo=UTC)
    if previous_at.tzinfo is None:
        previous_at = previous_at.replace(tzinfo=UTC)
    return (current_at.astimezone(LONDON).date() - previous_at.astimezone(LONDON).date()).days


def _as_london_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(LONDON).date()


def _as_aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _series_with_current(
    history: Iterable[tuple[datetime, Decimal]],
    current_at: datetime,
    current_price: Decimal,
) -> list[DailyPricePoint]:
    points = [
        DailyPricePoint(date=_as_london_date(observed_at), price=price)
        for observed_at, price in history
    ]
    points.reverse()
    points.append(
        DailyPricePoint(date=_as_london_date(current_at), price=current_price)
    )
    return points[-TREND_THRESHOLDS.max_series_days :]


def _visual_series_with_current(
    history: list[ObservedPricePoint],
    current_at: datetime,
    current_price: Decimal,
) -> list[ObservedPricePoint]:
    points = [*history]
    current = ObservedPricePoint(observed_at=current_at, price=current_price)
    if not points or points[-1] != current:
        points.append(current)
    points.sort(key=lambda point: point.observed_at)
    if len(points) <= 12:
        return points
    interior = points[1:-1]
    selected: list[ObservedPricePoint] = [points[0]]
    for bucket_index in range(5):
        start = bucket_index * len(interior) // 5
        end = (bucket_index + 1) * len(interior) // 5
        bucket = interior[start:end]
        if not bucket:
            continue
        extrema = {min(bucket, key=lambda point: point.price), max(bucket, key=lambda point: point.price)}
        selected.extend(sorted(extrema, key=lambda point: point.observed_at))
    selected.append(points[-1])
    return selected[:11] + [points[-1]] if len(selected) > 12 else selected


def _comparison(
    current_price: Decimal,
    previous_price: Decimal | None,
    previous_at: datetime | None,
    previous_run_id: str | None,
    now: datetime,
    series: list[DailyPricePoint],
    visual_series: list[ObservedPricePoint],
) -> PriceHistoryComparison:
    trend = _trend_fields(series)
    trend["visual_series"] = visual_series
    if previous_price is None or previous_at is None:
        return PriceHistoryComparison(
            history_status=HistoryStatus.FIRST_SEEN, **trend
        )
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
        day_difference=_london_day_difference(now, previous_at),
        previous_observation_run_id=previous_run_id,
        **trend,
    )


def _trend_fields(series: list[DailyPricePoint]) -> dict[str, object]:
    count = len(series)
    base: dict[str, object] = {
        "trend_status": TrendStatus.INSUFFICIENT_HISTORY,
        "observed_day_count": count,
        "daily_series": series,
    }
    if count < TREND_THRESHOLDS.minimum_observed_days:
        return base
    trend_series = series[-TREND_THRESHOLDS.classification_days :]
    first, last = trend_series[0], trend_series[-1]
    span = (last.date - first.date).days
    change = last.price - first.price
    percent = change / first.price * 100 if first.price else None
    trend_count = len(trend_series)
    offsets = [Decimal((point.date - first.date).days) for point in trend_series]
    normalized = [point.price / first.price * 100 for point in trend_series]
    mean_x = sum(offsets) / Decimal(trend_count)
    mean_y = sum(normalized) / Decimal(trend_count)
    denominator = sum((value - mean_x) ** 2 for value in offsets)
    slope = (
        sum((x - mean_x) * (y - mean_y) for x, y in zip(offsets, normalized, strict=True))
        / denominator
        if denominator
        else Decimal()
    )
    overall_sign = 1 if change > 0 else -1 if change < 0 else 0
    movements = [right.price - left.price for left, right in pairwise(trend_series)]
    agreeing = sum(
        1
        for movement in movements
        if (movement > 0 and overall_sign > 0) or (movement < 0 and overall_sign < 0)
    )
    consistency = Decimal(agreeing) / Decimal(len(movements))
    status = TrendStatus.FLAT
    if percent is not None and abs(percent) >= TREND_THRESHOLDS.flat_change_percent:
        if change > 0 and slope > 0 and consistency >= TREND_THRESHOLDS.minimum_direction_consistency:
            status = TrendStatus.RISING
        elif change < 0 and slope < 0 and consistency >= TREND_THRESHOLDS.minimum_direction_consistency:
            status = TrendStatus.FALLING
    return {
        **base,
        "trend_status": status,
        "trend_start_price": first.price,
        "trend_current_price": last.price,
        "trend_change_amount": change,
        "trend_change_percent": percent.quantize(Decimal("0.01")) if percent is not None else None,
        "trend_first_date": first.date,
        "trend_last_date": last.date,
        "trend_span_days": span,
        "price_slope_per_day": slope.quantize(Decimal("0.0001")),
        "direction_consistency": consistency.quantize(Decimal("0.01")),
    }


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
            option_history = self._trip_history_series(
                session, options, request, now
            )
            constituent_fingerprints = {
                fingerprint
                for option in options
                for fingerprint in option.constituent_fingerprints
            }
            offer_history = self._offer_history_series(
                session, constituent_fingerprints, request, now
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
    def _trip_history_series(
        session: Session,
        options: list[TripOption],
        request: TripSearchRequest,
        current_at: datetime,
    ) -> dict[tuple[str, str], HydratedHistory]:
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
                TripOptionObservation.observed_at <= current_at,
            )
            .order_by(TripOptionObservation.observed_at.desc())
        ).all()
        latest: dict[tuple[str, str], HydratedHistory] = {
            key: HydratedHistory(canonical=[], visual=[]) for key in keys
        }
        seen: dict[tuple[str, str], set[date]] = {key: set() for key in keys}
        current_day = _as_london_date(current_at)
        visual_start = current_day - timedelta(days=6)
        for row in rows:
            key = (row.direction, row.trip_option_fingerprint)
            local_day = _as_london_date(row.observed_at)
            if key not in latest:
                continue
            if visual_start <= local_day <= current_day:
                latest[key].visual.append(
                    ObservedPricePoint(
                        observed_at=_as_aware_utc(row.observed_at), price=row.base_price
                    )
                )
            if local_day < current_day and local_day not in seen[key]:
                latest[key].canonical.append(row)
                seen[key].add(local_day)
        for value in latest.values():
            value.canonical = value.canonical[
                : TREND_THRESHOLDS.max_series_days - 1
            ]
            value.visual.reverse()
        return latest

    @staticmethod
    def _offer_history_series(
        session: Session,
        fingerprints: set[str],
        request: TripSearchRequest,
        current_at: datetime,
    ) -> dict[str, HydratedHistory]:
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
                FlightObservation.observed_at <= current_at,
            )
            .order_by(FlightObservation.observed_at.desc())
        ).all()
        latest: dict[str, HydratedHistory] = {
            fingerprint: HydratedHistory(canonical=[], visual=[])
            for fingerprint in fingerprints
        }
        seen: dict[str, set[date]] = {fingerprint: set() for fingerprint in fingerprints}
        current_day = _as_london_date(current_at)
        visual_start = current_day - timedelta(days=6)
        for row in rows:
            fingerprint = row.offer_fingerprint
            if fingerprint is None:
                continue
            local_day = _as_london_date(row.observed_at)
            if visual_start <= local_day <= current_day:
                latest[fingerprint].visual.append(
                    ObservedPricePoint(
                        observed_at=_as_aware_utc(row.observed_at),
                        price=row.total_price or Decimal(),
                    )
                )
            if local_day < current_day and local_day not in seen[fingerprint]:
                latest[fingerprint].canonical.append(row)
                seen[fingerprint].add(local_day)
        for value in latest.values():
            value.canonical = value.canonical[
                : TREND_THRESHOLDS.max_series_days - 1
            ]
            value.visual.reverse()
        return latest

    @staticmethod
    def _enrich_option(
        option: TripOption,
        history: HydratedHistory,
        offer_history: dict[str, HydratedHistory],
        now: datetime,
    ) -> TripOption:
        prior_history = history.canonical
        prior = prior_history[0] if prior_history else None
        option_comparison = _comparison(
            option.base_price,
            prior.base_price if prior else None,
            prior.observed_at if prior else None,
            prior.observation_run_id if prior else None,
            now,
            _series_with_current(
                ((row.observed_at, row.base_price) for row in prior_history),
                now,
                option.base_price,
            ),
            _visual_series_with_current(history.visual, now, option.base_price),
        )
        legs = []
        for leg in option.legs:
            leg_history = (
                offer_history.get(leg.constituent_fingerprint)
                if leg.constituent_fingerprint
                else None
            ) or HydratedHistory(canonical=[], visual=[])
            previous_history = leg_history.canonical
            previous = previous_history[0] if previous_history else None
            legs.append(
                leg.model_copy(
                    update={
                        "history": _comparison(
                            leg.constituent_price or Decimal(),
                            previous.total_price if previous else None,
                            previous.observed_at if previous else None,
                            previous.observation_run_id if previous else None,
                            now,
                            _series_with_current(
                                (
                                    (row.observed_at, row.total_price or Decimal())
                                    for row in previous_history
                                ),
                                now,
                                leg.constituent_price or Decimal(),
                            ),
                            _visual_series_with_current(
                                leg_history.visual,
                                now,
                                leg.constituent_price or Decimal(),
                            ),
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
