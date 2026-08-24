"""Independent journey topology and ticketing classifications."""

from enum import StrEnum


class JourneyStructure(StrEnum):
    """Physical topology of a one-direction journey."""

    DIRECT = "direct"
    CONNECTION = "connection"


class TicketingType(StrEnum):
    """What is known about ticket protection across journey legs."""

    SINGLE_TICKET = "single_ticket"
    SEPARATE_TICKETS = "separate_tickets"
    UNKNOWN = "unknown"
