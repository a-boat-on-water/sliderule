"""NYC DOB adapter against the recorded Socrata fixture — no network."""

import json
from datetime import date
from pathlib import Path

from sliderule.adapters.nyc_dob import DATASET_URL, NycDob

FIXTURE = Path(__file__).parent / "fixtures" / "nyc_dob_w9ak_ipjd.json"


class RecordedFetch:
    def __init__(self):
        self.calls = []
        self._pages = [json.loads(FIXTURE.read_text())]

    def __call__(self, url, params):
        self.calls.append((url, params))
        return self._pages.pop(0) if self._pages else []


def test_yields_one_filing_per_flagged_work_type():
    source = NycDob(fetch_json=RecordedFetch())
    filings = list(source.fetch())

    by_external_id = {f.external_id: f for f in filings}
    # first row has two YES flags -> two records; last row has none -> zero
    assert set(by_external_id) == {
        "M00797798-I1:mechanical_systems",
        "M00797798-I1:plumbing",
        "Q01042595-P2:plumbing",
        "S01147390-S1:mechanical_systems",
    }

    keri = by_external_id["M00797798-I1:mechanical_systems"]
    assert keri.source == "nyc_dob"
    assert keri.firm_name == "KERI ENGINEERING, P.C."
    assert keri.project_address == "541 COLUMBUS AVENUE, Manhattan"
    assert keri.firm_address == "Suite 303 140 Mountain Ave,, Springfield, NJ, 07081"
    assert keri.latitude == 40.786585
    assert keri.filed_at == date(2026, 8, 14)

    # withdrawn filings can lack filing_date; that must not blow up
    assert by_external_id["S01147390-S1:mechanical_systems"].filed_at is None


def test_query_filters_work_types_and_since():
    fetch = RecordedFetch()
    list(NycDob(fetch_json=fetch).fetch(since=date(2026, 1, 1)))

    (url, params), = fetch.calls
    assert url == DATASET_URL
    where = params["$where"]
    assert "mechanical_systems_work_type_ = 'YES'" in where
    assert "plumbing_work_type = 'YES'" in where
    assert "sprinkler_work_type = 'YES'" in where
    assert "filing_date >= '2026-01-01T00:00:00'" in where
    assert params["$limit"] == 1000


def test_pagination_stops_after_short_page():
    class TwoPages:
        def __init__(self):
            self.offsets = []

        def __call__(self, url, params):
            self.offsets.append(params["$offset"])
            row = json.loads(FIXTURE.read_text())[0]
            return [row] * 1000 if params["$offset"] == 0 else [row]

    fetch = TwoPages()
    filings = list(NycDob(fetch_json=fetch).fetch())
    assert fetch.offsets == [0, 1000]
    assert len(filings) == 1001 * 2  # two work types per row
