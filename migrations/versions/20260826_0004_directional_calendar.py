"""Add durable directional direct-fare calendar observations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0004"
down_revision: str | None = "20260826_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calendar_price_observation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("market_key", sa.String(64), nullable=False),
        sa.Column("travel_date", sa.Date(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lowest_direct_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("passenger_context", sa.Text(), nullable=False),
        sa.Column("source_search_key", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_calendar_price_market_date_observed",
        "calendar_price_observation",
        ["market_key", "travel_date", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_calendar_price_market_date_observed",
        table_name="calendar_price_observation",
    )
    op.drop_table("calendar_price_observation")
