"""Shared test setup.

The important thing here is a guard, not a fixture. Adding the figure pass made
an existing test start calling the OpenAI API for real: it stubbed
``extract_page`` and knew nothing about ``extract_figures``, so the second pass
ran live inside what was supposed to be an offline unit test. It passed money
through and produced a confusing failure rather than an obvious one.

So every model entry point is stubbed by default and raises if reached. A test
that wants model output stubs it deliberately; a test that reaches one by
accident fails immediately, naming the call it did not expect to make.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def no_live_model_calls(monkeypatch):
    """Fail loudly rather than billing the user for an accidental call."""

    def forbid(name):
        def _raise(*_args, **_kwargs):
            raise AssertionError(
                f"{name} was called in a test without being stubbed. "
                "Stub it explicitly, or pass use_figures=False."
            )

        return _raise

    for module, attr in (
        ("fkl.pipeline", "extract_page"),
        ("fkl.pipeline", "extract_figures"),
        ("fkl.llm.client", "get_client"),
        ("fkl.llm.client", "raw_client"),
        ("fkl.metrics", "embed_texts"),
    ):
        monkeypatch.setattr(module + "." + attr, forbid(f"{module}.{attr}"), raising=False)
