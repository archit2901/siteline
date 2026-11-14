"""Export JSON Schema for every contract model.

Run from the repo root:  python -m contracts.export
Writes contracts/schema/<Model>.json, contracts/schema/all.json (every model under $defs) and VERSION.
web/ generates its TypeScript types from all.json (see `make types`).
"""

import json
from pathlib import Path

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from contracts import models

OUT = Path(__file__).parent / "schema"


def contract_classes() -> list[type[BaseModel]]:
    classes = []
    for name in dir(models):
        obj = getattr(models, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, BaseModel)
            and obj is not BaseModel
            and obj.__module__ == models.__name__
        ):
            classes.append(obj)
    return sorted(classes, key=lambda c: c.__name__)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    classes = contract_classes()
    for cls in classes:
        (OUT / f"{cls.__name__}.json").write_text(json.dumps(cls.model_json_schema(), indent=2))
    _, combined = models_json_schema([(c, "validation") for c in classes], title="SitelineContracts")
    (OUT / "all.json").write_text(json.dumps(combined, indent=2))
    (OUT / "VERSION").write_text(models.CONTRACT_VERSION + "\n")
    print(f"exported {len(classes)} schemas (contracts v{models.CONTRACT_VERSION}) to {OUT}")


if __name__ == "__main__":
    main()
