"""Worker loop: polls `jobs` with FOR UPDATE SKIP LOCKED, one step per job.

Steps live in sliderule/steps/ and register themselves with @register_step.
Jobs are idempotent: keyed on (step, campaign_person_id) or (step, firm_id)
via a unique idempotency_key, so enqueueing the same work twice is a no-op.
Routes enqueue; only this loop executes — no model calls, agent runs or
external API calls ever happen inside an API request.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sliderule import db

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
POLL_INTERVAL_SECONDS = 5.0
RETRY_BACKOFF_BASE_SECONDS = 60

# step name -> handler(conn, job_row). Populated by @register_step at import
# time (sliderule/steps/__init__.py imports every step module).
StepHandler = Callable[[psycopg.Connection, dict[str, Any]], None]
STEPS: dict[str, StepHandler] = {}


def register_step(name: str) -> Callable[[StepHandler], StepHandler]:
    def decorate(fn: StepHandler) -> StepHandler:
        if name in STEPS:
            raise ValueError(f"step {name!r} registered twice")
        STEPS[name] = fn
        return fn

    return decorate


def enqueue(
    conn: psycopg.Connection,
    step: str,
    *,
    campaign_person_id: int | None = None,
    firm_id: int | None = None,
    organization_id: int | None = None,
    payload: dict | None = None,
    run_after: datetime | None = None,
    key: str | None = None,
) -> int | None:
    """Enqueue a job idempotently. Returns the new job id, or None when a job
    with the same idempotency key already exists (the enqueue is a no-op).

    The key defaults to step + campaign_person_id / firm_id; steps that are
    neither (e.g. a scheduled sync_filings run) pass an explicit key.
    """
    if campaign_person_id is not None and firm_id is not None:
        raise ValueError("a job is keyed on campaign_person_id or firm_id, not both")
    if key is None:
        if campaign_person_id is not None:
            key = f"{step}:cp:{campaign_person_id}"
        elif firm_id is not None:
            key = f"{step}:firm:{firm_id}"
        else:
            key = step
    row = conn.execute(
        "INSERT INTO jobs (organization_id, step, campaign_person_id, firm_id,"
        " idempotency_key, payload, run_after)"
        " VALUES (%s, %s, %s, %s, %s, %s, coalesce(%s, now()))"
        " ON CONFLICT (idempotency_key) DO NOTHING"
        " RETURNING id",
        (organization_id, step, campaign_person_id, firm_id, key,
         Jsonb(payload) if payload is not None else None, run_after),
    ).fetchone()
    return row[0] if row else None


def _fetch_job(conn: psycopg.Connection, job_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, organization_id, step, campaign_person_id, firm_id,"
        " idempotency_key, attempts, payload FROM jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    keys = (
        "id", "organization_id", "step", "campaign_person_id", "firm_id",
        "idempotency_key", "attempts", "payload",
    )
    return dict(zip(keys, row))


def _claim(conn: psycopg.Connection) -> int | None:
    """Claim one due job. Own transaction so the lock is released and the
    'running' status is visible before the handler starts."""
    with conn.transaction():
        row = conn.execute(
            "SELECT id FROM jobs"
            " WHERE status = 'queued' AND run_after <= now()"
            " ORDER BY run_after"
            " LIMIT 1"
            " FOR UPDATE SKIP LOCKED"
        ).fetchone()
        if row is None:
            return None
        (job_id,) = row
        conn.execute(
            "UPDATE jobs SET status = 'running', attempts = attempts + 1,"
            " updated_at = now() WHERE id = %s",
            (job_id,),
        )
    return job_id


def _record_failure(conn: psycopg.Connection, job_id: int, exc: Exception) -> None:
    error = f"{type(exc).__name__}: {exc}"
    with conn.transaction():
        (attempts,) = conn.execute(
            "SELECT attempts FROM jobs WHERE id = %s FOR UPDATE", (job_id,)
        ).fetchone()
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE jobs SET status = 'failed', last_error = %s,"
                " updated_at = now() WHERE id = %s",
                (error, job_id),
            )
            log.error("job %s failed permanently after %s attempts: %s",
                      job_id, attempts, error)
        else:
            backoff = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
            conn.execute(
                "UPDATE jobs SET status = 'queued', last_error = %s,"
                " run_after = now() + make_interval(secs => %s),"
                " updated_at = now() WHERE id = %s",
                (error, backoff, job_id),
            )
            log.warning("job %s attempt %s failed, retrying in %ss: %s",
                        job_id, attempts, backoff, error)


def run_once(
    conn: psycopg.Connection, steps: dict[str, StepHandler] | None = None
) -> bool:
    """Claim and run one job. Returns False when no job was due.

    The handler's writes are one transaction: rolled back entirely on
    failure, committed before the job is marked done.
    """
    steps = STEPS if steps is None else steps
    job_id = _claim(conn)
    if job_id is None:
        return False
    job = _fetch_job(conn, job_id)
    # _fetch_job's read opened an implicit transaction; end it, or every
    # conn.transaction() below silently becomes a savepoint inside it and
    # nothing this function writes is ever visible to other connections.
    conn.commit()
    try:
        handler = steps.get(job["step"])
        if handler is None:
            raise LookupError(f"no handler registered for step {job['step']!r}")
        with conn.transaction():
            handler(conn, job)
    except Exception as exc:
        conn.rollback()
        _record_failure(conn, job_id, exc)
        return True
    with conn.transaction():
        conn.execute(
            "UPDATE jobs SET status = 'done', last_error = NULL,"
            " updated_at = now() WHERE id = %s",
            (job_id,),
        )
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    import sliderule.steps  # noqa: F401  (imports register every step)

    log.info("worker starting; %s steps registered", len(STEPS))
    while True:
        try:
            with db.connect() as conn:
                while True:
                    if not run_once(conn):
                        time.sleep(POLL_INTERVAL_SECONDS)
        except psycopg.OperationalError as exc:
            log.warning("database connection lost (%s); reconnecting", exc)
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    # `python -m sliderule.worker` loads this file as __main__, while the
    # step modules register into the separately-imported sliderule.worker —
    # two different STEPS dicts. Delegate to the canonical module so
    # registration and execution share one registry.
    from sliderule.worker import main as canonical_main

    canonical_main()
