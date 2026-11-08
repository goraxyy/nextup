"""Shared fixtures. Needs the local database: docker compose up -d db"""

import os
import pathlib
import sys

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tiger.migrate import migrate  # noqa: E402

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://postgres:nextup@localhost:5433/postgres")
TEST_DB = "nextup_test"


@pytest.fixture(scope="session")
def db_url():
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"drop database if exists {TEST_DB} with (force)")
        conn.execute(f"create database {TEST_DB}")
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB
    migrate(url, verbose=False)
    return url


@pytest.fixture
def conn(db_url):
    """A connection whose work is rolled back after the test."""
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        with c.transaction(force_rollback=True):
            yield c


@pytest.fixture
def room(conn):
    """Room r1 from the seed: s1 is the real stall, s2 to s5 are simulated, all free.

    The SQL simulator is switched off so tests are deterministic, and the outbox starts empty.
    """
    conn.execute("update settings set simulator_on = false, notify_min_session_minutes = 10")
    conn.execute("delete from notifies")
    conn.execute("delete from queue")
    conn.execute("update stalls set state = 'free', person = false, water = false, since = now() - interval '1 hour'")
    conn.execute("delete from outbox")
    return "r1"
