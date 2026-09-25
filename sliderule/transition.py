"""The state machine. The ONLY writer of campaign_people.stage.

The graph is data, taken from the transitions table in the Notion design
("Engineer Sourcing Pipeline (Concord)" -> Design 2026-09-20). The API serves
it via GET /stages so the dashboard draws the board from the same source.

Actors are "system" and "human". Agents are never actors: they propose by
writing to people / person_profiles / contact_methods, and the system
transitions based on what they wrote.

One deliberate reading of the design: the table has an "any -> opted_out
(system)" row, so opted_out is reachable from every stage. We allow both
actors on those edges (a human reading a reply can record an opt-out, and the
deterministic opt-out detector must work regardless of stage). Every other
edge carries exactly the actor the design table names.
"""

from __future__ import annotations

from datetime import datetime

import psycopg

SYSTEM = "system"
HUMAN = "human"

ANY_ACTOR = frozenset({SYSTEM, HUMAN})


class IllegalTransition(Exception):
    """The requested edge does not exist in the graph."""


class ActorNotAllowed(Exception):
    """The edge exists but this actor may not take it."""


GRAPH: dict[str, dict[str, frozenset[str]]] = {
    "sourced": {
        "screened": frozenset({SYSTEM}),   # profile evaluation
        "rejected": frozenset({SYSTEM}),   # clear No goes straight to rejected
        "opted_out": ANY_ACTOR,
    },
    "screened": {
        "approved": frozenset({HUMAN}),    # a decisions row; human only
        "rejected": frozenset({HUMAN}),    # rejection requires a reason
        "opted_out": ANY_ACTOR,
    },
    "approved": {
        "contactable": frozenset({SYSTEM}),  # a contact_methods row reaches valid
        "opted_out": ANY_ACTOR,
    },
    "contactable": {
        "contacted": frozenset({SYSTEM}),  # touch 1 sent
        "opted_out": ANY_ACTOR,
    },
    "contacted": {
        "replied": frozenset({SYSTEM}),    # inbound that is not an auto-reply
        "no_reply": frozenset({SYSTEM}),   # touch 3 + N days
        "opted_out": ANY_ACTOR,
    },
    "replied": {
        "screening_call": frozenset({HUMAN}),
        "declined": frozenset({HUMAN}),
        "parked": frozenset({HUMAN}),
        "opted_out": ANY_ACTOR,
    },
    "screening_call": {
        "interview": frozenset({HUMAN}),
        "rejected": frozenset({HUMAN}),
        "opted_out": ANY_ACTOR,
    },
    "interview": {
        "hired": frozenset({HUMAN}),
        "rejected": frozenset({HUMAN}),
        "opted_out": ANY_ACTOR,
    },
    # Terminal / parked stages. "any -> opted_out" still applies (a late
    # opt-out must always be honourable); nothing else leaves them.
    "hired": {"opted_out": ANY_ACTOR},
    "rejected": {"opted_out": ANY_ACTOR},
    "declined": {"opted_out": ANY_ACTOR},
    "no_reply": {"opted_out": ANY_ACTOR},
    "parked": {"opted_out": ANY_ACTOR},
    "opted_out": {},
}

STAGES = frozenset(GRAPH)

# Canonical display order: the pipeline path, then terminal / parked.
STAGE_ORDER = [
    "sourced", "screened", "approved", "contactable", "contacted",
    "replied", "screening_call", "interview", "hired",
    "rejected", "declined", "no_reply", "parked", "opted_out",
]
TERMINAL_STAGES = ["rejected", "declined", "no_reply", "parked", "opted_out"]

# Explicit raises, not asserts: python -O must not strip these guards.
if set(STAGE_ORDER) != STAGES:
    raise RuntimeError("STAGE_ORDER out of sync with GRAPH")
# Terminal means structurally terminal: no out-edges except opted_out.
# (hired also qualifies structurally but is deliberately a board column.)
if not all(set(GRAPH[s]) <= {"opted_out"} for s in TERMINAL_STAGES):
    raise RuntimeError("TERMINAL_STAGES contains a stage with live out-edges")


def transition(
    conn: psycopg.Connection,
    campaign_person_id: int,
    to_stage: str,
    actor: str,
    reason: str,
    next_action_at: datetime | None = None,
) -> None:
    """Move a campaign person along one edge of the graph, atomically.

    Row-locks campaign_people, checks the edge and the actor, appends a
    stage_events row, updates the stage cache and next_action_at — one
    transaction. Raises IllegalTransition / ActorNotAllowed and writes
    nothing when the move is not allowed. If to_stage is opted_out, also
    sets people.do_not_contact.
    """
    if actor not in (SYSTEM, HUMAN):
        raise ActorNotAllowed(f"unknown actor {actor!r}; actors are 'system' and 'human'")
    if to_stage not in STAGES:
        raise IllegalTransition(f"unknown stage {to_stage!r}")

    with conn.transaction():
        row = conn.execute(
            "SELECT organization_id, person_id, stage FROM campaign_people"
            " WHERE id = %s FOR UPDATE",
            (campaign_person_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"campaign_person {campaign_person_id} does not exist")
        organization_id, person_id, from_stage = row

        edges = GRAPH[from_stage]
        if to_stage not in edges:
            raise IllegalTransition(f"no edge {from_stage} -> {to_stage}")
        if actor not in edges[to_stage]:
            raise ActorNotAllowed(
                f"{from_stage} -> {to_stage} is not allowed for actor {actor!r}"
            )

        conn.execute(
            "INSERT INTO stage_events"
            " (organization_id, campaign_person_id, from_stage, to_stage, actor, reason)"
            " VALUES (%s, %s, %s::stage, %s::stage, %s::stage_actor, %s)",
            (organization_id, campaign_person_id, from_stage, to_stage, actor, reason),
        )
        conn.execute(
            "UPDATE campaign_people"
            " SET stage = %s::stage, next_action_at = %s, updated_at = now()"
            " WHERE id = %s",
            (to_stage, next_action_at, campaign_person_id),
        )
        if to_stage == "opted_out":
            conn.execute(
                "UPDATE people SET do_not_contact = true, updated_at = now()"
                " WHERE id = %s",
                (person_id,),
            )
