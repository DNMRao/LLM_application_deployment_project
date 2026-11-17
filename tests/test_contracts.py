"""Settings and request contracts reject bad values before anything runs."""

import pytest
from pydantic import ValidationError

from src.northstar.config import Settings
from src.northstar.models import GenerateRequest


@pytest.mark.parametrize("prompt", ["", " ", "\n\t"])
def test_blank_prompt_is_rejected(prompt):
    with pytest.raises(ValidationError):
        GenerateRequest(prompt=prompt)


@pytest.mark.parametrize(
    "values",
    [
        {"prompt": "x" * 8001},
        {"prompt": "hi", "max_tokens": 0},
        {"prompt": "hi", "max_tokens": 2049},
        {"prompt": "hi", "temperature": -0.1},
        {"prompt": "hi", "temperature": 2.1},
        {"prompt": "hi", "max_token": 50},  # typo: unknown field
    ],
)
def test_out_of_range_or_unknown_fields_are_rejected(values):
    with pytest.raises(ValidationError):
        GenerateRequest(**values)


def test_prompt_is_trimmed_and_defaults_apply():
    request = GenerateRequest(prompt="  locked account  ")
    assert request.prompt == "locked account"
    assert (request.max_tokens, request.temperature) == (200, 0.2)


@pytest.mark.parametrize(
    "values",
    [
        {"provider": "banana"},
        {"max_concurrency": 0},
        {"cache_ttl_seconds": -1},
        {"max_cache_entries": 0},
        {"request_timeout_seconds": 0},
        {"request_timeout_seconds": float("nan")},
        {"request_timeout_seconds": float("inf")},
    ],
)
def test_invalid_settings_fail_at_startup(values):
    with pytest.raises(ValueError):
        Settings(**values)
