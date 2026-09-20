"""firms dedupe by name

Filings name a firm as free text; the design says dedupe by firm name, so
sync_filings upserts on lower(name).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE UNIQUE INDEX firms_name_lower_uq ON firms (lower(name));
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX firms_name_lower_uq;
    """)
