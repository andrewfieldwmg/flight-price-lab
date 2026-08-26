"""Store provider cache payloads durably for cross-instance reuse."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0005"
down_revision: str | None = "20260826_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_cache",
        sa.Column("response_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("search_cache", "response_json")
