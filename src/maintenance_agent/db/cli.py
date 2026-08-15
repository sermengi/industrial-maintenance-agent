from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence

from alembic import command
from alembic.config import Config

from maintenance_agent.core.config import get_settings
from maintenance_agent.db.bootstrap import reset_database


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "reset":
        database_url = resolve_database_url(args.database)
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()

        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(reset_database(database_url))
        return

    parser.error(f"Unsupported command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maintenance-agent-db")
    subparsers = parser.add_subparsers(dest="command", required=True)

    reset_parser = subparsers.add_parser("reset", help="Run migrations and reseed a database.")
    reset_parser.add_argument(
        "--database",
        choices=("dev", "test"),
        default="dev",
        help="Database target to reset. Defaults to dev.",
    )

    return parser


def resolve_database_url(target: str) -> str:
    settings = get_settings()
    if target == "dev":
        database_url = settings.database_url
        variable_name = "DATABASE_URL"
    elif target == "test":
        database_url = settings.test_database_url
        variable_name = "TEST_DATABASE_URL"
    else:
        raise ValueError(f"Unsupported database target: {target}")

    if not database_url:
        raise RuntimeError(f"{variable_name} is required to reset the {target} database.")
    return database_url


if __name__ == "__main__":
    main()
