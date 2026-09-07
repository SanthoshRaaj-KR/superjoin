"""Command line entry point.

    python -m fkl.cli ingest <pdf> [--no-llm]
    python -m fkl.cli page <doc-id> <page-no> [--raw]
    python -m fkl.cli extract <doc-id> [--pages 0-9,35]
    python -m fkl.cli export <doc-id> [-o out/doc.json]
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
from .export import export_document, write_json
from .ingest import ingest_pdf
from .context import MATERIAL_AXES
from .models import Document, Page
from .pipeline import extract_document_claims


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
            if not result.reused and result.figure_pages:
                parts = [
                    f"{axis} {result.axis_coverage.get(axis, 0)}"
                    f" ({100 * result.axis_coverage.get(axis, 0) / result.figure_pages:.0f}%)"
                    for axis in MATERIAL_AXES
                ]
                print(f"          context on {result.figure_pages} pages with figures: "
                      + " · ".join(parts))
            if result.profile is not None:
                print(json.dumps(result.profile.model_dump(), indent=2))
    return 0


def cmd_page(args: argparse.Namespace) -> int:
    """Show a page as the extractor sees it, or as the PDF stores it.

    Being able to read both side by side is the fastest way to tell whether a
    bad claim came from a bad model call or from a page that was handed over
    scrambled.
    """
    init_db()
    with session_scope() as session:
        page = session.scalar(
            select(Page).where(
                Page.document_id == args.document_id, Page.page_no == args.page_no
            )
        )
        if page is None:
            print(f"no page {args.page_no} in document {args.document_id}")
            return 1
        if args.raw or not page.rendered_text:
            print(page.text)
        else:
            print(page.rendered_text)
    return 0


def parse_page_spec(spec: str | None) -> list[int] | None:
    """Turn '0-9,35,40' into a page list. None means every page."""
    if not spec:
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            pages.update(range(int(lo), int(hi) + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


def cmd_extract(args: argparse.Namespace) -> int:
    init_db()
    with session_scope() as session:
        run = extract_document_claims(
            session,
            args.document_id,
            pages=parse_page_spec(args.pages),
            workers=args.workers,
        )
        print(
            f"doc {run.document_id}: {run.pages_attempted} pages attempted, "
            f"{run.pages_failed} failed -> {run.measurements} measurements, "
            f"{run.states} states"
        )
        for page_no, note in run.notes:
            print(f"  note p{page_no}: {note}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    init_db()
    with session_scope() as session:
        payload = export_document(session, args.document_id)
        if args.output:
            path = write_json(payload, args.output)
            print(f"wrote {path}  ({payload['counts']})")
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
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

    p_page = sub.add_parser("page", help="print one page as the extractor sees it")
    p_page.add_argument("document_id", type=int)
    p_page.add_argument("page_no", type=int)
    p_page.add_argument("--raw", action="store_true", help="show the raw PDF text instead")
    p_page.set_defaults(func=cmd_page)

    p_extract = sub.add_parser("extract", help="extract claims from an ingested document")
    p_extract.add_argument("document_id", type=int)
    p_extract.add_argument("--pages", help="page selection, e.g. 0-9,35,40 (default: all)")
    p_extract.add_argument("--workers", type=int, default=6)
    p_extract.set_defaults(func=cmd_extract)

    p_export = sub.add_parser("export", help="dump a document and its claims as JSON")
    p_export.add_argument("document_id", type=int)
    p_export.add_argument("-o", "--output", help="write to this path instead of stdout")
    p_export.set_defaults(func=cmd_export)

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
