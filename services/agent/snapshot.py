"""What /rescore needs from a finished run, saved next to its artifacts so recorded runs can be re-ranked too.

A live run keeps its profile, sites, demand model, factors and inspections in memory (run.state), and the datasets
the agent found live only as columns of the API process's grid. A replay has neither, so a live run writes them
out: rescore_state.json (the state, as contract models) and features.npz (the found grid columns, keyed by h3).
record_golden.py copies both; app.rescore calls hydrate() for a run that has no state. Re-ranking stays math only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import TypeAdapter

from contracts.models import (CommunityNeed, DemandModel, Factor, PracticeProfile, RegionFeature, ScoringSpec,
                              SiteCandidate, SiteInspection)

log = logging.getLogger(__name__)
STATE, FEATURES = "rescore_state.json", "features.npz"
_TYPES: dict[str, Any] = {
    "profile": PracticeProfile, "rank_profile": PracticeProfile, "demand": DemandModel | None,
    "factors": list[Factor], "sites": list[SiteCandidate], "spec": ScoringSpec,
    "inspections": dict[str, SiteInspection], "community": dict[str, CommunityNeed],
}
_ADAPTERS = {k: TypeAdapter(t) for k, t in _TYPES.items()}


def _columns(state: dict[str, Any], run_id: str, columns: dict[str, np.ndarray]) -> list[str]:
    """Grid columns this run added or scores with: its found datasets, their counts, the competitor column."""
    used = set(state["spec"].weights) if "spec" in state else set()
    demand = state.get("demand")
    if demand is not None and demand.competitor_feature:
        used.add(demand.competitor_feature)
    used |= {f.feature_id for f in state.get("factors", []) if f.feature_id}
    used |= {f"{c}_count" for c in used}
    return sorted(c for c in columns if run_id[:6] in c or c in used)


def save(directory: Path, run_id: str, state: dict[str, Any], geo: Any) -> None:
    payload = {k: _ADAPTERS[k].dump_python(v, mode="json") for k, v in state.items() if k in _ADAPTERS}
    grid = getattr(geo, "grid", None)
    if grid is not None:
        names = _columns(state, run_id, grid.columns)
        found = getattr(geo, "found", {})
        payload["found"] = [found[n].model_dump(mode="json") for n in names if n in found]
        np.savez_compressed(directory / FEATURES, h3=np.array(grid.h3), **{f"c_{n}": grid.columns[n] for n in names})
    (directory / STATE).write_text(json.dumps(payload))


def hydrate(directory: Path, state: dict[str, Any], geo: Any) -> bool:
    """Fill run.state from a saved snapshot and put its found columns back on the grid; False if none was saved."""
    path = directory / STATE
    if not path.is_file():
        return False
    payload = json.loads(path.read_text())
    for k, adapter in _ADAPTERS.items():
        if k in payload:
            state[k] = adapter.validate_python(payload[k])
    grid = getattr(geo, "grid", None)
    if grid is not None and (directory / FEATURES).is_file():
        with np.load(directory / FEATURES, allow_pickle=False) as z:
            where = np.array([grid.index.get(str(h), -1) for h in z["h3"]])
            ok = where >= 0
            for key in z.files:
                name = key[2:]
                if key.startswith("c_") and name not in grid.columns:
                    col = np.full(len(grid.h3), np.nan)
                    col[where[ok]] = z[key][ok]
                    grid.columns[name] = col
        grid._competitors.clear()
        for row in payload.get("found", []):
            geo.found.setdefault(row["id"], RegionFeature.model_validate(row))
    return True
