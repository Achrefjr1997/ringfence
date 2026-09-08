"""Re-encrypt at-rest columns after a key rotation (T-7.4).

Reads every ``cases`` row, decrypts ``feedback_note`` / ``transcript`` with
whichever key in ``RF_DATA_ENCRYPTION_KEY`` still works, and rewrites them
with the first (current) key.  Idempotent and safe to run while the
service is up -- a second run reports 0 changes.

    python -m packages.db.reencrypt                  # uses RF_DATABASE_URL
    python -m packages.db.reencrypt postgres://...
"""

from __future__ import annotations

import sys

from packages.db.crypto import column_cipher

_COLS = ("feedback_note", "transcript")


async def _run(dsn: str) -> int:
    import asyncpg  # noqa: PLC0415 - optional 'db' extra

    cipher = column_cipher()
    if not cipher.active:
        print("RF_DATA_ENCRYPTION_KEY is not set - nothing to do", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(dsn)
    changed = 0
    try:
        rows = await conn.fetch("SELECT session_id, feedback_note, transcript FROM cases")
        for row in rows:
            updates = {col: cipher.rotate(row[col]) for col in _COLS}
            updates = {c: v for c, v in updates.items() if v is not None}
            if updates:
                sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(updates))
                await conn.execute(
                    f"UPDATE cases SET {sets} WHERE session_id = $1",
                    row["session_id"],
                    *updates.values(),
                )
                changed += 1
    finally:
        await conn.close()
    print(f"re-encrypted {changed} row(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    import asyncio  # noqa: PLC0415
    import os  # noqa: PLC0415

    args = sys.argv[1:] if argv is None else argv
    dsn = args[0] if args else os.environ.get("RF_DATABASE_URL", "")
    if not dsn:
        print("no DSN: pass one as an argument or set RF_DATABASE_URL", file=sys.stderr)
        return 2
    return asyncio.run(_run(dsn))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
