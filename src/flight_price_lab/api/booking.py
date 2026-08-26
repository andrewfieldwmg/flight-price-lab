"""Explicit, server-side booking preparation and carrier handoff sessions."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, parse_qsl, urlparse
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from flight_price_lab.api.registry import InMemorySearchRegistry
from flight_price_lab.api.search_logging import (
    development_diagnostics_enabled,
    search_log,
)
from flight_price_lab.models.flight import FlightOffer
from flight_price_lab.providers.searchapi import SearchAPIClient
from flight_price_lab.providers.searchapi_booking import BookingOptionsRequest


class BookingSessionState(StrEnum):
    CREATED = "CREATED"
    REPRICING = "REPRICING"
    READY = "READY"
    VERIFY_ON_AIRLINE = "VERIFY_ON_AIRLINE"
    UNAVAILABLE = "UNAVAILABLE"
    PRICE_CHANGED = "PRICE_CHANGED"
    HANDOFF_STARTED = "HANDOFF_STARTED"
    AIRLINE_VERIFIED = "AIRLINE_VERIFIED"
    FAILED = "FAILED"


class PriceChangeStatus(StrEnum):
    PRICE_DECREASED = "PRICE_DECREASED"
    UNCHANGED = "UNCHANGED"
    MINOR_INCREASE = "MINOR_INCREASE"
    MATERIAL_INCREASE = "MATERIAL_INCREASE"


class HandoffCapability(StrEnum):
    EXACT_CHECKOUT_HANDOFF = "EXACT_CHECKOUT_HANDOFF"
    EXACT_FLIGHT_HANDOFF = "EXACT_FLIGHT_HANDOFF"
    PREFILLED_SEARCH = "PREFILLED_SEARCH"
    GENERIC_BOOKING_PAGE = "GENERIC_BOOKING_PAGE"
    UNAVAILABLE = "UNAVAILABLE"


class BookingContextExpiredError(Exception):
    """The public option survived, but its private provider lineage did not."""


class BookingResolutionError(Exception):
    """Safe, classified failure while resolving private provider context."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PrepareBookingRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    search_id: str
    selected_option_ids: list[str] = Field(min_length=1)


class HandoffRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    acknowledge_material_change: bool = False


class BookingTicketResponse(BaseModel):
    ticket_id: str
    carrier: str
    flight_number: str
    route: str
    travel_date: str
    departure_at: datetime
    arrival_at: datetime
    original_price: Decimal
    current_price: Decimal | None
    price_delta: Decimal | None
    currency: str
    status: BookingSessionState
    price_change_status: PriceChangeStatus | None
    material_change_acknowledgement_required: bool
    capability: HandoffCapability | None
    fare_selected: bool = False
    adults: int
    children: int
    exact_flight_verified: bool
    passenger_composition_verified: bool
    preparation_failure_reason: str | None = None


class BookingSessionResponse(BaseModel):
    booking_session_id: str
    state: BookingSessionState
    tickets: list[BookingTicketResponse]
    original_total: Decimal
    current_total: Decimal | None
    price_delta: Decimal | None


@dataclass(frozen=True)
class ResolvedHandoff:
    current_price: Decimal | None
    booking_url: str
    booking_post_data: SecretStr
    capability: HandoffCapability
    fare_selected: bool = False
    adults: int = 1
    children: int = 0
    exact_flight_verified: bool = False
    passenger_composition_verified: bool = False
    carrier: str = ""
    flight_number: str = ""
    origin: str = ""
    destination: str = ""
    travel_date: str = ""


class CarrierHandoffAdapter(Protocol):
    capability: HandoffCapability

    def validate_redirect(self, url: str, handoff: ResolvedHandoff) -> str: ...


class RyanairHandoffAdapter:
    capability = HandoffCapability.EXACT_FLIGHT_HANDOFF

    def validate_redirect(self, url: str, handoff: ResolvedHandoff) -> str:
        parsed = urlparse(url)
        if "ryanair.com" not in parsed.netloc.lower():
            raise ValueError("Ryanair redirect was not returned")
        return url


class EasyJetHandoffAdapter:
    capability = HandoffCapability.EXACT_FLIGHT_HANDOFF

    def validate_redirect(self, url: str, handoff: ResolvedHandoff) -> str:
        parsed = urlparse(url)
        if "easyjet.com" not in parsed.netloc.lower():
            raise ValueError("easyJet redirect was not returned")
        query = parse_qs(parsed.query)
        expected_flight = handoff.flight_number.split()[-1]
        expected = {
            "xdfn": expected_flight,
            "dep": handoff.origin,
            "dest": handoff.destination,
            "dd": handoff.travel_date,
            "apax": str(handoff.adults),
            "cpax": str(handoff.children),
        }
        if any(query.get(key) != [value] for key, value in expected.items()):
            raise ValueError("easyJet redirect did not preserve booking context")
        return parsed._replace(scheme="https").geturl()


class WizzAirHandoffAdapter:
    capability = HandoffCapability.PREFILLED_SEARCH

    def validate_redirect(self, url: str, handoff: ResolvedHandoff) -> str:
        parsed = urlparse(url)
        if "wizzair.com" not in parsed.netloc.lower():
            raise ValueError("Wizz Air redirect was not returned")
        expected_path = (
            f"/booking/select-flight/{handoff.origin}/{handoff.destination}/"
            f"{handoff.travel_date}/null/{handoff.adults}/{handoff.children}/0"
        )
        if not parsed.path.lower().endswith(expected_path.lower()):
            raise ValueError("Wizz Air redirect did not preserve search context")
        return parsed._replace(scheme="https").geturl()


class AeroitaliaHandoffAdapter:
    capability = HandoffCapability.PREFILLED_SEARCH

    def validate_redirect(self, url: str, handoff: ResolvedHandoff) -> str:
        parsed = urlparse(url)
        if (
            parsed.netloc.lower() != "book.aeroitalia.com"
            or parsed.path != "/deeplink/search"
        ):
            raise ValueError("Aeroitalia prefilled search was not returned")
        query = parse_qs(parsed.query)
        expected = {
            "ADT": str(handoff.adults),
            "CHD": str(handoff.children),
            "o1": handoff.origin,
            "d1": handoff.destination,
            "dd1": handoff.travel_date,
        }
        if any(query.get(key) != [value] for key, value in expected.items()):
            raise ValueError("Aeroitalia redirect did not preserve search context")
        return parsed._replace(scheme="https").geturl()


class BritishAirwaysHandoffAdapter:
    capability = HandoffCapability.PREFILLED_SEARCH

    def validate_redirect(self, url: str, handoff: ResolvedHandoff) -> str:
        parsed = urlparse(url)
        if (
            "britishairways.com" not in parsed.netloc.lower()
            or parsed.path != "/nx/b/airselect/en/gb/book/metasearch"
        ):
            raise ValueError("British Airways prefilled search was not returned")
        query = parse_qs(parsed.query)
        flight = handoff.flight_number.replace(" ", "").upper()
        expected_context = (
            f"{handoff.origin}-{handoff.destination}_{handoff.travel_date}T"
        )
        context = query.get("ond1", [""])[0].upper()
        if (
            query.get("ad") != [str(handoff.adults)]
            or query.get("ch") != [str(handoff.children)]
            or not context.startswith(expected_context.upper())
            or f"_{flight[:2]}{flight[2:].zfill(4)}_" not in context
        ):
            raise ValueError(
                "British Airways redirect did not preserve booking context"
            )
        return parsed._replace(scheme="https").geturl()


class ITAAirwaysHandoffAdapter:
    """Validate ITA's partner deeplink context without claiming flight selection."""

    capability = HandoffCapability.PREFILLED_SEARCH
    _allowed_hosts = frozenset({"ita-airways.com", "www.ita-airways.com"})
    _deeplink_markers = (
        "https://www.ita-airways.com/deeplink/partner?",
        "https://ita-airways.com/deeplink/partner?",
    )

    def _direct_destination(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.hostname in self._allowed_hosts:
            return url
        if parsed.hostname != "ad.doubleclick.net":
            raise ValueError("ITA Airways partner handoff was not returned")
        for marker in self._deeplink_markers:
            offset = url.find(marker)
            if offset >= 0:
                destination = url[offset:]
                target = urlparse(destination)
                if (
                    target.hostname in self._allowed_hosts
                    and target.path == "/deeplink/partner"
                ):
                    return destination
        raise ValueError("ITA Airways destination was missing from tracking wrapper")

    def validate_redirect(self, url: str, handoff: ResolvedHandoff) -> str:
        destination = self._direct_destination(url)
        parsed = urlparse(destination)
        query = parse_qs(parsed.query)
        flight = handoff.flight_number.replace(" ", "").upper()
        departure = query.get("DepartureOut1", [""])[0]
        expected = {
            "OriginOut1": handoff.origin,
            "DestinationOut1": handoff.destination,
            "FlightOut1": flight,
            "PaxAdult": str(handoff.adults),
            "PaxChild": str(handoff.children),
        }
        if any(
            query.get(key) != [value] for key, value in expected.items()
        ) or not departure.startswith(handoff.travel_date):
            raise ValueError("ITA Airways redirect did not preserve search context")
        return destination


HANDOFF_ADAPTERS: dict[str, CarrierHandoffAdapter] = {
    "FR": RyanairHandoffAdapter(),
    "U2": EasyJetHandoffAdapter(),
    "W4": WizzAirHandoffAdapter(),
    "XZ": AeroitaliaHandoffAdapter(),
    "BA": BritishAirwaysHandoffAdapter(),
    "AZ": ITAAirwaysHandoffAdapter(),
}


class BookingResolver(Protocol):
    async def resolve(self, offer: FlightOffer) -> ResolvedHandoff: ...


class HandoffLauncher(Protocol):
    async def launch(self, handoff: ResolvedHandoff) -> str: ...


class GooglePostHandoffLauncher:
    """Exchange the secret Google POST server-side for its carrier redirect."""

    async def launch(self, handoff: ResolvedHandoff) -> str:
        fields = dict(
            parse_qsl(
                handoff.booking_post_data.get_secret_value(), keep_blank_values=True
            )
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(handoff.booking_url, data=fields)
            response.raise_for_status()
        match = re.search(r"https?://[^\"'<> ]+", response.text)
        if match is None:
            raise ValueError("carrier redirect was not returned")
        destination = __import__("html").unescape(match.group(0))
        adapter = HANDOFF_ADAPTERS.get(handoff.carrier)
        if adapter is None:
            raise ValueError("carrier handoff is not supported")
        return adapter.validate_redirect(destination, handoff)


class SearchAPIBookingResolver:
    """Resolve a fresh option while retaining the provider POST only server-side."""

    def __init__(
        self,
        client: SearchAPIClient,
        carrier_capabilities: dict[str, HandoffCapability] | None = None,
    ) -> None:
        self._client = client
        self._carrier_capabilities = carrier_capabilities or {
            "FR": HandoffCapability.EXACT_FLIGHT_HANDOFF,
            "U2": HandoffCapability.EXACT_FLIGHT_HANDOFF,
            "W4": HandoffCapability.PREFILLED_SEARCH,
            "XZ": HandoffCapability.PREFILLED_SEARCH,
            "BA": HandoffCapability.PREFILLED_SEARCH,
            "AZ": HandoffCapability.PREFILLED_SEARCH,
            "VY": HandoffCapability.UNAVAILABLE,
            "LX": HandoffCapability.UNAVAILABLE,
            "DE": HandoffCapability.UNAVAILABLE,
        }

    async def resolve(self, offer: FlightOffer) -> ResolvedHandoff:
        action = offer.raw_metadata.get("provider_action_metadata", {})
        token = action.get("booking_token") if isinstance(action, dict) else None
        if not isinstance(token, str) or not token:
            raise BookingResolutionError("MISSING_BOOKING_TOKEN")
        parameters = offer.raw_metadata.get("provider_search_context")
        if not isinstance(parameters, dict) or not parameters:
            parameters = self._legacy_search_context(offer)
        required = ("departure_id", "arrival_id", "outbound_date")
        if any(not parameters.get(field) for field in required):
            raise BookingResolutionError("BOOKING_CONTEXT_EXPIRED")
        request = BookingOptionsRequest(
            booking_token=token,
            departure_id=parameters["departure_id"],
            arrival_id=parameters["arrival_id"],
            outbound_date=parameters["outbound_date"],
            flight_type=parameters.get("flight_type", "one_way"),
            adults=parameters.get("adults"),
            children=parameters.get("children"),
            currency=parameters.get("currency"),
        )
        try:
            response = await asyncio.to_thread(self._client.booking_options, request)
        except Exception as error:
            raise BookingResolutionError("PROVIDER_BOOKING_LOOKUP_FAILED") from error
        expected = [leg.flight_number for leg in offer.legs]
        for option in response.get("booking_options", []):
            if option.get("flight_numbers") != expected:
                continue
            booking_request = option.get("booking_request", {})
            if not booking_request.get("url") or not booking_request.get("post_data"):
                continue
            carrier = expected[0].split()[0].upper()
            capability = self._carrier_capabilities.get(
                carrier, HandoffCapability.UNAVAILABLE
            )
            if capability is HandoffCapability.UNAVAILABLE:
                raise BookingResolutionError("HANDOFF_ADAPTER_FAILED")
            return ResolvedHandoff(
                current_price=(
                    Decimal(str(option["price"]))
                    if option.get("price") is not None
                    else None
                ),
                booking_url=booking_request["url"],
                booking_post_data=SecretStr(booking_request["post_data"]),
                capability=capability,
                adults=int(parameters.get("adults", 1)),
                children=int(parameters.get("children", 0)),
                exact_flight_verified=capability
                in {
                    HandoffCapability.EXACT_CHECKOUT_HANDOFF,
                    HandoffCapability.EXACT_FLIGHT_HANDOFF,
                },
                passenger_composition_verified=True,
                carrier=carrier,
                flight_number=expected[0],
                origin=offer.legs[0].origin,
                destination=offer.legs[-1].destination,
                travel_date=offer.legs[0].departure.date().isoformat(),
            )
        raise BookingResolutionError("PROVIDER_BOOKING_LOOKUP_FAILED")

    @staticmethod
    def _legacy_search_context(offer: FlightOffer) -> dict[str, object]:
        """Read legacy local captures only when that instance still owns the file."""

        if not offer.raw_reference:
            return {}
        path = Path(offer.raw_reference)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        parameters = payload.get("search_parameters", {})
        return parameters if isinstance(parameters, dict) else {}


@dataclass
class _Ticket:
    response: BookingTicketResponse
    handoff: ResolvedHandoff | None = None


@dataclass
class _Session:
    session_id: str
    state: BookingSessionState
    tickets: list[_Ticket] = field(default_factory=list)
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) + timedelta(minutes=15)
    )


def classify_price_change(original: Decimal, current: Decimal) -> PriceChangeStatus:
    delta = current - original
    if delta < 0:
        return PriceChangeStatus.PRICE_DECREASED
    if delta == 0:
        return PriceChangeStatus.UNCHANGED
    threshold = max(Decimal(10), original * Decimal("0.03"))
    return (
        PriceChangeStatus.MINOR_INCREASE
        if delta <= threshold
        else PriceChangeStatus.MATERIAL_INCREASE
    )


class InMemoryBookingSessionRegistry:
    """Booking state is deliberately separate from ordinary search snapshots."""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    def put(self, session: _Session) -> None:
        self._sessions[session.session_id] = session

    def get(self, session_id: str) -> _Session | None:
        session = self._sessions.get(session_id)
        if session and session.expires_at > datetime.now(UTC):
            return session
        return None


class BookingPreparationService:
    def __init__(
        self,
        searches: InMemorySearchRegistry,
        sessions: InMemoryBookingSessionRegistry,
        resolver: BookingResolver,
        launcher: HandoffLauncher,
    ) -> None:
        self.searches = searches
        self.sessions = sessions
        self.resolver = resolver
        self.launcher = launcher

    async def prepare(self, request: PrepareBookingRequest) -> BookingSessionResponse:
        session = _Session(str(uuid4()), BookingSessionState.CREATED)
        self.sessions.put(session)
        search_log("booking_prepare_started", booking_session_id=session.session_id)
        session.state = BookingSessionState.REPRICING
        candidates = []
        for option_id in request.selected_option_ids:
            snapshot = await self.searches.get(request.search_id)
            option = None
            if snapshot is not None:
                collections = [
                    snapshot.outbound.nonstop_options,
                    snapshot.outbound.feasible_options,
                ]
                if snapshot.return_ is not None:
                    collections.extend(
                        [
                            snapshot.return_.nonstop_options,
                            snapshot.return_.feasible_options,
                        ]
                    )
                option = next(
                    (
                        item
                        for collection in collections
                        for item in collection
                        if item.id == option_id
                    ),
                    None,
                )
            offers = await self.searches.get_booking_candidate(
                request.search_id, option_id
            )
            if development_diagnostics_enabled():
                search_log(
                    "BOOKING_PREPARE_OPTION_TRACE",
                    search_id=request.search_id,
                    selected_option_id=option_id,
                    option_found=option is not None,
                    option_type=(
                        "synthetic"
                        if option and option.is_self_transfer
                        else "direct"
                        if option
                        else None
                    ),
                    is_self_transfer=option.is_self_transfer if option else None,
                    ticketing_type=option.ticketing_type.value if option else None,
                    number_of_legs=len(option.legs) if option else None,
                    number_of_constituent_refs=len(offers) if offers else 0,
                    constituent_ids=[offer.fingerprint for offer in offers or ()],
                    constituent_flights=[
                        offer.legs[0].flight_number for offer in offers or ()
                    ],
                    resolved_underlying_offer_count=len(offers) if offers else 0,
                )
            if not offers:
                session.state = BookingSessionState.FAILED
                search_log(
                    "BOOKING_PREPARE_CONTEXT_EXPIRED",
                    search_id=request.search_id,
                    selected_option_id=option_id,
                    booking_ticket_count=0,
                )
                raise BookingContextExpiredError(option_id)
            candidates.extend(offers)

        async def resolve(offer: FlightOffer) -> _Ticket:
            leg = offer.legs[0]
            base = BookingTicketResponse(
                ticket_id=str(uuid4()),
                carrier=leg.flight_number.split()[0].upper(),
                flight_number=leg.flight_number,
                route=f"{leg.origin} → {offer.legs[-1].destination}",
                travel_date=leg.departure.date().isoformat(),
                departure_at=leg.departure,
                arrival_at=offer.legs[-1].arrival,
                original_price=offer.total_price,
                current_price=None,
                price_delta=None,
                currency=offer.currency,
                status=BookingSessionState.REPRICING,
                price_change_status=None,
                material_change_acknowledgement_required=False,
                capability=None,
                adults=max((offer.passenger_count or 1), 1),
                children=0,
                exact_flight_verified=False,
                passenger_composition_verified=False,
            )
            try:
                handoff = await self.resolver.resolve(offer)
                delta = (
                    handoff.current_price - offer.total_price
                    if handoff.current_price is not None
                    else None
                )
                change = (
                    classify_price_change(offer.total_price, handoff.current_price)
                    if handoff.current_price is not None
                    else None
                )
                response = base.model_copy(
                    update={
                        "current_price": handoff.current_price,
                        "price_delta": delta,
                        "status": (
                            BookingSessionState.VERIFY_ON_AIRLINE
                            if handoff.current_price is None
                            else BookingSessionState.READY
                        ),
                        "price_change_status": change,
                        "material_change_acknowledgement_required": change
                        is PriceChangeStatus.MATERIAL_INCREASE,
                        "capability": handoff.capability,
                        "fare_selected": handoff.fare_selected,
                        "adults": handoff.adults,
                        "children": handoff.children,
                        "exact_flight_verified": handoff.exact_flight_verified,
                        "passenger_composition_verified": handoff.passenger_composition_verified,
                    }
                )
                search_log(
                    "booking_reprice_completed",
                    booking_session_id=session.session_id,
                    carrier=response.carrier,
                    price_delta=str(delta) if delta is not None else None,
                )
                if change not in {None, PriceChangeStatus.UNCHANGED}:
                    search_log(
                        "price_changed",
                        carrier=response.carrier,
                        price_delta=str(delta),
                    )
                return _Ticket(response, handoff)
            except Exception as error:  # noqa: BLE001 - safe constituent failure
                reason = (
                    error.code
                    if isinstance(error, BookingResolutionError)
                    else "HANDOFF_ADAPTER_FAILED"
                )
                action = offer.raw_metadata.get("provider_action_metadata", {})
                context = offer.raw_metadata.get("provider_search_context", {})
                search_log(
                    "BOOKING_PREPARE_CONSTITUENT_FAILED",
                    booking_session_id=session.session_id,
                    candidate_found=True,
                    provider=offer.provider,
                    carrier=base.carrier,
                    flight_number=base.flight_number,
                    booking_token_present=isinstance(action, dict)
                    and bool(action.get("booking_token")),
                    departure_token_present=isinstance(action, dict)
                    and bool(action.get("departure_token")),
                    provider_offer_ref_present=bool(offer.provider_offer_id),
                    raw_provider_metadata_present=bool(offer.raw_metadata),
                    passenger_search_context_present=isinstance(context, dict)
                    and bool(context),
                    failure_reason=reason,
                )
                return _Ticket(
                    base.model_copy(
                        update={
                            "status": BookingSessionState.FAILED,
                            "preparation_failure_reason": reason,
                        }
                    )
                )

        session.tickets = list(
            await asyncio.gather(*(resolve(offer) for offer in candidates))
        )
        if any(
            ticket.response.status is BookingSessionState.FAILED
            for ticket in session.tickets
        ):
            session.state = BookingSessionState.FAILED
        elif any(
            ticket.response.price_change_status is PriceChangeStatus.MATERIAL_INCREASE
            for ticket in session.tickets
        ):
            session.state = BookingSessionState.PRICE_CHANGED
        else:
            session.state = BookingSessionState.READY
        if development_diagnostics_enabled():
            search_log(
                "BOOKING_PREPARE_RESPONSE_TRACE",
                search_id=request.search_id,
                booking_session_id=session.session_id,
                booking_ticket_count=len(session.tickets),
                ticket_ids=[ticket.response.ticket_id for ticket in session.tickets],
                carriers=[ticket.response.carrier for ticket in session.tickets],
                capabilities=[ticket.response.capability for ticket in session.tickets],
            )
        return self._response(session)

    async def handoff_url(
        self, session_id: str, ticket_id: str, acknowledgement: bool
    ) -> str:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError("booking session not found")
        if session.state not in {
            BookingSessionState.READY,
            BookingSessionState.PRICE_CHANGED,
            BookingSessionState.HANDOFF_STARTED,
        }:
            raise PermissionError("all constituent tickets must be ready")
        ticket = next(
            (item for item in session.tickets if item.response.ticket_id == ticket_id),
            None,
        )
        if ticket is None or ticket.handoff is None:
            raise KeyError("ticket handoff not ready")
        if (
            ticket.response.material_change_acknowledgement_required
            and not acknowledgement
        ):
            raise PermissionError("material price change acknowledgement required")
        session.state = BookingSessionState.HANDOFF_STARTED
        ticket.response = ticket.response.model_copy(
            update={"status": BookingSessionState.HANDOFF_STARTED}
        )
        search_log(
            "handoff_started",
            carrier=ticket.response.carrier,
            price_delta=str(ticket.response.price_delta),
        )
        return await self.launcher.launch(ticket.handoff)

    @staticmethod
    def _response(session: _Session) -> BookingSessionResponse:
        original = sum(
            (ticket.response.original_price for ticket in session.tickets), Decimal()
        )
        prices = [ticket.response.current_price for ticket in session.tickets]
        current = (
            sum((price for price in prices if price is not None), Decimal())
            if prices and all(price is not None for price in prices)
            else None
        )
        return BookingSessionResponse(
            booking_session_id=session.session_id,
            state=session.state,
            tickets=[ticket.response for ticket in session.tickets],
            original_total=original,
            current_total=current,
            price_delta=current - original if current is not None else None,
        )
