"""Stock packs (packs/*.yaml): loading with `extends`, and the feature catalog they define."""

from functools import lru_cache
from pathlib import Path

import yaml

from contracts.models import PackFeature, PracticeProfile, RegionFeature, StockPack

ROOT = Path(__file__).resolve().parents[3]
PACKS = ROOT / "packs"
BASE = "healthcare.base"
PROVENANCE = {
    "acs": "https://www.census.gov/data/developers/data-sets/acs-5year.html",
    "cms.doctors": "https://data.cms.gov/provider-data/dataset/mj5m-pzi6",
    "cms.hospitals": "https://data.cms.gov/provider-data/dataset/xubh-q36u",
}


@lru_cache
def _raw(pack_id: str) -> StockPack:
    path = PACKS / f"{pack_id}.yaml"
    if not path.is_file():
        raise KeyError(f"no pack {pack_id!r}")
    return StockPack.model_validate(yaml.safe_load(path.read_text()))


@lru_cache
def load_pack(pack_id: str) -> StockPack:
    """The pack merged with its `extends` chain; features with the same id override the parent's."""
    pack = _raw(pack_id)
    if not pack.extends:
        return pack
    parent = load_pack(pack.extends)
    features = {f.id: f for f in parent.features} | {f.id: f for f in pack.features}
    return pack.model_copy(update={
        "features": list(features.values()),
        "demand_age_bands": pack.demand_age_bands or parent.demand_age_bands,
        "visit_rates": parent.visit_rates | pack.visit_rates,
        "site_rules": parent.site_rules | pack.site_rules,
        "factor_hints": parent.factor_hints + pack.factor_hints,
    })


def pack_for(profile: PracticeProfile) -> StockPack:
    """profile.stock_pack, else healthcare.<specialty>, else healthcare.base."""
    for pack_id in (profile.stock_pack, f"healthcare.{profile.specialty}", BASE):
        if pack_id:
            try:
                return load_pack(pack_id)
            except KeyError:
                continue
    return load_pack(BASE)


def all_pack_ids() -> list[str]:
    return sorted(p.stem for p in PACKS.glob("*.yaml"))


@lru_cache
def feature_catalog() -> dict[str, PackFeature]:
    """Every stock feature defined by any pack (id -> definition; the first pack wins for label and direction)."""
    catalog: dict[str, PackFeature] = {}
    for pack_id in [BASE, *[p for p in all_pack_ids() if p != BASE]]:
        for feature in _raw(pack_id).features:
            catalog.setdefault(feature.id, feature)
    return catalog


def region_features() -> list[RegionFeature]:
    base_ids = {f.id for f in _raw(BASE).features}
    return [
        RegionFeature(
            id=f.id, label=f.label or f.id, source=f.source, unit=f.unit,
            layer="stock_base" if f.id in base_ids else "stock_pack",
            provenance_url=PROVENANCE.get(f.source, PROVENANCE["acs"]),  # type: ignore[arg-type]
        )
        for f in feature_catalog().values()
    ]
