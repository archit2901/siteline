"""A finished run's rescore state survives to a fresh process: recorded runs re-rank with no LLM."""

import json
from pathlib import Path

import numpy as np
import pytest

from contracts.models import RankRequest, RegionFeature, Report, ScoringSpec, SiteCandidate
from services.agent import snapshot
from services.agent.runs import FIXTURES
from services.agent.tools.geo_client import MockGeo


@pytest.mark.asyncio
async def test_snapshot_round_trip_restores_state_and_found_columns(tmp_path: Path) -> None:
    report = Report.model_validate_json((FIXTURES / "report.json").read_text())
    sites = [r.site for r in report.recommendations]
    geo = MockGeo()
    base = next(iter(geo.grid.columns))
    found = "found_bus_access_abc123"
    geo.grid.columns[found] = np.arange(len(geo.grid.h3), dtype=float)
    geo.found[found] = RegionFeature(id=found, label="Bus access", source="x", unit="stops", layer="agent_found", provenance_url="https://example.com")
    spec = ScoringSpec(weights={base: 0.5, found: 0.5})
    state = {"profile": report.profile, "sites": sites, "factors": report.factors, "spec": spec, "inspections": {}}
    snapshot.save(tmp_path, "abc123def456", state, geo)

    fresh, restored = MockGeo(), {}
    assert snapshot.hydrate(tmp_path, restored, fresh)
    assert np.array_equal(fresh.grid.columns[found], geo.grid.columns[found]) and found in fresh.found
    assert [s.id for s in restored["sites"]] == [s.id for s in sites] and isinstance(restored["sites"][0], SiteCandidate)
    ranked = await fresh.rank(RankRequest(profile=restored["profile"], spec=spec, candidates=restored["sites"]))
    assert len(ranked) == len(sites)
    assert not snapshot.hydrate(tmp_path / "none", {}, fresh)
