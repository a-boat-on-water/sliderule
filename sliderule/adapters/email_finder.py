"""Email-finder adapter interface: name + company domain -> candidate work
addresses. One implementation per provider (hunter, apollo). Every call is
paid, so callers go through the harness with a credit_cost; the provider is
picked by SLIDERULE_EMAIL_FINDER (default hunter).

HTTP goes through an injectable `request` callable so tests replay recorded
provider responses and never touch the network.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

# request(method, url, params=..., json=..., headers=...) -> parsed JSON body
Request = Callable[..., dict]


def _default_request(method: str, url: str, **kwargs) -> dict:
    response = httpx.request(method, url, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


@dataclass(frozen=True)
class FoundEmail:
    address: str
    confidence: int | None  # 0-100 as the provider reports it, None if unknown
    source: str


class EmailFinder(Protocol):
    name: str

    def find(self, *, first_name: str, last_name: str, domain: str) -> list[FoundEmail]: ...


class Hunter:
    """Hunter.io Email Finder: GET /v2/email-finder. One request per call."""

    name = "hunter"
    URL = "https://api.hunter.io/v2/email-finder"

    def __init__(self, request: Request = _default_request, api_key: str | None = None):
        self._request = request
        self._api_key = api_key

    def find(self, *, first_name: str, last_name: str, domain: str) -> list[FoundEmail]:
        body = self._request(
            "GET", self.URL,
            params={
                "domain": domain, "first_name": first_name,
                "last_name": last_name,
                "api_key": self._api_key or _require_env("HUNTER_API_KEY"),
            },
        )
        data = body.get("data") or {}
        if not data.get("email"):
            return []
        return [FoundEmail(data["email"], data.get("score"), self.name)]


class Apollo:
    """Apollo.io People Match: POST /api/v1/people/match. Work email only —
    personal emails are never requested."""

    name = "apollo"
    URL = "https://api.apollo.io/api/v1/people/match"
    _STATUS_CONFIDENCE = {"verified": 95, "likely to engage": 90,
                          "extrapolated": 60, "guessed": 40}

    def __init__(self, request: Request = _default_request, api_key: str | None = None):
        self._request = request
        self._api_key = api_key

    def find(self, *, first_name: str, last_name: str, domain: str) -> list[FoundEmail]:
        body = self._request(
            "POST", self.URL,
            json={
                "first_name": first_name, "last_name": last_name,
                "domain": domain, "reveal_personal_emails": False,
            },
            headers={"x-api-key": self._api_key or _require_env("APOLLO_API_KEY")},
        )
        person = body.get("person") or {}
        if not person.get("email"):
            return []
        status = (person.get("email_status") or "").lower()
        return [FoundEmail(person["email"], self._STATUS_CONFIDENCE.get(status),
                           self.name)]


FINDERS: dict[str, Callable[[], EmailFinder]] = {"hunter": Hunter, "apollo": Apollo}


def get_email_finder(name: str | None = None) -> EmailFinder:
    name = name or os.environ.get("SLIDERULE_EMAIL_FINDER", "hunter")
    if name not in FINDERS:
        raise LookupError(f"unknown email finder {name!r}; known: {sorted(FINDERS)}")
    return FINDERS[name]()
