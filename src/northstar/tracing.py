"""Trace sinks: where the record of each request is written.

A "sink" is just a destination. JSONL (one JSON object per line in a file)
works offline and in tests. A Langfuse sink with the same `record` method is
added in Milestone 2.
"""

from pathlib import Path
from typing import Protocol

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


def build_trace_sink(settings: Settings) -> TraceSink:
    if settings.trace_sink == "jsonl":
        return JsonlTraceSink(settings.trace_path)
    raise ValueError(f"Unsupported TRACE_SINK: {settings.trace_sink}")
