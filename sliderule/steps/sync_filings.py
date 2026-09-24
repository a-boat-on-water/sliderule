"""sync_filings: pull filings from a permit source into firms + filings.

Rerunnable: firms upsert on lower(name), filings are keyed on
(source, external_id) and conflicts are skipped. Payload:
{"source": "nyc_dob", "since": "2026-01-01"} — both optional.

Writes are batched (executemany pipelines statements), not per-record:
against a remote Postgres every roundtrip costs tens of milliseconds, and a
season of NYC filings is tens of thousands of rows.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import psycopg

from sliderule.adapters import nyc_dob  # noqa: F401  (registers the source)
from sliderule.adapters.permits import PermitFiling, get_source
from sliderule.worker import register_step

log = logging.getLogger(__name__)


def _upsert_firms(
    conn: psycopg.Connection, records: list[PermitFiling]
) -> dict[str, int]:
    """Insert unseen firms, then return {lower(name): id} for every firm in
    the batch — three roundtrips total, regardless of batch size."""
    unique: dict[str, PermitFiling] = {}
    for record in records:
        unique.setdefault(record.firm_name.lower(), record)
    if not unique:
        return {}
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO firms (name, office_address) VALUES (%s, %s)"
            " ON CONFLICT ((lower(name))) DO UPDATE SET"
            "   office_address = coalesce(firms.office_address, excluded.office_address),"
            "   updated_at = now()",
            [(r.firm_name, r.firm_address) for r in unique.values()],
        )
    rows = conn.execute(
        "SELECT lower(name), id FROM firms WHERE lower(name) = ANY(%s)",
        (list(unique),),
    ).fetchall()
    return dict(rows)


def _flush(conn: psycopg.Connection, batch: list[PermitFiling]) -> None:
    firm_ids = _upsert_firms(conn, batch)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO filings (firm_id, source, external_id, work_type,"
            " project_address, latitude, longitude, filed_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (source, external_id) DO NOTHING",
            [
                (
                    firm_ids[r.firm_name.lower()], r.source, r.external_id,
                    r.work_type, r.project_address, r.latitude, r.longitude,
                    r.filed_at,
                )
                for r in batch
            ],
        )


BATCH_SIZE = 500


@register_step("sync_filings")
def sync_filings(conn: psycopg.Connection, job: dict[str, Any]) -> None:
    payload = job["payload"] or {}
    source_name = payload.get("source", "nyc_dob")
    since = date.fromisoformat(payload["since"]) if payload.get("since") else None
    source = get_source(source_name)

    # Flush as records stream in: a long up-front download would leave the
    # DB connection idle past Neon's timeout, and one giant batch is
    # unbounded memory.
    total = 0
    batch: list[PermitFiling] = []
    for record in source.fetch(since=since):
        batch.append(record)
        if len(batch) >= BATCH_SIZE:
            _flush(conn, batch)
            total += len(batch)
            batch.clear()
            log.info("sync_filings(%s): %s records so far", source_name, total)
    if batch:
        _flush(conn, batch)
        total += len(batch)
    log.info("sync_filings(%s): done, %s records", source_name, total)
