"""Itinerary construction and route validation."""

from flight_price_lab.routing.connections import construct_itinerary
from flight_price_lab.routing.feasibility import (
    BaggageProfile,
    SelfTransferProfile,
    is_feasible_self_transfer,
    minimum_connection_duration,
)
from flight_price_lab.routing.self_transfer import (
    construct_self_transfer,
    synthesize_connections,
)

__all__ = [
    "BaggageProfile",
    "SelfTransferProfile",
    "construct_itinerary",
    "construct_self_transfer",
    "is_feasible_self_transfer",
    "minimum_connection_duration",
    "synthesize_connections",
]
