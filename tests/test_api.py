"""HTTP behaviour: status codes, contracts, and safe error messages."""

import asyncio

from src.northstar.provider import ProviderError

REFUND = {"prompt": "What evidence is required for a duplicate-payment refund?"}


def test_health_and_ready(client):
    http, _, _ = client
    assert http.get("/health").json() == {"status": "healthy"}
    ready = http.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["provider"] == "dry"
    assert ready.json()["model"] == "dry-northstar-v1"


def test_generate_returns_typed_answer_then_cache_hit(client):
    http, _, _ = client
    first = http.post("/generate", json=REFUND)
    second = http.post("/generate", json=REFUND)

    assert first.status_code == second.status_code == 200
    body = first.json()
    assert "receipt" in body["text"].lower()
    assert set(body) == {
        "text",
        "model",
        "usage",
        "cache_hit",
        "latency_ms",
        "quality_score",
        "trace_id",
    }
    assert (body["cache_hit"], second.json()["cache_hit"]) == (False, True)
    assert body["trace_id"] != second.json()["trace_id"]


def test_invalid_input_is_rejected_before_the_provider(client):
    http, service, sink = client
    for bad in ({"prompt": "   "}, {"prompt": "hi", "temperature": 9}, {}):
        assert http.post("/generate", json=bad).status_code == 422
    assert service.provider.calls == 0  # provider never touched
    assert sink.traces == []  # nothing reached the service


def test_provider_failure_returns_502_without_leaking_details(client):
    http, service, _ = client

    class BrokenProvider:
        model = "broken"

        async def generate(self, messages, request):
            raise ProviderError("secret upstream detail")

    service.provider = BrokenProvider()
    response = http.post("/generate", json=REFUND)

    assert response.status_code == 502
    assert response.json() == {"detail": "Generation provider unavailable"}
    assert "secret" not in response.text


def test_timeout_returns_504(client):
    http, service, _ = client

    class HangingProvider:
        model = "hang"

        async def generate(self, messages, request):
            await asyncio.sleep(5)

    service.provider = HangingProvider()
    service.settings = service.settings.__class__(request_timeout_seconds=0.05)
    response = http.post("/generate", json=REFUND)

    assert response.status_code == 504
    assert response.json() == {"detail": "Generation request timed out"}
