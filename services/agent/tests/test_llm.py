import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from services.agent import llm


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in ("LLM_PROVIDER", "MODEL_SMART", "MODEL_FAST", "MODEL_CRITIC", "LLM_PROVIDER_JUDGE", "LLM_CACHE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(llm, "_cache", None)
    yield


def fake_model(n: int = 5) -> GenericFakeChatModel:
    reply = AIMessage(content="ok", usage_metadata={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200})
    return GenericFakeChatModel(messages=iter([reply] * n))


def test_model_ids_per_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_SMART", "us.amazon.nova-premier-v1:0")
    assert llm.model_id("factor_analyst") == "us.amazon.nova-premier-v1:0"
    assert llm.model_id("dataset_scout") == llm.DEFAULT_MODEL_FAST
    monkeypatch.setenv("MODEL_CRITIC", "us.amazon.nova-pro-v1:0")
    assert llm.model_id("critic") == "us.amazon.nova-pro-v1:0"


def test_smart_model_must_be_set() -> None:
    with pytest.raises(RuntimeError, match="MODEL_SMART"):
        llm.model_id("orchestrator")


def test_per_role_provider_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_JUDGE", "gemini")
    assert llm.provider_for("judge") == "gemini"
    assert llm.provider_for("site_scout") == "bedrock"
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(ValueError):
        llm.provider_for("site_scout")


def test_temperature_zero_for_reasoning_roles() -> None:
    assert llm.temperature("factor_analyst") == llm.temperature("critic") == llm.temperature("judge") == 0.0
    assert llm.temperature("dataset_scout") > 0


def test_cost_estimate_strips_region_prefix() -> None:
    assert llm.estimate_cost("us.amazon.nova-2-lite-v1:0", 1_000_000, 1_000_000) == pytest.approx(2.80)
    assert llm.estimate_cost("some-free-model", 10_000, 10_000) == 0.0


def test_bedrock_model_builds_without_calling_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_SMART", "us.amazon.nova-premier-v1:0")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    model = llm.get_chat_model("factor_analyst")
    assert model.model_id == "us.amazon.nova-premier-v1:0"  # type: ignore[attr-defined]
    assert model.temperature == 0.0  # type: ignore[attr-defined]
    assert isinstance(model.cache, llm.SQLiteLLMCache)


def test_cache_hits_are_free_and_logged(tmp_path: Path) -> None:
    meter = llm.UsageMeter(run_id="r1", budget=5)
    model = llm.instrument(fake_model(), "dataset_scout", meter, "bedrock", "us.amazon.nova-2-lite-v1:0")
    assert model.invoke("hello").content == "ok"
    assert model.invoke("hello").content == "ok"  # same prompt -> cache
    snap = meter.snapshot()
    assert (snap["calls"], snap["cache_hits"]) == (1, 1)
    assert (snap["input_tokens"], snap["output_tokens"]) == (1000, 200)
    assert snap["cost_usd"] == pytest.approx(1000 * 0.30 / 1e6 + 200 * 2.50 / 1e6)
    with sqlite3.connect(tmp_path / "runs.db") as db:
        rows = db.execute("SELECT role, cached, input_tokens FROM llm_calls ORDER BY rowid").fetchall()
    assert rows == [("dataset_scout", 0, 1000), ("dataset_scout", 1, 0)]


def test_budget_stops_extra_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_CACHE", "0")
    meter = llm.UsageMeter(run_id="r2", budget=2, db_path=None)
    model = llm.instrument(fake_model(), "site_scout", meter, "bedrock", "us.amazon.nova-2-lite-v1:0")
    model.invoke("a")
    model.invoke("b")
    with pytest.raises(llm.LLMBudgetExceeded):
        model.invoke("c")
    assert meter.calls == 2
