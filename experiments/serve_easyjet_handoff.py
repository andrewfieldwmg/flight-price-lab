"""Temporary localhost page for manual easyJet U2 8309 handoff verification."""

import json
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import SecretStr

from flight_price_lab.api.booking import (
    GooglePostHandoffLauncher,
    HandoffCapability,
    ResolvedHandoff,
)

CAPTURE = Path(
    "data/raw/searchapi/booking_options/2026-08-25/"
    "20260825T063226Z_bff839e135bb755c4ea38a1e1333deea5ad127429d4269ee37eebca535f56d87.json"
)
payload = json.loads(CAPTURE.read_text(encoding="utf-8"))
option = next(
    item
    for item in payload["booking_options"]
    if item.get("flight_numbers") == ["U2 8309"]
)
request = option["booking_request"]
handoff = ResolvedHandoff(
    current_price=Decimal(str(option["price"])),
    booking_url=request["url"],
    booking_post_data=SecretStr(request["post_data"]),
    capability=HandoffCapability.PREFILLED_SEARCH,
    adults=2,
    children=2,
    carrier="U2",
    flight_number="U2 8309",
    origin="LGW",
    destination="MXP",
    travel_date="2026-12-18",
)

app = FastAPI(title="easyJet manual handoff check")


@app.get("/", response_class=HTMLResponse)
async def verification_page() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="en"><meta charset="utf-8"><title>easyJet handoff check</title>
<style>body{font:16px system-ui;max-width:680px;margin:48px auto;line-height:1.5}
button{font:inherit;padding:12px 18px;cursor:pointer}code{font-weight:700}</style>
<h1>easyJet U2 8309 handoff check</h1>
<p>This uses the saved handoff. It makes no SearchAPI request.</p>
<form method="post" action="/handoff"><button type="submit">Open easyJet test handoff</button></form>
<h2>Verify manually</h2>
<ul><li><code>LGW → MXP</code></li><li>18 Dec 2026</li><li><code>U2 8309</code></li>
<li>14:25 → 17:25</li><li>2 adults</li><li>2 children</li>
<li>Price around GBP 328</li></ul>
<p>Stop before fare continuation. Enter no passenger, contact, login, or payment data.</p>
</html>"""
    )


@app.post("/handoff", response_class=RedirectResponse)
async def open_handoff() -> RedirectResponse:
    destination = await GooglePostHandoffLauncher().launch(handoff)
    return RedirectResponse(destination, status_code=303)
