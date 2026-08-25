"""Add durable search session snapshots for cross-instance recovery."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0002"
down_revision: str | None = "20260825_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_sessions",
        sa.Column("search_id", sa.String(64), primary_key=True),
        sa.Column("search_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_search_sessions_search_key", "search_sessions", ["search_key"])


def downgrade() -> None:
    op.drop_index("ix_search_sessions_search_key", table_name="search_sessions")
    op.drop_table("search_sessions")
