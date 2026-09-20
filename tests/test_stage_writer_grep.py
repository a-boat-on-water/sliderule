"""The state machine is the only writer of campaign_people.stage.

Greps every source file in the repo (outside sliderule/transition.py) for SQL
that writes a stage column. Fails, naming the offending files, when the rule
is broken. Patterns are deliberately broad: an UPDATE that sets stage, or a
stage assignment to a SQL placeholder (%s, %(name)s, :name, $1) or a quoted
SQL literal.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWED = Path("sliderule/transition.py")

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    "web",
}

WRITE_PATTERNS = [
    # an UPDATE that sets the stage column
    re.compile(r"(?i)\bset\s+stage\b"),
    # a stage assignment to a SQL placeholder or quoted SQL literal
    re.compile(r"(?i)\bstage\s*=\s*(%s|%\(|:\w|\$\d|')"),
]


def source_files():
    for path in REPO_ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in {".py", ".sql"}:
            yield path


def test_only_transition_py_writes_stage():
    offenders = []
    for path in source_files():
        rel = path.relative_to(REPO_ROOT)
        if rel == ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in WRITE_PATTERNS:
            if pattern.search(text):
                offenders.append(str(rel))
                break
    assert not offenders, (
        "stage is written outside sliderule/transition.py — the state machine "
        f"is the only writer of stage. Offending files: {offenders}"
    )
