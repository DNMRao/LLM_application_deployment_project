"""FastAPI web layer: turns HTTP requests into service calls.

Run locally from the repository root:
    uv run uvicorn src.northstar.app:app --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from src.northstar.config import load_settings
from src.northstar.models import GenerateRequest, GenerateResponse
from src.northstar.provider import build_provider
from src.northstar.service import GenerationService
from src.northstar.tracing import build_trace_sink

logger = logging.getLogger(__name__)


def build_service() -> GenerationService:
    """Wire settings, provider and trace sink together.

    Runs once at startup. A bad setting or a missing provider stops the
    process here, before it ever reports itself ready.
    """
    settings = load_settings()
    return GenerationService(
        provider=build_provider(settings),
        settings=settings,
        trace_sink=build_trace_sink(settings),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # the server runs here
    # On shutdown: send any traces still queued (Langfuse batches in the background).
    flush = getattr(app.state.service.trace_sink, "flush", None)
    if callable(flush):
        flush()


app = FastAPI(title="Northstar Support Assistant", version="0.1.0", lifespan=lifespan)
app.state.service = build_service()


def get_service(request: Request) -> GenerationService:
    # Read from app.state so tests can swap in a service with a fake provider.
    return request.app.state.service


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up and answering HTTP. Cheap, no provider call."""
    return {"status": "healthy"}


@app.get("/ready")
def ready(request: Request) -> dict[str, str]:
    """Readiness: configuration loaded and a provider built. No provider call."""
    service = get_service(request)
    return {
        "status": "ready",
        "provider": service.settings.provider,
        "model": service.provider.model,
        "environment": service.settings.environment,
        "release": service.settings.release,
    }


@app.post("/generate", response_model=GenerateResponse)
async def generate(body: GenerateRequest, request: Request) -> GenerateResponse:
    """Answer one support question.

    422: invalid input (FastAPI + Pydantic, before the provider is called)
    504: the provider (or the queue for it) took longer than the timeout
    502: the provider failed; details are logged, not sent to the client
    """
    try:
        return await get_service(request).generate(body)
    except TimeoutError as error:
        raise HTTPException(status_code=504, detail="Generation request timed out") from error
    except Exception as error:
        logger.exception("Generation provider failed")
        raise HTTPException(
            status_code=502, detail="Generation provider unavailable"
        ) from error
