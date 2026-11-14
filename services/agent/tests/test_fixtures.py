from pathlib import Path

import pytest
from pydantic import ValidationError

from fixtures import validate


def test_every_fixture_validates() -> None:
    assert validate.main() == 0


def test_bad_event_line_fails(tmp_path: Path) -> None:
    bad = tmp_path / "events.jsonl"
    bad.write_text('{"run_id": "r", "seq": 0, "ts": "2026-11-14T05:00:00Z", "agent": "x", "type": "nope"}\n')
    with pytest.raises(ValidationError):
        validate.check_events(bad)


def test_grid_rows_need_h3(tmp_path: Path) -> None:
    bad = tmp_path / "grid.json"
    bad.write_text('[{"lat": 29.7}]')
    with pytest.raises(ValueError):
        validate.check_grid(bad)
