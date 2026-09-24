"""Shared FastAPI dependencies."""

from collections.abc import Iterator

import psycopg
from fastapi import Depends

from sliderule import db
from sliderule.api.auth import AuthContext, get_auth


def get_conn() -> Iterator[psycopg.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def get_org_id(
    auth: AuthContext = Depends(get_auth),
    conn: psycopg.Connection = Depends(get_conn),
) -> int:
    """Resolve the internal organization id for the org in the verified JWT,
    provisioning the row on first sight. The org id never comes from the
    request body or query string — only from here."""
    row = conn.execute(
        "SELECT id FROM organizations WHERE clerk_org_id = %s",
        (auth.clerk_org_id,),
    ).fetchone()
    if row:
        return row[0]
    conn.execute(
        "INSERT INTO organizations (clerk_org_id, name) VALUES (%s, %s)"
        " ON CONFLICT (clerk_org_id) DO NOTHING",
        (auth.clerk_org_id, auth.clerk_org_id),
    )
    conn.commit()
    (org_id,) = conn.execute(
        "SELECT id FROM organizations WHERE clerk_org_id = %s",
        (auth.clerk_org_id,),
    ).fetchone()
    return org_id
