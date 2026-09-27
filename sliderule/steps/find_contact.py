"""find_contact: the first paid step, and only ever after a human approved.

Runs the find_contact agent for one approved campaign person, then reads
contact_methods: if any row is valid the system moves approved ->
contactable. The agent never transitions; nothing here sends anything.

Gates, in order: stage must be approved (anything else is a no-op — the job
was enqueued for a person who has since moved on); do_not_contact wins
over everything; an already-valid address (from an earlier campaign, or
entered by hand) skips the agent and its spend entirely.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import psycopg

from sliderule.adapters.email_finder import EmailFinder, get_email_finder
from sliderule.adapters.model import AnthropicModelClient, ModelClient
from sliderule.adapters.verifier import Verifier, get_verifier
from sliderule.agents.find_contact import (
    AGENT_NAME, SYSTEM_PROMPT, Subject, build_tools, user_prompt,
)
from sliderule.harness import run_agent
from sliderule.transition import transition
from sliderule.worker import register_step

log = logging.getLogger(__name__)

# Injectable for tests; production lazily constructs the real ones.
model_client: ModelClient | None = None
email_finder: EmailFinder | None = None
verifier: Verifier | None = None


def _budget() -> float:
    return float(os.environ.get("SLIDERULE_FIND_CONTACT_BUDGET", "4"))


def _max_steps() -> int:
    return int(os.environ.get("SLIDERULE_FIND_CONTACT_MAX_STEPS", "8"))


def _valid_address(conn: psycopg.Connection, person_id: int) -> tuple | None:
    return conn.execute(
        "SELECT address, found_via FROM contact_methods"
        " WHERE person_id = %s AND verify_status = 'valid'"
        " ORDER BY verified_at DESC NULLS LAST, id LIMIT 1",
        (person_id,),
    ).fetchone()


@register_step("find_contact")
def find_contact(conn: psycopg.Connection, job: dict[str, Any]) -> None:
    cp_id = job["campaign_person_id"]
    row = conn.execute(
        "SELECT cp.stage, cp.organization_id, p.id, p.name, p.location,"
        "       p.do_not_contact, f.name, f.website"
        "  FROM campaign_people cp"
        "  JOIN people p ON p.id = cp.person_id"
        "  LEFT JOIN firms f ON f.id = p.firm_id"
        " WHERE cp.id = %s",
        (cp_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"campaign_person {cp_id} not found")
    (stage, org_id, person_id, name, location, do_not_contact,
     firm_name, firm_website) = row

    if stage != "approved":
        log.info("find_contact: cp %s is %s, not approved; skipping", cp_id, stage)
        return
    if do_not_contact:
        log.info("find_contact: person %s is do_not_contact; skipping", person_id)
        return

    existing_valid = _valid_address(conn, person_id)
    if existing_valid is None:
        existing = conn.execute(
            "SELECT address, verify_status, found_via FROM contact_methods"
            " WHERE person_id = %s ORDER BY id",
            (person_id,),
        ).fetchall()
        subject = Subject(
            person_id=person_id, organization_id=org_id, name=name,
            location=location, firm_name=firm_name, firm_website=firm_website,
            existing=[tuple(r) for r in existing],
        )
        client = model_client if model_client is not None else AnthropicModelClient()
        result = run_agent(
            conn,
            agent=AGENT_NAME,
            tools=build_tools(
                conn, subject,
                email_finder if email_finder is not None else get_email_finder(),
                verifier if verifier is not None else get_verifier(),
            ),
            system=SYSTEM_PROMPT,
            user=user_prompt(subject),
            budget_credits=_budget(),
            max_steps=_max_steps(),
            model_client=client,
            model_name=getattr(client, "model", None),
            job_id=job["id"],
            organization_id=org_id,
        )
        log.info("find_contact: run %s %s after %s steps, %s credits: %s",
                 result.agent_run_id, result.status, result.steps,
                 result.credits_spent, result.final_text)
        existing_valid = _valid_address(conn, person_id)

    if existing_valid is None:
        return  # stays approved; a human can add an address and re-enqueue
    address, found_via = existing_valid
    transition(conn, cp_id, "contactable", "system",
               f"verified {address} via {found_via or 'unknown'}")
