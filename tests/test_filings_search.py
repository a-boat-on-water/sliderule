"""Firms / filings search endpoints against seeded public data."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from sliderule.api.main import create_app
from tests.test_api import FakeVerifier

AUTH = {"Authorization": "Bearer good-token"}

# Times Square-ish vs. Staten Island — ~29 km apart
MIDTOWN = (40.7580, -73.9855)
STATEN_ISLAND = (40.6353, -74.1638)


@pytest.fixture
def client(conn, seed_filings):
    return TestClient(create_app(token_verifier=FakeVerifier()))


@pytest.fixture
def seed_filings(conn):
    (firm_a,) = conn.execute(
        "INSERT INTO firms (name) VALUES ('Midtown Mechanical PC') RETURNING id"
    ).fetchone()
    (firm_b,) = conn.execute(
        "INSERT INTO firms (name) VALUES ('Island Plumbing PC') RETURNING id"
    ).fetchone()
    rows = [
        (firm_a, "nyc_dob", "A-1:mechanical_systems", "mechanical_systems",
         *MIDTOWN, date(2026, 6, 1)),
        (firm_a, "nyc_dob", "A-2:mechanical_systems", "mechanical_systems",
         *MIDTOWN, date(2026, 7, 1)),
        (firm_a, "nyc_dob", "A-2:plumbing", "plumbing", *MIDTOWN, date(2026, 7, 1)),
        (firm_b, "nyc_dob", "B-1:plumbing", "plumbing",
         *STATEN_ISLAND, date(2025, 3, 1)),
    ]
    for firm_id, source, ext, work_type, lat, lng, filed in rows:
        conn.execute(
            "INSERT INTO filings (firm_id, source, external_id, work_type,"
            " latitude, longitude, filed_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (firm_id, source, ext, work_type, lat, lng, filed),
        )
    conn.commit()
    return {"firm_a": firm_a, "firm_b": firm_b}


def test_search_requires_auth(client):
    assert client.get("/firms/search").status_code == 401
    assert client.get("/filings/search").status_code == 401


def test_firm_table_aggregates_counts(client):
    response = client.get("/firms/search", headers=AUTH)
    assert response.status_code == 200
    firms = response.json()["firms"]
    assert [f["name"] for f in firms] == [
        "Midtown Mechanical PC", "Island Plumbing PC",
    ]  # ordered by filing count
    midtown = firms[0]
    assert midtown["filing_count"] == 3
    assert midtown["filings_by_work_type"] == {
        "mechanical_systems": 2, "plumbing": 1,
    }
    assert midtown["last_filed_at"] == "2026-07-01"


def test_work_type_filter(client):
    response = client.get(
        "/firms/search", params={"work_type": ["plumbing"]}, headers=AUTH
    )
    firms = response.json()["firms"]
    assert {f["name"] for f in firms} == {
        "Midtown Mechanical PC", "Island Plumbing PC",
    }
    assert all(f["filing_count"] == 1 for f in firms)


def test_radius_filter_keeps_only_nearby_filings(client):
    response = client.get(
        "/filings/search",
        params={"lat": MIDTOWN[0], "lng": MIDTOWN[1], "radius_km": 5},
        headers=AUTH,
    )
    filings = response.json()["filings"]
    assert len(filings) == 3
    assert all(f["firm_name"] == "Midtown Mechanical PC" for f in filings)


def test_date_filter(client):
    response = client.get(
        "/filings/search", params={"filed_from": "2026-01-01"}, headers=AUTH
    )
    assert len(response.json()["filings"]) == 3


def test_partial_geo_params_are_rejected(client):
    response = client.get("/filings/search", params={"lat": 40.0}, headers=AUTH)
    assert response.status_code == 422
