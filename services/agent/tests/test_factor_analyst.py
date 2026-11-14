from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.messages import AIMessage

from contracts.models import EvalCase, Factor
from services.agent import factor_analyst
from services.agent.factor_analyst import FactorList, analyze, repair, request_text

ROOT = Path(__file__).resolve().parents[3]
ORTHO = EvalCase.model_validate(yaml.safe_load((ROOT / "evals" / "orthopedic_group.yaml").read_text())).profile


def factor(fid: str, feature: str | None = None, priority: str = "high", direction: int = 1) -> Factor:
    return Factor(id=fid, factor=fid.replace("_", " "), why="w", proxy="p", direction=direction,  # type: ignore[arg-type]
                  priority=priority, stock_covered=feature is not None, feature_id=feature)  # type: ignore[arg-type]


class StubStructured:
    """Stands in for model.with_structured_output(..., include_raw=True): returns scripted results in order."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results: Iterator[dict[str, Any]] = iter(results)
        self.calls = 0

    async def ainvoke(self, messages: list[Any]) -> dict[str, Any]:
        self.calls += 1
        return next(self.results)


class StubModel:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.structured = StubStructured(results)

    def with_structured_output(self, schema: type, include_raw: bool = False) -> StubStructured:
        return self.structured


def test_request_text_lists_the_pack_features_and_hints() -> None:
    text = request_text(ORTHO)
    assert "n_orthopedics_within_10km" in text and "site:parking" in text
    assert "arthritis" in text  # pack factor hint
    assert "healthcare.orthopedics" in text


def test_repair_enforces_real_feature_ids_status_and_limits() -> None:
    drafts = [
        factor("competition", "n_orthopedics_within_10km", direction=-1),
        factor("made_up", "n_unicorns_within_5km"),
        factor("parking", "site:parking", priority="medium"),
        factor("competition", None, priority="low"),
        *[factor(f"extra_{i}", None, priority="low") for i in range(12)],
    ]
    fixed, notes = repair(drafts, ORTHO)
    assert len(fixed) == 12
    by_id = {f.id: f for f in fixed}
    assert by_id["competition"].status == "covered" and by_id["competition"].direction == -1
    assert by_id["made_up"].feature_id is None and by_id["made_up"].status == "searching"
    assert by_id["parking"].stock_covered and by_id["parking"].feature_id == "site:parking"
    assert "competition_2" in by_id  # duplicate id renamed
    assert any("n_unicorns_within_5km" in n for n in notes)


@pytest.mark.asyncio
async def test_analyze_retries_once_after_a_parsing_error() -> None:
    good = FactorList(factors=[factor("aging", "pop_45plus_within_5km")])
    model = StubModel([
        {"parsed": None, "parsing_error": "bad json", "raw": AIMessage(content="")},
        {"parsed": good, "parsing_error": None, "raw": AIMessage(content="")},
    ])
    factors, _ = await analyze(ORTHO, model=model)  # type: ignore[arg-type]
    assert [f.id for f in factors] == ["aging"] and model.structured.calls == 2


@pytest.mark.asyncio
async def test_analyze_accepts_plain_json_text_and_gives_up_after_two_failures() -> None:
    text = 'Here you go: {"factors": [{"id": "a", "factor": "A", "why": "w", "proxy": "p", "direction": 1, "priority": "high"}]}'
    model = StubModel([{"parsed": None, "parsing_error": "no tool call", "raw": AIMessage(content=text)}])
    factors, _ = await analyze(ORTHO, model=model)  # type: ignore[arg-type]
    assert factors[0].id == "a" and factors[0].status == "searching"

    failing = StubModel([{"parsed": None, "parsing_error": "nope", "raw": AIMessage(content="")}] * 2)
    with pytest.raises(ValueError, match="no valid factors"):
        await analyze(ORTHO, model=failing)  # type: ignore[arg-type]


def test_planned_graph_builds_without_calling_aws(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    from services.agent.graph import build_agent

    agent = build_agent(ORTHO, tmp_path / "run")
    assert hasattr(agent, "ainvoke")
    assert factor_analyst.PROMPT.strip()
