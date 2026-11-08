"""Seed weeks of simulated shower history so the rush-hour dashboards have something to show.

    python scripts/seed_history.py --database-url postgresql://... [--room r1] [--weeks 4]

Only simulated stalls get history (in r1: s2 to s5), and only before each stall's first real
event, so live data is never touched. Showers last 10 to 20 minutes; the gap between them follows
the same day profile as the live simulator (rushes 7-9 am and 9 pm-midnight), with later
mornings at weekends. Rows go into stall_events like real ones (in_use, finishing, free), then
the continuous aggregates are refreshed. The website says which part of the data is simulated.
"""

import argparse
import datetime as dt
import os
import random
import sys
from zoneinfo import ZoneInfo

import psycopg

NY = ZoneInfo("America/New_York")


def gap_minutes(at: dt.datetime) -> float:
    """Average minutes between showers at one stall, by New York hour (weekends start later)."""
    local = at.astimezone(NY)
    h, weekend = local.hour, local.weekday() >= 5
    morning = (10, 12) if weekend else (7, 9)
    if morning[0] <= h < morning[1]:
        return 2.5
    if 21 <= h:
        return 3
    if 17 <= h < 21:
        return 10
    if morning[1] <= h < 17:
        return 25
    return 60


def sessions(start: dt.datetime, end: dt.datetime, rng: random.Random):
    """(begin, length in seconds) pairs for one stall between start and end."""
    t = start
    while t < end:
        t += dt.timedelta(minutes=rng.expovariate(1 / gap_minutes(t)))
        length = rng.uniform(10, 20) * 60
        if t + dt.timedelta(seconds=length) >= end:
            return
        yield t, length
        t += dt.timedelta(seconds=length)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=os.environ.get("TIGER_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--room", default="r1")
    parser.add_argument("--weeks", type=int, default=4)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if not args.database_url:
        sys.exit("Set TIGER_DATABASE_URL or pass --database-url")

    rng = random.Random(args.seed)
    now = dt.datetime.now(dt.timezone.utc)
    total = 0
    with psycopg.connect(args.database_url) as conn:
        stalls = conn.execute(
            "select s.id, (select min(time) from stall_events e where e.stall_id = s.id) as first_real"
            "  from stalls s where s.room_id = %s and s.simulated order by s.id",
            [args.room],
        ).fetchall()
        for stall_id, first_real in stalls:
            end = (first_real or now) - dt.timedelta(minutes=1)
            start = now - dt.timedelta(weeks=args.weeks)
            if end <= start:
                continue
            rows, last_end = [], start
            for begin, length in sessions(start, end, rng):
                rows.append((begin, stall_id, "in_use", "free", (begin - last_end).total_seconds()))
                rows.append((begin + dt.timedelta(seconds=length - 60), stall_id, "finishing", "in_use", length - 60))
                last_end = begin + dt.timedelta(seconds=length)
                rows.append((last_end, stall_id, "free", "finishing", 60.0))
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into stall_events (time, stall_id, state, prev_state, prev_duration_s) values (%s, %s, %s, %s, %s)",
                    rows,
                )
            conn.commit()
            total += len(rows) // 3
            print(f"{stall_id}: {len(rows) // 3} simulated showers from {start:%b %d} to {end:%b %d %H:%M} UTC")

    # Refreshing a continuous aggregate can't run inside a transaction.
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        for view in ("occupancy_hourly", "shower_duration_daily"):
            conn.execute(f"call refresh_continuous_aggregate('{view}', null, null)")
    print(f"{total} showers seeded; aggregates refreshed")


if __name__ == "__main__":
    main()
