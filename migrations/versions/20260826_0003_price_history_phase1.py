"""Add observation runs and composite trip-option price history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0003"
down_revision: str | None = "20260825_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_observation_run",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("search_id", sa.String(64), nullable=False),
        sa.Column("search_key", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("children", sa.Integer(), nullable=False),
        sa.Column("passenger_count", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
    )
    op.create_index(
        "ix_search_observation_run_search_key_time",
        "search_observation_run",
        ["search_key", "observed_at"],
    )
    op.add_column(
        "flight_observation",
        sa.Column("observation_run_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "flight_observation",
        sa.Column("offer_fingerprint", sa.String(64), nullable=True),
    )
    op.add_column(
        "flight_observation", sa.Column("carrier", sa.String(16), nullable=True)
    )
    op.add_column(
        "flight_observation",
        sa.Column("arrival_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "flight_observation",
        sa.Column("total_price", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "flight_observation", sa.Column("adults", sa.Integer(), nullable=True)
    )
    op.add_column(
        "flight_observation", sa.Column("children", sa.Integer(), nullable=True)
    )
    op.add_column(
        "flight_observation",
        sa.Column("passenger_count", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_flight_observation_run",
        "flight_observation",
        "search_observation_run",
        ["observation_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_flight_observation_offer_context_time",
        "flight_observation",
        ["offer_fingerprint", "passenger_count", "currency", "observed_at"],
    )
    op.create_table(
        "trip_option_observation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "observation_run_id",
            sa.String(64),
            sa.ForeignKey("search_observation_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trip_option_fingerprint", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("base_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("children", sa.Integer(), nullable=False),
        sa.Column("passenger_count", sa.Integer(), nullable=False),
        sa.Column("is_nonstop", sa.Boolean(), nullable=False),
        sa.Column("is_self_transfer", sa.Boolean(), nullable=False),
        sa.Column("constituent_fingerprints_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_trip_option_observation_context_time",
        "trip_option_observation",
        [
            "trip_option_fingerprint",
            "direction",
            "passenger_count",
            "currency",
            "observed_at",
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_trip_option_observation_context_time",
        table_name="trip_option_observation",
    )
    op.drop_table("trip_option_observation")
    op.drop_index(
        "ix_flight_observation_offer_context_time",
        table_name="flight_observation",
    )
    op.drop_constraint(
        "fk_flight_observation_run", "flight_observation", type_="foreignkey"
    )
    for column in (
        "passenger_count",
        "children",
        "adults",
        "total_price",
        "arrival_at",
        "carrier",
        "offer_fingerprint",
        "observation_run_id",
    ):
        op.drop_column("flight_observation", column)
    op.drop_index(
        "ix_search_observation_run_search_key_time",
        table_name="search_observation_run",
    )
    op.drop_table("search_observation_run")
