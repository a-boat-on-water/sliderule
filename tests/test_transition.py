from datetime import datetime, timedelta, timezone

import pytest

from sliderule.transition import (
    ActorNotAllowed,
    IllegalTransition,
    transition,
)


def get_stage(conn, campaign_person_id):
    row = conn.execute(
        "SELECT stage FROM campaign_people WHERE id = %s", (campaign_person_id,)
    ).fetchone()
    return row[0]


def stage_event_count(conn, campaign_person_id):
    row = conn.execute(
        "SELECT count(*) FROM stage_events WHERE campaign_person_id = %s",
        (campaign_person_id,),
    ).fetchone()
    return row[0]


def test_legal_system_transition_writes_one_stage_event(conn, seed):
    ids = seed(stage="sourced")
    cp = ids["campaign_person_id"]

    transition(conn, cp, "screened", "system", "profile evaluated")

    assert get_stage(conn, cp) == "screened"
    events = conn.execute(
        "SELECT from_stage, to_stage, actor, reason FROM stage_events"
        " WHERE campaign_person_id = %s",
        (cp,),
    ).fetchall()
    assert events == [("sourced", "screened", "system", "profile evaluated")]


def test_illegal_edge_raises_and_writes_nothing(conn, seed):
    ids = seed(stage="sourced")
    cp = ids["campaign_person_id"]

    with pytest.raises(IllegalTransition):
        transition(conn, cp, "hired", "system", "impossible jump")

    assert get_stage(conn, cp) == "sourced"
    assert stage_event_count(conn, cp) == 0


def test_human_only_edge_rejects_system_actor(conn, seed):
    ids = seed(stage="screened")
    cp = ids["campaign_person_id"]

    with pytest.raises(ActorNotAllowed):
        transition(conn, cp, "approved", "system", "an agent must never do this")

    assert get_stage(conn, cp) == "screened"
    assert stage_event_count(conn, cp) == 0


def test_opted_out_sets_do_not_contact(conn, seed):
    ids = seed(stage="contacted")
    cp = ids["campaign_person_id"]

    transition(conn, cp, "opted_out", "system", "opt-out detected in inbound")

    assert get_stage(conn, cp) == "opted_out"
    row = conn.execute(
        "SELECT do_not_contact FROM people WHERE id = %s", (ids["person_id"],)
    ).fetchone()
    assert row[0] is True


def test_human_edge_succeeds_for_human_actor(conn, seed):
    ids = seed(stage="screened")
    cp = ids["campaign_person_id"]

    transition(conn, cp, "approved", "human", "reviewer said yes")

    assert get_stage(conn, cp) == "approved"
    assert stage_event_count(conn, cp) == 1


def test_stage_lists_partition_the_graph():
    from sliderule.transition import GRAPH, STAGE_ORDER, STAGES, TERMINAL_STAGES

    assert set(STAGE_ORDER) == STAGES
    assert STAGE_ORDER[-len(TERMINAL_STAGES):] == TERMINAL_STAGES
    # terminal means structurally terminal: no out-edges except opted_out
    for stage in TERMINAL_STAGES:
        assert set(GRAPH[stage]) <= {"opted_out"}


def test_missing_campaign_person_raises_lookup_error(conn):
    with pytest.raises(LookupError):
        transition(conn, 999_999_999, "screened", "system", "no such row")


def test_next_action_at_is_written(conn, seed):
    ids = seed(stage="contactable")
    cp = ids["campaign_person_id"]
    when = datetime.now(timezone.utc) + timedelta(days=3)

    transition(conn, cp, "contacted", "system", "touch 1 sent", next_action_at=when)

    row = conn.execute(
        "SELECT next_action_at FROM campaign_people WHERE id = %s", (cp,)
    ).fetchone()
    assert row[0] == when
