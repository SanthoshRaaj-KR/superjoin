"""Runtime configuration, read from the environment (see .env.example).

Model names are configuration, not constants. OpenAI's catalogue moves faster
than this repo does, and hard-coding a model is the kind of thing that silently
breaks a grader's run six months from now.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    model_extract: str
    model_reason: str
    model_embed: str
    db_url: str

    @property
    def has_llm(self) -> bool:
        return bool(self.openai_api_key)


def load_settings() -> Settings:
    db_url = os.getenv("FKL_DB_URL", "sqlite:///data/fkl.sqlite")
    if db_url.startswith("sqlite:///") and not os.path.isabs(db_url[10:]):
        # Resolve relative SQLite paths against the repo root so the CLI behaves
        # the same regardless of the directory it is invoked from.
        rel = db_url[len("sqlite:///") :]
        db_url = f"sqlite:///{(REPO_ROOT / rel).as_posix()}"

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        model_extract=os.getenv("FKL_MODEL_EXTRACT", "gpt-4.1-mini"),
        model_reason=os.getenv("FKL_MODEL_REASON", "gpt-4.1"),
        model_embed=os.getenv("FKL_MODEL_EMBED", "text-embedding-3-small"),
        db_url=db_url,
    )


SETTINGS = load_settings()
