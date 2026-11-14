"""Factor-analyst evals: run evals/*.yaml through the factor-analyst and score the factor lists.

  PYTHONPATH=. python -m services.agent.evals.run_evals [--mode scripted|planned|both] [--cases a,b] [--no-cache]

For each case and mode: validate list[Factor], ask the "judge" role (MODEL_FAST, temperature 0) whether each
must_include theme is covered and by which factor, check sign_checks on the matched factor, and print a table with
calls, tokens and estimated cost from the UsageMeter.
"""

import argparse
import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from contracts.models import EvalCase, Factor
from services.agent import llm

ROOT = Path(__file__).resolve().parents[3]
JUDGE_PROMPT = (
    "You check whether a list of location-planning factors for a business covers a required theme. "
    "A factor covers the theme if it measures the same idea, even in different words (for example 'People 45+ "
    "within 5 km' covers 'aging population'; 'Few orthopedic surgeons nearby' covers 'orthopedic surgeon "
    "competition'). Pick the single best matching factor id, or matched=false if none covers it."
)


class ThemeJudgment(BaseModel):
    matched: bool
    factor_id: str | None = None
    reason: str = ""


@dataclass
class CaseResult:
    case: str
    mode: str
    factors: list[Factor]
    themes: dict[str, ThemeJudgment] = field(default_factory=dict)
    forbidden: dict[str, ThemeJudgment] = field(default_factory=dict)
    signs: dict[str, bool] = field(default_factory=dict)
    usage: dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def themes_passed(self) -> int:
        return sum(j.matched for j in self.themes.values())

    @property
    def checks(self) -> tuple[int, int]:
        passed = self.themes_passed + sum(self.signs.values()) + sum(not j.matched for j in self.forbidden.values())
        total = len(self.themes) + len(self.signs) + len(self.forbidden)
        return passed, total


def factor_lines(factors: list[Factor]) -> str:
    return "\n".join(f"- {f.id}: {f.factor} (proxy: {f.proxy}; direction {f.direction:+d})" for f in factors)


async def judge(theme: str, factors: list[Factor], meter: llm.UsageMeter) -> ThemeJudgment:
    model = llm.get_chat_model("judge", meter).with_structured_output(ThemeJudgment)
    answer = await model.ainvoke([
        SystemMessage(JUDGE_PROMPT),
        HumanMessage(f"FACTORS:\n{factor_lines(factors)}\n\nTHEME: {theme}"),
    ])
    j = answer if isinstance(answer, ThemeJudgment) else ThemeJudgment.model_validate(answer)
    if j.matched and j.factor_id not in {f.id for f in factors}:
        return ThemeJudgment(matched=False, reason=f"judge named unknown factor {j.factor_id!r}")
    return j


async def run_case(case: EvalCase, mode: str) -> CaseResult:
    meter = llm.UsageMeter(run_id=f"eval-{case.id}-{mode}", budget=80)
    started = time.monotonic()
    result = CaseResult(case=case.id, mode=mode, factors=[])
    try:
        if mode == "scripted":
            from services.agent.factor_analyst import analyze

            result.factors, result.notes = await analyze(case.profile, meter)
        else:
            from services.agent.graph import planned_factors

            run_dir = llm.runs_dir() / "evals" / f"{case.id}-{mode}-{int(time.time())}"
            planned = await planned_factors(case.profile, meter.run_id, run_dir, meter)
            result.factors, result.notes = planned.factors, planned.notes + [f"tool calls: {planned.tool_calls}"]
            if not planned.ok:
                result.error = "; ".join(planned.notes)
    except Exception as exc:  # report the failure in the table instead of aborting the whole eval
        result.error = f"{type(exc).__name__}: {str(exc)[:200]}"

    if result.factors:
        by_id = {f.id: f for f in result.factors}
        for theme in case.must_include:
            result.themes[theme] = await judge(theme, result.factors, meter)
        for theme in case.must_not_include:
            result.forbidden[theme] = await judge(theme, result.factors, meter)
        for theme, expected in case.sign_checks.items():
            j = result.themes.get(theme) or await judge(theme, result.factors, meter)
            result.signs[theme] = bool(j.matched and j.factor_id and by_id[j.factor_id].direction == expected)
    else:
        result.themes = {t: ThemeJudgment(matched=False, reason="no factors") for t in case.must_include}
        result.signs = {t: False for t in case.sign_checks}
    result.usage = meter.snapshot()
    result.seconds = time.monotonic() - started
    return result


def print_table(results: list[CaseResult]) -> None:
    header = f"{'case':20s} {'mode':9s} {'themes':>7s} {'signs':>6s} {'factors':>7s} {'calls':>5s} {'tokens':>8s} {'cost':>8s} {'secs':>5s}  missed"
    print(header)
    print("-" * len(header))
    for r in results:
        missed = [t for t, j in r.themes.items() if not j.matched]
        missed += [f"sign:{t}" for t, ok in r.signs.items() if not ok]
        missed += [f"forbidden:{t}" for t, j in r.forbidden.items() if j.matched]
        tokens = int(r.usage.get("input_tokens", 0) + r.usage.get("output_tokens", 0))
        print(f"{r.case:20s} {r.mode:9s} {r.themes_passed:>3d}/{len(r.themes):<3d} "
              f"{sum(r.signs.values()):>2d}/{len(r.signs):<3d} {len(r.factors):>7d} {int(r.usage.get('calls', 0)):>5d} "
              f"{tokens:>8d} ${r.usage.get('cost_usd', 0):>7.4f} {r.seconds:>5.0f}  {', '.join(missed) or '-'}"
              + (f"  ERROR {r.error}" if r.error else ""))
    passed = sum(r.checks[0] for r in results)
    total = sum(r.checks[1] for r in results)
    cost = sum(r.usage.get("cost_usd", 0) for r in results)
    print(f"\noverall {passed}/{total} checks ({passed / max(total, 1):.0%}), estimated cost ${cost:.4f}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["scripted", "planned", "both"], default="both")
    parser.add_argument("--cases", default="", help="comma-separated eval ids (default: all)")
    parser.add_argument("--no-cache", action="store_true", help="bypass the LLM response cache")
    parser.add_argument("--verbose", action="store_true", help="print each factor list")
    args = parser.parse_args()
    if args.no_cache:
        os.environ["LLM_CACHE"] = "0"

    wanted = {c for c in args.cases.split(",") if c}
    cases = [EvalCase.model_validate(yaml.safe_load(p.read_text())) for p in sorted((ROOT / "evals").glob("*.yaml"))]
    cases = [c for c in cases if not wanted or c.id in wanted]
    modes = ["scripted", "planned"] if args.mode == "both" else [args.mode]

    results = []
    for case in cases:
        for mode in modes:
            r = await run_case(case, mode)
            results.append(r)
            if args.verbose:
                print(f"\n# {case.id} ({mode})\n{factor_lines(r.factors)}\nnotes: {r.notes}")
    print()
    print_table(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
