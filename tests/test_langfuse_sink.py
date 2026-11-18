"""LangfuseTraceSink with a fake client: checks the observation tree offline."""

import asyncio
from contextlib import contextmanager

import pytest

from src.northstar.config import Settings
from src.northstar.models import GenerateRequest
from src.northstar.provider import DryProvider
from src.northstar.service import GenerationService
from src.northstar.tracing import LangfuseTraceSink, MemoryTraceSink, build_trace_sink

REFUND = "What evidence is required for a duplicate-payment refund?"


class FakeObservation:
    def __init__(self, log, kwargs):
        self.log, self.kwargs = log, kwargs
        self.scores, self.updates = [], []

    def score(self, **kwargs):
        self.scores.append(kwargs)

    def update(self, **kwargs):
        self.updates.append(kwargs)


class FakeLangfuse:
    """Records every observation the sink opens, in order."""

    def __init__(self):
        self.observations: list[FakeObservation] = []
        self.flushed = False

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        observation = FakeObservation(self.observations, kwargs)
        self.observations.append(observation)
        yield observation

    def flush(self):
        self.flushed = True


def traces_for(*requests, provider=None, **settings):
    """Run requests through a real service and return the TraceRecords."""
    sink = MemoryTraceSink()
    service = GenerationService(provider or DryProvider(), Settings(**settings), sink)
    for request in requests:
        try:
            asyncio.run(service.generate(request))
        except Exception:
            pass
    return sink.traces


def export(trace):
    client = FakeLangfuse()
    LangfuseTraceSink(client=client).record(trace)
    return client.observations


def test_cache_miss_exports_root_plus_three_nested_stages():
    trace = traces_for(GenerateRequest(prompt=REFUND))[0]
    root, *children = export(trace)

    assert root.kwargs["trace_context"] == {"trace_id": trace.trace_id}
    assert [c.kwargs["name"] for c in children] == [
        "cache-lookup",
        "prompt-preparation",
        "llm-generation",
    ]
    generation = children[-1].kwargs
    assert generation["as_type"] == "generation"
    assert generation["model"] == "dry-northstar-v1"
    assert generation["usage_details"]["input"] > 0
    assert root.scores == [{"name": "quality", "value": 0.9}]


def test_cache_hit_exports_no_generation():
    request = GenerateRequest(prompt=REFUND)
    hit = traces_for(request, request)[1]
    root, *children = export(hit)

    assert hit.cache_hit is True
    assert [c.kwargs["name"] for c in children] == ["cache-lookup"]
    assert all(c.kwargs["as_type"] != "generation" for c in children)


def test_failure_is_marked_error_and_not_scored():
    class BrokenProvider:
        model = "broken"

        async def generate(self, messages, request):
            raise RuntimeError("upstream down")

    failed = traces_for(GenerateRequest(prompt=REFUND), provider=BrokenProvider())[0]
    root, *children = export(failed)

    assert root.kwargs["level"] == "ERROR"
    assert root.kwargs["status_message"] == "RuntimeError"
    assert children[-1].kwargs["level"] == "ERROR"
    assert root.scores == []  # failures are not quality samples


def test_raw_prompt_text_is_never_exported():
    trace = traces_for(GenerateRequest(prompt=REFUND))[0]
    exported = repr([o.kwargs for o in export(trace)])
    assert REFUND not in exported


def test_cost_uses_configured_prices():
    trace = traces_for(
        GenerateRequest(prompt=REFUND),
        input_price_per_mtok=1_000_000,  # $1 per token: easy arithmetic
        output_price_per_mtok=0,
    )[0]
    assert trace.cost_usd == trace.usage.input_tokens
    generation = export(trace)[-1].kwargs
    assert generation["cost_details"] == {"total": trace.cost_usd}


def test_langfuse_sink_requires_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="LANGFUSE_PUBLIC_KEY"):
        build_trace_sink(Settings(trace_sink="langfuse"))


def test_flush_is_forwarded():
    client = FakeLangfuse()
    LangfuseTraceSink(client=client).flush()
    assert client.flushed
