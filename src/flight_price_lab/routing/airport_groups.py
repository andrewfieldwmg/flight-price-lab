"""Named airport groups and initial candidate hubs."""

LONDON = ("STN", "LGW", "LTN", "LHR", "LCY")
SARDINIA = ("AHO", "CAG", "OLB")
INITIAL_CANDIDATE_HUBS = ("MXP", "BGY", "LIN", "FCO", "CIA", "BLQ", "PSA", "NAP")

_AIRPORT_SEARCH_PRIORITY = {
    airport: rank
    for group in (LONDON, SARDINIA)
    for rank, airport in enumerate(group)
}

# Offline 2026-08-26 London-Sardinia evidence: MXP supplied the cheapest
# frontier options and LIN supplied the fastest frontier option. Unscored hubs
# retain their existing order; this affects scheduling only, never eligibility.
HUB_SEARCH_PRIORITY = {"MXP": 2, "LIN": 1}


def prioritize_candidate_hubs(hubs: tuple[str, ...]) -> tuple[str, ...]:
    """Put evidence-backed hubs first while preserving stable fallback order."""

    indexed = enumerate(hubs)
    return tuple(
        hub
        for _, hub in sorted(
            indexed,
            key=lambda item: (-HUB_SEARCH_PRIORITY.get(item[1], 0), item[0]),
        )
    )


def prioritize_airports(airports: tuple[str, ...]) -> tuple[str, ...]:
    """Apply the product's stable airport preference within a selected group."""

    return tuple(
        airport
        for _, airport in sorted(
            enumerate(airports),
            key=lambda item: (
                _AIRPORT_SEARCH_PRIORITY.get(item[1], len(_AIRPORT_SEARCH_PRIORITY)),
                item[0],
            ),
        )
    )
