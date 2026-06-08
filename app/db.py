# app/db.py — a thin psycopg connection-pool helper.
#
# Usage:
#     from .db import pg
#     with pg() as cur:
#         cur.execute("SELECT ...")
#         rows = cur.fetchall()
#
# The context manager yields a cursor, commits on clean exit, rolls back on
# error, and always returns the connection to the pool.
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from .config import settings

# pgvector needs its adapters registered on every fresh connection so that
# Python lists round-trip to/from the `vector` column type.
try:
    from pgvector.psycopg import register_vector

    def _configure(conn):
        register_vector(conn)

except ImportError:  # pragma: no cover - pgvector optional at import time
    def _configure(conn):  # type: ignore
        return None


_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings().database_url,
            min_size=1,
            max_size=10,
            configure=_configure,
            open=True,
        )
    return _pool


@contextmanager
def pg():
    """Yield a cursor inside a transaction. Commits on success, rolls back on error."""
    pool = _get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            yield cur
            # Connection-level commit happens automatically when the `with
            # pool.connection()` block exits without an exception.
