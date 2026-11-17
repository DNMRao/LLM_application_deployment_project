"""Cache, concurrency, timeout, failure and trace behaviour of the service."""

import asyncio

import pytest

from src.northstar.models import GenerateRequest
from src.northstar.provider import ProviderError
from tests.conftest import CountingProvider, ok_result

REFUND = "What evidence is required for a duplicate-payment refund?"


def run(coro):
    return asyncio.run(coro)


# ---------- cache ----------


def test_repeated_request_hits_cache_and_skips_provider(make_service):
    provider = CountingProvider()
    service, sink = make_service(provider)
    request = GenerateRequest(prompt=REFUND)

    first = run(service.generate(request))
    second = run(service.generate(request))

    assert (first.cache_hit, second.cache_hit) == (False, True)
    assert provider.calls == 1  # the real proof: only one provider call
    assert second.text == first.text


def test_cache_hit_trace_has_no_generation_and_no_new_tokens(make_service):
    service, sink = make_service()
    request = GenerateRequest(prompt=REFUND)
    run(service.generate(request))
    run(service.generate(request))

    miss, hit = sink.traces
    assert [s.name for s in miss.stages] == [
        "cache-lookup",
        "prompt-preparation",
        "llm-generation",
    ]
    assert miss.usage.input_tokens > 0
    assert [s.name for s in hit.stages] == ["cache-lookup"]
    assert (hit.usage.input_tokens, hit.usage.output_tokens) == (0, 0)


@pytest.mark.parametrize(
    "changed", [{"temperature": 0.9}, {"max_tokens": 50}, {"prompt": REFUND + "!"}]
)
def test_every_output_affecting_parameter_changes_the_cache_key(make_service, changed):
    provider = CountingProvider()
    service, _ = make_service(provider)
    base = {"prompt": REFUND}
    run(service.generate(GenerateRequest(**base)))
    response = run(service.generate(GenerateRequest(**{**base, **changed})))

    assert response.cache_hit is False
    assert provider.calls == 2


def test_cache_entry_expires_after_ttl(make_service):
    provider = CountingProvider()
    service, _ = make_service(provider, cache_ttl_seconds=300)
    now = [1000.0]
    service.clock = lambda: now[0]  # fake clock: no real waiting
    request = GenerateRequest(prompt=REFUND)

    run(service.generate(request))
    now[0] += 299
    assert run(service.generate(request)).cache_hit is True
    now[0] += 2  # 301 s after storing
    assert run(service.generate(request)).cache_hit is False
    assert provider.calls == 2


def test_ttl_zero_disables_cache(make_service):
    provider = CountingProvider()
    service, _ = make_service(provider, cache_ttl_seconds=0)
    request = GenerateRequest(prompt=REFUND)
    hits = [run(service.generate(request)).cache_hit for _ in range(3)]
    assert hits == [False, False, False]
    assert provider.calls == 3


def test_cache_size_is_bounded(make_service):
    service, _ = make_service(max_cache_entries=2)
    for prompt in ("one", "two", "three"):
        run(service.generate(GenerateRequest(prompt=prompt)))
    assert len(service.cache) == 2


# ---------- concurrency and timeout ----------


def test_provider_calls_are_bounded_by_the_semaphore(make_service):
    class SlowProvider:
        model = "slow"
        active = peak = 0

        async def generate(self, messages, request):
            SlowProvider.active += 1
            SlowProvider.peak = max(SlowProvider.peak, SlowProvider.active)
            await asyncio.sleep(0.02)
            SlowProvider.active -= 1
            return ok_result()

    service, _ = make_service(SlowProvider(), max_concurrency=3, cache_ttl_seconds=0)

    async def many():
        await asyncio.gather(
            *(service.generate(GenerateRequest(prompt=f"q{i}")) for i in range(12))
        )

    run(many())
    assert SlowProvider.peak == 3


class HangingProvider:
    model = "hang"

    async def generate(self, messages, request):
        await asyncio.sleep(5)
        raise AssertionError("the timeout should have cancelled this call")


def test_slow_provider_times_out_and_records_error_trace(make_service):
    service, sink = make_service(HangingProvider(), request_timeout_seconds=0.05)

    with pytest.raises(TimeoutError):
        run(service.generate(GenerateRequest(prompt=REFUND)))

    trace = sink.traces[-1]
    assert trace.status == "error"
    assert trace.error_type == "TimeoutError"
    assert trace.stages[-1].name == "llm-generation"
    assert trace.stages[-1].status == "error"
    assert trace.quality_score is None  # failures are not quality samples


def test_timeout_also_covers_waiting_in_the_queue(make_service):
    service, _ = make_service(max_concurrency=1, request_timeout_seconds=0.05)

    async def scenario():
        await service.semaphore.acquire()  # every slot is busy
        try:
            await service.generate(GenerateRequest(prompt=REFUND))
        finally:
            service.semaphore.release()

    with pytest.raises(TimeoutError):
        run(scenario())


# ---------- failures ----------


def test_provider_failure_is_raised_recorded_and_not_cached(make_service):
    class BrokenProvider:
        model = "broken"

        async def generate(self, messages, request):
            raise ProviderError("upstream 500")

    service, sink = make_service(BrokenProvider())
    with pytest.raises(ProviderError):
        run(service.generate(GenerateRequest(prompt=REFUND)))

    assert sink.traces[-1].status == "error"
    assert service.cache == {}


def test_empty_provider_answer_is_an_error(make_service):
    class EmptyProvider:
        model = "empty"

        async def generate(self, messages, request):
            return ok_result(text="   ")

    service, _ = make_service(EmptyProvider())
    with pytest.raises(ProviderError):
        run(service.generate(GenerateRequest(prompt=REFUND)))


def test_trace_failure_does_not_break_a_good_answer(make_service):
    service, _ = make_service()

    class BrokenSink:
        def record(self, trace):
            raise OSError("disk full")

    service.trace_sink = BrokenSink()
    response = run(service.generate(GenerateRequest(prompt=REFUND)))
    assert "receipt" in response.text.lower()


def test_trace_does_not_store_raw_prompt_text(make_service):
    service, sink = make_service()
    run(service.generate(GenerateRequest(prompt=REFUND)))
    assert REFUND not in sink.traces[-1].model_dump_json()
