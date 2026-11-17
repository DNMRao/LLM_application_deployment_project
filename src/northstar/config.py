"""Service settings, read from environment variables.

Why: settings such as the provider, timeouts and API keys change between your
laptop, the container and Render. Keeping them outside the code means the same
image runs everywhere, and secrets never end up in git.
"""

import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Repository root: src/northstar/config.py -> parents[2]
ROOT = Path(__file__).resolve().parents[2]

SUPPORTED_PROVIDERS = {"dry", "groq"}
DEFAULT_MODELS = {"dry": "dry-northstar-v1", "groq": "openai/gpt-oss-20b"}


@dataclass(frozen=True)
class Settings:
    """All tunable values in one immutable object.

    `frozen=True` means nothing can change a setting while the service runs.
    `__post_init__` rejects bad values at startup, so a typo in `.env` fails
    loudly instead of causing strange behaviour later.
    """

    provider: str = "dry"
    model: str = DEFAULT_MODELS["dry"]
    api_key: str = ""
    cache_ttl_seconds: int = 300
    max_cache_entries: int = 1024
    max_concurrency: int = 4
    request_timeout_seconds: float = 30.0
    trace_sink: str = "jsonl"
    trace_path: Path = ROOT / "reports" / "generated" / "traces.jsonl"
    environment: str = "development"
    release: str = "local"

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported MODEL_PROVIDER={self.provider!r}; "
                f"choose one of {sorted(SUPPORTED_PROVIDERS)}"
            )
        if self.max_concurrency < 1:
            raise ValueError("MAX_CONCURRENT_REQUESTS must be at least 1")
        if self.cache_ttl_seconds < 0:
            raise ValueError("CACHE_TTL_SECONDS must be zero or positive")
        if self.max_cache_entries < 1:
            raise ValueError("Cache must hold at least one entry")
        if (
            not math.isfinite(self.request_timeout_seconds)
            or self.request_timeout_seconds <= 0
        ):
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be finite and positive")


def load_settings() -> Settings:
    """Build Settings from environment variables (and `.env` if present)."""
    load_dotenv()
    provider = os.getenv("MODEL_PROVIDER", "dry").strip().lower()
    prefix = provider.upper()
    return Settings(
        provider=provider,
        model=os.getenv(f"{prefix}_MODEL", DEFAULT_MODELS.get(provider, "")),
        api_key=os.getenv(f"{prefix}_API_KEY", ""),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "300")),
        max_concurrency=int(os.getenv("MAX_CONCURRENT_REQUESTS", "4")),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")),
        trace_sink=os.getenv("TRACE_SINK", "jsonl"),
        trace_path=Path(
            os.getenv("NORTHSTAR_TRACE_PATH", "reports/generated/traces.jsonl")
        ),
        environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "development"),
        release=os.getenv("LANGFUSE_RELEASE", "local"),
    )
