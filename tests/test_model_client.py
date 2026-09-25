"""AnthropicModelClient response handling — stubbed SDK, no network."""

import json
from types import SimpleNamespace

import pytest

from sliderule.adapters.model import AnthropicModelClient


def client_returning(**response_attrs) -> AnthropicModelClient:
    client = AnthropicModelClient(model="claude-sonnet-5")
    client._client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(**response_attrs)
        )
    )
    return client


def call(client):
    return client.complete_structured(system="s", user="u", schema={})


def test_valid_json_is_returned():
    block = SimpleNamespace(type="text", text=json.dumps({"bucket": "strong"}))
    client = client_returning(stop_reason="end_turn", content=[block])
    assert call(client) == {"bucket": "strong"}


def test_truncated_output_raises_instead_of_json_error():
    block = SimpleNamespace(type="text", text='{"bucket": "str')  # cut off
    client = client_returning(stop_reason="max_tokens", content=[block])
    with pytest.raises(RuntimeError, match="max_tokens"):
        call(client)


def test_refusal_raises():
    client = client_returning(stop_reason="refusal", content=[])
    with pytest.raises(RuntimeError, match="refusal"):
        call(client)
