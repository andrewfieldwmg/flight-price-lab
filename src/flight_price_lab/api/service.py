"""Progressive trip-search orchestration over existing domain services."""

import asyncio
import json
import math
import statistics
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from time import perf_counter
from uuid import uuid4

from flight_price_lab.analytics.carrier_baggage import (
    carrier_codes,
    estimate_offer_baggage,
    load_carrier_baggage_rules,
    price_itinerary_from_carrier_rules,
)
from flight_price_lab.api.models import (
    BaggageEstimate,
    Direction,
    DirectionResults,
    DirectionTimeWindow,
    PriceCompleteness,
    SearchError,
    SearchSnapshot,
    SearchStatus,
    SelfTransferPolicy,
    TripLegSummary,
    TripOption,
    TripSearchRequest,
)
from flight_price_lab.api.provider import ProviderGateway, ProviderSearchResult
from flight_price_lab.api.registry import SearchRegistry
from flight_price_lab.api.search_logging import (
    development_diagnostics_enabled,
    search_log,
)
from flight_price_lab.models import (
    BaggageRequirement,
    ConstructedItinerary,
    FlightOffer,
)
from flight_price_lab.models.carrier_baggage import AncillaryEstimateStatus
from flight_price_lab.providers.searchapi import SearchAPIError
from flight_price_lab.routing.airport_groups import INITIAL_CANDIDATE_HUBS
from flight_price_lab.routing.feasibility import BaggageProfile, SelfTransferProfile
from flight_price_lab.routing.hub_synthesis import synthesize_via_hubs
from flight_price_lab.routing.planning import RoutePlan
from flight_price_lab.storage.database import (
    canonical_search_json,
    canonical_search_key,
)


def _price_completeness(status: AncillaryEstimateStatus) -> PriceCompleteness:
    return {
        AncillaryEstimateStatus.COMPLETE: PriceCompleteness.COMPLETE,
        AncillaryEstimateStatus.PARTIAL: PriceCompleteness.PARTIAL,
        AncillaryEstimateStatus.UNKNOWN: PriceCompleteness.UNKNOWN,
    }[status]


def _minutes(delta: object) -> int:
    return int(delta.total_seconds() // 60)  # type: ignore[union-attr]


def matches_time_window(
    departure: datetime, arrival: datetime, window: DirectionTimeWindow
) -> bool:
    """Compare complete-journey endpoints using their airport-local wall times."""

    departure_time = departure.timetz().replace(tzinfo=None)
    arrival_time = arrival.timetz().replace(tzinfo=None)
    return not (
        window.earliest_departure_time is not None
        and departure_time < window.earliest_departure_time
    ) and not (
        window.latest_arrival_time is not None
        and arrival_time > window.latest_arrival_time
    )


def trip_search_parameters(request: TripSearchRequest) -> dict[str, object]:
    """Return provider-affecting identity inputs without UI-only state."""
    profile_minimum = {
        "CONSERVATIVE": 120,
        "STANDARD": 120,
        "AGGRESSIVE": 120,
    }[request.connection_profile.value]
    parameters: dict[str, object] = {
        "origins": request.origins,
        "destinations": request.destinations,
        "date": request.outbound_date.isoformat(),
        "return_date": (
            request.return_date.isoformat() if request.return_date is not None else None
        ),
        "adults": request.adults,
        "children": request.children,
        "currency": request.currency,
        "flight_type": "round_trip" if request.return_date else "one_way",
        "stops": (
            "nonstop"
            if request.self_transfer_policy is SelfTransferPolicy.NONE
            else "one_stop_or_fewer"
        ),
        "stop_policy": request.self_transfer_policy.value,
    }
    if request.self_transfer_policy is not SelfTransferPolicy.NONE:
        parameters["included_connecting_airports"] = list(INITIAL_CANDIDATE_HUBS)
        parameters["layover_duration_min"] = profile_minimum
        parameters["layover_duration_max"] = (
            request.outbound_time_window.max_connection_minutes
        )
    if request.baggage.cabin_bags:
        parameters["carry_on_bags"] = request.baggage.cabin_bags
    if request.baggage.checked_bags:
        parameters["checked_bags"] = request.baggage.checked_bags
    if request.return_date is not None and request.self_transfer_policy in (
        SelfTransferPolicy.RETURN_ONLY,
        SelfTransferPolicy.BOTH,
    ):
        parameters["return_layover_duration_max"] = (
            request.return_time_window.max_connection_minutes
        )
    return parameters


def trip_search_key(request: TripSearchRequest) -> str:
    """Identify equivalent user-visible searches without UI-only state."""

    return canonical_search_key(trip_search_parameters(request))


class TripSearchService:
    def __init__(
        self,
        provider: ProviderGateway,
        registry: SearchRegistry,
        *,
        hubs: tuple[str, ...] = INITIAL_CANDIDATE_HUBS,
        max_concurrency: int = 4,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.hubs = hubs
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._carrier_rules = load_carrier_baggage_rules()
        self._provider_tasks: dict[
            tuple[object, ...], asyncio.Task[list[FlightOffer]]
        ] = {}
        self._active_snapshot: SearchSnapshot | None = None
        self._active_provider_calls = 0
        self._provider_calls_concurrent_peak = 0
        self._checkpoint_lock = asyncio.Lock()
        self._last_checkpoint_clock = 0.0
        self._directions_remaining = 0

    async def start(self, request: TripSearchRequest) -> str:
        trip_id = await self.create(request)
        asyncio.create_task(self.run(trip_id, request))
        return trip_id

    async def create(self, request: TripSearchRequest) -> str:
        trip_id = uuid4().hex
        search_key = trip_search_key(request)
        snapshot = SearchSnapshot(
            search_id=trip_id,
            trip_id=trip_id,
            search_key=search_key,
            status=SearchStatus.STARTED,
            diagnostics={"trip_id": trip_id, "search_key": search_key},
        )
        fields: dict[str, object] = {
            "trip_id": trip_id,
            "search_key": search_key,
            "search_key_short": search_key[:12],
            "cache_bypass": request.refresh_prices,
            "request_summary": {
                "origins": sorted(request.origins),
                "destinations": sorted(request.destinations),
                "outbound_date": request.outbound_date,
                "return_date": request.return_date,
                "passengers": request.adults + request.children,
                "currency": request.currency,
                "self_transfer_policy": request.self_transfer_policy,
            },
        }
        if development_diagnostics_enabled():
            fields["canonical_request"] = json.loads(
                canonical_search_json(trip_search_parameters(request))
            )
        search_log("SEARCH_RECEIVED", **fields)
        await self.registry.create(snapshot, request)
        return trip_id

    async def _event(self, session_id: str, name: str, **data: object) -> None:
        snapshot = await self.registry.get(session_id)
        if snapshot is not None:
            data["snapshot"] = snapshot.model_dump(mode="json", by_alias=True)
        await self.registry.publish(session_id, name, data)

    async def run(self, search_id: str, request: TripSearchRequest) -> None:
        search_clock = perf_counter()
        snapshot = await self.registry.get(search_id)
        assert snapshot is not None
        snapshot.status = SearchStatus.RUNNING
        snapshot.diagnostics.search_started_at = datetime.now(UTC)
        self._active_snapshot = snapshot
        await self.registry.update(
            snapshot, persist=True, operation="update_running_session"
        )
        self._last_checkpoint_clock = perf_counter()
        await self._event(
            search_id,
            "search_started",
            search_id=search_id,
            search_key=snapshot.search_key,
        )
        try:
            direction_tasks = [
                self._direction(
                    snapshot,
                    request,
                    Direction.OUTBOUND,
                    tuple(request.origins),
                    tuple(request.destinations),
                    request.outbound_date,
                    self._self_transfer_enabled(request, Direction.OUTBOUND),
                )
            ]
            if request.return_date is not None:
                snapshot.return_ = DirectionResults()
                direction_tasks.append(
                    self._direction(
                        snapshot,
                        request,
                        Direction.RETURN,
                        tuple(request.destinations),
                        tuple(request.origins),
                        request.return_date,
                        self._self_transfer_enabled(request, Direction.RETURN),
                    )
                )
            self._directions_remaining = len(direction_tasks)
            await asyncio.gather(*direction_tasks)
            snapshot.status = (
                SearchStatus.PARTIAL_FAILURE
                if snapshot.errors
                else SearchStatus.COMPLETED
            )
            snapshot.diagnostics.original_provider_calls = (
                snapshot.diagnostics.provider_calls_this_invocation
            )
            snapshot.diagnostics.original_search_completed_at = (
                datetime.now().astimezone()
            )
            snapshot.diagnostics.search_completed_at = datetime.now(UTC)
            snapshot.diagnostics.total_duration_ms = (
                perf_counter() - search_clock
            ) * 1000
            snapshot.diagnostics.provider_calls_total = (
                snapshot.diagnostics.provider_calls_this_invocation
            )
            snapshot.diagnostics.provider_calls_concurrent_peak = (
                self._provider_calls_concurrent_peak
            )
            live_durations = [
                float(item["duration_ms"])
                for item in snapshot.diagnostics.provider_requests
                if not bool(item.get("cache_hit"))
            ]
            if live_durations:
                ordered = sorted(live_durations)
                snapshot.diagnostics.slowest_provider_call_ms = ordered[-1]
                snapshot.diagnostics.median_provider_call_ms = statistics.median(
                    ordered
                )
                snapshot.diagnostics.p95_provider_call_ms = ordered[
                    max(0, math.ceil(len(ordered) * 0.95) - 1)
                ]
            serialization_clock = perf_counter()
            snapshot.model_dump_json(by_alias=True)
            snapshot.diagnostics.final_serialization_ms = (
                perf_counter() - serialization_clock
            ) * 1000
            await self.registry.update(
                snapshot, persist=True, operation="persist_final_result"
            )
            await self._event(
                search_id, "search_completed", status=snapshot.status.value
            )
            search_log(
                "SEARCH_COMPLETED",
                trip_id=snapshot.trip_id,
                search_key=snapshot.search_key,
                status=snapshot.status.value,
                diagnostics=snapshot.diagnostics.model_dump(),
            )
        except Exception as error:  # noqa: BLE001  # defensive task boundary
            snapshot.status = SearchStatus.FAILED
            snapshot.errors.append(
                SearchError(code="provider_error", message=type(error).__name__)
            )
            await self.registry.update(
                snapshot, persist=True, operation="persist_failed_result"
            )
            await self._event(search_id, "search_failed", code="provider_error")
        finally:
            self.registry.close_persistence(search_id)

    async def _checkpoint_if_due(self, snapshot: SearchSnapshot) -> None:
        async with self._checkpoint_lock:
            now = perf_counter()
            if now - self._last_checkpoint_clock < 5:
                return
            await self.registry.update(
                snapshot, persist=True, operation="persist_partial_snapshot"
            )
            self._last_checkpoint_clock = now

    async def _checkpoint_direction_complete(
        self, snapshot: SearchSnapshot, direction: Direction
    ) -> None:
        async with self._checkpoint_lock:
            self._directions_remaining -= 1
            if self._directions_remaining <= 0:
                return
            await self.registry.update(
                snapshot,
                persist=True,
                operation=f"persist_{direction.value.lower()}_complete",
            )
            self._last_checkpoint_clock = perf_counter()

    @staticmethod
    def _self_transfer_enabled(
        request: TripSearchRequest, direction: Direction
    ) -> bool:
        policy = request.self_transfer_policy
        return (
            policy is SelfTransferPolicy.BOTH
            or (
                direction is Direction.OUTBOUND
                and policy is SelfTransferPolicy.OUTBOUND_ONLY
            )
            or (
                direction is Direction.RETURN
                and policy is SelfTransferPolicy.RETURN_ONLY
            )
        )

    async def _provider_search(
        self,
        request: TripSearchRequest,
        origins: tuple[str, ...],
        destinations: tuple[str, ...],
        travel_date: date,
        *,
        direction: Direction = Direction.OUTBOUND,
        query_type: str = "unspecified",
        hub: str | None = None,
    ) -> list[FlightOffer]:
        key: tuple[object, ...] = (
            tuple(sorted(origins)),
            tuple(sorted(destinations)),
            travel_date,
            request.adults,
            request.children,
            request.currency,
            request.baggage.cabin_bags,
            request.baggage.checked_bags,
        )
        task = self._provider_tasks.get(key)
        if task is None:
            task = asyncio.create_task(
                self._execute_provider_search(
                    request,
                    origins,
                    destinations,
                    travel_date,
                    direction=direction,
                    query_type=query_type,
                    hub=hub,
                )
            )
            self._provider_tasks[key] = task
        else:
            snapshot = self._active_snapshot
            if snapshot is not None:
                snapshot.diagnostics.provider_calls_avoided_this_invocation += 1
                search_log(
                    "PROVIDER_CALL_SKIPPED_CACHE",
                    trip_id=snapshot.trip_id,
                    search_key=snapshot.search_key,
                    reason="within_search_deduplication",
                )
        return await task

    async def _execute_provider_search(
        self,
        request: TripSearchRequest,
        origins: tuple[str, ...],
        destinations: tuple[str, ...],
        travel_date: date,
        *,
        direction: Direction,
        query_type: str,
        hub: str | None,
    ) -> list[FlightOffer]:
        async with self._semaphore:
            self._active_provider_calls += 1
            self._provider_calls_concurrent_peak = max(
                self._provider_calls_concurrent_peak, self._active_provider_calls
            )
            try:
                response = await self.provider.search_direct(
                    origins=origins,
                    destinations=destinations,
                    travel_date=travel_date,
                    adults=request.adults,
                    children=request.children,
                    currency=request.currency,
                    cabin_bags=request.baggage.cabin_bags,
                    checked_bags=request.baggage.checked_bags,
                    bypass_cache=request.refresh_prices,
                    trip_id=(
                        self._active_snapshot.trip_id if self._active_snapshot else ""
                    ),
                    trip_search_key=(
                        self._active_snapshot.search_key
                        if self._active_snapshot
                        else ""
                    ),
                    direction=direction.value,
                    query_type=query_type,
                    hub=hub,
                )
            finally:
                self._active_provider_calls -= 1
        if not isinstance(response, ProviderSearchResult):
            return response
        snapshot = self._active_snapshot
        if snapshot is not None:
            snapshot.diagnostics.provider_calls_this_invocation += (
                response.provider_calls
            )
            snapshot.diagnostics.backend_cache_hits += response.backend_cache_hits
            snapshot.diagnostics.backend_cache_misses += response.backend_cache_misses
            snapshot.diagnostics.provider_calls_avoided_this_invocation += (
                response.provider_calls_avoided
            )
            snapshot.diagnostics.normalization_ms += response.normalization_ms
            snapshot.diagnostics.postgres_write_ms += response.postgres_write_ms
            if response.request_timing is not None:
                snapshot.diagnostics.provider_requests.append(response.request_timing)
            if response.database_operation is not None:
                snapshot.diagnostics.database_operations.append(
                    response.database_operation
                )
            await self.registry.update(snapshot)
        return response.offers

    async def _direction(
        self,
        snapshot: SearchSnapshot,
        request: TripSearchRequest,
        direction: Direction,
        origins: tuple[str, ...],
        destinations: tuple[str, ...],
        travel_date: date,
        self_transfer: bool,
    ) -> None:
        direction_clock = perf_counter()
        results = (
            snapshot.outbound if direction is Direction.OUTBOUND else snapshot.return_
        )
        assert results is not None
        try:
            direct = await self._provider_search(
                request,
                origins,
                destinations,
                travel_date,
                direction=direction,
                query_type="direct_baseline",
            )
        except Exception as error:  # noqa: BLE001  # provider boundary
            snapshot.errors.append(
                SearchError(
                    code=(
                        "provider_timeout"
                        if isinstance(error, TimeoutError)
                        else "provider_error"
                    ),
                    message=type(error).__name__,
                    direction=direction,
                )
            )
            await self._checkpoint_direction_complete(snapshot, direction)
            await self._event(
                snapshot.search_id, "direction_completed", direction=direction.value
            )
            return
        window = (
            request.outbound_time_window
            if direction is Direction.OUTBOUND
            else request.return_time_window
        )
        direct = [
            offer
            for offer in direct
            if matches_time_window(
                offer.legs[0].departure, offer.legs[-1].arrival, window
            )
        ]
        direct_options = sorted(
            (self._direct_option(offer, direction, request) for offer in direct),
            key=lambda item: (item.base_price, item.total_journey_minutes),
        )
        offers_by_id = {offer.fingerprint: offer for offer in direct}
        for option in direct_options:
            await self.registry.register_booking_candidate(
                snapshot.search_id, option.id, (offers_by_id[option.id],)
            )
        results.nonstop_options = direct_options
        baseline_offer = min(direct, key=lambda item: item.total_price, default=None)
        if baseline_offer is None:
            snapshot.errors.append(
                SearchError(
                    code="no_direct_service",
                    message="No nonstop offer was returned",
                    direction=direction,
                )
            )
        else:
            results.baseline = direct_options[0]
            await self.registry.update(snapshot)
            await self._event(
                snapshot.search_id,
                "baseline_found",
                direction=direction.value,
                option_id=results.baseline.id,
            )
            await self._event(
                snapshot.search_id, "results_updated", direction=direction.value
            )
            if self_transfer:
                async with self._checkpoint_lock:
                    await self.registry.update(
                        snapshot,
                        persist=True,
                        operation=f"persist_{direction.value.lower()}_baseline",
                    )
                    self._last_checkpoint_clock = perf_counter()
        direct_duration_ms = (perf_counter() - direction_clock) * 1000
        if direction is Direction.OUTBOUND:
            snapshot.diagnostics.direct_outbound_ms = direct_duration_ms
        else:
            snapshot.diagnostics.direct_return_ms = direct_duration_ms
        if self_transfer:
            hub_clock = perf_counter()
            alternatives = await self._hub_alternatives(
                snapshot, request, direction, origins, destinations, travel_date
            )
            snapshot.diagnostics.hub_search_total_ms += (
                perf_counter() - hub_clock
            ) * 1000
            ranking_clock = perf_counter()
            self._set_rankings(results, alternatives)
            snapshot.diagnostics.ranking_filtering_ms += (
                perf_counter() - ranking_clock
            ) * 1000
            await self.registry.update(snapshot)
            await self._event(
                snapshot.search_id, "results_updated", direction=direction.value
            )
            if not alternatives:
                snapshot.errors.append(
                    SearchError(
                        code="no_feasible_self_transfer",
                        message="No feasible self-transfer was found",
                        direction=direction,
                    )
                )
        await self._checkpoint_direction_complete(snapshot, direction)
        await self._event(
            snapshot.search_id, "direction_completed", direction=direction.value
        )

    async def _hub_alternatives(
        self,
        snapshot: SearchSnapshot,
        request: TripSearchRequest,
        direction: Direction,
        origins: tuple[str, ...],
        destinations: tuple[str, ...],
        travel_date: date,
    ) -> list[TripOption]:
        accumulated: list[TripOption] = []
        update_lock = asyncio.Lock()
        results = (
            snapshot.outbound if direction is Direction.OUTBOUND else snapshot.return_
        )
        assert results is not None
        baseline = results.baseline

        async def search_hub(hub: str) -> list[TripOption]:
            await self._event(
                snapshot.search_id, "hub_started", direction=direction.value, hub=hub
            )
            try:
                first, second = await asyncio.gather(
                    self._provider_search(
                        request,
                        origins,
                        (hub,),
                        travel_date,
                        direction=direction,
                        query_type="hub_first_leg",
                        hub=hub,
                    ),
                    self._provider_search(
                        request,
                        (hub,),
                        destinations,
                        travel_date,
                        direction=direction,
                        query_type="hub_second_leg",
                        hub=hub,
                    ),
                )
                route_plan = RoutePlan(
                    origin_airports=origins,
                    destination_airports=destinations,
                    candidate_hubs=(hub,),
                    travel_date=travel_date,
                    adults=request.adults,
                    children=request.children,
                    currency=request.currency,
                    connection_profile=SelfTransferProfile(
                        request.connection_profile.value.lower()
                    ),
                    baggage_profile=(
                        BaggageProfile.CHECKED_BAG
                        if request.baggage.checked_bags
                        else BaggageProfile.CABIN_BAG
                    ),
                )
                by_route: dict[tuple[str, str], list[FlightOffer]] = {}
                for item in (*first, *second):
                    leg = item.legs[0]
                    by_route.setdefault((leg.origin, leg.destination), []).append(item)
                synthesis_clock = perf_counter()
                synthesized = synthesize_via_hubs(by_route, route_plan)
                snapshot.diagnostics.itinerary_synthesis_ms += (
                    perf_counter() - synthesis_clock
                ) * 1000
                window = (
                    request.outbound_time_window
                    if direction is Direction.OUTBOUND
                    else request.return_time_window
                )
                options = []
                for item in synthesized.itineraries:
                    itinerary = item.itinerary
                    if itinerary.connection_duration is None:
                        continue
                    if (
                        _minutes(itinerary.connection_duration)
                        > window.max_connection_minutes
                    ):
                        continue
                    if not matches_time_window(
                        itinerary.departure, itinerary.final_arrival, window
                    ):
                        continue
                    option = self._with_savings(
                        self._itinerary_option(itinerary, direction, request), baseline
                    )
                    options.append(option)
                    await self.registry.register_booking_candidate(
                        snapshot.search_id, option.id, itinerary.components
                    )
                    if development_diagnostics_enabled():
                        search_log(
                            "SYNTHETIC_BOOKING_LINEAGE_REGISTERED",
                            search_id=snapshot.search_id,
                            selected_option_id=option.id,
                            option_type="synthetic",
                            is_self_transfer=True,
                            ticketing_type=option.ticketing_type.value,
                            number_of_legs=len(option.legs),
                            number_of_constituent_refs=len(itinerary.components),
                            constituent_ids=[
                                offer.fingerprint for offer in itinerary.components
                            ],
                            constituent_flights=[
                                offer.legs[0].flight_number
                                for offer in itinerary.components
                            ],
                        )
                if (
                    request.max_extra_journey_minutes is not None
                    and baseline is not None
                ):
                    options = [
                        option
                        for option in options
                        if option.extra_minutes_vs_nonstop is not None
                        and option.extra_minutes_vs_nonstop
                        <= request.max_extra_journey_minutes
                    ]
                for option in options:
                    await self._event(
                        snapshot.search_id,
                        "alternative_found",
                        direction=direction.value,
                        hub=hub,
                        option_id=option.id,
                    )
                async with update_lock:
                    accumulated.extend(options)
                    ranking_clock = perf_counter()
                    self._set_rankings(results, accumulated)
                    snapshot.diagnostics.ranking_filtering_ms += (
                        perf_counter() - ranking_clock
                    ) * 1000
                    await self.registry.update(snapshot)
                    await self._checkpoint_if_due(snapshot)
                    await self._event(
                        snapshot.search_id,
                        "results_updated",
                        direction=direction.value,
                        hub=hub,
                    )
                await self._event(
                    snapshot.search_id,
                    "hub_completed",
                    direction=direction.value,
                    hub=hub,
                    count=len(options),
                )
                return options
            except Exception as error:  # noqa: BLE001  # one hub must not abort others
                snapshot.errors.append(
                    SearchError(
                        code=(
                            "provider_timeout"
                            if isinstance(error, TimeoutError)
                            else "provider_error"
                            if isinstance(error, SearchAPIError)
                            else "partial_search_failure"
                        ),
                        message=type(error).__name__,
                        direction=direction,
                        hub=hub,
                    )
                )
                await self._event(
                    snapshot.search_id,
                    "hub_completed",
                    direction=direction.value,
                    hub=hub,
                    failed=True,
                )
                return []

        groups = await asyncio.gather(*(search_hub(hub) for hub in self.hubs))
        return [option for group in groups for option in group]

    def _direct_option(
        self, offer: FlightOffer, direction: Direction, request: TripSearchRequest
    ) -> TripOption:
        estimate = estimate_offer_baggage(
            offer,
            BaggageRequirement(
                carry_on_bags=request.baggage.cabin_bags,
                checked_bags=request.baggage.checked_bags,
            ),
            self._carrier_rules,
        )
        leg = offer.legs[0]
        return TripOption(
            id=offer.fingerprint,
            direction=direction,
            route=[leg.origin, leg.destination],
            flight_numbers=[leg.flight_number],
            airlines=[leg.airline],
            legs=[
                TripLegSummary(
                    origin=leg.origin,
                    destination=leg.destination,
                    departure_at=leg.departure,
                    arrival_at=leg.arrival,
                    airline=leg.airline,
                    flight_number=leg.flight_number,
                )
            ],
            base_price=offer.total_price,
            ancillary_price_low=estimate.lower_bound,
            ancillary_price_high=estimate.upper_bound,
            baggage_estimates=[
                BaggageEstimate(
                    ticket_index=1,
                    carrier_codes=list(carrier_codes(offer)),
                    flight_numbers=[item.flight_number for item in offer.legs],
                    price_low=estimate.lower_bound,
                    price_high=estimate.upper_bound,
                    completeness=_price_completeness(estimate.completeness_status),
                    confidence=estimate.confidence.value,
                )
            ],
            cabin_bags=request.baggage.cabin_bags,
            checked_bags=request.baggage.checked_bags,
            effective_price_low=(
                offer.total_price + estimate.lower_bound
                if estimate.lower_bound is not None
                else None
            ),
            effective_price_high=(
                offer.total_price + estimate.upper_bound
                if estimate.upper_bound is not None
                else None
            ),
            currency=offer.currency,
            price_completeness=_price_completeness(estimate.completeness_status),
            is_nonstop=True,
            is_self_transfer=False,
            departure_at=leg.departure,
            arrival_at=leg.arrival,
            total_journey_minutes=_minutes(leg.arrival - leg.departure),
            ticketing_type=offer.ticketing_type,
            baggage_confidence=estimate.confidence.value,
        )

    def _itinerary_option(
        self,
        itinerary: ConstructedItinerary,
        direction: Direction,
        request: TripSearchRequest,
    ) -> TripOption:
        price = price_itinerary_from_carrier_rules(
            itinerary,
            BaggageRequirement(
                carry_on_bags=request.baggage.cabin_bags,
                checked_bags=request.baggage.checked_bags,
            ),
            self._carrier_rules,
        )
        requirement = BaggageRequirement(
            carry_on_bags=request.baggage.cabin_bags,
            checked_bags=request.baggage.checked_bags,
        )
        component_estimates = [
            estimate_offer_baggage(offer, requirement, self._carrier_rules)
            for offer in itinerary.components
        ]
        legs = [leg for offer in itinerary.components for leg in offer.legs]
        identity = sha256(
            "|".join(itinerary.constituent_offer_fingerprints).encode()
        ).hexdigest()
        return TripOption(
            id=identity,
            direction=direction,
            route=[legs[0].origin, *(leg.destination for leg in legs)],
            flight_numbers=[leg.flight_number for leg in legs],
            airlines=[leg.airline for leg in legs],
            legs=[
                TripLegSummary(
                    origin=leg.origin,
                    destination=leg.destination,
                    departure_at=leg.departure,
                    arrival_at=leg.arrival,
                    airline=leg.airline,
                    flight_number=leg.flight_number,
                )
                for leg in legs
            ],
            base_price=itinerary.total_price,
            ancillary_price_low=price.ancillary_low,
            ancillary_price_high=price.ancillary_high,
            baggage_estimates=[
                BaggageEstimate(
                    ticket_index=index,
                    carrier_codes=list(carrier_codes(offer)),
                    flight_numbers=[item.flight_number for item in offer.legs],
                    price_low=estimate.lower_bound,
                    price_high=estimate.upper_bound,
                    completeness=_price_completeness(estimate.completeness_status),
                    confidence=estimate.confidence.value,
                )
                for index, (offer, estimate) in enumerate(
                    zip(itinerary.components, component_estimates, strict=True), start=1
                )
            ],
            cabin_bags=request.baggage.cabin_bags,
            checked_bags=request.baggage.checked_bags,
            effective_price_low=price.effective_price_low,
            effective_price_high=price.effective_price_high,
            currency=itinerary.currency,
            price_completeness=_price_completeness(price.completeness_status),
            is_nonstop=False,
            is_self_transfer=True,
            connection_airport=itinerary.connection_airport,
            connection_minutes=_minutes(itinerary.connection_duration),
            departure_at=itinerary.departure,
            arrival_at=itinerary.final_arrival,
            total_journey_minutes=_minutes(
                itinerary.final_arrival - itinerary.departure
            ),
            ticketing_type=itinerary.ticketing_type,
            baggage_confidence=price.ancillary_confidence.value,
        )

    @staticmethod
    def _with_savings(option: TripOption, baseline: TripOption | None) -> TripOption:
        if baseline is None:
            return option
        saving = baseline.base_price - option.base_price
        percent = (
            (saving / baseline.base_price * 100).quantize(Decimal("0.01"))
            if baseline.base_price
            else None
        )
        return option.model_copy(
            update={
                "saving_vs_nonstop_amount": saving,
                "saving_vs_nonstop_percent": percent,
                "saving_vs_nonstop_low": saving,
                "saving_vs_nonstop_high": saving,
                "extra_minutes_vs_nonstop": (
                    option.total_journey_minutes - baseline.total_journey_minutes
                ),
            }
        )

    @staticmethod
    def _set_rankings(results: DirectionResults, options: list[TripOption]) -> None:
        results.feasible_options = sorted(
            options, key=lambda item: (item.base_price, item.total_journey_minutes)
        )
        comparable = sorted(
            options, key=lambda item: (item.base_price, item.total_journey_minutes)
        )
        results.cheapest_feasible = comparable[0] if comparable else None
        results.fastest_feasible = min(
            options, key=lambda item: item.total_journey_minutes, default=None
        )
        frontier = []
        for candidate in comparable:
            dominated = any(
                other.id != candidate.id
                and other.base_price <= candidate.base_price
                and other.total_journey_minutes <= candidate.total_journey_minutes
                and (
                    other.base_price < candidate.base_price
                    or other.total_journey_minutes < candidate.total_journey_minutes
                )
                for other in comparable
            )
            if not dominated:
                frontier.append(candidate)
        results.pareto_frontier = frontier
