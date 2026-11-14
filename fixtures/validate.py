"""Validate every fixture and YAML file against contracts/models.py.

Run from the repo root: PYTHONPATH=. python fixtures/validate.py
Missing files are skipped with a warning. Exits 1 if anything fails validation.
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from contracts.models import Catalog, EvalCase, Report, RunEvent, ScoreResponse, SiteCandidate, StockPack

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


def check_model(model: type[BaseModel]) -> Callable[[Path], str]:
    def check(path: Path) -> str:
        loader = yaml.safe_load if path.suffix in (".yaml", ".yml") else json.loads
        model.model_validate(loader(path.read_text()))
        return "ok"

    return check


def check_events(path: Path) -> str:
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    events = [RunEvent.model_validate_json(line) for line in lines]
    seqs = [e.seq for e in events]
    if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
        raise ValueError("seq values are not strictly increasing")
    if len({e.run_id for e in events}) > 1:
        raise ValueError("events belong to more than one run_id")
    return f"{len(events)} events"


def check_sites(path: Path) -> str:
    sites = TypeAdapter(list[SiteCandidate]).validate_json(path.read_text())
    return f"{len(sites)} sites"


def check_grid(path: Path) -> str:
    rows: Any = json.loads(path.read_text())
    if not isinstance(rows, list) or not rows:
        raise ValueError("expected a non-empty JSON list")
    bad = [i for i, r in enumerate(rows) if not isinstance(r, dict) or "h3" not in r]
    if bad:
        raise ValueError(f"{len(bad)} rows without an 'h3' key (first at index {bad[0]})")
    return f"{len(rows)} cells"


def targets() -> list[tuple[Path, Callable[[Path], str]]]:
    items: list[tuple[Path, Callable[[Path], str]]] = [
        (FIXTURES / "events.jsonl", check_events),
        (FIXTURES / "zones.json", check_model(ScoreResponse)),
        (FIXTURES / "report.json", check_model(Report)),
        (FIXTURES / "sites.json", check_sites),
        (FIXTURES / "fake_grid.json", check_grid),
        (ROOT / "catalog" / "houston.yaml", check_model(Catalog)),
    ]
    items += [(p, check_model(StockPack)) for p in sorted((ROOT / "packs").glob("*.yaml"))]
    items += [(p, check_model(EvalCase)) for p in sorted((ROOT / "evals").glob("*.yaml"))]
    golden = FIXTURES / "golden"
    items += [(p, check_events) for p in sorted(golden.glob("*.jsonl"))]
    for d in sorted(p for p in golden.glob("*") if p.is_dir()):
        for name, model in (("zones.json", ScoreResponse), ("report.json", Report)):
            if (d / name).exists():
                items.append((d / name, check_model(model)))
    return items


def main() -> int:
    failures = 0
    for path, check in targets():
        rel = path.relative_to(ROOT)
        if not path.exists():
            print(f"WARN  {rel}: missing, skipped")
            continue
        try:
            print(f"OK    {rel}: {check(path)}")
        except (ValidationError, ValueError, yaml.YAMLError) as exc:
            failures += 1
            print(f"FAIL  {rel}: {exc}")
    print(f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
