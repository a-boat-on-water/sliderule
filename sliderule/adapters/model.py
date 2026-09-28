"""Model access — the one injectable seam for AI calls.

Every model call in the product goes through a ModelClient. Tests inject a
fake or replay recorded JSON fixtures; nothing outside this module imports
the anthropic SDK. Model: claude-sonnet-5 (per CLAUDE.md) unless
SLIDERULE_MODEL overrides it.

Two call shapes:
  complete_structured — one call, JSON out (evaluate_profile).
  complete_with_tools — one turn of a tool-use conversation, used by the
  harness. Messages and content blocks are plain dicts in the Messages API
  wire shape, so a recorded response can be appended back to the
  conversation verbatim (thinking blocks included).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

DEFAULT_MODEL = "claude-sonnet-5"


class ModelClient(Protocol):
    def complete_structured(self, *, system: str, user: str, schema: dict) -> dict:
        """One model call returning JSON that validates against schema."""
        ...

    def complete_with_tools(
        self, *, system: str, messages: list[dict], tools: list[dict]
    ) -> dict:
        """One turn. Returns {"stop_reason": str, "content": [block dicts]}
        where blocks are text / tool_use / thinking in wire shape."""
        ...


class AnthropicModelClient:
    """Real client. Lazy: constructing it never touches the network or
    requires ANTHROPIC_API_KEY — only the first call does."""

    def __init__(self, model: str | None = None):
        self._model = model or os.environ.get("SLIDERULE_MODEL", DEFAULT_MODEL)
        # Single structured calls (classification, extraction) don't need
        # deep reasoning; keep spend bounded but overridable.
        self._effort = os.environ.get("SLIDERULE_MODEL_EFFORT", "low")
        self._client = None

    @property
    def model(self) -> str:
        return self._model

    def _anthropic(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    @staticmethod
    def _check_stop(response) -> None:
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined the request (stop_reason=refusal)")
        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                "model output truncated at max_tokens; the output is unusable"
            )

    def complete_structured(self, *, system: str, user: str, schema: dict) -> dict:
        # Adaptive thinking is on by default and shares the max_tokens budget
        # with the answer — keep generous headroom so the JSON never truncates.
        response = self._anthropic().messages.create(
            model=self._model,
            max_tokens=8192,
            system=system,
            output_config={
                "format": {"type": "json_schema", "schema": schema},
                "effort": self._effort,
            },
            messages=[{"role": "user", "content": user}],
        )
        self._check_stop(response)
        text = next(
            (block.text for block in response.content if block.type == "text"), None
        )
        if text is None:
            raise RuntimeError(
                f"model returned no text block (stop_reason={response.stop_reason})"
            )
        return json.loads(text)

    def complete_with_tools(
        self, *, system: str, messages: list[dict], tools: list[dict]
    ) -> dict:
        response = self._anthropic().messages.create(
            model=self._model,
            max_tokens=8192,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": self._effort},
        )
        self._check_stop(response)
        as_dict = response.to_dict()
        return {"stop_reason": as_dict["stop_reason"], "content": as_dict["content"]}


class ReplayModelClient:
    """Replays recorded calls from a JSON fixture: a list of
    {"request": {...}, "response": {...}} entries, served in order. Both
    call shapes draw from the same queue, in recording order."""

    def __init__(self, fixture_path: str | Path):
        self._calls = json.loads(Path(fixture_path).read_text())
        self._index = 0
        self.requests: list[dict] = []  # what the code under test asked for

    def _next(self, request: dict) -> dict:
        if self._index >= len(self._calls):
            raise LookupError(
                f"fixture exhausted after {self._index} calls; no response recorded"
            )
        call = self._calls[self._index]
        self._index += 1
        self.requests.append(request)
        return call["response"]

    def complete_structured(self, *, system: str, user: str, schema: dict) -> dict:
        return self._next({"system": system, "user": user, "schema": schema})

    def complete_with_tools(
        self, *, system: str, messages: list[dict], tools: list[dict]
    ) -> dict:
        return self._next({"system": system, "messages": messages, "tools": tools})
