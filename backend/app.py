"""NextUp API: a thin layer over the team's schema and rules in Tiger Data.

Writes call SQL functions in Tiger (tiger/migrations/005_web_access.sql). Triggers write the
resulting live updates to the outbox (004), and the outbox goes to Supabase Realtime before
each response returns. Deployed as a Vercel Function (api/index.py); run locally with
`uvicorn backend.app:app --reload`.
"""

import datetime as dt
import json
import uuid
from typing import Literal

import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import db, events, insights, music, supabase_admin
from .auth import browser_id, device_stall, hash_secret, require_cron, verify_supabase_token
from .config import settings
from .realtime import flush_outbox, flush_quietly

log = logging.getLogger("nextup.api")

app = FastAPI(title="NextUp API", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.exception_handler(db.DatabaseNotConfigured)
def _no_database(_: Request, exc: db.DatabaseNotConfigured) -> JSONResponse:
    return JSONResponse({"error": "database not configured", "detail": str(exc)}, status_code=503)


def _room(conn, room: str) -> dict:
    row = conn.execute("select id, name, join_code_hash from rooms where id = %s", [room]).fetchone()
    if row is None:
        raise HTTPException(404, "no such room")
    return row


# Health ------------------------------------------------------------------------------


@app.get("/api/healthz")
def healthz() -> JSONResponse:
    s = settings()
    body = {
        "database": "not configured",
        "realtime": "configured" if s.supabase_url and s.supabase_secret_key else "not configured",
        "cron": "configured" if s.cron_secret else "not configured",
    }
    ok = False
    if s.database_url:
        try:
            with db.pool().connection() as conn:
                row = conn.execute("select count(*) as backlog from outbox where sent_at is null").fetchone()
            body["database"] = "ok"
            body["outbox_backlog"] = row["backlog"]
            ok = True
        except Exception as exc:  # noqa: BLE001
            body["database"] = f"error: {type(exc).__name__}"
    body["ok"] = ok
    return JSONResponse(body, status_code=200 if ok else 503)


# Room status (public) ------------------------------------------------------------------

HURRY_SQL = (
    "(select json_build_object('status', n.status, 'created_at', n.created_at)"
    "   from notifies n where n.stall_id = s.id and n.created_at > now() - interval '10 minutes'"
    "  order by n.created_at desc limit 1) as hurry"
)


@app.get("/api/rooms/{room}")
def get_room(room: str) -> dict:
    with db.pool().connection() as conn:
        info = _room(conn, room)
        stalls = conn.execute(
            "select s.id, s.name, s.state, s.since, s.person, s.water, s.timer_end, s.simulated,"
            "       coalesce(d.last_seen > now() - interval '60 seconds', false) as online,"
            "       (select json_agg(json_build_object('id', r.id, 'type', r.type))"
            "          from reports r where r.stall_id = s.id and r.status = 'open') as reports,"
            f"      {HURRY_SQL}"
            "  from stalls s left join devices d on d.stall_id = s.id"
            " where s.room_id = %s order by s.id",
            [room],
        ).fetchall()
        waiting = conn.execute("select count(*) as n from queue where status = 'waiting'").fetchone()["n"]
        st = conn.execute("select notify_min_session_minutes, notify_stall_cooldown_minutes,"
                          " notify_requester_cooldown_minutes from settings where id = 1").fetchone()
        now = conn.execute("select now() as now").fetchone()["now"]
    return {
        "id": info["id"],
        "name": info["name"],
        "server_time": now,
        "stalls": stalls,
        "waiting": waiting,
        "hurry_rules": st,
    }


@app.get("/api/rooms/{room}/events")
def room_events(room: str, limit: int = 8) -> dict:
    """Upcoming campus events from Columbia's calendar (refreshed by the tick)."""
    with db.pool().connection() as conn:
        _room(conn, room)
        rows = events.upcoming(conn, limit=max(1, min(limit, 20)))
    return {"events": rows, "source": "Columbia University Events (events.columbia.edu)"}


@app.get("/api/rooms/{room}/insights")
def room_insights(room: str) -> dict:
    """Rush hours: today's expected busyness, best times to go, weekly heatmap, wait estimate."""
    with db.pool().connection() as conn:
        _room(conn, room)
        return insights.room_insights(conn, room)


@app.get("/api/rooms/{room}/nowplaying")
def room_now_playing(room: str) -> dict:
    """What the room's speaker is playing, with synced lyrics if LRCLIB has them."""
    with db.pool().connection() as conn:
        _room(conn, room)
        return music.current(conn, room)


class NowPlayingBody(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_s: float | None = None
    position_s: float | None = None
    playing: bool = False
    latency_ms: float = Field(default=200, ge=0, le=2000)


@app.post("/api/rooms/{room}/nowplaying")
def report_now_playing(room: str, body: NowPlayingBody, x_audio_token: str | None = Header(default=None)) -> dict:
    """Called by scripts/now_playing_mac.py on the Mac that plays Apple Music to the speaker."""
    with db.pool().connection() as conn:
        info = conn.execute("select audio_token_hash from rooms where id = %s", [room]).fetchone()
        if info is None:
            raise HTTPException(404, "no such room")
        if not x_audio_token or not info["audio_token_hash"] or hash_secret(x_audio_token) != info["audio_token_hash"]:
            raise HTTPException(401, "bad audio token")
        result = music.report(conn, room, body.title, body.artist, body.album, body.duration_s,
                              body.position_s, body.playing, body.latency_ms)
    flush_quietly()
    return result


# Phone actions (signed in anonymously) ------------------------------------------------------


@app.get("/api/rooms/{room}/me")
def my_status(room: str, browser: uuid.UUID = Depends(browser_id)) -> dict:
    visitor = str(browser)
    with db.pool().connection() as conn:
        _room(conn, room)
        entry = conn.execute(
            "select q.id, q.status, q.called_stall_id as stall_id, s.name as stall_name, q.joined_at,"
            "       q.called_at + interval '2 minutes' as claim_by,"
            "       (select count(*) from queue w where w.status = 'waiting'"
            "          and (w.joined_at, w.id) <= (q.joined_at, q.id)) as position"
            "  from queue q left join stalls s on s.id = q.called_stall_id"
            " where q.visitor_id = %s and q.status in ('waiting', 'called')"
            " order by q.joined_at desc limit 1",
            [visitor],
        ).fetchone()
        hurries = conn.execute(
            "select n.id as notify_id, n.stall_id, n.status, n.created_at from notifies n"
            " where n.requester_id = %s and n.created_at > now() - interval '10 minutes'"
            " order by n.created_at desc",
            [visitor],
        ).fetchall()
        cooldown_until = conn.execute(
            "select max(n.created_at) + make_interval(mins => st.notify_requester_cooldown_minutes) as until"
            "  from notifies n, settings st where st.id = 1 and n.requester_id = %s"
            " group by st.notify_requester_cooldown_minutes",
            [visitor],
        ).fetchone()
    return {"visitor_id": visitor, "queue": entry, "hurries": hurries,
            "hurry_cooldown_until": cooldown_until["until"] if cooldown_until else None}


@app.post("/api/rooms/{room}/queue")
def join_queue(room: str, browser: uuid.UUID = Depends(browser_id)) -> dict:
    with db.pool().connection() as conn:
        _room(conn, room)
        result = conn.execute("select join_queue(%s) as r", [str(browser)]).fetchone()["r"]
    flush_quietly()
    return result


@app.delete("/api/queue/{queue_id}")
def leave(queue_id: int, browser: uuid.UUID = Depends(browser_id)) -> dict:
    with db.pool().connection() as conn:
        left = conn.execute("select leave_queue(%s, %s) as ok", [str(browser), queue_id]).fetchone()["ok"]
    flush_quietly()
    if not left:
        raise HTTPException(404, "no such place in line")
    return {"status": "left"}


@app.post("/api/rooms/{room}/stalls/{stall}/hurry")
def hurry(room: str, stall: str, browser: uuid.UUID = Depends(browser_id)) -> dict:
    """Ask the person in this stall to start wrapping up (a pop-up on the stall's screen)."""
    with db.pool().connection() as conn:
        _room(conn, room)
        if conn.execute("select 1 from stalls where id = %s and room_id = %s", [stall, room]).fetchone() is None:
            raise HTTPException(404, "no such stall in this room")
        result = conn.execute("select request_hurry(%s, %s) as r", [str(browser), stall]).fetchone()["r"]
    flush_quietly()
    return result


# Devices -----------------------------------------------------------------------------

EventType = Literal[
    "session.start", "session.end", "state", "reading", "heartbeat",
    "input.hold", "notify.delivered", "safety.alert",
]


class DeviceEvent(BaseModel):
    event_id: uuid.UUID
    ts: dt.datetime
    type: EventType
    payload: dict = Field(default_factory=dict)

    @field_validator("ts")
    @classmethod
    def _aware(cls, value: dt.datetime) -> dt.datetime:
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


class DeviceBatch(BaseModel):
    events: list[DeviceEvent] = Field(min_length=1, max_length=500)


@app.post("/api/device/events")
def device_events(batch: DeviceBatch, stall: str = Depends(device_stall)) -> dict:
    applied = replayed = 0
    errors = []
    with db.pool().connection() as conn:
        for event in sorted(batch.events, key=lambda e: e.ts):
            try:
                with conn.transaction():
                    ok = conn.execute(
                        "select record_device_event(%s, %s, %s, %s, %s::jsonb) as applied",
                        [stall, event.event_id, event.ts, event.type, json.dumps(event.payload)],
                    ).fetchone()["applied"]
                applied += ok
                replayed += not ok
            except Exception as exc:  # noqa: BLE001  one bad event must not block the rest
                errors.append({"event_id": str(event.event_id), "error": type(exc).__name__})
    flush_quietly()
    return {"stall": stall, "applied": applied, "replayed": replayed, "errors": errors}


class LinkBody(BaseModel):
    access_token: str


@app.post("/api/device/link")
def device_link(body: LinkBody, stall: str = Depends(device_stall)) -> dict:
    """Let the Pi's Supabase user read device:<stall>.

    The Pi signs in to Supabase anonymously, then calls this with its device token and its
    Supabase access token. After a token refresh, its tokens carry app_metadata.device_id,
    which the Realtime policy for device topics checks.
    """
    claims = verify_supabase_token(body.access_token)
    if (claims.get("app_metadata") or {}).get("device_id") != stall:
        try:
            supabase_admin.set_device_claim(claims["sub"], stall)
        except supabase_admin.AdminNotConfigured as exc:
            raise HTTPException(503, str(exc)) from exc
    return {"stall": stall, "user_id": claims["sub"], "topic": f"device:{stall}"}


@app.get("/api/device/state")
def device_state(stall: str = Depends(device_stall)) -> dict:
    """Everything the stall's screen should show now, for (re)connects."""
    with db.pool().connection() as conn:
        row = conn.execute(
            "select id, name, state, since, room_id from stalls where id = %s", [stall]
        ).fetchone()
        notify = conn.execute(
            "select id as notify_id, status, created_at, rechimed_at from notifies"
            " where stall_id = %s and status in ('sent', 'delivered') order by created_at desc limit 1",
            [stall],
        ).fetchone()
        reserved = conn.execute(
            "select id as queue_id, called_at + interval '2 minutes' as until from queue"
            " where called_stall_id = %s and status = 'called' order by called_at desc limit 1",
            [stall],
        ).fetchone()
        now = conn.execute("select now() as now").fetchone()["now"]
    return {"stall": row, "notify": notify, "reserved": reserved, "server_time": now}


@app.get("/api/device/screen")
def device_screen(stall: str = Depends(device_stall)) -> dict:
    """Content for the stall screen's cycling pages; the Pi fetches it every few minutes."""
    with db.pool().connection() as conn:
        row = conn.execute("select id, name, room_id from stalls where id = %s", [stall]).fetchone()
        waiting = conn.execute("select count(*) as n from queue where status = 'waiting'").fetchone()["n"]
        upcoming = events.upcoming(conn, limit=6, for_screen=True)
        now = conn.execute("select now() as now").fetchone()["now"]
        summary = insights.room_insights(conn, row["room_id"])
        playing = music.current(conn, row["room_id"])
    # Water: the flow rate lets the screen show the water used by the shower that's running.
    water = {k: summary["water"][k] for k in ("gpm", "lpm", "today_gallons", "today_liters", "per_shower_gallons")}
    return {"stall": row, "waiting": waiting, "events": upcoming, "server_time": now,
            "insights": {**{k: summary[k] for k in ("now", "today", "current_hour", "best_times", "stats")},
                         "water": water},
            "music": playing}


@app.get("/api/device/nowplaying")
def device_now_playing(stall: str = Depends(device_stall)) -> dict:
    with db.pool().connection() as conn:
        room = conn.execute("select room_id from stalls where id = %s", [stall]).fetchone()["room_id"]
        return music.current(conn, room)


# Timers ------------------------------------------------------------------------------


@app.post("/api/tick", dependencies=[Depends(require_cron)])
def tick() -> dict:
    with db.pool().connection() as conn:
        result = conn.execute("select api_tick() as r").fetchone()["r"]
    result["sent"] = flush_outbox()
    try:  # campus events every 30 minutes; a broken feed must never break the tick
        with db.pool().connection() as conn:
            result["events"] = events.refresh_if_stale(conn)
    except Exception as exc:  # noqa: BLE001
        log.warning("campus events refresh failed: %s", exc)
        result["events"] = f"error: {type(exc).__name__}"
    return result
