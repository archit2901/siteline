"""One chat-model factory for every agent role.

Provider: LLM_PROVIDER=bedrock (default; Amazon Nova through ChatBedrockConverse, default AWS credential chain),
or gemini / openrouter for development only. Per-role overrides: LLM_PROVIDER_<ROLE>, MODEL_<ROLE>.
Every model gets a shared per-provider rate limiter, provider retries with backoff, an optional SQLite response cache
(runs/llm_cache.db, LLM_CACHE=1) and, when a UsageMeter is passed, a per-run call budget plus token / cost logging
to runs/runs.db.
"""

import json
import os
import sqlite3
import threading
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from langchain_core.caches import RETURN_VAL_TYPE, BaseCache
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.load import dumps, loads
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core._api import LangChainBetaWarning

Role = Literal["orchestrator", "factor_analyst", "dataset_scout", "site_scout", "critic", "judge"]
Provider = Literal["bedrock", "gemini", "openrouter"]

SMART_ROLES: frozenset[str] = frozenset({"orchestrator", "factor_analyst", "critic"})
ZERO_TEMPERATURE_ROLES: frozenset[str] = frozenset({"factor_analyst", "critic", "judge"})
DEFAULT_MODEL_FAST = "us.amazon.nova-2-lite-v1:0"
DEFAULT_RPS: dict[str, float] = {"bedrock": 2.0, "gemini": 0.15, "openrouter": 0.2}
OPENROUTER_URL = "https://openrouter.ai/api/v1"

# USD per 1M tokens (input, output). Bedrock standard tier, from aws.amazon.com/bedrock/pricing (Nov 2026).
# Keys drop the cross-region prefix ("us.", "global.", ...). Unknown models cost 0 (free dev providers).
PRICES: dict[str, tuple[float, float]] = {
    "amazon.nova-2-lite-v1:0": (0.30, 2.50),
    "amazon.nova-2-pro-v1:0": (1.25, 10.00),
    "amazon.nova-premier-v1:0": (2.50, 12.50),
    "amazon.nova-pro-v1:0": (0.80, 3.20),
    "amazon.nova-lite-v1:0": (0.06, 0.24),
    "amazon.nova-micro-v1:0": (0.035, 0.14),
}
REGION_PREFIXES = ("us.", "eu.", "apac.", "jp.", "au.", "ca.", "us-gov.", "global.")

ROOT = Path(__file__).resolve().parents[2]


class LLMBudgetExceeded(RuntimeError):
    pass


def env(name: str, default: str | None = None) -> str | None:
    """Environment variable, treating an empty value as unset."""
    value = os.environ.get(name, "").strip()
    return value or default


def runs_dir() -> Path:
    return Path(env("RUNS_DIR", str(ROOT / "runs")) or "runs")


def provider_for(role: Role) -> Provider:
    value = env(f"LLM_PROVIDER_{role.upper()}") or env("LLM_PROVIDER", "bedrock")
    if value not in ("bedrock", "gemini", "openrouter"):
        raise ValueError(f"unknown LLM provider {value!r}")
    return value  # type: ignore[return-value]


def model_id(role: Role) -> str:
    override = env(f"MODEL_{role.upper()}")
    if override:
        return override
    tier = "MODEL_SMART" if role in SMART_ROLES else "MODEL_FAST"
    value = env(tier)
    if value:
        return value
    if provider_for(role) == "bedrock" and tier == "MODEL_FAST":
        return DEFAULT_MODEL_FAST
    raise RuntimeError(f"{tier} is not set (needed for role {role!r})")


def temperature(role: Role) -> float:
    return 0.0 if role in ZERO_TEMPERATURE_ROLES else float(env("LLM_TEMPERATURE", "0.3") or 0.3)


def price_per_million(model: str) -> tuple[float, float]:
    key = next((model[len(p):] for p in REGION_PREFIXES if model.startswith(p)), model)
    return PRICES.get(key, (0.0, 0.0))


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = price_per_million(model)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------
class SQLiteLLMCache(BaseCache):
    """Exact-match cache keyed by (serialized prompt, model settings)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS llm_cache (prompt TEXT, llm TEXT, value TEXT, PRIMARY KEY (prompt, llm))")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=30)

    def lookup(self, prompt: str, llm_string: str) -> RETURN_VAL_TYPE | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value FROM llm_cache WHERE prompt = ? AND llm = ?", (prompt, llm_string)).fetchone()
        if row is None:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LangChainBetaWarning)
            return [loads(g, allowed_objects="core") for g in json.loads(row[0])]

    def update(self, prompt: str, llm_string: str, return_val: RETURN_VAL_TYPE) -> None:
        value = json.dumps([dumps(g) for g in return_val])
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?)", (prompt, llm_string, value))

    def clear(self, **kwargs: Any) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM llm_cache")


_cache: SQLiteLLMCache | None = None


def response_cache() -> SQLiteLLMCache | None:
    global _cache
    if env("LLM_CACHE", "1") != "1":
        return None
    if _cache is None:
        _cache = SQLiteLLMCache(runs_dir() / "llm_cache.db")
    return _cache


# ---------------------------------------------------------------------------
# Per-run budget, token and cost logging
# ---------------------------------------------------------------------------
@dataclass
class UsageMeter:
    """Counts LLM calls for one run. Cache hits are free and do not count against the budget."""

    run_id: str
    budget: int = field(default_factory=lambda: int(env("LLM_MAX_CALLS_PER_RUN", "40") or 40))
    db_path: Path | None = field(default_factory=lambda: runs_dir() / "runs.db")
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        """Run totals for tool_result payloads ("usage")."""
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "budget": self.budget,
        }

    def record(self, row: dict[str, Any]) -> None:
        if self.db_path is None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=30) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS llm_calls (run_id TEXT, ts TEXT, role TEXT, provider TEXT, model TEXT, "
                "input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, cached INTEGER, latency_ms INTEGER)"
            )
            db.execute(
                "INSERT INTO llm_calls VALUES (:run_id, :ts, :role, :provider, :model, :input_tokens, :output_tokens, "
                ":cost_usd, :cached, :latency_ms)",
                row,
            )


class UsageHandler(BaseCallbackHandler):
    """Enforces the meter's budget before each call and logs tokens and cost after it."""

    raise_error = True  # a budget overrun must stop the call, not be swallowed

    def __init__(self, meter: UsageMeter, role: Role, provider: str, model: str) -> None:
        self.meter, self.role, self.provider, self.model = meter, role, provider, model
        self._started: dict[UUID, float] = {}

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any) -> None:
        with self.meter.lock:
            if self.meter.calls >= self.meter.budget:
                raise LLMBudgetExceeded(f"run {self.meter.run_id} reached LLM_MAX_CALLS_PER_RUN={self.meter.budget}")
            self.meter.calls += 1
        self._started[run_id] = time.monotonic()

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._started.pop(run_id, time.monotonic())
        usage: dict[str, Any] = {}
        for generations in response.generations:
            for gen in generations:
                usage = getattr(getattr(gen, "message", None), "usage_metadata", None) or usage
        cached = usage.get("total_cost") == 0  # langchain-core marks cache hits this way
        tokens_in = 0 if cached else int(usage.get("input_tokens", 0))
        tokens_out = 0 if cached else int(usage.get("output_tokens", 0))
        cost = estimate_cost(self.model, tokens_in, tokens_out)
        with self.meter.lock:
            if cached:
                self.meter.calls -= 1
                self.meter.cache_hits += 1
            self.meter.input_tokens += tokens_in
            self.meter.output_tokens += tokens_out
            self.meter.cost_usd += cost
        self.meter.record({
            "run_id": self.meter.run_id, "ts": datetime.now(timezone.utc).isoformat(), "role": self.role,
            "provider": self.provider, "model": self.model, "input_tokens": tokens_in, "output_tokens": tokens_out,
            "cost_usd": cost, "cached": int(cached), "latency_ms": int((time.monotonic() - started) * 1000),
        })


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
_limiters: dict[str, InMemoryRateLimiter] = {}


def rate_limiter(provider: str) -> InMemoryRateLimiter:
    if provider not in _limiters:
        rps = float(env("LLM_RPS", str(DEFAULT_RPS[provider])) or DEFAULT_RPS[provider])
        _limiters[provider] = InMemoryRateLimiter(requests_per_second=rps, check_every_n_seconds=0.1, max_bucket_size=2)
    return _limiters[provider]


def instrument(model: BaseChatModel, role: Role, meter: UsageMeter | None, provider: str, model_name: str) -> BaseChatModel:
    """Attach the response cache and, with a meter, the budget / usage handler."""
    cache = response_cache()
    if cache is not None:
        model.cache = cache
    if meter is not None:
        model.callbacks = [*(model.callbacks or []), UsageHandler(meter, role, provider, model_name)]  # type: ignore[list-item]
    return model


def get_chat_model(role: Role, meter: UsageMeter | None = None) -> BaseChatModel:
    provider = provider_for(role)
    name = model_id(role)
    retries = int(env("LLM_MAX_RETRIES", "6") or 6)
    max_tokens = int(env("LLM_MAX_TOKENS", "4096") or 4096)
    common: dict[str, Any] = {"temperature": temperature(role), "rate_limiter": rate_limiter(provider)}

    model: BaseChatModel
    if provider == "bedrock":
        from botocore.config import Config
        from langchain_aws import ChatBedrockConverse

        model = ChatBedrockConverse(
            model=name,
            region_name=env("AWS_REGION", "us-east-1"),
            max_tokens=max_tokens,
            config=Config(retries={"max_attempts": retries, "mode": "adaptive"}, read_timeout=300, connect_timeout=10),
            **common,
        )
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(
            model=name, google_api_key=env("GEMINI_API_KEY"), max_retries=retries, max_output_tokens=max_tokens, **common
        )
    else:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=name, base_url=OPENROUTER_URL, api_key=env("OPENROUTER_API_KEY"), max_retries=retries,
            max_completion_tokens=max_tokens, **common,
        )
    return instrument(model, role, meter, provider, name)
