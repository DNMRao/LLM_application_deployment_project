"""Provider boundary: the only code that talks to an LLM.

`Provider` is a Protocol, a description of the shape a provider must have:
a `model` name and an async `generate(...)`. Anything with that shape works,
so tests can plug in fakes (slow, failing, counting) and production can plug
in Groq, without the rest of the service changing.
"""

from typing import Protocol

from src.northstar.config import Settings
from src.northstar.models import ChatMessage, GenerateRequest, ProviderResult, Usage


class ProviderError(Exception):
    """The upstream model failed or returned an unusable answer."""


class Provider(Protocol):
    model: str

    async def generate(
        self, messages: list[ChatMessage], request: GenerateRequest
    ) -> ProviderResult: ...


class DryProvider:
    """Offline stand-in: fixed answers, no network, no API key, no cost.

    Like testing a simulation against a toy model with a known answer: if the
    service misbehaves here, the problem is in our code, not the LLM.
    """

    model = "dry-northstar-v1"

    ANSWERS = (
        ("duplicate-payment", "Collect the receipt and transaction identifier before review."),
        ("locked account", "Verify identity, then issue the approved reset flow."),
        ("delete all personal", "Route the privacy request to human review."),
        ("active incident", "Share only confirmed updates from the status page."),
    )
    FALLBACK = "I cannot confirm this from approved policy."

    async def generate(
        self, messages: list[ChatMessage], request: GenerateRequest
    ) -> ProviderResult:
        question = messages[-1].content.lower()
        text = next(
            (answer for trigger, answer in self.ANSWERS if trigger in question),
            self.FALLBACK,
        )
        prompt_words = sum(len(m.content.split()) for m in messages)
        return ProviderResult(
            text=text,
            model=self.model,
            # Word counts stand in for tokens; real providers report true counts.
            usage=Usage(input_tokens=prompt_words, output_tokens=len(text.split())),
        )


def build_provider(settings: Settings) -> Provider:
    """Pick the provider named in MODEL_PROVIDER."""
    if settings.provider == "dry":
        return DryProvider()
    if settings.provider == "groq":
        # Added in the next stage: a GroqProvider with the same shape as DryProvider.
        raise NotImplementedError(
            "The Groq provider is not built yet. Set MODEL_PROVIDER=dry for now."
        )
    raise ValueError(f"Unsupported provider: {settings.provider}")
