"""Command line entry point.

    python -m fkl.cli ingest <pdf> [--no-llm]
    python -m fkl.cli docs
    python -m fkl.cli models [--prefix gpt]

The API arrives in a later phase. Until then this is how the pipeline is driven
and inspected.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from sqlalchemy import select

from .db import init_db, session_scope
from .ingest import ingest_pdf
from .models import Document


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_ingest(args: argparse.Namespace) -> int:
    init_db()
    with session_scope() as session:
        for path in args.paths:
            result = ingest_pdf(session, path, use_llm=not args.no_llm, force=args.force)
            state = "reused" if result.reused else "ingested"
            print(f"[{state}] doc {result.document_id}  {result.filename}  "
                  f"{result.n_pages} pages, {result.n_chars:,} chars")
            if result.profile is not None:
                print(json.dumps(result.profile.model_dump(), indent=2))
    return 0


def cmd_docs(_args: argparse.Namespace) -> int:
    init_db()
    with session_scope() as session:
        rows = session.scalars(select(Document).order_by(Document.id)).all()
        if not rows:
            print("no documents ingested")
            return 0
        for d in rows:
            print(
                f"{d.id:3}  {d.filename[:44]:46} {d.n_pages:4}p  "
                f"{str(d.doc_type or '-'):24} as_of={d.as_of_date or '-'}  "
                f"entity={d.primary_entity or '-'}"
            )
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    from .llm.client import list_available_models

    for m in list_available_models(args.prefix):
        print(m)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fkl", description="Fact Knowledge Layer")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="extract, profile and store PDFs")
    p_ingest.add_argument("paths", nargs="+")
    p_ingest.add_argument(
        "--no-llm",
        action="store_true",
        help="store text without profiling; exercises the pipeline with no spend",
    )
    p_ingest.add_argument("--force", action="store_true", help="re-ingest even if unchanged")
    p_ingest.set_defaults(func=cmd_ingest)

    p_docs = sub.add_parser("docs", help="list ingested documents")
    p_docs.set_defaults(func=cmd_docs)

    p_models = sub.add_parser("models", help="list model ids visible to your API key")
    p_models.add_argument("--prefix", default="")
    p_models.set_defaults(func=cmd_models)

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
