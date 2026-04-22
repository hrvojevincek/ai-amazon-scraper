"""initial: pgvector extension, products, price_history

Revision ID: 0001
Revises:
Create Date: 2026-04-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extensions are schema too — committing them in a migration means every
    # environment (dev, CI, prod) gets them the same way. Step 7 will use this
    # for the embedding column.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "products",
        sa.Column("asin", sa.String(10), primary_key=True),
        sa.Column("country_code", sa.String(2), primary_key=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("brand", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("availability", sa.Text(), nullable=True),
        sa.Column("product_url", sa.Text(), nullable=True),
        sa.Column("images", JSONB(), nullable=False, server_default="[]"),
        sa.Column("categories", JSONB(), nullable=False, server_default="[]"),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "price_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("asin", sa.String(10), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["asin", "country_code"],
            ["products.asin", "products.country_code"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_price_history_asin_country",
        "price_history",
        ["asin", "country_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_price_history_asin_country", table_name="price_history")
    op.drop_table("price_history")
    op.drop_table("products")
    # Do NOT drop the vector extension — other objects may depend on it.
