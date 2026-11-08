"""Issue an API token for every stall's device in a room.

    python scripts/setup_room.py --database-url postgresql://... [--room r1] [--api https://...] [--out devices.local.json]

The team's migration 001 already created room r1 and its stalls (s1 real, s2 to s5
simulated). This stores a fresh token's hash in devices.token_hash for each stall and writes
devices.local.json (git-ignored) with the plain tokens for sim/device.py, the smoke test and
the Pi. Running it again issues new tokens. (There's no floor code since migration 006.)
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

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("TIGER_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--room", default="r1", help="room id (default r1)")
    parser.add_argument("--audio", action="store_true", help="also issue the room's audio token, for scripts/now_playing_mac.py")
    parser.add_argument("--only-audio", action="store_true", help="issue only the audio token; keep the stalls' tokens")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL to store for the scripts")
    parser.add_argument("--out", default="devices.local.json", help="where to write the tokens (git-ignored devices.*.json)")
    args = parser.parse_args()
    out = ROOT / args.out
    if not args.database_url:
        sys.exit("Set TIGER_DATABASE_URL or pass --database-url")

    devices = {}
    with psycopg.connect(args.database_url) as conn:
        if conn.execute("select 1 from rooms where id = %s", [args.room]).fetchone() is None:
            sys.exit(f"room {args.room} not found: run tiger/migrate.py first")
        stalls = [] if args.only_audio else conn.execute(
            "select id, name, simulated from stalls where room_id = %s order by id", [args.room]).fetchall()
        audio_token = None
        if args.audio or args.only_audio:
            audio_token = secrets.token_urlsafe(24)
            conn.execute("update rooms set audio_token_hash = %s where id = %s", [hash_secret(audio_token), args.room])
        for stall_id, name, simulated in stalls:
            token = secrets.token_urlsafe(24)
            conn.execute(
                "insert into devices (stall_id, token_hash) values (%s, %s)"
                " on conflict (stall_id) do update set token_hash = excluded.token_hash",
                [stall_id, hash_secret(token)],
            )
            devices[stall_id] = {"stall_id": stall_id, "name": name, "simulated": simulated, "token": token}

    previous = json.loads(out.read_text()) if out.exists() else {}
    data = {"api": args.api, "room": args.room, "devices": devices or previous.get("devices", {})}
    if audio_token or previous.get("audio_token"):
        data["audio_token"] = audio_token or previous["audio_token"]
    out.write_text(json.dumps(data, indent=2))
    out.chmod(0o600)
    issued = list(devices) + (["the audio token"] if audio_token else [])
    print(f"room {args.room}: tokens for {', '.join(issued)} written to {out.name}")


if __name__ == "__main__":
    main()
