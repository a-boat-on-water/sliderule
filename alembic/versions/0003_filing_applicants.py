"""filings: applicant of record

Every DOB filing names the licensed professional who signed it (first/last
name, PE or RA, license number). That person is the engineer the product is
looking for, so the filing keeps them. Public data, no organization_id.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE filings
        ADD COLUMN applicant_name    text,
        ADD COLUMN applicant_title   text,
        ADD COLUMN applicant_license text;
    CREATE INDEX filings_applicant_idx
        ON filings (lower(applicant_name), applicant_license);
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX filings_applicant_idx;
    ALTER TABLE filings
        DROP COLUMN applicant_name,
        DROP COLUMN applicant_title,
        DROP COLUMN applicant_license;
    """)
