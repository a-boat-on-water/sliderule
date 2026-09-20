from datetime import datetime, timedelta, timezone

import pytest

from sliderule import worker
from sliderule.worker import MAX_ATTEMPTS, enqueue, register_step, run_once


def get_job(conn, job_id):
    row = conn.execute(
        "SELECT status, attempts, run_after, last_error FROM jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    return dict(zip(("status", "attempts", "run_after", "last_error"), row))


def test_enqueue_is_idempotent(conn, seed):
    ids = seed()
    cp = ids["campaign_person_id"]
    first = enqueue(conn, "evaluate_profile", campaign_person_id=cp)
    second = enqueue(conn, "evaluate_profile", campaign_person_id=cp)
    conn.commit()

    assert first is not None
    assert second is None
    (count,) = conn.execute("SELECT count(*) FROM jobs").fetchone()
    assert count == 1


def test_enqueue_rejects_both_keys(conn):
    with pytest.raises(ValueError):
        enqueue(conn, "step", campaign_person_id=1, firm_id=1)


def test_run_once_executes_and_marks_done(conn, seed):
    ids = seed()
    cp = ids["campaign_person_id"]
    seen = []

    def handler(handler_conn, job):
        seen.append((job["step"], job["campaign_person_id"]))

    job_id = enqueue(conn, "dummy", campaign_person_id=cp)
    conn.commit()

    assert run_once(conn, steps={"dummy": handler}) is True
    assert seen == [("dummy", cp)]
    job = get_job(conn, job_id)
    assert job["status"] == "done"
    assert job["attempts"] == 1
    assert run_once(conn, steps={"dummy": handler}) is False  # nothing left


def test_failing_step_requeues_with_backoff_then_fails(conn, seed):
    ids = seed()
    cp = ids["campaign_person_id"]

    def handler(handler_conn, job):
        raise RuntimeError("boom")

    job_id = enqueue(conn, "dummy", campaign_person_id=cp)
    conn.commit()

    assert run_once(conn, steps={"dummy": handler}) is True
    job = get_job(conn, job_id)
    assert job["status"] == "queued"
    assert job["attempts"] == 1
    assert "boom" in job["last_error"]
    assert job["run_after"] > datetime.now(timezone.utc)

    for attempt in range(2, MAX_ATTEMPTS + 1):
        conn.execute(
            "UPDATE jobs SET run_after = now() WHERE id = %s", (job_id,)
        )
        conn.commit()
        assert run_once(conn, steps={"dummy": handler}) is True

    job = get_job(conn, job_id)
    assert job["status"] == "failed"
    assert job["attempts"] == MAX_ATTEMPTS


def test_failed_handler_writes_are_rolled_back(conn, seed):
    ids = seed()
    cp = ids["campaign_person_id"]

    def handler(handler_conn, job):
        handler_conn.execute(
            "INSERT INTO decisions (organization_id, campaign_person_id, verdict)"
            " VALUES (%s, %s, 'approve')",
            (job["organization_id"], job["campaign_person_id"]),
        )
        raise RuntimeError("after a write")

    enqueue(conn, "dummy", campaign_person_id=cp, organization_id=ids["org_id"])
    conn.commit()

    assert run_once(conn, steps={"dummy": handler}) is True
    (count,) = conn.execute("SELECT count(*) FROM decisions").fetchone()
    assert count == 0  # the partial write did not survive


def test_unregistered_step_is_a_failure(conn, seed):
    ids = seed()
    job_id = enqueue(conn, "no_such_step", campaign_person_id=ids["campaign_person_id"])
    conn.commit()

    assert run_once(conn, steps={}) is True
    job = get_job(conn, job_id)
    assert job["status"] == "queued"
    assert "no_such_step" in job["last_error"]


def test_job_not_due_yet_is_not_claimed(conn, seed):
    ids = seed()
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    enqueue(
        conn, "dummy", campaign_person_id=ids["campaign_person_id"], run_after=later
    )
    conn.commit()

    assert run_once(conn, steps={"dummy": lambda c, j: None}) is False


def test_register_step_rejects_duplicates():
    name = "test_register_step_unique_name"
    try:
        register_step(name)(lambda c, j: None)
        with pytest.raises(ValueError):
            register_step(name)(lambda c, j: None)
    finally:
        worker.STEPS.pop(name, None)
