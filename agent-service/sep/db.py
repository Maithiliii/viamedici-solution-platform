"""PostgreSQL access: connection pool with pgvector support."""
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)


@lru_cache
def get_pool() -> ConnectionPool:
    return ConnectionPool(
        get_settings().database_url,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row, "autocommit": True},
        configure=_configure,
        open=True,
    )


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn


def fetch_all(sql: str, params: Any = None) -> list[dict]:
    with connection() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql: str, params: Any = None) -> dict | None:
    with connection() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: Any = None) -> None:
    with connection() as conn:
        conn.execute(sql, params)
