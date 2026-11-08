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

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import db
from .auth import browser_id, check_join_code, device_stall, require_cron
from .config import settings
from .realtime import flush_outbox, flush_quietly

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


@app.get("/api/rooms/{room}")
def get_room(room: str) -> dict:
    with db.pool().connection() as conn:
        info = _room(conn, room)
        stalls = conn.execute(
            "select s.id, s.name, s.state, s.since, s.person, s.water, s.timer_end, s.simulated,"
            "       coalesce(d.last_seen > now() - interval '60 seconds', false) as online,"
            "       (select json_agg(json_build_object('id', r.id, 'type', r.type))"
            "          from reports r where r.stall_id = s.id and r.status = 'open') as reports"
            "  from stalls s left join devices d on d.stall_id = s.id"
            " where s.room_id = %s order by s.id",
            [room],
        ).fetchall()
        waiting = conn.execute("select count(*) as n from queue where status = 'waiting'").fetchone()["n"]
        now = conn.execute("select now() as now").fetchone()["now"]
    return {
        "id": info["id"],
        "name": info["name"],
        "needs_code": info["join_code_hash"] is not None,
        "server_time": now,
        "stalls": stalls,
        "waiting": waiting,
    }


class JoinBody(BaseModel):
    code: str


@app.post("/api/rooms/{room}/join")
def join_room(room: str, body: JoinBody) -> dict:
    with db.pool().connection() as conn:
        check_join_code(_room(conn, room), body.code)
    return {"ok": True}


# Phone actions (signed in anonymously, with the floor code) --------------------------------


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
        notify = conn.execute(
            "select n.id as notify_id, n.status, s.name as stall_name"
            "  from notifies n join stalls s on s.id = n.stall_id"
            " where n.requester_id = %s and n.created_at > now() - interval '15 minutes'"
            " order by n.created_at desc limit 1",
            [visitor],
        ).fetchone()
    return {"visitor_id": visitor, "queue": entry, "notify": notify}


@app.post("/api/rooms/{room}/queue")
def join_queue(room: str, browser: uuid.UUID = Depends(browser_id),
               x_floor_code: str | None = Header(default=None)) -> dict:
    with db.pool().connection() as conn:
        check_join_code(_room(conn, room), x_floor_code)
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


@app.post("/api/rooms/{room}/notify")
def notify(room: str, browser: uuid.UUID = Depends(browser_id),
           x_floor_code: str | None = Header(default=None)) -> dict:
    with db.pool().connection() as conn:
        check_join_code(_room(conn, room), x_floor_code)
        result = conn.execute("select request_notify(%s) as r", [str(browser)]).fetchone()["r"]
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


# Timers ------------------------------------------------------------------------------


@app.post("/api/tick", dependencies=[Depends(require_cron)])
def tick() -> dict:
    with db.pool().connection() as conn:
        result = conn.execute("select api_tick() as r").fetchone()["r"]
    result["sent"] = flush_outbox()
    return result
