"""FastAPI application and progressive-search routes."""

import asyncio
import json
import os
from contextlib import suppress
from datetime import UTC, date, datetime
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
    BookingResolver,
    BookingSessionResponse,
    GooglePostHandoffLauncher,
    HandoffLauncher,
    InMemoryBookingSessionRegistry,
    PrepareBookingRequest,
    SearchAPIBookingResolver,
)
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
    SearchSessionStore,
    database_health,
)


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
    candidate_store = BookingCandidateStore()
    search_store = SearchSessionStore()
    registry = InMemorySearchRegistry(candidate_store, search_store)
    application.state.registry = registry
    application.state.search_store = search_store
    application.state.provider = provider
    application.state.booking_resolver = booking_resolver
    application.state.booking_sessions = InMemoryBookingSessionRegistry()
    application.state.handoff_launcher = handoff_launcher or GooglePostHandoffLauncher()
    application.state.usage = (
        CachedProviderUsage(usage_gateway) if usage_gateway is not None else None
    )

    def get_provider() -> ProviderGateway:
        configured = application.state.provider
        if configured is None:
            settings = Settings()
            configured = SearchAPIProviderGateway(
                SearchAPIClient(settings.searchapi_key)
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
        service = TripSearchService(get_provider(), registry)
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
        service = TripSearchService(get_provider(), registry)
        search_id = await service.create(request)

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
                        stream_timings = {
                            "request_received": request_received_at.isoformat(),
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
                            "time_to_complete_ms": (
                                perf_counter() - request_clock
                            )
                            * 1000,
                        }
                        event.data["stream_timings"] = stream_timings
                        search_log(
                            "STREAM_TIMINGS",
                            trip_id=search_id,
                            **stream_timings,
                        )
                    yield json.dumps(
                        {
                            "sequence": event.sequence,
                            "event": event.event,
                            "data": event.data,
                        },
                        separators=(",", ":"),
                    ) + "\n"
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
    ) -> CalendarResponse:
        if date_to < date_from:
            raise HTTPException(status_code=422, detail="date_to precedes date_from")
        try:
            prices = await get_provider().calendar(
                origins=origins,
                destinations=destinations,
                date_from=date_from,
                date_to=date_to,
                adults=adults,
                children=children,
                currency=currency.upper(),
            )
        except TimeoutError:
            raise HTTPException(status_code=504, detail="provider timeout") from None
        except (SearchAPIError, NotImplementedError):
            raise HTTPException(status_code=502, detail="provider error") from None
        return CalendarResponse(prices=prices)

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
