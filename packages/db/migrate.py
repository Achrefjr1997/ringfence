"""Apply every Postgres schema RingFence owns (T-7.2b).

The single entrypoint a deploy runs.  Each ``schema.sql`` is idempotent, so
this is safe on every boot.

    python -m packages.db.migrate                  # uses RF_DATABASE_URL
    python -m packages.db.migrate postgres://...    # explicit DSN
"""

from __future__ import annotations

import sys

from packages.billing.pg_meter import schema_sql as _billing_schema
from packages.identity.migrate import schema_sql as _identity_schema
from packages.intervene.pg_cases import schema_sql as _cases_schema

# each schema is self-contained (no cross-table FKs between them)
_SCHEMAS = (
    ("identity", _identity_schema),
    ("cases", _cases_schema),
    ("billing", _billing_schema),
)


async def _run(dsn: str) -> None:
    import asyncpg  # noqa: PLC0415 - optional 'db' extra

    conn = await asyncpg.connect(dsn)
    try:
        for name, sql in _SCHEMAS:
            await conn.execute(sql())
            print(f"applied {name} schema")
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
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
