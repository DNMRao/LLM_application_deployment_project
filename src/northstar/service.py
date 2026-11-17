"""Generation service: cache, concurrency limit, timeout, quality, traces.

This is the heart of the application. The API (app.py) and the evaluation
both call `GenerationService.generate()`, so every request gets the same
protections no matter where it comes from.
"""

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from src.northstar.config import Settings
from src.northstar.models import (
    GenerateRequest,
    GenerateResponse,
    ProviderResult,
    Stage,
    TraceRecord,
    Usage,
)
from src.northstar.policy import POLICY_VERSION, build_messages
from src.northstar.provider import Provider, ProviderError
from src.northstar.tracing import TraceSink

logger = logging.getLogger(__name__)

# Words that show an answer follows an approved policy (or declines properly).
POLICY_TERMS = ("receipt", "identity", "privacy", "status page", "cannot confirm")


def quality_score(text: str) -> float:
    """Keyword heuristic: 0.9 if the answer uses approved-policy language.

    This is a cheap signal, NOT proof of a correct answer: a wrong sentence
    containing "receipt" would still score 0.9. The report must say so.
    """
    lowered = text.lower()
    return 0.9 if any(term in lowered for term in POLICY_TERMS) else 0.4


class GenerationService:
    def __init__(
        self,
        provider: Provider,
        settings: Settings,
        trace_sink: TraceSink,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.trace_sink = trace_sink
        self.clock = clock  # replaceable so tests can "fast-forward" time
        # Process-local cache: key -> (expiry time, result).
        # Lives in this process's memory only: each instance has its own,
        # and a restart empties it.
        self.cache: dict[str, tuple[float, ProviderResult]] = {}
        # At most `max_concurrency` provider calls run at the same time.
        self.semaphore = asyncio.Semaphore(settings.max_concurrency)

    # ---------- cache ----------

    def cache_key(self, request: GenerateRequest) -> str:
        """Fingerprint of EVERYTHING that changes the answer."""
        raw = "|".join(
            [
                self.settings.provider,
                self.settings.model,
                POLICY_VERSION,
                str(request.temperature),
                str(request.max_tokens),
                request.prompt,
            ]
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_get(self, key: str) -> ProviderResult | None:
        entry = self.cache.get(key)
        if entry is None:
            return None
        expires_at, result = entry
        if expires_at <= self.clock():
            del self.cache[key]  # expired: treat as a miss
            return None
        return result

    def _cache_put(self, key: str, result: ProviderResult) -> None:
        if self.settings.cache_ttl_seconds == 0:
            return  # TTL 0 switches caching off
        if key not in self.cache and len(self.cache) >= self.settings.max_cache_entries:
            oldest = next(iter(self.cache))  # dicts keep insertion order
            del self.cache[oldest]
        self.cache[key] = (self.clock() + self.settings.cache_ttl_seconds, result)

    # ---------- main entry point ----------

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        started = time.perf_counter()
        trace_id = uuid.uuid4().hex
        stages: list[Stage] = []
        stage_name, stage_start = "cache-lookup", time.perf_counter()

        def finish_stage(status: str = "ok") -> None:
            stages.append(
                Stage(
                    name=stage_name,
                    duration_ms=(time.perf_counter() - stage_start) * 1000,
                    status=status,
                )
            )

        try:
            # Stage 1: cache lookup
            key = self.cache_key(request)
            cached = self._cache_get(key)
            finish_stage()

            if cached is not None:
                result, cache_hit = cached, True
            else:
                cache_hit = False
                # Stage 2: prompt preparation (policy + question)
                stage_name, stage_start = "prompt-preparation", time.perf_counter()
                messages = build_messages(request.prompt)
                finish_stage()

                # Stage 3: LLM generation, bounded in time AND in concurrency.
                # The timeout also covers waiting in the semaphore queue.
                stage_name, stage_start = "llm-generation", time.perf_counter()
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    async with self.semaphore:
                        result = await self.provider.generate(messages, request)
                if not result.text.strip():
                    raise ProviderError("Provider returned an empty answer")
                finish_stage()
                self._cache_put(key, result)
        except Exception as error:
            finish_stage("error")
            self._record(
                request,
                trace_id=trace_id,
                started=started,
                stages=stages,
                status="error",
                error_type=type(error).__name__,
            )
            raise  # app.py turns this into 502 or 504

        response = GenerateResponse(
            text=result.text,
            model=result.model,
            usage=result.usage,  # the answer's original token counts
            cache_hit=cache_hit,
            latency_ms=(time.perf_counter() - started) * 1000,
            quality_score=quality_score(result.text),
            trace_id=trace_id,
        )
        self._record(
            request,
            trace_id=trace_id,
            started=started,
            stages=stages,
            status="ok",
            result=result,
            response=response,
        )
        return response

    # ---------- tracing ----------

    def _record(
        self,
        request: GenerateRequest,
        *,
        trace_id: str,
        started: float,
        stages: list[Stage],
        status: str,
        error_type: str | None = None,
        result: ProviderResult | None = None,
        response: GenerateResponse | None = None,
    ) -> None:
        cache_hit = response.cache_hit if response else False
        trace = TraceRecord(
            trace_id=trace_id,
            timestamp=datetime.now(UTC).isoformat(),
            environment=self.settings.environment,
            release=self.settings.release,
            provider=self.settings.provider,
            model=result.model if result else self.settings.model,
            status=status,
            error_type=error_type,
            cache_hit=cache_hit,
            latency_ms=(time.perf_counter() - started) * 1000,
            stages=stages,
            # Only NEW provider tokens are billed: zero on a cache hit or error.
            usage=result.usage
            if result and not cache_hit
            else Usage(input_tokens=0, output_tokens=0),
            quality_score=response.quality_score if response else None,
            prompt_sha256=hashlib.sha256(request.prompt.encode()).hexdigest(),
            prompt_chars=len(request.prompt),
            output_chars=len(result.text) if result else 0,
        )
        try:
            self.trace_sink.record(trace)
        except Exception:
            # A logging failure must never turn a good answer into an error.
            logger.exception("Trace export failed")
