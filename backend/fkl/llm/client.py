"""OpenAI access, wrapped so the rest of the codebase never imports it directly.

Everything that reaches the model goes through ``structured()``, which returns a
validated Pydantic object or raises. There is no path in this project where the
model returns free text and something downstream parses it with a regex.

Two deliberate constraints:

- ``temperature=0``. Extraction is not a creative task, and a verdict that
  changes between runs is not a verdict.
- No key, no silent fallback. If ``OPENAI_API_KEY`` is unset the caller gets a
  clear error rather than an empty result that looks like a clean run.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

import instructor
from openai import OpenAI
from pydantic import BaseModel

from ..config import SETTINGS

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_client: Any | None = None


class LLMUnavailable(RuntimeError):
    """Raised when an LLM call is attempted without credentials."""


def get_client() -> Any:
    """Return the instructor-wrapped OpenAI client."""
    global _client
    if not SETTINGS.has_llm:
        raise LLMUnavailable(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add a key, "
            "or run with --no-llm to exercise the pipeline without model calls."
        )
    if _client is None:
        _client = instructor.from_openai(
            OpenAI(api_key=SETTINGS.openai_api_key), mode=instructor.Mode.TOOLS
        )
    return _client


def structured(
    *,
    response_model: type[T],
    system: str,
    user: str,
    model: str | None = None,
    max_retries: int = 2,
    temperature: float = 0.0,
) -> T:
    """One structured-output call. Returns a validated model or raises.

    ``max_retries`` is instructor's validation retry: when the model returns
    something that fails Pydantic validation, the errors are fed back and it
    tries again. That is a correctness mechanism, not a network retry.
    """
    client = get_client()
    return client.chat.completions.create(
        model=model or SETTINGS.model_extract,
        response_model=response_model,
        max_retries=max_retries,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )


def list_available_models(prefix: str = "") -> list[str]:
    """List model ids visible to the configured key.

    Model availability differs by account and changes over time, so the correct
    model name is something to look up rather than assume. Exposed through
    ``python -m fkl.cli models`` for exactly that.
    """
    client = OpenAI(api_key=SETTINGS.openai_api_key)
    if not SETTINGS.has_llm:
        raise LLMUnavailable("OPENAI_API_KEY is not set.")
    ids = sorted(m.id for m in client.models.list().data)
    return [m for m in ids if m.startswith(prefix)] if prefix else ids
