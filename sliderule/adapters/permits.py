"""Permit-source adapter interface. One implementation per source
(nyc_dob now; NJ municipal sources deferred on purpose)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class PermitFiling:
    """One (filing, work type) pair as reported by a source. A filing that
    covers several work types yields one record per work type, so external_id
    embeds the work type."""

    source: str
    external_id: str
    firm_name: str
    work_type: str
    project_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    filed_at: date | None = None
    firm_address: str | None = None


class PermitSource(Protocol):
    name: str

    def fetch(self, since: date | None = None) -> Iterable[PermitFiling]: ...


SOURCES: dict[str, Callable[[], PermitSource]] = {}


def register_source(name: str) -> Callable:
    def decorate(factory: Callable[[], PermitSource]) -> Callable[[], PermitSource]:
        if name in SOURCES:
            raise ValueError(f"permit source {name!r} registered twice")
        SOURCES[name] = factory
        return factory

    return decorate


def get_source(name: str) -> PermitSource:
    if name not in SOURCES:
        raise LookupError(f"unknown permit source {name!r}")
    return SOURCES[name]()
