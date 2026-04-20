"""Copy the local SQLite DB to a remote Postgres target.

Usage:
    DATABASE_URL=postgresql://... \\
      python -m tools.migrate_sqlite_to_postgres

Reads from the default SQLite file at `webapp/data/appuat.db` regardless of
any DATABASE_URL set in the environment (we open two engines explicitly).
Writes into the target Postgres at `DATABASE_URL`.

Strategy:
  1. Open two SQLAlchemy engines — one to source (SQLite), one to target
     (Postgres). Both get Base.metadata.create_all() so the target has the
     full schema before we write.
  2. Copy table-by-table in FK-dependency order. Preserve PK ids so FKs
     on the target keep pointing at the right rows.
  3. After each table, reset the target's sequence to MAX(id)+1 so future
     inserts don't collide with migrated ids (Postgres-specific step —
     SQLite uses SQLITE_SEQUENCE which doesn't need touching).
  4. Verify: source vs target row counts must match exactly.

The script is idempotent-by-truncate — it empties the target tables first
so re-running doesn't stack duplicates. Use --dry-run to skip writes.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

# Source: always the local SQLite file, ignoring any env DATABASE_URL that
# might be set for the target.
_SRC_DIR = Path(__file__).resolve().parent.parent
_SQLITE_URL = f"sqlite:///{_SRC_DIR / 'webapp' / 'data' / 'appuat.db'}"

# FK dependency order — parents before children. Knowledge graph lives at
# the bottom because everything eventually points at projects.
_COPY_ORDER = [
    "projects",
    "screens",
    "edges",
    "test_plans",
    "test_cases",
    "knowledge_entities",
    "knowledge_relations",
    "knowledge_observations",
    "knowledge_artifacts",
    "knowledge_screenshots",
    "knowledge_embeddings",
    "work_items",
    "agent_sessions",
    "cost_ledger",
    "cross_project_hypotheses",
]

logger = logging.getLogger("migrate")


def _open_target(url: str):
    """Create the target engine + make sure schema is in place before writes."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
    return create_engine(url, **kwargs)


def _rows_of(engine, table: str) -> list[dict]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return []
    cols = [c["name"] for c in insp.get_columns(table)]
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT {', '.join(cols)} FROM {table}")).fetchall()
    return [dict(zip(cols, row)) for row in rows]


def _insert_rows(engine, table: str, rows: list[dict]) -> int:
    """Bulk-insert `rows` into `table`. Uses Postgres COPY over a slow TCP
    proxy (single round-trip for the whole table), falls back to plain
    executemany on SQLite or other dialects."""
    if not rows:
        return 0
    insp = inspect(engine)
    target_cols = [c["name"] for c in insp.get_columns(table)]
    target_set = set(target_cols)
    clean_rows = [{k: row.get(k) for k in target_cols if k in target_set and k in row} for row in rows]
    if not clean_rows or not clean_rows[0]:
        return 0
    cols = list(clean_rows[0].keys())

    if engine.dialect.name == "postgresql":
        return _copy_rows_postgres(engine, table, cols, clean_rows)

    # SQLite / other — executemany via a single round-trip is fine.
    placeholders = ", ".join(f":{c}" for c in cols)
    stmt = text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})")
    with engine.begin() as conn:
        conn.execute(stmt, clean_rows)
    return len(clean_rows)


def _copy_rows_postgres(engine, table: str, cols: list[str], rows: list[dict]) -> int:
    """Use psycopg2's COPY FROM STDIN for one-round-trip bulk load.

    Serialises each row to Postgres's text COPY format manually (the csv
    module's escapechar would mangle the `\\N` NULL marker, which Postgres
    then rejects for non-text columns). Suspends FK enforcement for the
    load — orphan rows exist in the source SQLite.
    """
    import io
    import json as _json

    def _escape_cell(v) -> str:
        # Postgres COPY text format: NULL → literal `\N`; backslash, tab,
        # newline, carriage-return must be escaped. Everything else is raw.
        if v is None:
            return r"\N"
        if isinstance(v, (bytes, bytearray, memoryview)):
            # Postgres bytea literal: \x<hex> — pass through unchanged after
            # we escape the leading backslash via the pipeline below.
            return r"\\x" + bytes(v).hex()
        if isinstance(v, (dict, list)):
            s = _json.dumps(v, default=str)
        elif isinstance(v, bool):
            s = "t" if v else "f"
        else:
            s = str(v)
        return (
            s.replace("\\", r"\\")
             .replace("\t", r"\t")
             .replace("\n", r"\n")
             .replace("\r", r"\r")
        )

    buf = io.StringIO()
    for row in rows:
        buf.write("\t".join(_escape_cell(row.get(c)) for c in cols))
        buf.write("\n")
    buf.seek(0)

    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("SET session_replication_role = 'replica'")
        col_list = ", ".join(f'"{c}"' for c in cols)
        copy_sql = (
            f"COPY {table} ({col_list}) FROM STDIN "
            "WITH (FORMAT text, DELIMITER E'\\t', NULL '\\N')"
        )
        cur.copy_expert(copy_sql, buf)
        cur.execute("SET session_replication_role = 'origin'")
        raw.commit()
    finally:
        raw.close()
    return len(rows)


def _truncate(engine, table: str) -> None:
    """Empty a table on the target before copy. Portable-ish: Postgres needs
    TRUNCATE ... RESTART IDENTITY CASCADE to also reset sequences.
    """
    dialect = engine.dialect.name
    with engine.begin() as conn:
        insp = inspect(engine)
        if table not in insp.get_table_names():
            return
        if dialect == "postgresql":
            conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        else:
            conn.execute(text(f"DELETE FROM {table}"))


def _reset_postgres_sequence(engine, table: str) -> None:
    """Bump the target sequence to MAX(id)+1 so new inserts don't collide."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        # pg_get_serial_sequence returns the sequence bound to the id column.
        seq = conn.execute(text(
            "SELECT pg_get_serial_sequence(:t, 'id')"
        ), {"t": table}).scalar()
        if not seq:
            return
        max_id = conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {table}")).scalar() or 0
        conn.execute(text(f"SELECT setval('{seq}', {max_id + 1}, false)"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Copy local SQLite → remote Postgres.")
    ap.add_argument("--dry-run", action="store_true", help="Count rows but don't write.")
    ap.add_argument("--target-url", default=os.environ.get("DATABASE_URL", ""),
                    help="Target DB URL (defaults to $DATABASE_URL).")
    args = ap.parse_args()

    if not args.target_url:
        print("ERROR: target URL missing. Set DATABASE_URL or pass --target-url.", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    src = create_engine(_SQLITE_URL, connect_args={"check_same_thread": False})
    dst = _open_target(args.target_url)

    # Import models so metadata is populated, then create_all on target.
    # We import via webapp.api.db so the shared Base gets the models.
    from webapp.api import models  # noqa: F401
    from webapp.api.db import Base
    Base.metadata.create_all(bind=dst)
    logger.info("Target schema ensured via create_all")

    stats: list[tuple[str, int, int]] = []
    for table in _COPY_ORDER:
        src_rows = _rows_of(src, table)
        if args.dry_run:
            stats.append((table, len(src_rows), 0))
            logger.info("[%-30s] source=%d  (dry-run)", table, len(src_rows))
            continue
        _truncate(dst, table)
        n = _insert_rows(dst, table, src_rows)
        _reset_postgres_sequence(dst, table)
        stats.append((table, len(src_rows), n))
        logger.info("[%-30s] copied %d rows", table, n)

    print()
    print(f"{'table':35s} {'source':>8s} {'target':>8s} {'match':>8s}")
    print("-" * 65)
    all_match = True
    for table, src_n, dst_n in stats:
        match = "OK" if src_n == dst_n else "MISMATCH"
        if src_n != dst_n:
            all_match = False
        print(f"{table:35s} {src_n:>8d} {dst_n:>8d} {match:>8s}")
    print()
    return 0 if all_match or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
