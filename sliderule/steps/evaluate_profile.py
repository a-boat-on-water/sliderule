"""evaluate_profile: one structured model call per (person, role).

Free evaluation before approval — no paid enrichment, no agent loop. The
model reads the profile raw text against the role's rubric (rubric is org
data, nothing Concord-specific here) and returns a bucket. The step writes
the result to person_profiles and lets the state machine move the person:
sourced -> screened, or straight to rejected on a clear no.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sliderule.adapters.model import AnthropicModelClient, ModelClient
from sliderule.transition import transition
from sliderule.worker import register_step

# Injectable for tests; production lazily constructs the real client.
model_client: ModelClient | None = None


def _client() -> ModelClient:
    return model_client if model_client is not None else AnthropicModelClient()


EVALUATION_SCHEMA = {
    "type": "object",
    "properties": {
        "bucket": {
            "type": "string",
            "enum": ["strong", "possible", "no"],
            "description": "strong: meets every hard requirement with green "
            "flags. possible: plausibly meets the hard requirements. no: "
            "clearly fails a hard requirement or shows a red flag.",
        },
        "reasoning": {
            "type": "string",
            "description": "Two or three sentences a reviewer can check "
            "against the profile. Cite the evidence, note what is missing.",
        },
        "extracted": {
            "type": "object",
            "properties": {
                "current_title": {"type": ["string", "null"]},
                "years_experience": {"type": ["number", "null"]},
                "location": {"type": ["string", "null"]},
                "licenses": {"type": "array", "items": {"type": "string"}},
                "skills": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "current_title", "years_experience", "location",
                "licenses", "skills",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["bucket", "reasoning", "extracted"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You screen candidate profiles for small licensed-profession firms "
    "(engineering, architecture) hiring part-time or contract production "
    "engineers. You are given one candidate's raw profile text and the "
    "role's rubric: hard requirements, green flags, red flags. Judge only "
    "from the evidence in the profile. Missing evidence for a hard "
    "requirement means 'possible', not 'no' — reserve 'no' for profiles "
    "that clearly fail a hard requirement or show a red flag. Do not "
    "invent facts; extract only what the text supports."
)


def _user_prompt(name: str, role_title: str, rubric: dict, raw_text: str) -> str:
    return (
        f"Role: {role_title}\n"
        f"Rubric:\n{json.dumps(rubric, indent=2, sort_keys=True)}\n\n"
        f"Candidate: {name}\n"
        f"Profile text:\n{raw_text}"
    )


@register_step("evaluate_profile")
def evaluate_profile(conn: psycopg.Connection, job: dict[str, Any]) -> None:
    row = conn.execute(
        "SELECT cp.stage, p.name, pr.id, pr.raw_text, rw.title, rw.rubric"
        "  FROM campaign_people cp"
        "  JOIN campaigns c ON c.id = cp.campaign_id"
        "  JOIN roles_wanted rw ON rw.id = c.role_wanted_id"
        "  JOIN people p ON p.id = cp.person_id"
        "  LEFT JOIN person_profiles pr"
        "    ON pr.person_id = p.id AND pr.role_wanted_id = rw.id"
        " WHERE cp.id = %s",
        (job["campaign_person_id"],),
    ).fetchone()
    if row is None:
        raise LookupError(f"campaign_person {job['campaign_person_id']} not found")
    stage, name, profile_id, raw_text, role_title, rubric = row

    if stage not in ("sourced", "screened"):
        return  # past the human gate (or terminal): evaluation is frozen
    if profile_id is None or not raw_text:
        raise ValueError("no profile raw text to evaluate")

    result = _client().complete_structured(
        system=SYSTEM_PROMPT,
        user=_user_prompt(name, role_title, rubric, raw_text),
        schema=EVALUATION_SCHEMA,
    )

    conn.execute(
        "UPDATE person_profiles SET extracted = %s, ai_reasoning = %s,"
        " bucket = %s, updated_at = now() WHERE id = %s",
        (Jsonb(result["extracted"]), result["reasoning"], result["bucket"],
         profile_id),
    )
    if stage == "screened":
        return  # re-evaluation of updated text: bucket refreshed in place
    if result["bucket"] == "no":
        transition(conn, job["campaign_person_id"], "rejected", "system",
                   "clear no from profile evaluation")
    else:
        transition(conn, job["campaign_person_id"], "screened", "system",
                   f"evaluated: {result['bucket']}")
