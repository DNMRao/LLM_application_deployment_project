"""Trace sinks: where the record of each request is written.

A "sink" is just a destination. JSONL (one JSON object per line in a file)
works offline and in tests. Langfuse sends the same record to a web UI where
each request appears as a tree of nested observations.
"""

import os
from pathlib import Path
from typing import Any, Protocol

from langfuse import get_client, propagate_attributes

from src.northstar.config import Settings
from src.northstar.models import TraceRecord


class TraceSink(Protocol):
    def record(self, trace: TraceRecord) -> None: ...


class JsonlTraceSink:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, trace: TraceRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(trace.model_dump_json() + "\n")


class MemoryTraceSink:
    """Keeps traces in a list. Handy in tests."""

    def __init__(self) -> None:
        self.traces: list[TraceRecord] = []

    def record(self, trace: TraceRecord) -> None:
        self.traces.append(trace)


class LangfuseTraceSink:
    """Send each TraceRecord to Langfuse as a tree of observations.

        northstar-support-request        (root span: status, latency, quality score)
        ├── cache-lookup                 (span)
        ├── prompt-preparation           (span)       } only on a cache miss
        └── llm-generation               (generation: model, tokens, cost)

    The Langfuse trace ID is OUR trace_id, so the ID returned by /generate
    can be pasted straight into the Langfuse search box.

    Masking: only fingerprints and lengths are sent, never raw prompt or
    answer text. Sampling: every request is sent (100%); fine at this volume.

    Timing note: observations are created after the request finished, so
    Langfuse's own durations measure export work. The real stage timings are
    in each observation's metadata (`duration_ms`) and the root's
    `request_latency_ms`.
    """

    def __init__(self, client: Any | None = None) -> None:
        self.client = client or get_client()

    def record(self, trace: TraceRecord) -> None:
        level = "ERROR" if trace.status == "error" else "DEFAULT"
        with (
            propagate_attributes(
                trace_name=trace.name,
                version=trace.release,
                environment=trace.environment,
                tags=["northstar", trace.provider, f"cache-{'hit' if trace.cache_hit else 'miss'}"],
                metadata={"cache_hit": str(trace.cache_hit).lower(), "status": trace.status},
            ),
            self.client.start_as_current_observation(
                trace_context={"trace_id": trace.trace_id},
                name=trace.name,
                as_type="span",
                version=trace.release,
                level=level,
                status_message=trace.error_type,
                input={"prompt_sha256": trace.prompt_sha256, "prompt_chars": trace.prompt_chars},
                metadata={
                    "provider": trace.provider,
                    "model": trace.model,
                    "cache_hit": trace.cache_hit,
                    "request_latency_ms": round(trace.latency_ms, 3),
                    "timestamp": trace.timestamp,
                },
            ) as root,
        ):
            for stage in trace.stages:
                stage_level = "ERROR" if stage.status == "error" else "DEFAULT"
                stage_meta = {"duration_ms": round(stage.duration_ms, 3), "status": stage.status}
                if stage.name == "llm-generation":
                    with self.client.start_as_current_observation(
                        name=stage.name,
                        as_type="generation",
                        model=trace.model,
                        level=stage_level,
                        metadata=stage_meta,
                        usage_details={
                            "input": trace.usage.input_tokens,
                            "output": trace.usage.output_tokens,
                        },
                        cost_details={"total": trace.cost_usd},
                    ):
                        pass
                else:
                    with self.client.start_as_current_observation(
                        name=stage.name, as_type="span", level=stage_level, metadata=stage_meta
                    ):
                        pass
            # Failures are not quality samples: score only successful answers.
            if trace.quality_score is not None:
                root.score(name="quality", value=trace.quality_score)
            root.update(
                output={
                    "status": trace.status,
                    "error_type": trace.error_type,
                    "output_chars": trace.output_chars,
                }
            )

    def flush(self) -> None:
        """Send anything still queued. Called once at shutdown."""
        self.client.flush()


def build_trace_sink(settings: Settings) -> TraceSink:
    if settings.trace_sink == "jsonl":
        return JsonlTraceSink(settings.trace_path)
    if settings.trace_sink == "langfuse":
        missing = [
            key
            for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
            if not os.getenv(key)
        ]
        if missing:
            # Fail at startup: otherwise traces would silently go nowhere.
            raise ValueError(f"TRACE_SINK=langfuse needs {', '.join(missing)}")
        return LangfuseTraceSink()
    raise ValueError(f"Unsupported TRACE_SINK: {settings.trace_sink}")
