"""NYC DOB permit source: DOB NOW Build – Job Application Filings, Socrata
dataset w9ak-ipjd on NYC Open Data.

Work types arrive as per-column YES/NO flags; we emit one PermitFiling per
flagged work type (mechanical_systems, plumbing, sprinkler — the three the
design filters to). The HTTP call is behind an injectable fetch_json so tests
replay JSON fixtures and never touch the network.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import date, datetime

import httpx

from sliderule.adapters.permits import PermitFiling, register_source

DATASET_URL = "https://data.cityofnewyork.us/resource/w9ak-ipjd.json"
PAGE_SIZE = 1000

# our canonical work_type -> the dataset's YES/NO flag column
WORK_TYPE_COLUMNS = {
    "mechanical_systems": "mechanical_systems_work_type_",
    "plumbing": "plumbing_work_type",
    "sprinkler": "sprinkler_work_type",
}

SELECT_FIELDS = [
    "job_filing_number",
    "applicant_business_name",
    "applicant_street_name",
    "city",
    "state",
    "zip",
    "house_no",
    "street_name",
    "borough",
    "latitude",
    "longitude",
    "filing_date",
    *WORK_TYPE_COLUMNS.values(),
]

FetchJson = Callable[[str, dict], list[dict]]


def _default_fetch_json(url: str, params: dict) -> list[dict]:
    headers = {}
    app_token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
    if app_token:
        headers["X-App-Token"] = app_token
    response = httpx.get(url, params=params, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


def _join(parts: list[str | None], sep: str = " ") -> str | None:
    joined = sep.join(p.strip() for p in parts if p and p.strip())
    return joined or None


def _parse_filed_at(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.fromisoformat(value).date()


def _parse_float(value: str | None) -> float | None:
    return float(value) if value else None


@register_source("nyc_dob")
class NycDob:
    name = "nyc_dob"

    def __init__(self, fetch_json: FetchJson | None = None):
        self._fetch_json = fetch_json or _default_fetch_json

    def _where(self, since: date | None) -> str:
        flags = " OR ".join(
            f"{column} = 'YES'" for column in WORK_TYPE_COLUMNS.values()
        )
        where = f"({flags}) AND applicant_business_name IS NOT NULL"
        if since is not None:
            where += f" AND filing_date >= '{since.isoformat()}T00:00:00'"
        return where

    def fetch(self, since: date | None = None) -> Iterator[PermitFiling]:
        offset = 0
        while True:
            rows = self._fetch_json(
                DATASET_URL,
                {
                    "$select": ",".join(SELECT_FIELDS),
                    "$where": self._where(since),
                    "$order": "job_filing_number",
                    "$limit": PAGE_SIZE,
                    "$offset": offset,
                },
            )
            for row in rows:
                yield from self._to_filings(row)
            if len(rows) < PAGE_SIZE:
                return
            offset += PAGE_SIZE

    def _to_filings(self, row: dict) -> Iterator[PermitFiling]:
        firm_name = (row.get("applicant_business_name") or "").strip()
        filing_number = row.get("job_filing_number")
        if not firm_name or not filing_number:
            return
        project_address = _join(
            [row.get("house_no"), row.get("street_name")]
        )
        if project_address and row.get("borough"):
            project_address = f"{project_address}, {row['borough']}"
        firm_address = _join(
            [row.get("applicant_street_name"), row.get("city"),
             row.get("state"), row.get("zip")],
            sep=", ",
        )
        for work_type, column in WORK_TYPE_COLUMNS.items():
            if (row.get(column) or "").upper() != "YES":
                continue
            yield PermitFiling(
                source=self.name,
                external_id=f"{filing_number}:{work_type}",
                firm_name=firm_name,
                work_type=work_type,
                project_address=project_address,
                latitude=_parse_float(row.get("latitude")),
                longitude=_parse_float(row.get("longitude")),
                filed_at=_parse_filed_at(row.get("filing_date")),
                firm_address=firm_address,
            )
