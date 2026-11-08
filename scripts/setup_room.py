"""Set room r1's floor code and issue an API token for every stall's device.

    python scripts/setup_room.py --database-url postgresql://... [--code 4821] [--api https://...]

The team's migration 001 already created the room and its stalls (s1 real, s2 to s5
simulated). This stores the floor code's hash in rooms.join_code_hash and a fresh token's
hash in devices.token_hash for each stall, and writes devices.local.json (git-ignored) with
the plain tokens for sim/device.py, the smoke test and the Pi. Running it again issues new
tokens and keeps the floor code unless you pass --code.
"""

import argparse
import json
import os
import pathlib
import secrets
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from backend.auth import hash_secret  # noqa: E402

ROOM = "r1"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("TIGER_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--code", help="floor code (default: keep the one in devices.local.json, or make a 4-digit one)")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL to store for the scripts")
    parser.add_argument("--out", default="devices.local.json", help="where to write the tokens (git-ignored devices.*.json)")
    args = parser.parse_args()
    out = ROOT / args.out
    if not args.database_url:
        sys.exit("Set TIGER_DATABASE_URL or pass --database-url")

    previous = json.loads(out.read_text()) if out.exists() else {}
    code = args.code or previous.get("floor_code") or f"{secrets.randbelow(10_000):04d}"

    devices = {}
    with psycopg.connect(args.database_url) as conn:
        updated = conn.execute("update rooms set join_code_hash = %s where id = %s", [hash_secret(code), ROOM]).rowcount
        if not updated:
            sys.exit(f"room {ROOM} not found: run tiger/migrate.py first")
        stalls = conn.execute("select id, name, simulated from stalls where room_id = %s order by id", [ROOM]).fetchall()
        for stall_id, name, simulated in stalls:
            token = secrets.token_urlsafe(24)
            conn.execute(
                "insert into devices (stall_id, token_hash) values (%s, %s)"
                " on conflict (stall_id) do update set token_hash = excluded.token_hash",
                [stall_id, hash_secret(token)],
            )
            devices[stall_id] = {"stall_id": stall_id, "name": name, "simulated": simulated, "token": token}

    out.write_text(json.dumps({"api": args.api, "room": ROOM, "floor_code": code, "devices": devices}, indent=2))
    out.chmod(0o600)
    print(f"room {ROOM}: floor code {code}; tokens for {', '.join(devices)} written to {out.name}")


if __name__ == "__main__":
    main()
