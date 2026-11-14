"""deepagents orchestrator (AGENT_MODE=planned). Runtime: deepagents -> langchain create_agent -> LangGraph graph.

Task 4 scope: site-strategist + factor-analyst subagent. The factor-analyst writes /factors.json in the run
filesystem (runs/<run_id>/), which Python then validates and repairs exactly like scripted mode.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from contracts.models import Factor, PracticeProfile
from services.agent import llm
from services.agent.factor_analyst import PROMPT as FACTOR_PROMPT
from services.agent.factor_analyst import FactorList, repair, request_text
from services.agent.tools.geo import stock_feature_summary

PROMPTS = Path(__file__).parent / "prompts"
STRATEGIST_PROMPT = (PROMPTS / "strategist.md").read_text()
FACTORS_FILE = "factors.json"
SUBAGENT_OUTPUT = (
    "\n\n## In this run\nWhen your list is ready, call the save_factors tool once with the whole list. If it reports "
    "an error, fix the list and call it again. Then reply with one sentence: how many factors you saved. Do not "
    "return the list in your reply."
)


def build_agent(profile: PracticeProfile, run_dir: Path, meter: llm.UsageMeter | None = None,
                saved: list[list[Factor]] | None = None) -> CompiledStateGraph:
    """The orchestrator graph. Each validated save_factors call is appended to `saved` (Python's source of truth;
    the orchestrator has file tools and could overwrite /factors.json)."""
    run_dir.mkdir(parents=True, exist_ok=True)

    def list_stock_features() -> str:
        """List the stock features available for this practice (id, label, unit, default direction)."""
        return stock_feature_summary(profile)

    @tool(args_schema=FactorList)
    def save_factors(factors: list[Factor]) -> str:
        """Save this practice's factor list (6-12 factors). Arguments are validated against the Factor contract."""
        (run_dir / FACTORS_FILE).write_text(FactorList(factors=factors).model_dump_json(indent=2))
        if saved is not None:
            saved.append(list(factors))
        return f"Saved {len(factors)} factors to /{FACTORS_FILE}."

    factor_analyst = {
        "name": "factor-analyst",
        "description": "Works out 6-12 factors that make a new office succeed for this practice and writes them to "
                       "/factors.json. Give it the full practice intake.",
        # The intake goes into the subagent's own prompt: subagents only see the task description, and the
        # orchestrator may paraphrase it.
        "system_prompt": f"{FACTOR_PROMPT}\n\n## This practice\n{request_text(profile)}{SUBAGENT_OUTPUT}",
        "tools": [list_stock_features, save_factors],
        "model": llm.get_chat_model("factor_analyst", meter),
    }
    return create_deep_agent(
        model=llm.get_chat_model("orchestrator", meter),
        tools=[list_stock_features],
        system_prompt=STRATEGIST_PROMPT,
        subagents=[factor_analyst],  # type: ignore[list-item]
        backend=FilesystemBackend(root_dir=run_dir, virtual_mode=True),
        checkpointer=InMemorySaver(),
        name="site-strategist",
    )


@dataclass
class PlannedResult:
    factors: list[Factor]
    notes: list[str]
    tool_calls: list[str] = field(default_factory=list)
    ok: bool = True


async def planned_factors(profile: PracticeProfile, run_id: str, run_dir: Path,
                          meter: llm.UsageMeter | None = None, recursion_limit: int = 60) -> PlannedResult:
    """Run the orchestrator until the factor-analyst has written /factors.json; validate and repair it in Python."""
    saved: list[list[Factor]] = []
    agent = build_agent(profile, run_dir, meter, saved)
    config: dict[str, Any] = {"configurable": {"thread_id": run_id}, "recursion_limit": recursion_limit}
    state = await agent.ainvoke({"messages": [{"role": "user", "content": request_text(profile)}]}, config)
    calls = [c["name"] for m in state.get("messages", []) if isinstance(m, AIMessage) for c in m.tool_calls]

    if not saved:
        return PlannedResult([], ["the factor-analyst never saved a valid factor list"], calls, ok=False)
    factors, notes = repair(saved[-1], profile)
    return PlannedResult(factors, notes, calls, ok=bool(factors))
