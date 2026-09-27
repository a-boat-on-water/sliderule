"""The agent harness: budget refusal, step cap, audit log. Scripted fake
model, fake tools, no network."""

import json

import pytest

from sliderule.harness import Tool, ToolError, run_agent


class ScriptedModel:
    """Serves canned turns; records every request it saw."""

    def __init__(self, turns: list[dict]):
        self.turns = list(turns)
        self.requests: list[dict] = []

    def complete_with_tools(self, *, system, messages, tools):
        self.requests.append({"system": system, "messages": messages, "tools": tools})
        if not self.turns:
            raise AssertionError("model called more times than scripted")
        return self.turns.pop(0)


def call(tool_id: str, name: str, **args) -> dict:
    return {
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
    }


def done(text: str = "done") -> dict:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def make_tools(log: list):
    def paid(args):
        log.append(("paid", args))
        return {"ok": True, "echo": args}

    def free(args):
        log.append(("free", args))
        if args.get("bad"):
            raise ToolError("bad argument")
        return {"ok": True}

    schema = {"type": "object", "properties": {}, "additionalProperties": True}
    return [
        Tool("paid", "costs one", schema, credit_cost=1, run=paid),
        Tool("free", "costs nothing", schema, credit_cost=0, run=free),
    ]


def tool_calls(conn, run_id):
    return conn.execute(
        "SELECT tool_name, arguments, result, credit_cost FROM agent_tool_calls"
        " WHERE agent_run_id = %s ORDER BY id",
        (run_id,),
    ).fetchall()


def run_status(conn, run_id):
    return conn.execute(
        "SELECT status, credits_spent, finished_at IS NOT NULL FROM agent_runs"
        " WHERE id = %s", (run_id,),
    ).fetchone()


def test_every_call_is_logged_and_results_go_back_to_the_model(conn):
    log = []
    model = ScriptedModel([call("t1", "paid", x=1), call("t2", "free"), done("fin")])
    result = run_agent(
        conn, agent="test", tools=make_tools(log), system="sys", user="go",
        budget_credits=5, max_steps=10, model_client=model,
    )
    assert result.status == "done"
    assert result.final_text == "fin"
    assert result.credits_spent == 1
    assert result.steps == 3
    assert log == [("paid", {"x": 1}), ("free", {})]

    calls = tool_calls(conn, result.agent_run_id)
    assert [(c[0], c[1], c[3]) for c in calls] == [("paid", {"x": 1}, 1), ("free", {}, 0)]
    assert run_status(conn, result.agent_run_id) == ("done", 1, True)

    # the third request carries the full transcript: user, assistant,
    # tool_result, assistant, tool_result
    transcript = model.requests[2]["messages"]
    assert [m["role"] for m in transcript] == ["user", "assistant", "user",
                                               "assistant", "user"]
    first_result = transcript[2]["content"][0]
    assert first_result["type"] == "tool_result"
    assert first_result["tool_use_id"] == "t1"
    assert first_result["is_error"] is False
    assert json.loads(first_result["content"]) == {"ok": True, "echo": {"x": 1}}
    # tools are sent as raw JSON Schema specs, never with credit_cost
    assert model.requests[0]["tools"][0] == {
        "name": "paid", "description": "costs one",
        "input_schema": {"type": "object", "properties": {},
                         "additionalProperties": True},
    }


def test_budget_refuses_execution_and_tells_the_model(conn):
    log = []
    model = ScriptedModel([
        call("t1", "paid"), call("t2", "paid"), call("t3", "paid"), done(),
    ])
    result = run_agent(
        conn, agent="test", tools=make_tools(log), system="s", user="u",
        budget_credits=2, max_steps=10, model_client=model,
    )
    assert len(log) == 2  # third call never executed
    assert result.credits_spent == 2
    assert result.status == "budget_exceeded"

    calls = tool_calls(conn, result.agent_run_id)
    assert len(calls) == 3  # the refusal is logged too, at zero cost
    assert calls[2][3] == 0
    assert "budget exceeded" in calls[2][2]["error"]

    refusal = model.requests[3]["messages"][-1]["content"][0]
    assert refusal["is_error"] is True
    assert "2 of 2 spent" in refusal["content"]


def test_step_cap_ends_the_run(conn):
    log = []
    model = ScriptedModel([call("t", "free")] * 3 + [done()])
    result = run_agent(
        conn, agent="test", tools=make_tools(log), system="s", user="u",
        budget_credits=5, max_steps=3, model_client=model,
    )
    assert result.status == "step_capped"
    assert result.steps == 3
    assert result.final_text is None
    assert len(model.turns) == 1  # the done() turn was never requested
    assert run_status(conn, result.agent_run_id)[0] == "step_capped"


def test_tool_error_and_unknown_tool_are_reported_not_raised(conn):
    log = []
    model = ScriptedModel([call("t1", "free", bad=True), call("t2", "nope"), done()])
    result = run_agent(
        conn, agent="test", tools=make_tools(log), system="s", user="u",
        budget_credits=5, max_steps=10, model_client=model,
    )
    assert result.status == "done"
    calls = tool_calls(conn, result.agent_run_id)
    assert calls[0][2] == {"error": "bad argument"}
    assert "unknown tool" in calls[1][2]["error"]
    for request in model.requests[1:]:
        assert request["messages"][-1]["content"][0]["is_error"] is True


def test_parallel_tool_uses_return_all_results_in_one_message(conn):
    log = []
    model = ScriptedModel([
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "a", "name": "free", "input": {}},
            {"type": "tool_use", "id": "b", "name": "paid", "input": {}},
        ]},
        done(),
    ])
    result = run_agent(
        conn, agent="test", tools=make_tools(log), system="s", user="u",
        budget_credits=5, max_steps=10, model_client=model,
    )
    results = model.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert result.credits_spent == 1


def test_unexpected_tool_exception_propagates(conn):
    def boom(args):
        raise ConnectionError("provider down")

    schema = {"type": "object", "properties": {}}
    model = ScriptedModel([call("t", "boom"), done()])
    with pytest.raises(ConnectionError):
        run_agent(
            conn, agent="test", tools=[Tool("boom", "", schema, 1, boom)],
            system="s", user="u", budget_credits=5, max_steps=5,
            model_client=model,
        )
