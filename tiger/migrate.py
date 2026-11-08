"""Apply tiger/migrations/*.sql in order, each once, each in its own transaction.

    python tiger/migrate.py                          # uses TIGER_DATABASE_URL, then DATABASE_URL
    python tiger/migrate.py --database-url postgresql://...

Use Tiger's direct connection string here, not the pooler. Never edit a migration that
has already run on Tiger: add the next number instead.
"""

import argparse
import os
import pathlib
import sys

import psycopg

MIGRATIONS = pathlib.Path(__file__).resolve().parent / "migrations"


def migrate(url: str, verbose: bool = True) -> list[str]:
    applied = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "create table if not exists schema_migrations ("
            " name text primary key,"
            " applied_at timestamptz not null default now())"
        )
        done = {row[0] for row in conn.execute("select name from schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("insert into schema_migrations (name) values (%s)", [path.name])
            applied.append(path.name)
            if verbose:
                print(f"applied {path.name}")
    if verbose and not applied:
        print("nothing to apply")
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("TIGER_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        sys.exit("Set TIGER_DATABASE_URL or pass --database-url")
    migrate(args.database_url)


if __name__ == "__main__":
    main()
