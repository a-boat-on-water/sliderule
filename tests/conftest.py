"""Test fixtures. DB tests run against the Neon dev branch via DATABASE_URL
(loaded from .env). They are skipped with a clear message when DATABASE_URL is
not set. Never point DATABASE_URL at the Neon main branch when running tests:
every table is truncated between tests.
"""

import os

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()

# Truncation order does not matter: TRUNCATE ... CASCADE.
ALL_TABLES = [
    "agent_tool_calls",
    "agent_runs",
    "jobs",
    "replies",
    "messages",
    "decisions",
    "stage_events",
    "campaign_people",
    "campaigns",
    "contact_methods",
    "person_profiles",
    "people",
    "filings",
    "firms",
    "roles_wanted",
    "users",
    "organizations",
]


@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set; DB tests run against the Neon dev branch")
    return url


@pytest.fixture
def conn(db_url):
    with psycopg.connect(db_url) as conn:
        conn.execute(
            "TRUNCATE " + ", ".join(ALL_TABLES) + " RESTART IDENTITY CASCADE"
        )
        conn.commit()
        yield conn


@pytest.fixture
def seed(conn):
    """Insert one org / role / campaign / person / campaign_person and return
    their ids. The campaign_person row is created directly at the given stage
    (test setup only; product code always starts at sourced and moves via
    transition())."""

    def make(stage="sourced"):
        cur = conn.execute(
            "INSERT INTO organizations (clerk_org_id, name)"
            " VALUES ('org_test', 'Test Org') RETURNING id"
        )
        (org_id,) = cur.fetchone()
        cur = conn.execute(
            "INSERT INTO roles_wanted (organization_id, title)"
            " VALUES (%s, 'Mechanical engineer') RETURNING id",
            (org_id,),
        )
        (role_id,) = cur.fetchone()
        cur = conn.execute(
            "INSERT INTO campaigns (organization_id, role_wanted_id, name)"
            " VALUES (%s, %s, 'Test campaign') RETURNING id",
            (org_id, role_id),
        )
        (campaign_id,) = cur.fetchone()
        cur = conn.execute(
            "INSERT INTO people (organization_id, identity_key, name)"
            " VALUES (%s, 'linkedin.com/in/test-person', 'Test Person')"
            " RETURNING id",
            (org_id,),
        )
        (person_id,) = cur.fetchone()
        cur = conn.execute(
            "INSERT INTO campaign_people"
            " (organization_id, campaign_id, person_id, stage)"
            " VALUES (%s, %s, %s, %s::stage) RETURNING id",
            (org_id, campaign_id, person_id, stage),
        )
        (campaign_person_id,) = cur.fetchone()
        conn.commit()
        return {
            "org_id": org_id,
            "role_id": role_id,
            "campaign_id": campaign_id,
            "person_id": person_id,
            "campaign_person_id": campaign_person_id,
        }

    return make
