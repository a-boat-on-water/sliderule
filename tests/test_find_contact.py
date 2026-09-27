"""find_contact step: replayed model turns, fake finder and verifier, and
the approved -> contactable gate."""

from pathlib import Path

import pytest

from sliderule.adapters.email_finder import FoundEmail
from sliderule.adapters.model import ReplayModelClient
from sliderule.adapters.verifier import VerifyResult
from sliderule.agents.find_contact import domain_from_website
from sliderule.steps import find_contact as step_module
from sliderule.worker import enqueue, run_once

FIXTURE = Path(__file__).parent / "fixtures" / "find_contact.json"


class FakeFinder:
    name = "fake-finder"

    def __init__(self, found: list[FoundEmail]):
        self.found = found
        self.calls = []

    def find(self, *, first_name, last_name, domain):
        self.calls.append((first_name, last_name, domain))
        return self.found


class FakeVerifier:
    name = "fake-verifier"

    def __init__(self, status: str):
        self.status = status
        self.calls = []

    def verify(self, address):
        self.calls.append(address)
        return VerifyResult(self.status, "fake")


@pytest.fixture
def approved(conn, seed):
    ids = seed(stage="approved")
    (firm_id,) = conn.execute(
        "INSERT INTO firms (name, website) VALUES"
        " ('Acme Engineering', 'https://www.acme-eng.com/') RETURNING id"
    ).fetchone()
    conn.execute("UPDATE people SET firm_id = %s WHERE id = %s",
                 (firm_id, ids["person_id"]))
    enqueue(conn, "find_contact", campaign_person_id=ids["campaign_person_id"],
            organization_id=ids["org_id"])
    conn.commit()
    return ids


def run_with(monkeypatch, conn, *, model=None, finder=None, verifier=None):
    monkeypatch.setattr(step_module, "model_client",
                        model or ReplayModelClient(FIXTURE))
    monkeypatch.setattr(step_module, "email_finder",
                        finder or FakeFinder([FoundEmail("tperson@acme-eng.com", 92, "hunter")]))
    monkeypatch.setattr(step_module, "verifier", verifier or FakeVerifier("valid"))
    assert run_once(conn) is True


def stage_of(conn, cp_id):
    return conn.execute("SELECT stage FROM campaign_people WHERE id = %s",
                        (cp_id,)).fetchone()[0]


def test_replayed_run_verifies_and_moves_to_contactable(monkeypatch, conn, approved):
    ids = approved
    model = ReplayModelClient(FIXTURE)
    finder = FakeFinder([FoundEmail("tperson@acme-eng.com", 92, "hunter")])
    verifier = FakeVerifier("valid")
    run_with(monkeypatch, conn, model=model, finder=finder, verifier=verifier)

    assert stage_of(conn, ids["campaign_person_id"]) == "contactable"
    assert finder.calls == [("Test", "Person", "acme-eng.com")]
    assert verifier.calls == ["tperson@acme-eng.com"]

    address, status, via, verified = conn.execute(
        "SELECT address, verify_status, found_via, verified_at IS NOT NULL"
        " FROM contact_methods WHERE person_id = %s", (ids["person_id"],)
    ).fetchone()
    assert (address, status, via, verified) == (
        "tperson@acme-eng.com", "valid", "hunter", True)

    events = conn.execute(
        "SELECT to_stage, actor, reason FROM stage_events"
        " WHERE campaign_person_id = %s", (ids["campaign_person_id"],)
    ).fetchall()
    assert events == [("contactable", "system", "verified tperson@acme-eng.com via hunter")]

    agent, status, spent, job_id = conn.execute(
        "SELECT agent, status, credits_spent, job_id FROM agent_runs"
    ).fetchone()
    assert (agent, status, spent) == ("find_contact", "done", 2)
    assert job_id is not None
    logged = conn.execute(
        "SELECT tool_name, credit_cost FROM agent_tool_calls ORDER BY id"
    ).fetchall()
    assert logged == [("find_email", 1), ("record_contact_method", 0),
                      ("verify_email", 1)]

    # the model saw the firm and the derived domain, and no transition tool
    first = model.requests[0]
    assert "acme-eng.com" in first["messages"][0]["content"]
    assert {t["name"] for t in first["tools"]} == {
        "find_email", "record_contact_method", "verify_email"}


def test_invalid_verification_leaves_person_approved(monkeypatch, conn, approved):
    ids = approved
    run_with(monkeypatch, conn, verifier=FakeVerifier("invalid"))
    assert stage_of(conn, ids["campaign_person_id"]) == "approved"
    (status,) = conn.execute(
        "SELECT verify_status FROM contact_methods WHERE person_id = %s",
        (ids["person_id"],)).fetchone()
    assert status == "invalid"
    assert conn.execute("SELECT count(*) FROM stage_events").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "done"


def test_not_approved_is_a_noop_with_no_spend(monkeypatch, conn, seed):
    ids = seed(stage="screened")
    enqueue(conn, "find_contact", campaign_person_id=ids["campaign_person_id"])
    conn.commit()
    finder = FakeFinder([])
    run_with(monkeypatch, conn, finder=finder)
    assert finder.calls == []
    assert conn.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0
    assert stage_of(conn, ids["campaign_person_id"]) == "screened"


def test_do_not_contact_is_a_noop(monkeypatch, conn, approved):
    ids = approved
    conn.execute("UPDATE people SET do_not_contact = true WHERE id = %s",
                 (ids["person_id"],))
    conn.commit()
    run_with(monkeypatch, conn)
    assert conn.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0
    assert stage_of(conn, ids["campaign_person_id"]) == "approved"


def test_existing_valid_address_skips_the_agent(monkeypatch, conn, approved):
    ids = approved
    conn.execute(
        "INSERT INTO contact_methods (organization_id, person_id, address,"
        " found_via, verify_status, verified_at)"
        " VALUES (%s, %s, 'known@acme-eng.com', 'manual', 'valid', now())",
        (ids["org_id"], ids["person_id"]),
    )
    conn.commit()
    finder = FakeFinder([])
    run_with(monkeypatch, conn, finder=finder)
    assert finder.calls == []
    assert conn.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0
    assert stage_of(conn, ids["campaign_person_id"]) == "contactable"


def test_budget_stops_paid_calls(monkeypatch, conn, approved):
    ids = approved
    monkeypatch.setenv("SLIDERULE_FIND_CONTACT_BUDGET", "1")
    verifier = FakeVerifier("valid")
    run_with(monkeypatch, conn, verifier=verifier)
    # find_email spent the only credit; verify_email was refused
    assert verifier.calls == []
    assert stage_of(conn, ids["campaign_person_id"]) == "approved"
    status, spent = conn.execute(
        "SELECT status, credits_spent FROM agent_runs").fetchone()
    assert (status, spent) == ("budget_exceeded", 1)


@pytest.mark.parametrize("website,domain", [
    ("https://www.acme-eng.com/", "acme-eng.com"),
    ("acme-eng.com", "acme-eng.com"),
    ("http://Sub.Example.co.uk/about", "sub.example.co.uk"),
    ("not a website", None),
    (None, None),
])
def test_domain_from_website(website, domain):
    assert domain_from_website(website) == domain
