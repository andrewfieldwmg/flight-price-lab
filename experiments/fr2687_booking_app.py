"""Local seeded app for the controlled FR 2687 booking-preparation check."""

import json
from pathlib import Path

from flight_price_lab.api.app import create_app
from flight_price_lab.api.models import SearchSnapshot
from flight_price_lab.providers.searchapi_mapper import normalize_searchapi_response

CAPTURE = Path(
    "data/raw/searchapi/2026-08-24/"
    "20260824T111053Z_LGW-STN-LTN-LHR-LCY_CAG_2026-12-18.json"
)
payload = json.loads(CAPTURE.read_text(encoding="utf-8"))
offers, _ = normalize_searchapi_response(payload, raw_reference=str(CAPTURE))
selected = next(
    offer
    for offer in offers
    if [leg.flight_number for leg in offer.legs] == ["FR 2687"]
)

app = create_app()


@app.on_event("startup")
async def seed_fr2687() -> None:
    await app.state.registry.create(
        SearchSnapshot(search_id="fr2687-live-check", status="completed")
    )
    await app.state.registry.register_booking_candidate(
        "fr2687-live-check", selected.fingerprint, (selected,)
    )
