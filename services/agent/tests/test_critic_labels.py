"""Digits inside a Python-written feature label are not numbers to verify; the value after 'here:' still is."""

import asyncio

from contracts.models import Claim
from services.agent.critic import verify_claims
from services.agent.tools.geo_client import MockGeo


def test_label_digits_are_not_claims() -> None:
    geo = MockGeo()
    cell = geo.grid.h3[0]
    value = asyncio.run(geo.feature_value("pop_total_within_5km", cell)).value
    ok = Claim(text=f"People within 5 km here: {value:,.0f}.", evidence_refs=[f"feature:pop_total_within_5km@{cell}"])
    bad = Claim(text="People within 5 km here: 999,999,999.", evidence_refs=[f"feature:pop_total_within_5km@{cell}"])

    class Rec:
        def __init__(self, claims: list[Claim]) -> None:
            self.justification = claims
            self.site = type("S", (), {"id": "s1"})()

    report = type("R", (), {"recommendations": [Rec([ok, bad])]})()
    problems = asyncio.run(verify_claims(report, {}, {}, geo))  # type: ignore[arg-type]
    assert [p.claim for p in problems] == [bad.text]
