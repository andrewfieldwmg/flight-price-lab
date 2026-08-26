"""FastAPI application and progressive-search routes."""

import asyncio
import json
import os
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import ValidationError

from flight_price_lab.api.booking import (
    BookingContextExpiredError,
    BookingPreparationService,
    BookingResolutionCache,
    BookingResolver,
    BookingSessionResponse,
    GooglePostHandoffLauncher,
    HandoffLauncher,
    InMemoryBookingSessionRegistry,
    PrepareBookingRequest,
    SearchAPIBookingResolver,
)
from flight_price_lab.api.calendar import DirectionalCalendarService
from flight_price_lab.api.models import (
    CalendarResponse,
    ErrorResponse,
    ProviderUsage,
    SearchError,
    SearchKeyResponse,
    SearchSnapshot,
    SearchStartedResponse,
    TripSearchRequest,
)
from flight_price_lab.api.provider import ProviderGateway, SearchAPIProviderGateway
from flight_price_lab.api.provider_usage import (
    CachedProviderUsage,
    ProviderUsageGateway,
    SearchAPIUsageGateway,
)
from flight_price_lab.api.registry import InMemorySearchRegistry
from flight_price_lab.api.search_logging import search_log
from flight_price_lab.api.service import TripSearchService, trip_search_key
from flight_price_lab.config import Settings
from flight_price_lab.providers.searchapi import SearchAPIClient, SearchAPIError
from flight_price_lab.storage.database import (
    BookingCandidateStore,
    CalendarPriceStore,
    SearchResponseCache,
    SearchSessionStore,
    create_database_engine,
    database_health,
)
from flight_price_lab.storage.price_history import PriceHistoryStore


def create_app(
    provider: ProviderGateway | None = None,
    usage_gateway: ProviderUsageGateway | None = None,
    booking_resolver: BookingResolver | None = None,
    handoff_launcher: HandoffLauncher | None = None,
) -> FastAPI:
    application = FastAPI(title="Flight Price Lab API", version="1.0.0")
    allowed = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip() for origin in allowed.split(",") if origin.strip()
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    database_engine = create_database_engine()
    candidate_store = BookingCandidateStore(engine=database_engine)
    search_store = SearchSessionStore(engine=database_engine)
    price_history_store = PriceHistoryStore(database_engine)
    calendar_price_store = CalendarPriceStore(engine=database_engine)
    registry = InMemorySearchRegistry(candidate_store, search_store)
    application.state.registry = registry
    application.state.search_store = search_store
    application.state.provider = provider
    application.state.booking_resolver = booking_resolver
    application.state.booking_sessions = InMemoryBookingSessionRegistry()
    application.state.booking_resolution_cache = BookingResolutionCache()
    application.state.handoff_launcher = handoff_launcher or GooglePostHandoffLauncher()
    application.state.usage = (
        CachedProviderUsage(usage_gateway) if usage_gateway is not None else None
    )

    def new_search_service() -> TripSearchService:
        concurrency = Settings().search_provider_concurrency
        return TripSearchService(
            get_provider(),
            registry,
            max_concurrency=concurrency,
            price_history_store=price_history_store,
            calendar_price_store=calendar_price_store,
        )

    def get_provider() -> ProviderGateway:
        configured = application.state.provider
        if configured is None:
            settings = Settings()
            configured = SearchAPIProviderGateway(
                SearchAPIClient(settings.searchapi_key),
                SearchResponseCache(engine=database_engine),
            )
            application.state.provider = configured
        return configured

    def get_usage_service() -> CachedProviderUsage:
        configured = application.state.usage
        if configured is None:
            settings = Settings()
            configured = CachedProviderUsage(
                SearchAPIUsageGateway(SearchAPIClient(settings.searchapi_key))
            )
            application.state.usage = configured
        return configured

    def get_booking_service() -> BookingPreparationService:
        resolver = application.state.booking_resolver
        if resolver is None:
            settings = Settings()
            resolver = SearchAPIBookingResolver(SearchAPIClient(settings.searchapi_key))
            application.state.booking_resolver = resolver
        return BookingPreparationService(
            registry,
            application.state.booking_sessions,
            resolver,
            application.state.handoff_launcher,
            application.state.booking_resolution_cache,
        )

    @application.get("/api/health")
    async def health() -> dict[str, str | bool]:
        available = database_health(candidate_store.engine)
        return {
            "status": "ok" if available else "degraded",
            "database_mode": "postgres",
            "database_available": available,
        }

    @application.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request
        response = ErrorResponse(
            error=SearchError(code="invalid_input", message=str(error.errors()))
        )
        return JSONResponse(status_code=422, content=response.model_dump(mode="json"))

    @application.post(
        "/api/search", response_model=SearchStartedResponse, status_code=202
    )
    async def start_search(request: TripSearchRequest) -> SearchStartedResponse:
        service = new_search_service()
        search_id = await service.start(request)
        return SearchStartedResponse(
            search_id=search_id,
            trip_id=search_id,
            search_key=trip_search_key(request),
        )

    @application.post("/api/search/stream")
    async def stream_search(request: TripSearchRequest) -> StreamingResponse:
        """Run and stream one search on the same request/function instance."""

        request_received_at = datetime.now(UTC)
        request_clock = perf_counter()
        service = new_search_service()
        search_id = await service.create(request)
        session_initialized_at = datetime.now(UTC)
        session_initialization_ms = (perf_counter() - request_clock) * 1000

        async def stream():
            task = asyncio.create_task(service.run(search_id, request))
            first_event_at: datetime | None = None
            first_results_at: datetime | None = None
            first_outbound_at: datetime | None = None
            first_return_at: datetime | None = None
            try:
                async for event in registry.events(search_id):
                    sent_at = datetime.now(UTC)
                    if first_event_at is None:
                        first_event_at = sent_at
                    direction = event.data.get("direction")
                    if event.event == "results_updated":
                        if first_results_at is None:
                            first_results_at = sent_at
                        if direction == "OUTBOUND" and first_outbound_at is None:
                            first_outbound_at = sent_at
                        if direction == "RETURN" and first_return_at is None:
                            first_return_at = sent_at
                    if event.event in {"search_completed", "search_failed"}:
                        snapshot = event.data.get("snapshot")
                        diagnostics = (
                            snapshot.get("diagnostics", {})
                            if isinstance(snapshot, dict)
                            else {}
                        )
                        raw_requests = diagnostics.get("provider_requests", [])
                        provider_requests = [
                            {
                                "request_id": item.get("request_id"),
                                "planned_id": item.get("planned_id"),
                                "direction": item.get("direction"),
                                "query_type": item.get("query_type"),
                                "route": item.get("route"),
                                "started_at": item.get("started_at"),
                                "completed_at": item.get("completed_at"),
                                "duration_ms": item.get("duration_ms"),
                                "cache_hit": item.get("cache_hit"),
                                "http_status": item.get("http_status"),
                                "status": item.get("status"),
                                "error_type": item.get("error_type"),
                                "result_count": item.get("result_count"),
                            }
                            for item in raw_requests
                            if isinstance(item, dict)
                        ]
                        started_values = [
                            datetime.fromisoformat(str(item["started_at"]))
                            for item in provider_requests
                            if item.get("started_at")
                        ]
                        completed_values = [
                            datetime.fromisoformat(str(item["completed_at"]))
                            for item in provider_requests
                            if item.get("completed_at")
                        ]
                        stream_timings = {
                            "invocation_started": request_received_at.isoformat(),
                            "request_received": request_received_at.isoformat(),
                            "session_initialized": session_initialized_at.isoformat(),
                            "session_initialization_ms": session_initialization_ms,
                            "first_event_sent": (
                                first_event_at.isoformat() if first_event_at else None
                            ),
                            "first_outbound_results_sent": (
                                first_outbound_at.isoformat()
                                if first_outbound_at
                                else None
                            ),
                            "first_return_results_sent": (
                                first_return_at.isoformat() if first_return_at else None
                            ),
                            "final_event_sent": sent_at.isoformat(),
                            "time_to_first_event_ms": (
                                (first_event_at - request_received_at).total_seconds()
                                * 1000
                                if first_event_at
                                else None
                            ),
                            "time_to_first_results_ms": (
                                (first_results_at - request_received_at).total_seconds()
                                * 1000
                                if first_results_at
                                else None
                            ),
                            "time_to_complete_ms": (perf_counter() - request_clock)
                            * 1000,
                            "request_received_to_provider_calls_started_ms": (
                                (
                                    min(started_values) - request_received_at
                                ).total_seconds()
                                * 1000
                                if started_values
                                else None
                            ),
                            "last_provider_call_completed_to_search_complete_ms": (
                                (sent_at - max(completed_values)).total_seconds() * 1000
                                if completed_values
                                else None
                            ),
                        }
                        timings = {
                            "total_duration_ms": diagnostics.get("total_duration_ms"),
                            "provider_calls_total": diagnostics.get(
                                "provider_calls_total"
                            ),
                            "provider_requests_planned": diagnostics.get(
                                "provider_requests_planned"
                            ),
                            "provider_requests_started": diagnostics.get(
                                "provider_requests_started"
                            ),
                            "provider_requests_succeeded": diagnostics.get(
                                "provider_requests_succeeded"
                            ),
                            "provider_requests_failed": diagnostics.get(
                                "provider_requests_failed"
                            ),
                            "provider_requests_timed_out": diagnostics.get(
                                "provider_requests_timed_out"
                            ),
                            "provider_requests_cancelled": diagnostics.get(
                                "provider_requests_cancelled"
                            ),
                            "provider_calls_concurrent_peak": diagnostics.get(
                                "provider_calls_concurrent_peak"
                            ),
                            "provider_median_ms": diagnostics.get(
                                "median_provider_call_ms"
                            ),
                            "provider_p95_ms": diagnostics.get("p95_provider_call_ms"),
                            "provider_slowest_ms": diagnostics.get(
                                "slowest_provider_call_ms"
                            ),
                            "postgres_total_ms": diagnostics.get("postgres_write_ms"),
                            "normalization_ms": diagnostics.get("normalization_ms"),
                            "synthesis_ms": diagnostics.get("itinerary_synthesis_ms"),
                            "ranking_ms": diagnostics.get("ranking_filtering_ms"),
                            "provider_requests": provider_requests,
                            "database_operations": diagnostics.get(
                                "database_operations", []
                            ),
                            "planned_provider_requests": diagnostics.get(
                                "planned_provider_requests", []
                            ),
                            "orchestration_tail": {
                                "last_task_terminal_at": diagnostics.get(
                                    "last_task_terminal_at"
                                ),
                                "final_persistence_started_at": diagnostics.get(
                                    "final_persistence_started_at"
                                ),
                                "final_persistence_completed_at": diagnostics.get(
                                    "final_persistence_completed_at"
                                ),
                                "final_event_sent_at": sent_at.isoformat(),
                            },
                            "stream": stream_timings,
                        }
                        event.data["stream_timings"] = stream_timings
                        event.data["timings"] = timings
                        search_log(
                            "SEARCH_SERVER_TIMING",
                            trip_id=search_id,
                            timings=timings,
                        )
                    yield (
                        json.dumps(
                            {
                                "sequence": event.sequence,
                                "event": event.event,
                                "data": event.data,
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                await task
            finally:
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

        return StreamingResponse(
            stream(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache, no-transform"},
        )

    @application.post("/api/search/key", response_model=SearchKeyResponse)
    async def derive_search_key(request: TripSearchRequest) -> SearchKeyResponse:
        return SearchKeyResponse(search_key=trip_search_key(request))

    @application.get("/api/search/{search_id}", response_model=SearchSnapshot)
    async def get_search(search_id: str) -> SearchSnapshot:
        snapshot = search_store.get(search_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="search not found")
        return snapshot

    @application.get("/api/search/{search_id}/events")
    async def search_events(search_id: str) -> StreamingResponse:
        if await registry.get(search_id) is None:
            raise HTTPException(status_code=404, detail="search not found")

        async def stream():
            async for event in registry.events(search_id):
                payload = json.dumps(event.data, separators=(",", ":"))
                yield f"id: {event.sequence}\nevent: {event.event}\ndata: {payload}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @application.get("/api/calendar", response_model=CalendarResponse)
    async def calendar(
        origins: Annotated[list[str], Query(min_length=1)],
        destinations: Annotated[list[str], Query(min_length=1)],
        date_from: Annotated[date, Query()],
        date_to: Annotated[date, Query()],
        adults: Annotated[int, Query(ge=1)],
        children: Annotated[int, Query(ge=0)] = 0,
        currency: Annotated[str, Query(min_length=3, max_length=3)] = "GBP",
        direction: Annotated[str, Query(pattern="^(OUTBOUND|RETURN)$")] = "OUTBOUND",
    ) -> CalendarResponse:
        if date_to < date_from:
            raise HTTPException(status_code=422, detail="date_to precedes date_from")
        if (date_to - date_from).days > 30:
            raise HTTPException(
                status_code=422, detail="calendar range exceeds 31 days"
            )
        dates = [
            date_from + timedelta(days=offset)
            for offset in range((date_to - date_from).days + 1)
        ]
        return await DirectionalCalendarService(
            get_provider(),
            calendar_price_store,
            max_concurrency=Settings().search_provider_concurrency,
        ).prices(
            origins=origins,
            destinations=destinations,
            dates=dates,
            adults=adults,
            children=children,
            currency=currency.upper(),
            direction=direction,
        )

    @application.get("/api/provider-usage", response_model=ProviderUsage)
    async def provider_usage(refresh: bool = False) -> ProviderUsage:
        try:
            return await get_usage_service().get(force_refresh=refresh)
        except ValidationError:
            raise HTTPException(
                status_code=503, detail="provider credentials unavailable"
            ) from None
        except (SearchAPIError, TypeError, ValueError):
            raise HTTPException(
                status_code=502, detail="provider usage unavailable"
            ) from None

    @application.post("/api/booking/prepare", response_model=BookingSessionResponse)
    async def prepare_booking(
        request: PrepareBookingRequest,
    ) -> BookingSessionResponse:
        try:
            return await get_booking_service().prepare(request)
        except BookingContextExpiredError:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "booking_context_expired",
                    "message": "Booking context expired; run a fresh search before preparing booking.",
                },
            ) from None

    @application.post(
        "/api/booking/{session_id}/handoff/{ticket_id}",
        response_class=RedirectResponse,
    )
    async def start_booking_handoff(
        session_id: str,
        ticket_id: str,
        acknowledge_material_change: bool = False,
    ) -> RedirectResponse:
        try:
            url = await get_booking_service().handoff_url(
                session_id, ticket_id, acknowledge_material_change
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        except PermissionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return RedirectResponse(url, status_code=303)

    return application


app = create_app()
