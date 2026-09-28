"""Firms / filings search over the public permit data.

Filters: work types, filed-date range, radius from a point (haversine, no
PostGIS). /firms/search returns the firm-level table the design asks for
(firm, filing counts by work type, last filed); /filings/search returns raw
filings for the map. Auth required like everything else; the data itself is
not tenant-owned.
"""

from __future__ import annotations

from datetime import date

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from sliderule.api.auth import AuthContext, get_auth
from sliderule.api.deps import get_conn

router = APIRouter()

EARTH_RADIUS_KM = 6371.0

HAVERSINE_KM = (
    f"(2 * {EARTH_RADIUS_KM} * asin(sqrt("
    " power(sin(radians((f.latitude - %(lat)s) / 2)), 2)"
    " + cos(radians(%(lat)s)) * cos(radians(f.latitude))"
    " * power(sin(radians((f.longitude - %(lng)s) / 2)), 2)"
    ")))"
)


def _filing_filters(
    work_type: list[str] | None,
    filed_from: date | None,
    filed_to: date | None,
    lat: float | None,
    lng: float | None,
    radius_km: float | None,
) -> tuple[str, dict]:
    geo_args = [lat, lng, radius_km]
    if any(a is not None for a in geo_args) and None in geo_args:
        raise HTTPException(
            status_code=422, detail="lat, lng and radius_km must be given together"
        )
    clauses = ["true"]
    params: dict = {}
    if work_type:
        clauses.append("f.work_type = ANY(%(work_types)s)")
        params["work_types"] = work_type
    if filed_from is not None:
        clauses.append("f.filed_at >= %(filed_from)s")
        params["filed_from"] = filed_from
    if filed_to is not None:
        clauses.append("f.filed_at <= %(filed_to)s")
        params["filed_to"] = filed_to
    if lat is not None:
        clauses.append("f.latitude IS NOT NULL AND f.longitude IS NOT NULL")
        clauses.append(f"{HAVERSINE_KM} <= %(radius_km)s")
        params.update({"lat": lat, "lng": lng, "radius_km": radius_km})
    return " AND ".join(clauses), params


@router.get("/firms/search")
def search_firms(
    work_type: list[str] | None = Query(default=None),
    filed_from: date | None = None,
    filed_to: date | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    auth: AuthContext = Depends(get_auth),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    where, params = _filing_filters(work_type, filed_from, filed_to, lat, lng, radius_km)
    rows = conn.execute(
        f"""
        WITH matching AS (
            SELECT f.firm_id, f.work_type, f.filed_at FROM filings f WHERE {where}
        )
        SELECT fi.id, fi.name, fi.office_address, fi.website, fi.size_estimate,
               fi.principal,
               count(*) AS filing_count,
               max(m.filed_at) AS last_filed_at,
               (SELECT jsonb_object_agg(t.work_type, t.n)
                  FROM (SELECT work_type, count(*) AS n FROM matching
                         WHERE firm_id = fi.id GROUP BY work_type) t
               ) AS filings_by_work_type
          FROM matching m
          JOIN firms fi ON fi.id = m.firm_id
         GROUP BY fi.id
         ORDER BY filing_count DESC, fi.name
         LIMIT %(limit)s
        """,
        {**params, "limit": limit},
    ).fetchall()
    keys = ("id", "name", "office_address", "website", "size_estimate",
            "principal", "filing_count", "last_filed_at", "filings_by_work_type")
    return {"firms": [dict(zip(keys, row)) for row in rows]}


@router.get("/filings/search")
def search_filings(
    work_type: list[str] | None = Query(default=None),
    filed_from: date | None = None,
    filed_to: date | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    auth: AuthContext = Depends(get_auth),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    where, params = _filing_filters(work_type, filed_from, filed_to, lat, lng, radius_km)
    rows = conn.execute(
        f"""
        SELECT f.id, f.firm_id, fi.name AS firm_name, f.work_type,
               f.project_address, f.latitude, f.longitude, f.filed_at
          FROM filings f
          JOIN firms fi ON fi.id = f.firm_id
         WHERE {where}
         ORDER BY f.filed_at DESC NULLS LAST, f.id
         LIMIT %(limit)s
        """,
        {**params, "limit": limit},
    ).fetchall()
    keys = ("id", "firm_id", "firm_name", "work_type", "project_address",
            "latitude", "longitude", "filed_at")
    return {"filings": [dict(zip(keys, row)) for row in rows]}


@router.get("/engineers/search")
def search_engineers(
    work_type: list[str] | None = Query(default=None),
    filed_from: date | None = None,
    filed_to: date | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
    firm_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(get_auth),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    """The applicants of record on matching filings: one row per licensed
    professional per firm, ranked by how many matching filings they signed.
    Grouped on license number when the source gives one (names vary in
    casing and spelling), otherwise on lower(name)."""
    where, params = _filing_filters(work_type, filed_from, filed_to, lat, lng, radius_km)
    if firm_id is not None:
        where += " AND f.firm_id = %(firm_id)s"
        params["firm_id"] = firm_id
    rows = conn.execute(
        f"""
        WITH matching AS (
            SELECT f.firm_id, f.work_type, f.filed_at, f.project_address,
                   f.applicant_name, f.applicant_title, f.applicant_license,
                   coalesce(f.applicant_license, lower(f.applicant_name)) AS who
              FROM filings f
             WHERE {where} AND f.applicant_name IS NOT NULL
        )
        SELECT max(m.applicant_name) AS applicant,
               max(m.applicant_title) AS title,
               max(m.applicant_license) AS license,
               fi.id, fi.name, fi.office_address,
               count(*) AS filing_count,
               max(m.filed_at) AS last_filed_at,
               (SELECT jsonb_object_agg(t.work_type, t.n)
                  FROM (SELECT work_type, count(*) AS n FROM matching x
                         WHERE x.who = m.who AND x.firm_id = fi.id
                         GROUP BY work_type) t) AS filings_by_work_type,
               (SELECT array_agg(DISTINCT x.project_address)
                  FROM (SELECT project_address FROM matching y
                         WHERE y.who = m.who AND y.firm_id = fi.id
                           AND y.project_address IS NOT NULL
                         ORDER BY y.filed_at DESC NULLS LAST LIMIT 3) x
               ) AS recent_projects
          FROM matching m
          JOIN firms fi ON fi.id = m.firm_id
         GROUP BY m.who, fi.id
         ORDER BY filing_count DESC, applicant
         LIMIT %(limit)s
        """,
        {**params, "limit": limit},
    ).fetchall()
    keys = ("name", "title", "license", "firm_id", "firm_name", "firm_address",
            "filing_count", "last_filed_at", "filings_by_work_type",
            "recent_projects")
    return {"engineers": [dict(zip(keys, row)) for row in rows]}
