"""Connection pool for Tiger Data."""

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings


class DatabaseNotConfigured(RuntimeError):
    pass


_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        url = settings().database_url
        if not url:
            raise DatabaseNotConfigured("Set TIGER_POOLER_URL or TIGER_DATABASE_URL")
        _pool = ConnectionPool(
            url,
            min_size=1,
            max_size=4,
            timeout=10,
            open=True,
            # Tiger's transaction pooler can't keep prepared statements between transactions.
            kwargs={"prepare_threshold": None, "row_factory": dict_row},
        )
    return _pool


def reset() -> None:
    """Close the pool (tests use this after changing settings)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
