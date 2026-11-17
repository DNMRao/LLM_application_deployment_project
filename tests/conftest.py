"""Shared test helpers ("fixtures").

The root conftest.py already forces MODEL_PROVIDER=dry and disables .env,
so these tests never need an API key and never call a real LLM.
"""

import pytest
from fastapi.testclient import TestClient

from src.northstar.app import app
from src.northstar.config import Settings
from src.northstar.models import GenerateRequest, ProviderResult, Usage
from src.northstar.provider import DryProvider
from src.northstar.service import GenerationService
from src.northstar.tracing import MemoryTraceSink


class CountingProvider(DryProvider):
    """Dry answers, but remembers how many times it was called."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages, request: GenerateRequest) -> ProviderResult:
        self.calls += 1
        return await super().generate(messages, request)


def ok_result(text: str = "Collect the receipt.") -> ProviderResult:
    return ProviderResult(
        text=text, model="fake", usage=Usage(input_tokens=1, output_tokens=1)
    )


@pytest.fixture
def make_service():
    """Build a service with a chosen provider and settings; traces go to memory."""

    def _make(provider=None, **settings_values):
        sink = MemoryTraceSink()
        service = GenerationService(
            provider or CountingProvider(), Settings(**settings_values), sink
        )
        return service, sink

    return _make


@pytest.fixture
def client(make_service):
    """HTTP client for the real FastAPI app, with a fresh in-memory service.

    Yields (client, service, sink). Swapping app.state.service keeps every
    test isolated: no shared cache, no trace files written.
    """
    service, sink = make_service()
    original = app.state.service
    app.state.service = service
    with TestClient(app) as test_client:
        yield test_client, service, sink
    app.state.service = original
