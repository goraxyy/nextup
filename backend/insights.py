"""Rush hours and the best time to go, from the busy_profile() SQL function (migration 007).

busy_profile() averages the continuous aggregate occupancy_hourly by weekday and hour. Here that
becomes: today's expected busyness, the quietest windows in the next 12 hours, a weekly heatmap,
a wait estimate for someone joining the line now, and a few stats.
"""

import datetime as dt
import heapq
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _label(at: dt.datetime) -> str:
    return at.astimezone(NY).strftime("%I:%M %p").lstrip("0")


def best_times(profile: list[list[float]], now: dt.datetime, hours_ahead: int = 12, count: int = 3) -> list[dict]:
    """The quietest half-hour windows in the next `hours_ahead` hours, at least an hour apart."""
    local = now.astimezone(NY)
    start = local.replace(minute=0 if local.minute < 30 else 30, second=0, microsecond=0) + dt.timedelta(minutes=30)
    slots = []
    for i in range(hours_ahead * 2):
        at = start + dt.timedelta(minutes=30 * i)
        here = profile[(at.weekday() + 1) % 7][at.hour]          # weekday(): Monday 0; dow: Sunday 0
        nxt = at + dt.timedelta(hours=1)
        after = profile[(nxt.weekday() + 1) % 7][nxt.hour]
        occupancy = here if at.minute == 0 else (here + after) / 2
        slots.append((occupancy, at))
    picked: list[tuple[float, dt.datetime]] = []
    for occupancy, at in sorted(slots, key=lambda s: (round(s[0], 3), s[1])):
        if all(abs((at - p[1]).total_seconds()) >= 3600 for p in picked):
            picked.append((occupancy, at))
        if len(picked) == count:
            break
    return [{"at": at.isoformat(), "label": _label(at), "occupancy": round(occ, 2),
             "in_minutes": round((at - local).total_seconds() / 60)} for occ, at in sorted(picked, key=lambda p: p[1])]


def wait_minutes(real_stalls: list[dict], waiting: int, avg_minutes: float, now: dt.datetime) -> float:
    """Minutes until someone joining the line now gets a real stall (only real stalls call people)."""
    if not real_stalls:
        return 0.0
    free_at = []
    for s in real_stalls:
        if s["state"] == "free":
            free_at.append(0.0)
        else:
            elapsed = (now - s["since"]).total_seconds() / 60
            free_at.append(max(1.0, avg_minutes - elapsed))
    heapq.heapify(free_at)
    for _ in range(waiting):            # people ahead take the stalls first
        t = heapq.heappop(free_at)
        heapq.heappush(free_at, t + avg_minutes)
    return round(heapq.heappop(free_at), 1)


def room_insights(conn, room: str) -> dict:
    now = conn.execute("select now() as now").fetchone()["now"]
    profile = [[0.0] * 24 for _ in range(7)]
    for r in conn.execute("select dow, hour, occupancy from busy_profile(%s, 4)", [room]).fetchall():
        profile[r["dow"]][r["hour"]] = round(r["occupancy"], 3)

    local = now.astimezone(NY)
    dow = (local.weekday() + 1) % 7
    stalls = conn.execute("select id, state, since, simulated, timer_end from stalls where room_id = %s order by id",
                          [room]).fetchall()
    waiting = conn.execute("select count(*) as n from queue where status = 'waiting'").fetchone()["n"]
    stats = conn.execute(
        "select count(*) filter (where e.state = 'in_use' and e.prev_state = 'free'"
        "         and e.time >= date_trunc('day', now() at time zone 'America/New_York') at time zone 'America/New_York') as today,"
        "       sum(e.prev_duration_s) filter (where e.prev_state in ('in_use', 'finishing'))"
        "         / nullif(count(*) filter (where e.state = 'in_use' and e.prev_state = 'free'), 0) / 60 as avg_minutes,"
        "       count(*) filter (where s.simulated) as simulated_events, count(*) as all_events,"
        "       min(e.time) as since"
        "  from stall_events e join stalls s on s.id = e.stall_id"
        " where s.room_id = %s and e.time > now() - interval '4 weeks'",
        [room],
    ).fetchone()
    avg_minutes = float(stats["avg_minutes"] or 15.0)

    in_use = sum(1 for s in stalls if s["state"] != "free")
    occupancy_now = in_use / len(stalls) if stalls else 0.0
    expected_now = profile[dow][local.hour]
    busiest_hour = max(range(24), key=lambda h: profile[dow][h])
    if waiting or occupancy_now >= 0.8:
        mood = "busy"
    elif occupancy_now <= 0.4 and expected_now <= 0.4:
        mood = "quiet"
    else:
        mood = "moderate"

    weeks = (now - stats["since"]).days / 7 if stats["since"] else 0
    return {
        "water": water(conn, room, stalls, now),
        "server_time": now,
        "now": {
            "in_use": in_use, "stalls": len(stalls), "waiting": waiting,
            "occupancy": round(occupancy_now, 2), "expected": round(expected_now, 2), "mood": mood,
            "wait_minutes": wait_minutes([s for s in stalls if not s["simulated"]], waiting, avg_minutes, now),
        },
        "today": [{"hour": h, "occupancy": profile[dow][h]} for h in range(24)],
        "current_hour": local.hour,
        "best_times": best_times(profile, now),
        "heatmap": profile,  # [dow 0 = Sunday][hour 0-23], share of stalls in use
        "stats": {
            "avg_shower_minutes": round(avg_minutes, 1),
            "showers_today": stats["today"],
            "busiest_hour_today": busiest_hour,
        },
        "basis": {
            "weeks": round(min(4.0, weeks), 1),
            "simulated_share": round((stats["simulated_events"] or 0) / stats["all_events"], 2) if stats["all_events"] else 0,
        },
    }


LITERS_PER_GALLON = 3.785


def water(conn, room: str, stalls: list[dict], now: dt.datetime) -> dict:
    """Estimated water use: minutes with the water on (in_use) × the settings' flow rate."""
    gpm = float(conn.execute("select water_gpm from settings where id = 1").fetchone()["water_gpm"])
    day_start = now.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    done = conn.execute(
        "select coalesce(sum(e.prev_duration_s), 0) / 60 as minutes from stall_events e join stalls s on s.id = e.stall_id"
        " where s.room_id = %s and e.prev_state = 'in_use' and e.time >= %s",
        [room, day_start],
    ).fetchone()["minutes"]
    running = [s for s in stalls if s["state"] == "in_use"]
    live = sum((now - max(s["since"], day_start)).total_seconds() / 60 for s in running)
    per_shower = conn.execute(
        "select avg(e.prev_duration_s) / 60 as minutes from stall_events e join stalls s on s.id = e.stall_id"
        " where s.room_id = %s and e.prev_state = 'in_use' and e.time > now() - interval '4 weeks'",
        [room],
    ).fetchone()["minutes"] or 0
    daily = conn.execute(
        "select time_bucket('1 day', e.time, 'America/New_York') as day,"
        "       sum(e.prev_duration_s) filter (where e.prev_state = 'in_use') / 60 as minutes,"
        "       count(*) filter (where e.state = 'in_use' and e.prev_state = 'free') as showers"
        "  from stall_events e join stalls s on s.id = e.stall_id"
        " where s.room_id = %s and e.time >= %s - interval '6 days'"
        " group by 1 order by 1",
        [room, day_start],
    ).fetchall()
    today_gal = (float(done) + live) * gpm
    return {
        "gpm": gpm,
        "lpm": round(gpm * LITERS_PER_GALLON, 1),
        "today_gallons": round(today_gal),
        "today_liters": round(today_gal * LITERS_PER_GALLON),
        "per_shower_gallons": round(float(per_shower) * gpm, 1),
        "running_now": len(running),
        "flowing_now_gpm": round(len(running) * gpm, 1),
        "daily": [{"day": r["day"].astimezone(NY).date().isoformat(),
                   "gallons": round(float(r["minutes"] or 0) * gpm), "showers": r["showers"]} for r in daily],
    }
