"""Roles, campaigns, candidates and the review queue.

Routes validate -> write -> enqueue -> return. No model calls here: adding a
person enqueues evaluate_profile for the worker. Human decisions (approve /
reject) call transition() synchronously with actor="human" — that is a DB
write, not an external call, and the human gate must be immediate.
Every query is filtered by the org id from the JWT.
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from sliderule.api.auth import AuthContext, get_auth
from sliderule.api.deps import get_conn, get_org_id
from sliderule.identity import identity_key
from sliderule.transition import ActorNotAllowed, IllegalTransition, transition
from sliderule.worker import enqueue

router = APIRouter()


# ---------------------------------------------------------------- roles


class RubricIn(BaseModel):
    hard_requirements: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)


class RoleIn(BaseModel):
    title: str = Field(min_length=1)
    rubric: RubricIn = Field(default_factory=RubricIn)


@router.post("/roles", status_code=201)
def create_role(
    body: RoleIn,
    org_id: int = Depends(get_org_id),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    (role_id,) = conn.execute(
        "INSERT INTO roles_wanted (organization_id, title, rubric)"
        " VALUES (%s, %s, %s) RETURNING id",
        (org_id, body.title, Jsonb(body.rubric.model_dump())),
    ).fetchone()
    conn.commit()
    return {"id": role_id}


@router.get("/roles")
def list_roles(
    org_id: int = Depends(get_org_id),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    rows = conn.execute(
        "SELECT id, title, rubric FROM roles_wanted"
        " WHERE organization_id = %s ORDER BY id",
        (org_id,),
    ).fetchall()
    return {"roles": [dict(zip(("id", "title", "rubric"), r)) for r in rows]}


# ------------------------------------------------------------- campaigns


class CampaignIn(BaseModel):
    name: str = Field(min_length=1)
    role_wanted_id: int


def _campaign_or_404(conn: psycopg.Connection, campaign_id: int, org_id: int) -> tuple:
    row = conn.execute(
        "SELECT id, role_wanted_id FROM campaigns"
        " WHERE id = %s AND organization_id = %s",
        (campaign_id, org_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    return row


@router.post("/campaigns", status_code=201)
def create_campaign(
    body: CampaignIn,
    org_id: int = Depends(get_org_id),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    role = conn.execute(
        "SELECT id FROM roles_wanted WHERE id = %s AND organization_id = %s",
        (body.role_wanted_id, org_id),
    ).fetchone()
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    (campaign_id,) = conn.execute(
        "INSERT INTO campaigns (organization_id, role_wanted_id, name)"
        " VALUES (%s, %s, %s) RETURNING id",
        (org_id, body.role_wanted_id, body.name),
    ).fetchone()
    conn.commit()
    return {"id": campaign_id}


@router.get("/campaigns")
def list_campaigns(
    org_id: int = Depends(get_org_id),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    rows = conn.execute(
        "SELECT c.id, c.name, c.status, rw.title,"
        "       (SELECT count(*) FROM campaign_people cp WHERE cp.campaign_id = c.id)"
        "  FROM campaigns c JOIN roles_wanted rw ON rw.id = c.role_wanted_id"
        " WHERE c.organization_id = %s ORDER BY c.id DESC",
        (org_id,),
    ).fetchall()
    keys = ("id", "name", "status", "role_title", "people_count")
    return {"campaigns": [dict(zip(keys, r)) for r in rows]}


@router.get("/campaigns/{campaign_id}/board")
def campaign_board(
    campaign_id: int,
    org_id: int = Depends(get_org_id),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    _campaign_or_404(conn, campaign_id, org_id)
    rows = conn.execute(
        "SELECT cp.id, cp.stage, cp.next_action_at, p.name, f.name, pr.bucket"
        "  FROM campaign_people cp"
        "  JOIN campaigns c ON c.id = cp.campaign_id"
        "  JOIN people p ON p.id = cp.person_id"
        "  LEFT JOIN firms f ON f.id = p.firm_id"
        "  LEFT JOIN person_profiles pr"
        "    ON pr.person_id = p.id AND pr.role_wanted_id = c.role_wanted_id"
        " WHERE cp.campaign_id = %s"
        " ORDER BY cp.id",
        (campaign_id,),
    ).fetchall()
    keys = ("id", "stage", "next_action_at", "person_name", "firm_name", "bucket")
    board: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        card = dict(zip(keys, row))
        board.setdefault(card["stage"], []).append(card)
    return {"board": board}


# ------------------------------------------------------------- candidates


class PersonIn(BaseModel):
    name: str = Field(min_length=1)
    linkedin_url: str | None = None
    firm_name: str | None = None
    location: str | None = None
    source: str = "manual"
    raw_profile: str = Field(min_length=1)


@router.post("/campaigns/{campaign_id}/people", status_code=201)
def add_person(
    campaign_id: int,
    body: PersonIn,
    org_id: int = Depends(get_org_id),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    _, role_wanted_id = _campaign_or_404(conn, campaign_id, org_id)

    firm_id = None
    if body.firm_name:
        (firm_id,) = conn.execute(
            "INSERT INTO firms (name) VALUES (%s)"
            " ON CONFLICT ((lower(name))) DO UPDATE SET updated_at = now()"
            " RETURNING id",
            (body.firm_name,),
        ).fetchone()

    try:
        key = identity_key(body.name, body.linkedin_url, firm_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    (person_id,) = conn.execute(
        "INSERT INTO people (organization_id, identity_key, name, location,"
        " firm_id, source)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (organization_id, identity_key) DO UPDATE SET"
        "   name = excluded.name,"
        "   location = coalesce(excluded.location, people.location),"
        "   firm_id = coalesce(excluded.firm_id, people.firm_id),"
        "   updated_at = now()"
        " RETURNING id",
        (org_id, key, body.name, body.location, firm_id, body.source),
    ).fetchone()

    conn.execute(
        "INSERT INTO person_profiles (organization_id, person_id,"
        " role_wanted_id, raw_text)"
        " VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (person_id, role_wanted_id) DO UPDATE SET"
        "   raw_text = excluded.raw_text, updated_at = now()",
        (org_id, person_id, role_wanted_id, body.raw_profile),
    )

    cp_row = conn.execute(
        "INSERT INTO campaign_people (organization_id, campaign_id, person_id)"
        " VALUES (%s, %s, %s)"
        " ON CONFLICT (campaign_id, person_id) DO NOTHING"
        " RETURNING id",
        (org_id, campaign_id, person_id),
    ).fetchone()
    if cp_row is None:
        # Already in the campaign: undo the name/profile overwrites too — a
        # rejected request must not desync raw_text from the stored evaluation.
        conn.rollback()
        raise HTTPException(
            status_code=409, detail="person is already in this campaign"
        )
    (campaign_person_id,) = cp_row

    enqueue(conn, "evaluate_profile", campaign_person_id=campaign_person_id,
            organization_id=org_id)
    conn.commit()
    return {"campaign_person_id": campaign_person_id, "person_id": person_id}


# ----------------------------------------------------------- review queue


@router.get("/campaigns/{campaign_id}/review")
def review_queue(
    campaign_id: int,
    org_id: int = Depends(get_org_id),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    _campaign_or_404(conn, campaign_id, org_id)
    rows = conn.execute(
        "SELECT cp.id, p.name, f.name, p.location,"
        "       pr.bucket, pr.ai_reasoning, pr.extracted, pr.raw_text"
        "  FROM campaign_people cp"
        "  JOIN campaigns c ON c.id = cp.campaign_id"
        "  JOIN people p ON p.id = cp.person_id"
        "  LEFT JOIN firms f ON f.id = p.firm_id"
        "  LEFT JOIN person_profiles pr"
        "    ON pr.person_id = p.id AND pr.role_wanted_id = c.role_wanted_id"
        " WHERE cp.campaign_id = %s AND cp.stage = 'screened'"
        " ORDER BY CASE pr.bucket WHEN 'strong' THEN 0 ELSE 1 END, cp.id",
        (campaign_id,),
    ).fetchall()
    keys = ("id", "person_name", "firm_name", "location", "bucket",
            "ai_reasoning", "extracted", "raw_text")
    return {"review": [dict(zip(keys, r)) for r in rows]}


class DecisionIn(BaseModel):
    verdict: str = Field(pattern="^(approve|reject)$")
    reason: str | None = None
    note: str | None = None


@router.post("/campaign-people/{campaign_person_id}/decision")
def decide(
    campaign_person_id: int,
    body: DecisionIn,
    org_id: int = Depends(get_org_id),
    auth: AuthContext = Depends(get_auth),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    row = conn.execute(
        "SELECT cp.id, pr.bucket, pr.ai_reasoning"
        "  FROM campaign_people cp"
        "  JOIN campaigns c ON c.id = cp.campaign_id"
        "  LEFT JOIN person_profiles pr ON pr.person_id = cp.person_id"
        "   AND pr.role_wanted_id = c.role_wanted_id"
        " WHERE cp.id = %s AND cp.organization_id = %s",
        (campaign_person_id, org_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="campaign person not found")
    _, bucket, ai_reasoning = row

    if body.verdict == "reject" and not body.reason:
        raise HTTPException(status_code=422, detail="rejection requires a reason")

    conn.execute(
        "INSERT INTO decisions (organization_id, campaign_person_id, verdict,"
        " reason, note, ai_said)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (org_id, campaign_person_id, body.verdict, body.reason, body.note,
         Jsonb({"bucket": bucket, "reasoning": ai_reasoning})),
    )
    to_stage = "approved" if body.verdict == "approve" else "rejected"
    try:
        transition(conn, campaign_person_id, to_stage, "human",
                   body.reason or f"human {body.verdict}")
    except (IllegalTransition, ActorNotAllowed) as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    conn.commit()
    return {"stage": to_stage}


class ProfileUpdateIn(BaseModel):
    raw_profile: str = Field(min_length=1)


@router.patch("/campaign-people/{campaign_person_id}/profile")
def update_profile(
    campaign_person_id: int,
    body: ProfileUpdateIn,
    org_id: int = Depends(get_org_id),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    """Replace the profile text and re-run the evaluation. Only meaningful
    before the human gate: at sourced or screened. The re-run updates the
    bucket in place; it never re-transitions a screened person."""
    row = conn.execute(
        "SELECT cp.stage, cp.person_id, c.role_wanted_id"
        "  FROM campaign_people cp JOIN campaigns c ON c.id = cp.campaign_id"
        " WHERE cp.id = %s AND cp.organization_id = %s",
        (campaign_person_id, org_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="campaign person not found")
    stage, person_id, role_wanted_id = row
    if stage not in ("sourced", "screened"):
        raise HTTPException(
            status_code=409,
            detail=f"profile is frozen once past the review gate (stage {stage})",
        )
    conn.execute(
        "INSERT INTO person_profiles (organization_id, person_id,"
        " role_wanted_id, raw_text) VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (person_id, role_wanted_id) DO UPDATE SET"
        "   raw_text = excluded.raw_text, updated_at = now()",
        (org_id, person_id, role_wanted_id, body.raw_profile),
    )
    (revision,) = conn.execute(
        "SELECT extract(epoch FROM updated_at)::bigint FROM person_profiles"
        " WHERE person_id = %s AND role_wanted_id = %s",
        (person_id, role_wanted_id),
    ).fetchone()
    enqueue(
        conn, "evaluate_profile", campaign_person_id=campaign_person_id,
        organization_id=org_id,
        key=f"evaluate_profile:cp:{campaign_person_id}:r{revision}",
    )
    conn.commit()
    return {"enqueued": True}


@router.post("/campaigns/{campaign_id}/approve-all-strong")
def approve_all_strong(
    campaign_id: int,
    org_id: int = Depends(get_org_id),
    auth: AuthContext = Depends(get_auth),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    """One request, one transition() per person, always with the human actor.

    FOR UPDATE locks the screened+strong rows up front: a person decided
    concurrently is simply not selected (or we wait for that decision to
    commit first), so the batch is atomic — no per-person race handling.
    """
    _campaign_or_404(conn, campaign_id, org_id)
    rows = conn.execute(
        "SELECT cp.id, pr.ai_reasoning"
        "  FROM campaign_people cp"
        "  JOIN campaigns c ON c.id = cp.campaign_id"
        "  JOIN person_profiles pr ON pr.person_id = cp.person_id"
        "   AND pr.role_wanted_id = c.role_wanted_id"
        " WHERE cp.campaign_id = %s AND cp.stage = 'screened'"
        "   AND pr.bucket = 'strong'"
        " ORDER BY cp.id"
        " FOR UPDATE OF cp",
        (campaign_id,),
    ).fetchall()
    for cp_id, ai_reasoning in rows:
        conn.execute(
            "INSERT INTO decisions (organization_id, campaign_person_id,"
            " verdict, reason, ai_said)"
            " VALUES (%s, %s, 'approve', 'approve all strong', %s)",
            (org_id, cp_id,
             Jsonb({"bucket": "strong", "reasoning": ai_reasoning})),
        )
        transition(conn, cp_id, "approved", "human", "approve all strong")
    conn.commit()
    return {"approved": len(rows)}
