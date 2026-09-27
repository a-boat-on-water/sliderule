"""Agents propose; the system transitions. Nothing under sliderule/agents/
may import the state machine, the job queue, or a mailbox — an agent's only
way to affect the pipeline is to write people / person_profiles /
contact_methods through its tools.
"""

import re
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[1] / "sliderule" / "agents"

FORBIDDEN = [
    re.compile(r"(?m)^\s*(from|import)\s+sliderule\.transition\b"),
    re.compile(r"(?m)^\s*from\s+sliderule\.worker\s+import\b"),
    re.compile(r"(?m)^\s*(from|import)\s+sliderule\.adapters\.(gmail|microsoft365|mailbox)\b"),
    re.compile(r"\btransition\("),
    re.compile(r"\benqueue\("),
]


def test_agents_never_transition_enqueue_or_send():
    offenders = []
    for path in AGENTS_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            if pattern.search(text):
                offenders.append(f"{path.name}: {pattern.pattern}")
    assert not offenders, f"agents reach past their tools: {offenders}"
