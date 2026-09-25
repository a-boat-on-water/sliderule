"""evaluate_profile step: fixture-replayed model call, no network."""

from pathlib import Path

import pytest

from sliderule.adapters.model import ReplayModelClient
from sliderule.steps import evaluate_profile as step_module
from sliderule.worker import enqueue, run_once

FIXTURE = Path(__file__).parent / "fixtures" / "evaluate_profile.json"


class FakeModel:
    def __init__(self, bucket: str):
        self.bucket = bucket
        self.calls: list[dict] = []

    def complete_structured(self, *, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return {
            "bucket": self.bucket,
            "reasoning": f"fake: {self.bucket}",
            "extracted": {
                "current_title": None, "years_experience": None,
                "location": None, "licenses": [], "skills": [],
            },
        }


@pytest.fixture
def sourced_with_profile(conn, seed):
    ids = seed(stage="sourced")
    conn.execute(
        "INSERT INTO person_profiles (organization_id, person_id,"
        " role_wanted_id, raw_text) VALUES (%s, %s, %s,"
        " 'Senior mechanical designer, 8 years at a NYC MEP firm.')",
        (ids["org_id"], ids["person_id"], ids["role_id"]),
    )
    enqueue(conn, "evaluate_profile", campaign_person_id=ids["campaign_person_id"],
            organization_id=ids["org_id"])
    conn.commit()
    return ids


def run_with(monkeypatch, conn, client):
    monkeypatch.setattr(step_module, "model_client", client)
    assert run_once(conn) is True


def get_stage(conn, cp_id):
    return conn.execute(
        "SELECT stage FROM campaign_people WHERE id = %s", (cp_id,)
    ).fetchone()[0]


def test_replayed_strong_evaluation_screens(monkeypatch, conn, sourced_with_profile):
    ids = sourced_with_profile
    run_with(monkeypatch, conn, ReplayModelClient(FIXTURE))

    assert get_stage(conn, ids["campaign_person_id"]) == "screened"
    bucket, reasoning, extracted = conn.execute(
        "SELECT bucket, ai_reasoning, extracted FROM person_profiles"
        " WHERE person_id = %s", (ids["person_id"],)
    ).fetchone()
    assert bucket == "strong"
    assert "hard requirement" in reasoning
    assert extracted["years_experience"] == 8


def test_model_sees_rubric_and_profile(monkeypatch, conn, sourced_with_profile):
    fake = FakeModel("possible")
    run_with(monkeypatch, conn, fake)

    (call,) = fake.calls
    assert "Mechanical engineer" in call["user"]  # the role title from seed
    assert "8 years" in call["user"]              # the raw profile text
    assert call["schema"]["properties"]["bucket"]["enum"] == [
        "strong", "possible", "no",
    ]


def test_clear_no_goes_straight_to_rejected(monkeypatch, conn, sourced_with_profile):
    ids = sourced_with_profile
    run_with(monkeypatch, conn, FakeModel("no"))

    assert get_stage(conn, ids["campaign_person_id"]) == "rejected"
    events = conn.execute(
        "SELECT to_stage, actor FROM stage_events WHERE campaign_person_id = %s",
        (ids["campaign_person_id"],),
    ).fetchall()
    assert events == [("rejected", "system")]


def test_past_the_gate_is_a_noop(monkeypatch, conn, seed):
    ids = seed(stage="approved")
    conn.execute(
        "INSERT INTO person_profiles (organization_id, person_id,"
        " role_wanted_id, raw_text, bucket) VALUES (%s, %s, %s, 'text', 'strong')",
        (ids["org_id"], ids["person_id"], ids["role_id"]),
    )
    enqueue(conn, "evaluate_profile", campaign_person_id=ids["campaign_person_id"])
    conn.commit()

    fake = FakeModel("no")
    run_with(monkeypatch, conn, fake)
    assert fake.calls == []  # no model call, no transition, bucket untouched
    assert get_stage(conn, ids["campaign_person_id"]) == "approved"


def test_screened_reevaluation_updates_bucket_without_transition(
    monkeypatch, conn, seed
):
    ids = seed(stage="screened")
    conn.execute(
        "INSERT INTO person_profiles (organization_id, person_id,"
        " role_wanted_id, raw_text, bucket) VALUES (%s, %s, %s,"
        " 'updated text', 'strong')",
        (ids["org_id"], ids["person_id"], ids["role_id"]),
    )
    enqueue(conn, "evaluate_profile", campaign_person_id=ids["campaign_person_id"])
    conn.commit()

    run_with(monkeypatch, conn, FakeModel("no"))
    (bucket,) = conn.execute(
        "SELECT bucket FROM person_profiles WHERE person_id = %s",
        (ids["person_id"],),
    ).fetchone()
    assert bucket == "no"  # refreshed
    # but never re-transitioned: a screened person's edges are human-only
    assert get_stage(conn, ids["campaign_person_id"]) == "screened"
    events = conn.execute(
        "SELECT count(*) FROM stage_events WHERE campaign_person_id = %s",
        (ids["campaign_person_id"],),
    ).fetchone()[0]
    assert events == 0


def test_missing_profile_fails_the_job(monkeypatch, conn, seed):
    ids = seed(stage="sourced")
    job_id = enqueue(conn, "evaluate_profile",
                     campaign_person_id=ids["campaign_person_id"])
    conn.commit()

    run_with(monkeypatch, conn, FakeModel("strong"))
    status, error = conn.execute(
        "SELECT status, last_error FROM jobs WHERE id = %s", (job_id,)
    ).fetchone()
    assert status == "queued"  # retrying; will fail permanently
    assert "raw text" in error
