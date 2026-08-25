# Flight Price Lab

Flight Price Lab is a Python portfolio project for exploring flight-offer ingestion,
normalization, historical price snapshots, itinerary synthesis, and pricing analytics.
Its core use case is finding inexpensive routes, including one-stop journeys assembled
from separately purchased tickets.

## Current scope

The current foundation contains normalized Pydantic models and validation for a single
direction of travel: direct routes and routes with at most one stop. Journey topology
is modeled separately from ticketing: provider connections remain unknown unless there
is explicit single-ticket evidence, while independently synthesized offers use separate
tickets. SearchAPI Google Flights prices are treated as total search-party prices and
paired with their passenger count; they are never multiplied or currency-converted.
No minimum-connection-time or ranking decision is made. Provider calls, persistence
implementation, notebooks, AI-assisted features, and a frontend come later. Raw
provider payloads will be stored separately from normalized data and referenced from
offers.

## Architecture direction

- `providers`: provider-specific ingestion and raw payload handling
- `models`: provider-independent flight and itinerary models
- `routing`: connection validation and itinerary synthesis
- `storage`: raw snapshots and normalized historical persistence
- `analytics`: price-history and itinerary analysis
- `notebooks`: exploratory analysis

Outbound and return routes will be constructed independently, so each may use a
different hub. Domain datetimes are timezone-aware. An overnight connection is defined
as a calendar-date change in the connection airport's local timezone. Because the
project does not yet include airport-to-timezone data, itinerary construction accepts a
timezone resolver; without one, `overnight_connection` is `None` (unknown), never a
guess based on the timestamps' supplied offsets.

Leg fingerprints use stable schedule attributes. Offer fingerprints use ordered leg
fingerprints, so price and observation time changes do not change offer identity.

## Local commands

```powershell
uv sync --dev
uv run ruff format .
uv run ruff check .
uv run pytest
uv run uvicorn flight_price_lab.api.app:app --reload
```

Run the separate Next.js development app in another terminal:

```powershell
cd web
npm install
npm run dev
```

Copy `web/.env.example` to a local environment file only when needed. The frontend
defaults to `http://localhost:8000` and uses the backend's progressive SSE endpoint.

Copy `.env.example` to `.env` only when local provider integration begins. Never commit
the real `.env` file.

## Carrier baggage reference data

`config/carrier_baggage.yaml` records carrier ancillary rules retrieved on
2026-08-24. The official sources currently represented are
[easyJet](https://www.easyjet.com/en/terms-and-conditions/fees),
[Ryanair](https://www.ryanair.com/gb/en/useful-info/help-centre/fees),
[Vueling](https://help.vueling.com/hc/en-gb/articles/19798784049041-Checked-Luggage-Allowance),
[SWISS](https://www.swiss.com/gb/en/prepare/baggage/checked-baggage), and
[Condor](https://www.condor.com/eu/flight-preparation/baggage-and-animals/carry-on.jsp).
The project notes supplied Wizz Air policy values but contained no usable official
source URL, so that URL remains explicitly unknown.

Exact prices have equal lower and upper bounds. Ranges retain both bounds. Dynamic
prices retain only researched bounds and fare-dependent prices remain unknown unless
the selected fare provides inclusion evidence. Native currencies are preserved and
never converted or added to a differently denominated itinerary. Airline ancillary
prices and policies change frequently; refresh the source and retrieval date before
using these values for a current decision.

The local HTTP API keeps progressive search state in process memory. This is suitable
for one local development process only and must be replaced by a shared registry such
as Redis or a database before horizontally scaled deployment.

Provider search responses are indexed in PostgreSQL with a 60-minute
TTL. A normal search reuses fresh entries; only the explicit **Refresh prices** action
bypasses them. Raw JSON captures remain under `data/raw/searchapi/` after expiry. Exact,
reconstructable historical captures can be indexed without a provider call with:

```powershell
uv run python scripts/seed_search_cache.py data/raw/searchapi
```

## SearchAPI schema discovery

SearchAPI is the only V1 flight-data provider. Provider responses are initially saved
as complete raw JSON so normalization can be designed from captured real responses.
Live calls are manual and confirmation-gated during schema discovery; automated tests
use an in-memory HTTP transport and never contact SearchAPI.

## Assisted handoff validation

Manual browser checks completed in August 2026 established the current V1 handoff
capabilities: Ryanair and easyJet are `EXACT_FLIGHT_HANDOFF`; Wizz Air remains
`PREFILLED_SEARCH` because route, date, and passenger composition were confirmed but
exact-flight preservation was not independently verified. SearchAPI/airline price
differences remain visible as repricing deltas and are never silently reconciled.

## Vercel deployment

The repository deploys as one Vercel Services project. Set the Vercel project
Framework Preset to **Services**. Public `/api/*` requests are routed to FastAPI
without changing their path; all other requests go to Next.js. The frontend uses
same-origin `/api/*` URLs in production and `http://localhost:8000/api/*` locally.

Configure these server-only Vercel project environment variables:

```text
SEARCHAPI_KEY=<secret>
DATABASE_URL=<managed-postgres-connection>
```

Never expose it through a `NEXT_PUBLIC_*` variable. `NEXT_PUBLIC_API_BASE_URL` is only
needed for local development or an intentional external API origin.

Production requires a pre-migrated PostgreSQL database.
Apply migrations during deployment administration, before starting the application:

```powershell
uv run alembic upgrade head
```

PostgreSQL is required in every environment. Local `.env` configuration uses separate
development and test databases:

```text
DATABASE_URL=postgresql+psycopg://flight_price_lab:<password>@localhost:5432/flight_price_lab
TEST_DATABASE_URL=postgresql+psycopg://flight_price_lab:<password>@localhost:5432/flight_price_lab_test
```

Tests refuse to run when both URLs target the same server and database. Alembic is the
only schema authority; application startup never creates tables automatically.

The initial deployment preserves the current architecture with serverless limits:
search state and SSE queues remain process-local, so another function instance may not
find the same active search ID and instance termination loses that live state. Cache
metadata and booking lineage use Postgres in production. Raw JSON captures still need
external object storage for durable cross-instance access; the Vercel filesystem is
ephemeral and is not treated as durable history.

After creating a local `.env` containing `SEARCHAPI_KEY`, run the guarded probe with:

```powershell
uv run python scripts/probe_searchapi.py
```
