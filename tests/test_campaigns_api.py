"""Campaigns API: the full sourced -> screened -> approved flow over HTTP,
plus tenancy isolation. Model calls are faked; the worker runs inline."""

import pytest
from fastapi.testclient import TestClient

from sliderule.api.auth import AuthContext, InvalidToken
from sliderule.api.main import create_app
from sliderule.steps import evaluate_profile as step_module
from sliderule.worker import run_once
from tests.test_evaluate_profile import FakeModel


class OrgVerifier:
    """Token "tok-<clerk-org-id>" authenticates as that org."""

    def verify(self, token: str) -> AuthContext:
        if not token.startswith("tok-"):
            raise InvalidToken("bad token")
        return AuthContext(
            clerk_user_id="user_1",
            clerk_org_id=token.removeprefix("tok-"),
            role="admin",
        )


def auth(org: str) -> dict:
    return {"Authorization": f"Bearer tok-{org}"}


@pytest.fixture
def client(conn):
    return TestClient(create_app(token_verifier=OrgVerifier()))


def make_campaign(client, org: str) -> int:
    role = client.post(
        "/roles",
        json={"title": "Mechanical engineer (PT)", "rubric": {
            "hard_requirements": ["NYC mechanical production experience"],
            "green_flags": ["files DOB drawings"],
            "red_flags": ["licensed PE seeking principal role"],
        }},
        headers=auth(org),
    )
    assert role.status_code == 201
    campaign = client.post(
        "/campaigns",
        json={"name": "Fall mechanical", "role_wanted_id": role.json()["id"]},
        headers=auth(org),
    )
    assert campaign.status_code == 201
    return campaign.json()["id"]


def add_candidate(client, org: str, campaign_id: int, name: str,
                  linkedin: str | None = None):
    response = client.post(
        f"/campaigns/{campaign_id}/people",
        json={
            "name": name,
            "linkedin_url": linkedin,
            "firm_name": "MG Engineering D.P.C.",
            "raw_profile": f"{name}: mechanical designer, 8 years, NYC filings.",
        },
        headers=auth(org),
    )
    assert response.status_code == 201, response.text
    return response.json()["campaign_person_id"]


def test_free_text_rubric_flows_end_to_end(client, conn, monkeypatch):
    """The simple-UI path: one description sentence instead of three lists."""
    role = client.post(
        "/roles",
        json={"title": "PT mechanical engineer",
              "rubric": {"description": "NYC production engineer, no principals"}},
        headers=auth("org_a"),
    )
    campaign = client.post(
        "/campaigns",
        json={"name": "PT mechanical — Sep 2026",
              "role_wanted_id": role.json()["id"]},
        headers=auth("org_a"),
    )
    campaign_id = campaign.json()["id"]
    add_candidate(client, "org_a", campaign_id, "Dana Okafor")

    fake = FakeModel("strong")
    monkeypatch.setattr(step_module, "model_client", fake)
    run_once(conn)

    # the free-text rubric reaches the model verbatim
    (call,) = fake.calls
    assert "no principals" in call["user"]
    review = client.get(f"/campaigns/{campaign_id}/review", headers=auth("org_a"))
    assert len(review.json()["review"]) == 1


def test_org_is_provisioned_from_the_token(client, conn):
    response = client.get("/campaigns", headers=auth("org_new"))
    assert response.status_code == 200
    (clerk_id,) = conn.execute(
        "SELECT clerk_org_id FROM organizations WHERE clerk_org_id = 'org_new'"
    ).fetchone()
    assert clerk_id == "org_new"


def test_full_flow_add_evaluate_review_approve(client, conn, monkeypatch):
    campaign_id = make_campaign(client, "org_a")
    cp_id = add_candidate(client, "org_a", campaign_id, "Dana Okafor",
                          linkedin="https://www.LinkedIn.com/in/dana-okafor/")

    # the route enqueued evaluation; the worker runs it with a faked model
    monkeypatch.setattr(step_module, "model_client", FakeModel("strong"))
    assert run_once(conn) is True

    review = client.get(f"/campaigns/{campaign_id}/review", headers=auth("org_a"))
    (card,) = review.json()["review"]
    assert card["person_name"] == "Dana Okafor"
    assert card["bucket"] == "strong"
    assert card["firm_name"] == "MG Engineering D.P.C."

    decision = client.post(
        f"/campaign-people/{cp_id}/decision",
        json={"verdict": "approve"},
        headers=auth("org_a"),
    )
    assert decision.status_code == 200
    assert decision.json()["stage"] == "approved"

    board = client.get(f"/campaigns/{campaign_id}/board", headers=auth("org_a"))
    assert [c["id"] for c in board.json()["board"]["approved"]] == [cp_id]

    # decision row recorded what the AI said
    verdict, ai_said = conn.execute(
        "SELECT verdict, ai_said FROM decisions WHERE campaign_person_id = %s",
        (cp_id,),
    ).fetchone()
    assert verdict == "approve"
    assert ai_said["bucket"] == "strong"


def test_reject_requires_a_reason(client, conn, monkeypatch):
    campaign_id = make_campaign(client, "org_a")
    cp_id = add_candidate(client, "org_a", campaign_id, "Sam Lin")
    monkeypatch.setattr(step_module, "model_client", FakeModel("possible"))
    run_once(conn)

    no_reason = client.post(
        f"/campaign-people/{cp_id}/decision",
        json={"verdict": "reject"},
        headers=auth("org_a"),
    )
    assert no_reason.status_code == 422

    with_reason = client.post(
        f"/campaign-people/{cp_id}/decision",
        json={"verdict": "reject", "reason": "wrong discipline"},
        headers=auth("org_a"),
    )
    assert with_reason.status_code == 200
    assert with_reason.json()["stage"] == "rejected"


def test_approve_all_strong_skips_possible(client, conn, monkeypatch):
    campaign_id = make_campaign(client, "org_a")
    add_candidate(client, "org_a", campaign_id, "Strong One")
    add_candidate(client, "org_a", campaign_id, "Possible One")

    monkeypatch.setattr(step_module, "model_client", FakeModel("strong"))
    run_once(conn)
    monkeypatch.setattr(step_module, "model_client", FakeModel("possible"))
    run_once(conn)

    response = client.post(
        f"/campaigns/{campaign_id}/approve-all-strong", headers=auth("org_a")
    )
    assert response.json()["approved"] == 1

    board = client.get(f"/campaigns/{campaign_id}/board", headers=auth("org_a"))
    stages = {c["person_name"]: c["stage"]
              for cards in board.json()["board"].values() for c in cards}
    assert stages == {"Strong One": "approved", "Possible One": "screened"}


def test_duplicate_person_in_campaign_is_409_and_writes_nothing(client, conn):
    campaign_id = make_campaign(client, "org_a")
    add_candidate(client, "org_a", campaign_id, "Dana Okafor",
                  linkedin="linkedin.com/in/dana-okafor")
    dup = client.post(
        f"/campaigns/{campaign_id}/people",
        json={"name": "D. Okafor",
              "linkedin_url": "https://www.linkedin.com/in/dana-okafor/",
              "raw_profile": "same human, messier name"},
        headers=auth("org_a"),
    )
    assert dup.status_code == 409  # same identity key -> same person

    # the rejected request must not have overwritten anything: the stored
    # evaluation would otherwise describe text the model never saw
    name, raw_text = conn.execute(
        "SELECT p.name, pr.raw_text FROM people p"
        " JOIN person_profiles pr ON pr.person_id = p.id"
    ).fetchone()
    assert name == "Dana Okafor"
    assert "messier name" not in raw_text


def test_approve_all_strong_excludes_already_decided(client, conn, monkeypatch):
    campaign_id = make_campaign(client, "org_a")
    first = add_candidate(client, "org_a", campaign_id, "First Strong")
    add_candidate(client, "org_a", campaign_id, "Second Strong")
    monkeypatch.setattr(step_module, "model_client", FakeModel("strong"))
    run_once(conn)
    run_once(conn)

    # one person is decided individually before the bulk approve fires;
    # the FOR UPDATE select simply no longer matches them
    individual = client.post(
        f"/campaign-people/{first}/decision",
        json={"verdict": "approve"},
        headers=auth("org_a"),
    )
    assert individual.status_code == 200

    response = client.post(
        f"/campaigns/{campaign_id}/approve-all-strong", headers=auth("org_a")
    )
    assert response.json() == {"approved": 1}
    (decisions,) = conn.execute("SELECT count(*) FROM decisions").fetchone()
    assert decisions == 2  # one individual, one bulk — no duplicates


def test_profile_update_reevaluates_without_retransition(client, conn, monkeypatch):
    campaign_id = make_campaign(client, "org_a")
    cp_id = add_candidate(client, "org_a", campaign_id, "Dana Okafor")
    monkeypatch.setattr(step_module, "model_client", FakeModel("strong"))
    run_once(conn)

    patched = client.patch(
        f"/campaign-people/{cp_id}/profile",
        json={"raw_profile": "Corrected resume: actually a plumbing designer."},
        headers=auth("org_a"),
    )
    assert patched.status_code == 200, patched.text

    monkeypatch.setattr(step_module, "model_client", FakeModel("possible"))
    assert run_once(conn) is True  # the revision-keyed job

    bucket, raw_text = conn.execute(
        "SELECT bucket, raw_text FROM person_profiles"
    ).fetchone()
    assert bucket == "possible"          # refreshed in place
    assert "plumbing" in raw_text
    (stage,) = conn.execute(
        "SELECT stage FROM campaign_people WHERE id = %s", (cp_id,)
    ).fetchone()
    assert stage == "screened"           # no second transition
    (events,) = conn.execute(
        "SELECT count(*) FROM stage_events WHERE campaign_person_id = %s", (cp_id,)
    ).fetchone()
    assert events == 1


def test_profile_update_frozen_past_the_gate(client, conn, monkeypatch):
    campaign_id = make_campaign(client, "org_a")
    cp_id = add_candidate(client, "org_a", campaign_id, "Dana Okafor")
    monkeypatch.setattr(step_module, "model_client", FakeModel("strong"))
    run_once(conn)
    client.post(f"/campaign-people/{cp_id}/decision",
                json={"verdict": "approve"}, headers=auth("org_a"))

    response = client.patch(
        f"/campaign-people/{cp_id}/profile",
        json={"raw_profile": "too late"},
        headers=auth("org_a"),
    )
    assert response.status_code == 409


def test_decision_works_without_a_profile_row(client, conn, seed):
    ids = seed(stage="screened")  # seed creates no person_profiles row
    org = conn.execute(
        "SELECT clerk_org_id FROM organizations WHERE id = %s", (ids["org_id"],)
    ).fetchone()[0]
    response = client.post(
        f"/campaign-people/{ids['campaign_person_id']}/decision",
        json={"verdict": "reject", "reason": "not a fit"},
        headers=auth(org),
    )
    assert response.status_code == 200
    assert response.json()["stage"] == "rejected"


def test_tenancy_org_b_cannot_see_org_a(client, conn):
    campaign_id = make_campaign(client, "org_a")

    assert client.get("/campaigns", headers=auth("org_b")).json()["campaigns"] == []
    for path in (f"/campaigns/{campaign_id}/board",
                 f"/campaigns/{campaign_id}/review"):
        assert client.get(path, headers=auth("org_b")).status_code == 404
    assert client.post(
        f"/campaigns/{campaign_id}/people",
        json={"name": "Intruder", "raw_profile": "x"},
        headers=auth("org_b"),
    ).status_code == 404
