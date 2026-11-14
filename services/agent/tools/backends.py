"""Pick GeoClient / SitesClient from AGENT_BACKEND = mock (default) | local | http. One instance per process."""

import os
from functools import lru_cache

from services.agent.tools.geo_client import GeoClient, HttpGeo, LocalGeo, MockGeo
from services.agent.tools.sites_client import HttpSites, LocalSites, MockSites, SitesClient

BACKENDS = ("mock", "local", "http")


def backend() -> str:
    value = os.environ.get("AGENT_BACKEND", "").strip() or "mock"
    if value not in BACKENDS:
        raise ValueError(f"AGENT_BACKEND must be one of {BACKENDS}, got {value!r}")
    return value


@lru_cache
def _geo(kind: str) -> GeoClient:
    return {"mock": MockGeo, "local": LocalGeo, "http": HttpGeo}[kind]()


@lru_cache
def _sites(kind: str) -> SitesClient:
    return {"mock": MockSites, "local": LocalSites, "http": HttpSites}[kind]()


def get_geo() -> GeoClient:
    return _geo(backend())


def get_sites() -> SitesClient:
    return _sites(backend())
