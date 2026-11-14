"""Regressions found while wiring the Houston catalog: zero-filled LAT/LON attributes and short ArcGIS pages."""

import asyncio

from services.agent.tools import ingest
from services.agent.tools.ingest import guess_geo


def test_geometry_coordinates_beat_zero_attribute_columns() -> None:
    # City of Houston layers carry LAT/LON attribute columns that are all 0; the geometry has the real point.
    rows = [{"LAT": 0, "LON": 0, "_lat": 29.84, "_lon": -95.42}] * 5
    assert guess_geo(rows) == ("_lat,_lon", "point")


def test_attribute_coordinates_used_when_real() -> None:
    assert guess_geo([{"latitude": "29.7", "longitude": "-95.3"}] * 3) == ("latitude,longitude", "point")


def test_empty_coordinates_are_not_a_geography() -> None:
    assert guess_geo([{"lat": None, "lon": None}] * 3) == (None, None)


def test_arcgis_paging_continues_past_server_page_cap(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A server whose maxRecordCount (1,000) is below our page size must still be read to the end."""
    total, cap, offsets = 2500, 1000, []

    class Resp:
        def __init__(self, n: int, start: int) -> None:
            self.feats = [{"type": "Feature", "properties": {"i": start + i},
                           "geometry": {"type": "Point", "coordinates": [-95.4, 29.8]}} for i in range(n)]

        def json(self) -> dict:  # type: ignore[type-arg]
            return {"features": self.feats}

    async def fake_get(client, url, params=None, cap_bytes=None):  # type: ignore[no-untyped-def]
        start = params["resultOffset"]
        offsets.append(start)
        return Resp(max(0, min(cap, total - start)), start)

    monkeypatch.setattr(ingest, "_get", fake_get)
    rows = asyncio.run(ingest.fetch_rows("https://services.arcgis.com/x/arcgis/rest/services/y/FeatureServer/0", "arcgis"))
    assert len(rows.rows) == total
    assert offsets == [0, 1000, 2000, 2500]


def test_scout_drops_filters_on_identifier_columns() -> None:
    from contracts.models import DatasetCandidate, DatasetPreview, Factor
    from services.agent.scout import SpecDraft, build_spec

    factor = Factor(id="schools", factor="Schools nearby", why="w", proxy="Schools within 5 km", direction=1, priority="medium")
    cand = DatasetCandidate(id="c", factor_id="schools", title="Schools", url="https://services.arcgis.com/x/FeatureServer/0",  # type: ignore[arg-type]
                            publisher="City of Houston", format="arcgis", geo_level="point", why="w")
    sample = [{"_lat": 29.7, "_lon": -95.4, "School_Name": n, "Level": "Elementary"} for n in ("BRAZOS EL", "ALIEF MS", "BELLAIRE HS")]
    preview = DatasetPreview(url=cand.url, format="arcgis", columns=["_lat", "_lon", "School_Name", "Level"], sample_rows=sample,
                             geo_key_guess="_lat,_lon", geo_level_guess="point", ingestible=True)
    by_name = build_spec(factor, cand, preview, SpecDraft(agg="count_within", filter_column="School_Name", filter_values=["BRAZOS EL"]))
    assert by_name is not None and by_name.filter_column is None and by_name.filter_values == []
    by_level = build_spec(factor, cand, preview, SpecDraft(agg="count_within", filter_column="Level", filter_values=["Elementary"]))
    assert by_level is not None and by_level.filter_column == "Level"


def test_filter_falls_back_to_containment_when_exact_values_miss():
    from types import SimpleNamespace
    rows = [{"Grade_Range": "09-12"}, {"Grade_Range": "PK 09-12"}, {"Grade_Range": "EE-05"}]
    spec = SimpleNamespace(filter_column="Grade_Range", filter_values=["9-12"])
    assert [r["Grade_Range"] for r in ingest._apply_filter(rows, spec)] == ["09-12", "PK 09-12"]
    exact = SimpleNamespace(filter_column="Grade_Range", filter_values=["09-12"])
    assert len(ingest._apply_filter(rows, exact)) == 1


def test_distance_direction_follows_whether_near_helps():
    from contracts.models import Factor
    from services.agent.scout import distance_direction
    mk = lambda name, proxy, d: Factor(id="x", factor=name, why="", proxy=proxy, direction=d, priority="high")
    assert distance_direction(mk("Regional hospitals for surgeries and rounding", "Drive time to hospital campuses", 1)) == -1
    assert distance_direction(mk("Few competing orthopedic clinics", "Distance to nearest orthopedic clinic", -1)) == 1
    assert distance_direction(mk("Flood hazard vulnerability", "Distance to the 100-year floodplain", -1)) == 1
