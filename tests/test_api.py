"""The API end to end against the local database, with Realtime captured in memory.

These tests commit, so the fixture resets room r1 (s1 real, s2 to s5 simulated) first.
"""

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import auth, config, db, realtime
from backend.app import app

PHONE = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
TOKENS = {"s1": "token-real-s1", "s3": "token-sim-s3"}


@pytest.fixture
def api(db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    for name in ("TIGER_POOLER_URL", "TIGER_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CRON_SECRET", "test-cron")
    config.settings.cache_clear()
    db.reset()

    sent: list[dict] = []
    monkeypatch.setattr(realtime, "broadcast", lambda messages: sent.extend(messages))
    app.dependency_overrides[auth.browser_id] = lambda: PHONE

    with db.pool().connection() as conn:
        conn.execute("update settings set simulator_on = false, notify_min_session_minutes = 0")
        conn.execute("delete from notifies")
        conn.execute("delete from queue")
        conn.execute("update stalls set state = 'free', person = false, water = false")
        conn.execute("update rooms set join_code_hash = %s where id = 'r1'", [auth.hash_secret("1234")])
        for stall, token in TOKENS.items():
            conn.execute("update devices set token_hash = %s where stall_id = %s", [auth.hash_secret(token), stall])
        conn.execute("delete from outbox")

    with TestClient(app) as client:
        yield {"client": client, "sent": sent}

    app.dependency_overrides.clear()
    db.reset()
    config.settings.cache_clear()


def post_event(api, stall, event_type, token=None):
    return api["client"].post(
        "/api/device/events",
        headers={"Authorization": f"Bearer {token or TOKENS[stall]}"},
        json={"events": [{"event_id": str(uuid.uuid4()),
                          "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                          "type": event_type}]},
    )


def sent(api, event):
    return [m for m in api["sent"] if m["event"] == event]


def test_health_reports_the_database(api):
    body = api["client"].get("/api/healthz").json()
    assert body["ok"] is True and body["database"] == "ok"


def test_device_event_changes_the_stall_and_broadcasts_privately(api):
    assert post_event(api, "s1", "session.start").json()["applied"] == 1
    room = api["client"].get("/api/rooms/r1").json()
    assert {s["id"]: s["state"] for s in room["stalls"]}["s1"] == "in_use"

    changed = sent(api, "stall.changed")
    assert changed[0]["topic"] == "room:r1" and changed[0]["private"] is True
    assert changed[0]["payload"]["id"] == "s1"  # the stall, not overwritten
    assert "msg_id" in changed[0]["payload"]  # outbox id, for de-duplication


def test_unknown_device_is_rejected(api):
    assert post_event(api, "s1", "session.start", token="not-a-token").status_code == 401


def test_the_line_needs_the_floor_code(api):
    client = api["client"]
    assert client.post("/api/rooms/r1/queue").status_code == 403
    ok = client.post("/api/rooms/r1/queue", headers={"X-Floor-Code": "1234"})
    assert ok.status_code == 200 and ok.json()["status"] == "waiting"


def test_the_full_line_through_the_api(api):
    client = api["client"]
    post_event(api, "s1", "session.start")
    post_event(api, "s3", "session.start")
    client.post("/api/rooms/r1/queue", headers={"X-Floor-Code": "1234"})

    post_event(api, "s3", "session.end")  # simulated stall: nobody is called
    assert not sent(api, "queue.go")

    post_event(api, "s1", "session.end")  # real stall: the phone hears "go!"
    assert [m["topic"] for m in sent(api, "queue.go")] == [f"browser:{PHONE}"]

    me = client.get("/api/rooms/r1/me").json()
    assert me["queue"]["status"] == "called" and me["queue"]["stall_name"] == "Stall 1"
    assert client.delete(f"/api/queue/{me['queue']['id']}").json() == {"status": "left"}


def test_notify_goes_to_the_real_stall_and_a_hand_hold_answers_it(api):
    client = api["client"]
    post_event(api, "s3", "session.start")
    post_event(api, "s1", "session.start")
    result = client.post("/api/rooms/r1/notify", headers={"X-Floor-Code": "1234"}).json()
    assert result["status"] == "sent" and result["stall_id"] == "s1"
    assert [m["topic"] for m in sent(api, "notify.deliver")] == ["device:s1"]

    post_event(api, "s1", "input.hold")
    assert [m["payload"]["status"] for m in sent(api, "notify.receipt")] == ["sent", "acknowledged"]


def test_tick_needs_the_cron_secret(api):
    client = api["client"]
    assert client.post("/api/tick").status_code == 401
    body = client.post("/api/tick", headers={"X-Cron-Secret": "test-cron"}).json()
    assert {"called", "sent"} <= body.keys()


def test_failed_broadcast_is_resent_by_the_next_tick(api, monkeypatch):
    def down(_messages):
        raise RuntimeError("Realtime is down")

    monkeypatch.setattr(realtime, "broadcast", down)
    assert post_event(api, "s1", "session.start").status_code == 200  # the request still succeeds

    monkeypatch.setattr(realtime, "broadcast", lambda messages: api["sent"].extend(messages))
    api["client"].post("/api/tick", headers={"X-Cron-Secret": "test-cron"})
    assert sent(api, "stall.changed")
