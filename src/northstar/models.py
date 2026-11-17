"""Typed contracts: what goes in, what comes out, and what gets recorded.

Pydantic checks every field the way you would check a DataFrame's columns and
dtypes before processing it. Invalid input is rejected (HTTP 422) before any
model is called, so bad requests cost nothing.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    """Body of POST /generate."""

    # Strip spaces so "   " counts as empty; refuse unknown fields.
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt: str = Field(min_length=1, max_length=8000)
    max_tokens: int = Field(default=200, ge=1, le=2048)
    temperature: float = Field(default=0.2, ge=0, le=2)


class ChatMessage(BaseModel):
    """One message sent to the model (system instructions or user question)."""

    role: Literal["system", "user"]
    content: str


class Usage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ProviderResult(BaseModel):
    """What any provider (dry or live) must return."""

    text: str
    model: str
    usage: Usage


class GenerateResponse(BaseModel):
    """Body returned by POST /generate."""

    text: str
    model: str
    usage: Usage
    cache_hit: bool
    latency_ms: float = Field(ge=0)
    quality_score: float = Field(ge=0, le=1)
    trace_id: str


class Stage(BaseModel):
    """One nested step of a request (cache lookup, preparation, generation)."""

    name: str
    duration_ms: float = Field(ge=0)
    status: Literal["ok", "error"] = "ok"


class TraceRecord(BaseModel):
    """Everything we log about one request.

    Masking choice: raw prompt and answer text are NOT stored. We keep their
    length and a SHA-256 fingerprint, which is enough to match repeated
    requests without keeping customer text.
    """

    trace_id: str
    name: str = "northstar-support-request"
    timestamp: str
    environment: str
    release: str
    provider: str
    model: str
    status: Literal["ok", "error"]
    error_type: str | None = None
    cache_hit: bool
    latency_ms: float
    stages: list[Stage]
    usage: Usage  # NEW provider tokens only: zero on a cache hit
    quality_score: float | None  # None on errors: failures are not quality samples
    prompt_sha256: str
    prompt_chars: int
    output_chars: int


class EvaluationCase(BaseModel):
    case_id: str
    prompt: str
    expected_terms: list[str]
    repeat: int = Field(ge=1)


class CaseResult(BaseModel):
    case_id: str
    passed: bool
    cache_verified: bool | None
    quality_score: float
    latency_ms: float
