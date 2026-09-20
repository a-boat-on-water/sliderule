"""Schema-level guarantees: append-only stage_events, no outbound double-send."""

import psycopg
import pytest

from sliderule.transition import transition


def test_stage_events_is_append_only(conn, seed):
    ids = seed(stage="sourced")
    transition(conn, ids["campaign_person_id"], "screened", "system", "evaluated")
    conn.commit()

    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE stage_events SET reason = 'tampered'")
    conn.rollback()

    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM stage_events")
    conn.rollback()


def test_outbound_double_send_is_impossible(conn, seed):
    ids = seed(stage="contacted")
    cp = ids["campaign_person_id"]
    org = ids["org_id"]

    conn.execute(
        "INSERT INTO messages (organization_id, campaign_person_id, direction,"
        " touch_number, body) VALUES (%s, %s, 'outbound', 1, 'touch one')",
        (org, cp),
    )
    conn.commit()

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO messages (organization_id, campaign_person_id, direction,"
            " touch_number, body) VALUES (%s, %s, 'outbound', 1, 'touch one again')",
            (org, cp),
        )
    conn.rollback()

    # inbound messages carry no touch number and are not subject to the index
    for _ in range(2):
        conn.execute(
            "INSERT INTO messages (organization_id, campaign_person_id, direction,"
            " body) VALUES (%s, %s, 'inbound', 'a reply')",
            (org, cp),
        )
    conn.commit()


def test_outbound_requires_touch_number(conn, seed):
    ids = seed(stage="contacted")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO messages (organization_id, campaign_person_id, direction,"
            " body) VALUES (%s, %s, 'outbound', 'no touch number')",
            (ids["org_id"], ids["campaign_person_id"]),
        )
    conn.rollback()
