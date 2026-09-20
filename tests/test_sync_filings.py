"""sync_filings step: firms deduped by name, filings idempotent on rerun."""

import json
from pathlib import Path

import pytest

import sliderule.steps  # noqa: F401  (registers sync_filings with the worker)
from sliderule.adapters import permits
from sliderule.adapters.nyc_dob import NycDob
from sliderule.worker import enqueue, run_once

FIXTURE = Path(__file__).parent / "fixtures" / "nyc_dob_w9ak_ipjd.json"


@pytest.fixture
def fixture_source(monkeypatch):
    """Replace the nyc_dob factory with one whose HTTP seam replays the fixture."""

    def fetch_json(url, params):
        if params["$offset"] == 0:
            return json.loads(FIXTURE.read_text())
        return []

    monkeypatch.setitem(
        permits.SOURCES, "nyc_dob", lambda: NycDob(fetch_json=fetch_json)
    )


def run_sync(conn, key):
    job_id = enqueue(conn, "sync_filings", key=key)
    conn.commit()
    assert job_id is not None
    assert run_once(conn) is True
    (status,) = conn.execute(
        "SELECT status FROM jobs WHERE id = %s", (job_id,)
    ).fetchone()
    assert status == "done"


def test_sync_upserts_firms_and_filings(conn, fixture_source):
    run_sync(conn, key="sync-1")

    # KERI appears twice with different casing -> one firm; ATOZ -> one firm;
    # the no-work-type row contributes nothing
    firms = conn.execute("SELECT name, office_address FROM firms ORDER BY name").fetchall()
    assert len(firms) == 2
    names = [name for name, _ in firms]
    assert "KERI ENGINEERING, P.C." in names
    assert "ATOZ Consulting Engineers, PC" in names
    assert all(address for _, address in firms)

    filings = conn.execute(
        "SELECT external_id, work_type, filed_at FROM filings ORDER BY external_id"
    ).fetchall()
    assert len(filings) == 4
    assert {work_type for _, work_type, _ in filings} == {
        "mechanical_systems", "plumbing",
    }


def test_sync_is_idempotent_across_reruns(conn, fixture_source):
    run_sync(conn, key="sync-1")
    run_sync(conn, key="sync-2")  # same data, new job

    (firm_count,) = conn.execute("SELECT count(*) FROM firms").fetchone()
    (filing_count,) = conn.execute("SELECT count(*) FROM filings").fetchone()
    assert firm_count == 2
    assert filing_count == 4


def test_unknown_source_fails_the_job(conn):
    job_id = enqueue(conn, "sync_filings", key="bad", payload={"source": "nope"})
    conn.commit()
    assert run_once(conn) is True
    status, error = conn.execute(
        "SELECT status, last_error FROM jobs WHERE id = %s", (job_id,)
    ).fetchone()
    assert status == "queued"  # will retry, then fail permanently
    assert "nope" in error
