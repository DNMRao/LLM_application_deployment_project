"""Functional evaluation: run the five shared cases and write a results table.

Run from the repository root:
    uv run python -m src.northstar.evaluation

This checks BEHAVIOUR (right policy terms, cache works). It is not a load
test: the load probe (scripts/load_test.py) measures latency and errors
under concurrency, and its results are reported separately.
"""

import asyncio
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from src.northstar.config import ROOT, Settings, load_settings
from src.northstar.models import CaseResult, EvaluationCase, GenerateRequest
from src.northstar.provider import build_provider
from src.northstar.service import GenerationService
from src.northstar.tracing import JsonlTraceSink

CASES_PATH = ROOT / "data" / "evaluation" / "cases.json"
REPORT_PATH = ROOT / "reports" / "generated" / "evaluation.md"
TRACE_PATH = ROOT / "reports" / "generated" / "evaluation_traces.jsonl"


def load_cases(path: Path = CASES_PATH) -> list[EvaluationCase]:
    """Read the shared cases exactly as given (labels are never edited)."""
    return [EvaluationCase.model_validate(item) for item in json.loads(path.read_text())]


def contains_term(text: str, term: str) -> bool:
    """Whole word/phrase, case-insensitive: 'reset' must not match 'preset'."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


async def evaluate_case(service: GenerationService, case: EvaluationCase) -> CaseResult:
    """Ask the case's question `repeat` times and judge the answers."""
    responses = [
        await service.generate(GenerateRequest(prompt=case.prompt))
        for _ in range(case.repeat)
    ]
    terms_ok = all(
        contains_term(r.text, term) for r in responses for term in case.expected_terms
    )
    # Cache check only makes sense for repeated cases: miss first, then hits.
    cache_verified = (
        None
        if case.repeat == 1
        else (not responses[0].cache_hit and all(r.cache_hit for r in responses[1:]))
    )
    return CaseResult(
        case_id=case.case_id,
        passed=terms_ok and cache_verified is not False,
        cache_verified=cache_verified,
        quality_score=sum(r.quality_score for r in responses) / len(responses),
        latency_ms=sum(r.latency_ms for r in responses) / len(responses),
    )


async def run_evaluation(settings: Settings) -> list[CaseResult]:
    # A fresh service = empty cache, so the first refund request is a miss.
    service = GenerationService(
        provider=build_provider(settings),
        settings=settings,
        trace_sink=JsonlTraceSink(TRACE_PATH),
    )
    return [await evaluate_case(service, case) for case in load_cases()]


def to_markdown(results: list[CaseResult], settings: Settings) -> str:
    passed = sum(r.passed for r in results)
    mean_quality = sum(r.quality_score for r in results) / len(results)
    lines = [
        "# Functional Evaluation",
        "",
        f"- Command: `uv run python -m src.northstar.evaluation`",
        f"- Run at: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- Provider / model: {settings.provider} / {settings.model}",
        f"- Environment: in-process service (no HTTP), cold cache",
        f"- Result: **{passed}/{len(results)} cases passed**, "
        f"mean quality {mean_quality:.2f} over {len(results)} cases",
        "",
        "| Case | Passed | Cache verified | Quality | Mean latency (ms) |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        cache = "n/a" if r.cache_verified is None else ("yes" if r.cache_verified else "NO")
        lines.append(
            f"| {r.case_id} | {'yes' if r.passed else 'NO'} | {cache} "
            f"| {r.quality_score:.2f} | {r.latency_ms:.3f} |"
        )
    lines += [
        "",
        "Quality is a keyword heuristic (0.9 if approved-policy language appears, "
        "else 0.4). It is not proof of correct policy advice.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    # Traces go to a local JSONL file, never Langfuse, so evaluation runs
    # don't mix with production traces.
    settings = replace(load_settings(), trace_sink="jsonl", trace_path=TRACE_PATH)
    results = asyncio.run(run_evaluation(settings))
    report = to_markdown(results, settings)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)
    print(report)
    print(f"Written to {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
