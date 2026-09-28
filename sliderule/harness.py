"""Hand-written agent tool-use loop.

An agent is a system prompt, a user prompt and a list of Tools. The harness
runs the conversation against the injectable ModelClient and enforces the
rules from CLAUDE.md:

  * every tool carries a credit_cost; a call that would take the run past
    its budget is refused — not executed, not charged — and the refusal is
    returned to the model as an error tool_result so it can adapt or stop;
  * a step cap ends the run (status step_capped) however the model feels;
  * every tool call — executed, refused or invalid — is written to
    agent_tool_calls before its result goes back to the model.

Agents never receive a transition, send or unbounded-spend tool: the tools
are whatever the caller passes, and only sliderule/steps/ calls run_agent.
A grep test keeps transition() and enqueue() out of sliderule/agents/.

The loop runs inside the caller's transaction (the worker wraps each step in
one). A tool that raises anything other than ToolError aborts the run: the
job fails and retries, and the partial log rolls back with it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from sliderule.adapters.model import ModelClient


class ToolError(Exception):
    """A tool rejected its arguments. Reported to the model as an error
    result and not charged; the run continues."""


ToolFn = Callable[[dict], dict]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    credit_cost: float
    run: ToolFn

    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class AgentResult:
    agent_run_id: int
    status: str  # done | budget_exceeded | step_capped
    final_text: str | None
    credits_spent: float
    steps: int


def run_agent(
    conn: psycopg.Connection,
    *,
    agent: str,
    tools: list[Tool],
    system: str,
    user: str,
    budget_credits: float,
    max_steps: int,
    model_client: ModelClient,
    model_name: str | None = None,
    job_id: int | None = None,
    organization_id: int | None = None,
) -> AgentResult:
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")
    by_name = {t.name: t for t in tools}
    if len(by_name) != len(tools):
        raise ValueError("duplicate tool names")
    specs = [t.spec() for t in tools]

    (run_id,) = conn.execute(
        "INSERT INTO agent_runs (organization_id, job_id, agent, model)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (organization_id, job_id, agent, model_name),
    ).fetchone()

    messages: list[dict] = [{"role": "user", "content": user}]
    spent = 0.0
    refused = False
    final_text: str | None = None
    status = "step_capped"
    steps = 0

    for _ in range(max_steps):
        steps += 1
        # a snapshot per turn, so a recording client sees what this turn saw
        response = model_client.complete_with_tools(
            system=system, messages=list(messages), tools=specs
        )
        content = response["content"]
        messages.append({"role": "assistant", "content": content})
        tool_uses = [b for b in content if b.get("type") == "tool_use"]

        if response["stop_reason"] != "tool_use" or not tool_uses:
            final_text = "\n".join(
                b["text"] for b in content if b.get("type") == "text"
            ) or None
            status = "budget_exceeded" if refused else "done"
            break

        results = []
        for use in tool_uses:
            name, args = use["name"], use.get("input") or {}
            tool = by_name.get(name)
            is_error = True
            cost = 0.0
            if tool is None:
                result = {"error": f"unknown tool {name!r}"}
            elif spent + tool.credit_cost > budget_credits:
                refused = True
                result = {
                    "error": "budget exceeded: this tool costs "
                    f"{tool.credit_cost:g} credit(s); {spent:g} of "
                    f"{budget_credits:g} spent. Not executed. Finish with what "
                    "you have.",
                }
            else:
                try:
                    result = tool.run(args)
                    cost = tool.credit_cost
                    spent += cost
                    is_error = False
                except ToolError as exc:
                    result = {"error": str(exc)}
            # Log before the model sees the result: the audit trail is
            # complete even when the model never gets another turn.
            conn.execute(
                "INSERT INTO agent_tool_calls"
                " (agent_run_id, tool_name, arguments, result, credit_cost)"
                " VALUES (%s, %s, %s, %s, %s)",
                (run_id, name, Jsonb(args), Jsonb(result), cost),
            )
            results.append({
                "type": "tool_result",
                "tool_use_id": use["id"],
                "content": json.dumps(result),
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": results})

    conn.execute(
        "UPDATE agent_runs SET status = %s, credits_spent = %s,"
        " finished_at = now() WHERE id = %s",
        (status, spent, run_id),
    )
    return AgentResult(
        agent_run_id=run_id, status=status, final_text=final_text,
        credits_spent=spent, steps=steps,
    )
