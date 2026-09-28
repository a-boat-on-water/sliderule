"""Email finder and verifier adapters against recorded provider responses —
no network, no API keys."""

import pytest

from sliderule.adapters.email_finder import (
    Apollo, FoundEmail, Hunter, get_email_finder,
)
from sliderule.adapters.verifier import HunterVerifier, get_verifier


class Recorded:
    def __init__(self, body: dict):
        self.body = body
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.body


HUNTER_FOUND = {"data": {"first_name": "Test", "last_name": "Person",
                         "email": "tperson@acme-eng.com", "score": 92,
                         "domain": "acme-eng.com"}}
HUNTER_MISS = {"data": {"email": None, "score": None}}
APOLLO_FOUND = {"person": {"email": "tperson@acme-eng.com",
                           "email_status": "verified"}}
APOLLO_MISS = {"person": None}


def test_hunter_parses_a_hit_and_sends_the_query():
    request = Recorded(HUNTER_FOUND)
    found = Hunter(request, api_key="k").find(
        first_name="Test", last_name="Person", domain="acme-eng.com",
    )
    assert found == [FoundEmail("tperson@acme-eng.com", 92, "hunter")]
    (method, url, kwargs), = request.calls
    assert method == "GET" and url == Hunter.URL
    assert kwargs["params"] == {
        "domain": "acme-eng.com", "first_name": "Test", "last_name": "Person",
        "api_key": "k",
    }


def test_hunter_miss_is_an_empty_list():
    assert Hunter(Recorded(HUNTER_MISS), api_key="k").find(
        first_name="a", last_name="b", domain="x.com") == []


def test_apollo_parses_a_hit_and_never_asks_for_personal_emails():
    request = Recorded(APOLLO_FOUND)
    found = Apollo(request, api_key="k").find(
        first_name="Test", last_name="Person", domain="acme-eng.com",
    )
    assert found == [FoundEmail("tperson@acme-eng.com", 95, "apollo")]
    (method, url, kwargs), = request.calls
    assert method == "POST"
    assert kwargs["json"]["reveal_personal_emails"] is False
    assert kwargs["headers"] == {"x-api-key": "k"}


def test_apollo_miss_is_an_empty_list():
    assert Apollo(Recorded(APOLLO_MISS), api_key="k").find(
        first_name="a", last_name="b", domain="x.com") == []


def test_missing_api_key_fails_at_call_time_not_construction(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    finder = Hunter(Recorded(HUNTER_FOUND))  # fine
    with pytest.raises(RuntimeError, match="HUNTER_API_KEY"):
        finder.find(first_name="a", last_name="b", domain="x.com")


def test_finder_is_picked_by_env(monkeypatch):
    monkeypatch.setenv("SLIDERULE_EMAIL_FINDER", "apollo")
    assert get_email_finder().name == "apollo"
    monkeypatch.delenv("SLIDERULE_EMAIL_FINDER")
    assert get_email_finder().name == "hunter"
    with pytest.raises(LookupError):
        get_email_finder("nope")


@pytest.mark.parametrize("result,status,expected", [
    ("deliverable", "valid", "valid"),
    ("undeliverable", "invalid", "invalid"),
    ("risky", "accept_all", "risky"),
    ("", "unknown", "risky"),      # provider couldn't say: never valid
])
def test_hunter_verifier_maps_results(result, status, expected):
    body = {"data": {"result": result, "status": status}}
    verified = HunterVerifier(Recorded(body), api_key="k").verify("a@x.com")
    assert verified.status == expected
    assert verified.detail == status


def test_verifier_is_picked_by_env(monkeypatch):
    monkeypatch.delenv("SLIDERULE_VERIFIER", raising=False)
    assert get_verifier().name == "hunter"
    with pytest.raises(LookupError):
        get_verifier("nope")
