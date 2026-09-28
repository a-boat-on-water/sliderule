"""Email-verifier adapter interface: address -> valid / invalid / risky. The
statuses are exactly the contact_methods.verify_status values, so the agent
tool writes the result straight through. Provider picked by
SLIDERULE_VERIFIER (default hunter). Paid; goes through the harness.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from sliderule.adapters.email_finder import Request, _default_request, _require_env

Status = Literal["valid", "invalid", "risky"]


@dataclass(frozen=True)
class VerifyResult:
    status: Status
    detail: str  # provider's own word for it, for the audit log


class Verifier(Protocol):
    name: str

    def verify(self, address: str) -> VerifyResult: ...


class HunterVerifier:
    """Hunter.io Email Verifier: GET /v2/email-verifier. `result` is
    deliverable / undeliverable / risky; `status` is the finer reason."""

    name = "hunter"
    URL = "https://api.hunter.io/v2/email-verifier"
    _RESULT_STATUS: dict[str, Status] = {
        "deliverable": "valid", "undeliverable": "invalid", "risky": "risky",
    }

    def __init__(self, request: Request = _default_request, api_key: str | None = None):
        self._request = request
        self._api_key = api_key

    def verify(self, address: str) -> VerifyResult:
        body = self._request(
            "GET", self.URL,
            params={"email": address,
                    "api_key": self._api_key or _require_env("HUNTER_API_KEY")},
        )
        data = body.get("data") or {}
        result = (data.get("result") or "").lower()
        # Anything the provider can't call deliverable or undeliverable is
        # risky: the contactable gate needs an explicit valid.
        status = self._RESULT_STATUS.get(result, "risky")
        return VerifyResult(status, data.get("status") or result or "unknown")


VERIFIERS: dict[str, Callable[[], Verifier]] = {"hunter": HunterVerifier}


def get_verifier(name: str | None = None) -> Verifier:
    name = name or os.environ.get("SLIDERULE_VERIFIER", "hunter")
    if name not in VERIFIERS:
        raise LookupError(f"unknown verifier {name!r}; known: {sorted(VERIFIERS)}")
    return VERIFIERS[name]()
