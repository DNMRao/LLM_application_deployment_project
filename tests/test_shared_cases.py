"""Run every shared evaluation case (unedited) against the dry provider."""

import asyncio
import json
import re

import pytest

from src.northstar.config import ROOT
from src.northstar.models import EvaluationCase, GenerateRequest

CASES = [
    EvaluationCase.model_validate(item)
    for item in json.loads((ROOT / "data" / "evaluation" / "cases.json").read_text())
]


def test_all_five_shared_cases_are_loaded():
    assert len(CASES) == 5


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_shared_case_passes_with_dry_provider(make_service, case):
    service, _ = make_service()

    async def ask_repeatedly():
        return [
            await service.generate(GenerateRequest(prompt=case.prompt))
            for _ in range(case.repeat)
        ]

    responses = asyncio.run(ask_repeatedly())

    for response in responses:
        for term in case.expected_terms:
            # whole word / phrase, case-insensitive
            assert re.search(rf"(?<!\w){re.escape(term)}(?!\w)", response.text, re.I)
        assert response.quality_score >= 0.8

    if case.repeat > 1:  # refund_supported: miss first, then hits
        assert responses[0].cache_hit is False
        assert all(r.cache_hit for r in responses[1:])
