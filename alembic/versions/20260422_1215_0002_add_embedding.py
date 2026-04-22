"""add embedding column to products + HNSW index

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMS = 1536  # OpenAI text-embedding-3-small


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("embedding", Vector(EMBEDDING_DIMS), nullable=True),
    )
    # HNSW = fast approximate nearest-neighbor. vector_cosine_ops matches the
    # distance function used at query time. Without a matching ops class, the
    # planner ignores the index and falls back to a sequential scan.
    op.execute(
        "CREATE INDEX ix_products_embedding "
        "ON products USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_products_embedding")
    op.drop_column("products", "embedding")
