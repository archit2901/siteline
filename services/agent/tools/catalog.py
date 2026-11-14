"""catalog_search over catalog/houston.yaml (curated, official dataset URLs only)."""

import re
from functools import lru_cache
from pathlib import Path

import yaml

from contracts.models import Catalog, CatalogEntry

ROOT = Path(__file__).resolve().parents[3]


@lru_cache
def load_catalog(region_id: str = "hou") -> Catalog:
    path = ROOT / "catalog" / {"hou": "houston.yaml"}[region_id]
    return Catalog.model_validate(yaml.safe_load(path.read_text()))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def catalog_search(query: str, region_id: str = "hou", limit: int = 5) -> list[CatalogEntry]:
    """Entries ranked by how many query words appear in their title, measures and notes."""
    q = _words(query)
    scored = []
    for entry in load_catalog(region_id).entries:
        hits = len(q & _words(" ".join([entry.title, entry.publisher, *entry.measures, entry.notes])))
        if hits:
            scored.append((hits, entry))
    return [e for _, e in sorted(scored, key=lambda x: (-x[0], x[1].id))[:limit]]
