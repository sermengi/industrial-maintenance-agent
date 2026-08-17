from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from maintenance_agent.core.config import get_settings
from maintenance_agent.db.cli import resolve_database_url
from maintenance_agent.rag.corpus import load_corpus_chunks
from maintenance_agent.rag.ingestion import RagIngestionResult, ingest_loaded_corpus


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ingest":
        database_url = resolve_database_url(args.database)
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()

        command.upgrade(Config("alembic.ini"), "head")
        result = asyncio.run(ingest_database(database_url, force=args.force))
        print(_format_result(result))
        return

    parser.error(f"Unsupported command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maintenance-agent-rag")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest RAG corpus chunks.")
    ingest_parser.add_argument(
        "--database",
        choices=("dev", "test"),
        default="dev",
        help="Database target to ingest into. Defaults to dev.",
    )
    ingest_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed every chunk even when the stored content hash matches.",
    )

    return parser


async def ingest_database(database_url: str, *, force: bool = False) -> RagIngestionResult:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await ingest_loaded_corpus(
                session,
                load_corpus_chunks(),
                force=force,
            )
            await session.commit()
            return result
    finally:
        await engine.dispose()


def _format_result(result: RagIngestionResult) -> str:
    return (
        "RAG ingest complete: "
        f"desired={result.desired_chunks}, "
        f"embedded={result.embedded_chunks}, "
        f"skipped={result.skipped_chunks}, "
        f"pruned={result.pruned_chunks}"
    )


if __name__ == "__main__":
    main()
