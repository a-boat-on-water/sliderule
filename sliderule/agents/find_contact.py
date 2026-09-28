"""find_contact agent: prompts and tools for finding one verified work email.

The agent proposes; it writes only to contact_methods, through the
validating tools below. It gets no transition, no send, and every paid call
is metered by the harness. The step (sliderule/steps/find_contact.py) reads
contact_methods afterwards and lets the state machine decide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import psycopg

from sliderule.adapters.email_finder import EmailFinder
from sliderule.adapters.verifier import Verifier
from sliderule.harness import Tool, ToolError

AGENT_NAME = "find_contact"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]+\.)+[a-z]{2,}$")

SYSTEM_PROMPT = (
    "You find one verified work email address for a candidate a small "
    "engineering firm has already decided to contact. Work from the facts "
    "given: the candidate's name and location, their firm and its website. "
    "Every find_email and verify_email call spends a credit from a small "
    "budget, so plan before calling: derive the firm's domain from its "
    "website, split the name sensibly, call find_email once per plausible "
    "domain, record_contact_method for each address worth keeping, then "
    "verify_email the most likely one. Stop as soon as an address verifies "
    "valid. If the budget refuses a call, or nothing verifies, stop and say "
    "so. Never invent addresses that no tool returned, and never guess "
    "personal (gmail, yahoo, etc.) addresses. Finish with one line: the "
    "verified address, or why none was found."
)


@dataclass(frozen=True)
class Subject:
    """What the agent is told about the person. Built by the step."""

    person_id: int
    organization_id: int
    name: str
    location: str | None
    firm_name: str | None
    firm_website: str | None
    existing: list[tuple[str, str, str | None]]  # (address, verify_status, found_via)


def domain_from_website(website: str | None) -> str | None:
    if not website:
        return None
    raw = website.strip().lower()
    host = urlparse(raw if "://" in raw else f"//{raw}").hostname or ""
    host = host.removeprefix("www.")
    return host if DOMAIN_RE.match(host) else None


def user_prompt(subject: Subject) -> str:
    lines = [f"Candidate: {subject.name}"]
    if subject.location:
        lines.append(f"Location: {subject.location}")
    lines.append(f"Firm: {subject.firm_name or 'unknown'}")
    if subject.firm_website:
        lines.append(f"Firm website: {subject.firm_website}")
        domain = domain_from_website(subject.firm_website)
        if domain:
            lines.append(f"Firm domain (derived): {domain}")
    if subject.existing:
        lines.append("Addresses already on file:")
        for address, status, via in subject.existing:
            lines.append(f"  - {address} ({status}, via {via or 'unknown'})")
        lines.append(
            "Verify an unverified address on file before spending on a search."
        )
    else:
        lines.append("No addresses on file.")
    return "\n".join(lines)


def build_tools(
    conn: psycopg.Connection,
    subject: Subject,
    finder: EmailFinder,
    verifier: Verifier,
) -> list[Tool]:
    def find_email(args: dict) -> dict:
        first = (args.get("first_name") or "").strip()
        last = (args.get("last_name") or "").strip()
        domain = (args.get("domain") or "").strip().lower()
        if not first or not last:
            raise ToolError("first_name and last_name are required")
        if not DOMAIN_RE.match(domain):
            raise ToolError(f"{domain!r} is not a bare domain like example.com")
        found = finder.find(first_name=first, last_name=last, domain=domain)
        return {
            "provider": finder.name,
            "candidates": [
                {"address": f.address, "confidence": f.confidence} for f in found
            ],
        }

    def record_contact_method(args: dict) -> dict:
        address = (args.get("address") or "").strip().lower()
        found_via = (args.get("found_via") or "").strip() or None
        if not EMAIL_RE.match(address):
            raise ToolError(f"{address!r} is not an email address")
        row = conn.execute(
            "INSERT INTO contact_methods"
            " (organization_id, person_id, address, found_via)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (person_id, address) DO UPDATE SET"
            "   found_via = coalesce(contact_methods.found_via, excluded.found_via),"
            "   updated_at = now()"
            " RETURNING id, verify_status",
            (subject.organization_id, subject.person_id, address, found_via),
        ).fetchone()
        return {"contact_method_id": row[0], "verify_status": row[1]}

    def verify_email(args: dict) -> dict:
        address = (args.get("address") or "").strip().lower()
        row = conn.execute(
            "SELECT id, verify_status FROM contact_methods"
            " WHERE person_id = %s AND address = %s",
            (subject.person_id, address),
        ).fetchone()
        if row is None:
            raise ToolError(
                f"{address!r} is not on file for this person; call "
                "record_contact_method first"
            )
        contact_id, current = row
        if current != "unverified":
            raise ToolError(f"{address!r} is already {current}; no need to verify")
        result = verifier.verify(address)
        conn.execute(
            "UPDATE contact_methods SET verify_status = %s, verified_at = %s,"
            " updated_at = now() WHERE id = %s",
            (result.status, datetime.now().astimezone(), contact_id),
        )
        return {"address": address, "verify_status": result.status,
                "detail": result.detail, "provider": verifier.name}

    return [
        Tool(
            name="find_email",
            description="Look up a work email for a person at a company "
            "domain with the email-finder provider. Costs 1 credit. Returns "
            "zero or more candidate addresses with the provider's confidence.",
            input_schema={
                "type": "object",
                "properties": {
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "domain": {"type": "string",
                               "description": "bare domain, e.g. example.com"},
                },
                "required": ["first_name", "last_name", "domain"],
                "additionalProperties": False,
            },
            credit_cost=1,
            run=find_email,
        ),
        Tool(
            name="record_contact_method",
            description="Save an address for this person as unverified. Free. "
            "Required before verify_email.",
            input_schema={
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "found_via": {"type": "string",
                                  "description": "e.g. hunter, apollo, firm website"},
                },
                "required": ["address", "found_via"],
                "additionalProperties": False,
            },
            credit_cost=0,
            run=record_contact_method,
        ),
        Tool(
            name="verify_email",
            description="Verify a recorded address with the verifier provider "
            "and store the result (valid / invalid / risky). Costs 1 credit.",
            input_schema={
                "type": "object",
                "properties": {"address": {"type": "string"}},
                "required": ["address"],
                "additionalProperties": False,
            },
            credit_cost=1,
            run=verify_email,
        ),
    ]
