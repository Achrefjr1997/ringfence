"""Apply the identity schema to a Postgres database (T-7.2a).

``schema.sql`` is idempotent, so this is safe to run on every deploy.

    python -m packages.identity.migrate                  # uses RF_DATABASE_URL
    python -m packages.identity.migrate postgres://...    # explicit DSN
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


async def apply_schema(conn: Any) -> None:
    """Execute ``schema.sql`` on an open ``asyncpg`` connection."""
    await conn.execute(schema_sql())


async def _run(dsn: str) -> None:
    import asyncpg  # noqa: PLC0415 - optional 'db' extra

    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    import asyncio  # noqa: PLC0415
    import os  # noqa: PLC0415

    args = sys.argv[1:] if argv is None else argv
    dsn = args[0] if args else os.environ.get("RF_DATABASE_URL", "")
    if not dsn:
        print("no DSN: pass one as an argument or set RF_DATABASE_URL", file=sys.stderr)
        return 2
    asyncio.run(_run(dsn))
    print("identity schema applied")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
