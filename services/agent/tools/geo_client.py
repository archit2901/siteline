"""GeoClient: the agent's only way to geo data and math. Contract shapes in and out.

MockGeo  -> fixtures/grid.json (committed real Census/CMS columns; works with no data/ folder)
LocalGeo -> data/raw/houston_h3_r8.parquet (all public columns)
HttpGeo  -> geo-service at GEO_URL (endpoints in services/geo/CLAUDE.md)
MockGeo and LocalGeo share the math in geomath.py.
"""

import json
import os
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np
from pydantic import BaseModel, TypeAdapter

from contracts.models import (
    DatasetPreview,
    FeatureValue,
    HuffRequest,
    HuffResult,
    IngestRequest,
    PreviewRequest,
    RankedSite,
    RankRequest,
    RegionFeature,
    ScoreRequest,
    ScoreResponse,
    SensitivityRequest,
    SensitivityResult,
    ValidationReport,
)
from services.agent.tools import geomath
from services.agent.tools.packs import feature_catalog, pack_for, region_features

ROOT = Path(__file__).resolve().parents[3]
GRID_FIXTURE = ROOT / "fixtures" / "grid.json"
GRID_PARQUET = ROOT / "data" / "raw" / "houston_h3_r8.parquet"
GENERAL_STOCK = ROOT / "data" / "derived" / "general_stock.parquet"  # scripts/build_general_stock.py
REGIONS = {"hou"}
# CoStar-derived grid columns never enter the feature store (repo is public; see docs/HANDOFF.md section 6).
COSTAR_COLUMNS = ("n_costar", "n_lease_spaces", "n_medical_lease_spaces")


class GeoClient(Protocol):
    async def list_features(self, region_id: str) -> list[RegionFeature]: ...
    async def score(self, request: ScoreRequest) -> ScoreResponse: ...
    async def huff(self, request: HuffRequest) -> list[HuffResult]: ...
    async def rank(self, request: RankRequest) -> list[RankedSite]: ...
    async def sensitivity(self, request: SensitivityRequest) -> list[SensitivityResult]: ...
    async def feature_value(self, feature_id: str, h3: str, region_id: str = "hou") -> FeatureValue: ...
    async def preview(self, request: PreviewRequest) -> DatasetPreview: ...
    async def ingest(self, request: IngestRequest) -> ValidationReport: ...


class GridGeo:
    """GeoClient over an in-memory Grid."""

    def __init__(self, grid: geomath.Grid) -> None:
        self.grid = grid

    def _check_region(self, region_id: str) -> None:
        if region_id not in REGIONS:
            raise ValueError(f"unknown region {region_id!r}")

    async def list_features(self, region_id: str) -> list[RegionFeature]:
        self._check_region(region_id)
        return region_features()

    async def score(self, request: ScoreRequest) -> ScoreResponse:
        self._check_region(request.region_id)
        return geomath.score(self.grid, pack_for(request.profile), request.profile, request.spec, request.region_id,
                             request.top_n)

    async def huff(self, request: HuffRequest) -> list[HuffResult]:
        self._check_region(request.region_id)
        return geomath.huff(self.grid, pack_for(request.profile), request.profile, request.spec, request.candidates,
                            request.competitor_feature)

    async def rank(self, request: RankRequest) -> list[RankedSite]:
        self._check_region(request.region_id)
        return geomath.rank(self.grid, pack_for(request.profile), request.profile, request.spec, request.candidates,
                            request.competitor_feature, request.with_stability)

    async def sensitivity(self, request: SensitivityRequest) -> list[SensitivityResult]:
        self._check_region(request.region_id)
        return geomath.sensitivity(self.grid, pack_for(request.profile), request.profile, request.spec,
                                   request.candidates, request.draws, request.kappa)

    async def feature_value(self, feature_id: str, h3: str, region_id: str = "hou") -> FeatureValue:
        self._check_region(region_id)
        cell = self.grid.index.get(h3)
        if cell is None:
            raise KeyError(f"cell {h3} is not in region {region_id}")
        known = feature_catalog().get(feature_id)
        value = self.grid.column(known.value or known.id if known else feature_id)[cell]
        return FeatureValue(feature_id=feature_id, h3=h3, value=None if np.isnan(value) else float(value))

    async def preview(self, request: PreviewRequest) -> DatasetPreview:
        raise NotImplementedError("generic ingestion arrives in Task 6")

    async def ingest(self, request: IngestRequest) -> ValidationReport:
        raise NotImplementedError("generic ingestion arrives in Task 6")


def _grid_from_rows(rows: list[dict[str, object]]) -> geomath.Grid:
    skip = {"h3", "cell_lat", "cell_lon", "county", "GEOID"}
    names = [k for k in rows[0] if k not in skip and not k.startswith(COSTAR_COLUMNS)]
    columns = {n: np.array([np.nan if r[n] is None else r[n] for r in rows], dtype=float) for n in names}
    return geomath.Grid(
        h3=[str(r["h3"]) for r in rows],
        lat=np.array([r["cell_lat"] for r in rows], dtype=float),
        lon=np.array([r["cell_lon"] for r in rows], dtype=float),
        columns=columns,
    )


class MockGeo(GridGeo):
    def __init__(self, path: Path = GRID_FIXTURE) -> None:
        super().__init__(_grid_from_rows(json.loads(path.read_text())))


class LocalGeo(GridGeo):
    def __init__(self, path: Path = GRID_PARQUET) -> None:
        import pyarrow.parquet as pq

        if not path.exists():
            raise FileNotFoundError(f"{path} missing: AGENT_BACKEND=local needs the real data in data/raw/")
        import pyarrow.types as pat

        table = pq.read_table(path)
        numeric = [f.name for f in table.schema if pat.is_integer(f.type) or pat.is_floating(f.type)]
        keep = ["h3", *[c for c in numeric if not c.startswith(COSTAR_COLUMNS)]]
        rows = table.select(keep).to_pylist()
        if GENERAL_STOCK.exists():  # renters, housing, vehicles, households, jobs (ACS + LODES)
            extra = {r["h3"]: r for r in pq.read_table(GENERAL_STOCK).to_pylist()}
            for row in rows:
                row.update({k: v for k, v in extra.get(row["h3"], {}).items() if k != "h3"})
        super().__init__(_grid_from_rows(rows))


class HttpGeo:
    def __init__(self, base_url: str | None = None, timeout: float = 60.0) -> None:
        self.client = httpx.AsyncClient(base_url=base_url or os.environ.get("GEO_URL", "http://localhost:8001"),
                                        timeout=timeout)

    async def _post(self, path: str, body: BaseModel) -> object:
        resp = await self.client.post(path, json=body.model_dump(mode="json"))
        resp.raise_for_status()
        return resp.json()

    async def list_features(self, region_id: str) -> list[RegionFeature]:
        resp = await self.client.get(f"/regions/{region_id}/features")
        resp.raise_for_status()
        return TypeAdapter(list[RegionFeature]).validate_python(resp.json())

    async def score(self, request: ScoreRequest) -> ScoreResponse:
        return ScoreResponse.model_validate(await self._post("/score", request))

    async def huff(self, request: HuffRequest) -> list[HuffResult]:
        return TypeAdapter(list[HuffResult]).validate_python(await self._post("/huff", request))

    async def rank(self, request: RankRequest) -> list[RankedSite]:
        return TypeAdapter(list[RankedSite]).validate_python(await self._post("/rank", request))

    async def sensitivity(self, request: SensitivityRequest) -> list[SensitivityResult]:
        return TypeAdapter(list[SensitivityResult]).validate_python(await self._post("/sensitivity", request))

    async def feature_value(self, feature_id: str, h3: str, region_id: str = "hou") -> FeatureValue:
        resp = await self.client.get(f"/features/{feature_id}/value", params={"h3": h3, "region_id": region_id})
        resp.raise_for_status()
        return FeatureValue.model_validate(resp.json())

    async def preview(self, request: PreviewRequest) -> DatasetPreview:
        return DatasetPreview.model_validate(await self._post("/features/profile", request))

    async def ingest(self, request: IngestRequest) -> ValidationReport:
        return ValidationReport.model_validate(await self._post("/features/ingest", request))
