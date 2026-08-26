"""Named airport groups and initial candidate hubs."""

LONDON = ("LGW", "STN", "LTN", "LHR", "LCY")
SARDINIA = ("CAG", "OLB", "AHO")
INITIAL_CANDIDATE_HUBS = ("MXP", "BGY", "LIN", "FCO", "CIA", "BLQ", "PSA", "NAP")

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
