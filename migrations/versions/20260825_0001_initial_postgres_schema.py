"""Initial cache, booking-lineage, and price-observation schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_cache",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("raw_response_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "booking_candidates",
        sa.Column("search_id", sa.String(64), primary_key=True),
        sa.Column("option_id", sa.String(64), primary_key=True),
        sa.Column("offers_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "market_observation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("market_key", sa.String(255), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cheapest_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
    )
    op.create_index(
        "ix_market_observation_market_time",
        "market_observation",
        ["market_key", "observed_at"],
    )
    op.create_table(
        "flight_observation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("flight_fingerprint", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("flight_number", sa.String(20), nullable=False),
        sa.Column("origin", sa.String(3), nullable=False),
        sa.Column("destination", sa.String(3), nullable=False),
        sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("search_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_flight_observation_fingerprint_time",
        "flight_observation",
        ["flight_fingerprint", "observed_at"],
    )
    op.create_index(
        "ix_flight_observation_departure",
        "flight_observation",
        ["departure_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_flight_observation_departure", table_name="flight_observation")
    op.drop_index(
        "ix_flight_observation_fingerprint_time", table_name="flight_observation"
    )
    op.drop_table("flight_observation")
    op.drop_index("ix_market_observation_market_time", table_name="market_observation")
    op.drop_table("market_observation")
    op.drop_table("booking_candidates")
    op.drop_table("search_cache")
