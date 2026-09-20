"""sync_filings: pull filings from a permit source into firms + filings.

Rerunnable: firms upsert on lower(name), filings are keyed on
(source, external_id) and conflicts are skipped. Payload:
{"source": "nyc_dob", "since": "2026-01-01"} — both optional.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import psycopg

from sliderule.adapters import nyc_dob  # noqa: F401  (registers the source)
from sliderule.adapters.permits import get_source
from sliderule.worker import register_step

log = logging.getLogger(__name__)


def upsert_firm(conn: psycopg.Connection, name: str, address: str | None) -> int:
    (firm_id,) = conn.execute(
        "INSERT INTO firms (name, office_address) VALUES (%s, %s)"
        " ON CONFLICT ((lower(name))) DO UPDATE SET"
        "   office_address = coalesce(firms.office_address, excluded.office_address),"
        "   updated_at = now()"
        " RETURNING id",
        (name, address),
    ).fetchone()
    return firm_id


@register_step("sync_filings")
def sync_filings(conn: psycopg.Connection, job: dict[str, Any]) -> None:
    payload = job["payload"] or {}
    source_name = payload.get("source", "nyc_dob")
    since = date.fromisoformat(payload["since"]) if payload.get("since") else None
    source = get_source(source_name)

    firm_ids: dict[str, int] = {}
    inserted = 0
    for record in source.fetch(since=since):
        key = record.firm_name.lower()
        if key not in firm_ids:
            firm_ids[key] = upsert_firm(conn, record.firm_name, record.firm_address)
        row = conn.execute(
            "INSERT INTO filings (firm_id, source, external_id, work_type,"
            " project_address, latitude, longitude, filed_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (source, external_id) DO NOTHING"
            " RETURNING id",
            (
                firm_ids[key], record.source, record.external_id, record.work_type,
                record.project_address, record.latitude, record.longitude,
                record.filed_at,
            ),
        ).fetchone()
        if row is not None:
            inserted += 1
    log.info("sync_filings(%s): %s firms seen, %s new filings",
             source_name, len(firm_ids), inserted)
