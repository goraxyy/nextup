"""The API end to end against the local database, with Realtime captured in memory.

These tests commit, so the fixture resets room r1 (s1 real, s2 to s5 simulated) first.
"""

import datetime as dt
import json
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
        conn.execute("update settings set simulator_on = false, notify_min_session_minutes = 0,"
                     " notify_requester_cooldown_minutes = 2, sim_gap_override_minutes = null")
        conn.execute("delete from campus_events")
        conn.execute("delete from app_state")
        conn.execute("delete from notifies")
        conn.execute("delete from queue")
        conn.execute("update stalls set state = 'free', person = false, water = false")
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


def test_the_line_needs_no_floor_code(api):
    ok = api["client"].post("/api/rooms/r1/queue")
    assert ok.status_code == 200 and ok.json()["status"] == "waiting"
    assert "needs_code" not in api["client"].get("/api/rooms/r1").json()


def test_the_full_line_through_the_api(api):
    client = api["client"]
    post_event(api, "s1", "session.start")
    post_event(api, "s3", "session.start")
    client.post("/api/rooms/r1/queue")

    post_event(api, "s3", "session.end")  # simulated stall: nobody is called
    assert not sent(api, "queue.go")

    post_event(api, "s1", "session.end")  # real stall: the phone hears "go!"
    assert [m["topic"] for m in sent(api, "queue.go")] == [f"browser:{PHONE}"]

    me = client.get("/api/rooms/r1/me").json()
    assert me["queue"]["status"] == "called" and me["queue"]["stall_name"] == "Stall 1"
    assert client.delete(f"/api/queue/{me['queue']['id']}").json() == {"status": "left"}


def test_hurry_reaches_the_chosen_stall_and_a_hand_hold_answers_it(api):
    client = api["client"]
    post_event(api, "s3", "session.start")
    post_event(api, "s1", "session.start")
    result = client.post("/api/rooms/r1/stalls/s1/hurry").json()
    assert result["status"] == "sent" and result["stall_id"] == "s1"
    assert [m["topic"] for m in sent(api, "notify.deliver")] == ["device:s1"]
    assert [m["topic"] for m in sent(api, "hurry.changed")] == ["room:r1"]

    room = client.get("/api/rooms/r1").json()
    assert {s["id"]: (s["hurry"] or {}).get("status") for s in room["stalls"]}["s1"] == "sent"
    me = client.get("/api/rooms/r1/me").json()
    assert [(h["stall_id"], h["status"]) for h in me["hurries"]] == [("s1", "sent")]
    assert me["hurry_cooldown_until"] is not None

    post_event(api, "s1", "input.hold")
    assert [m["payload"]["status"] for m in sent(api, "notify.receipt")] == ["sent", "acknowledged"]


def test_hurry_explains_why_not(api):
    client = api["client"]
    assert client.post("/api/rooms/r1/stalls/s2/hurry").json()["status"] == "free"
    assert client.post("/api/rooms/r1/stalls/nope/hurry").status_code == 404
    post_event(api, "s1", "session.start")
    post_event(api, "s3", "session.start")
    assert client.post("/api/rooms/r1/stalls/s1/hurry").json()["status"] == "sent"
    again = client.post("/api/rooms/r1/stalls/s3/hurry").json()
    assert again["status"] == "cooldown" and 0 < again["retry_in_s"] <= 120


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


def test_device_state_shows_the_open_pop_up_and_reservation(api):
    client = api["client"]
    post_event(api, "s1", "session.start")
    client.post("/api/rooms/r1/stalls/s1/hurry")
    state = client.get("/api/device/state", headers={"Authorization": f"Bearer {TOKENS['s1']}"}).json()
    assert state["stall"]["state"] == "in_use"
    assert state["notify"]["status"] == "sent" and state["reserved"] is None


def test_device_link_stamps_the_stall_on_the_supabase_user(api, monkeypatch):
    from backend import app as app_module

    stamped = {}
    monkeypatch.setattr(app_module, "verify_supabase_token", lambda token: {"sub": "user-1", "app_metadata": {}})
    monkeypatch.setattr(app_module.supabase_admin, "set_device_claim", lambda uid, stall: stamped.update({uid: stall}))
    body = api["client"].post("/api/device/link", json={"access_token": "jwt"},
                              headers={"Authorization": f"Bearer {TOKENS['s1']}"}).json()
    assert body["topic"] == "device:s1" and stamped == {"user-1": "s1"}


def test_device_link_needs_a_device_token(api):
    assert api["client"].post("/api/device/link", json={"access_token": "jwt"}).status_code == 401


FEED = {"bwEventList": {"events": [
    {"summary": "Pizza &amp; Pitch Night", "status": "CONFIRMED", "guid": "g1", "recurrenceId": "",
     "start": {"allday": "false", "utcdate": "{s1}"}, "end": {"allday": "false", "utcdate": "{e1}"},
     "location": {"address": "Lerner Hall, 2920 Broadway"}, "eventlink": "https://events.columbia.edu/e/1",
     "description": "Free pizza for everyone"},
    {"summary": "All Year Thing", "status": "CONFIRMED", "guid": "g2", "recurrenceId": "",
     "start": {"allday": "false", "utcdate": "{s1}"}, "end": {"allday": "false", "utcdate": "20991231T000000Z"},
     "location": {}, "eventlink": "https://events.columbia.edu/e/2"},
    {"summary": "Cancelled Talk", "status": "CANCELLED", "guid": "g3", "recurrenceId": "",
     "start": {"allday": "false", "utcdate": "{s1}"}, "end": {"allday": "false", "utcdate": "{e1}"},
     "location": {}, "eventlink": "https://events.columbia.edu/e/3"},
]}}


def fake_feed():
    start = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
    text = json.dumps(FEED).replace("{s1}", start.strftime("%Y%m%dT%H%M%SZ"))
    return json.loads(text.replace("{e1}", (start + dt.timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ")))


def test_the_tick_stores_campus_events(api, monkeypatch):
    from backend import events

    monkeypatch.setattr(events, "fetch", fake_feed)
    assert api["client"].post("/api/tick", headers={"X-Cron-Secret": "test-cron"}).json()["events"] == 1
    assert api["client"].post("/api/tick", headers={"X-Cron-Secret": "test-cron"}).json()["events"] is None  # fresh

    listed = api["client"].get("/api/rooms/r1/events").json()["events"]
    assert [(e["title"], e["location"], "free food" in e["tags"]) for e in listed] == [("Pizza & Pitch Night", "Lerner Hall", True)]

    screen = api["client"].get("/api/device/screen", headers={"Authorization": f"Bearer {TOKENS['s1']}"}).json()
    assert screen["stall"]["id"] == "s1" and screen["events"] == []  # the screen only shows events with pictures


def test_a_broken_events_feed_does_not_break_the_tick(api, monkeypatch):
    from backend import events

    def broken():
        raise RuntimeError("feed down")

    monkeypatch.setattr(events, "fetch", broken)
    body = api["client"].post("/api/tick", headers={"X-Cron-Secret": "test-cron"}).json()
    assert body["events"] == "error: RuntimeError" and "called" in body


# 007: insights, Apple Music now playing, event images -------------------------------------


def test_insights_have_a_profile_best_times_and_a_wait_estimate(api):
    post_event(api, "s1", "session.start")
    body = api["client"].get("/api/rooms/r1/insights").json()
    assert len(body["heatmap"]) == 7 and all(len(day) == 24 for day in body["heatmap"])
    assert len(body["today"]) == 24 and 0 <= body["current_hour"] <= 23
    assert 1 <= len(body["best_times"]) <= 3 and all("label" in b for b in body["best_times"])
    assert body["now"]["in_use"] >= 1 and body["now"]["wait_minutes"] >= 1  # the only real stall is busy


def test_now_playing_needs_the_audio_token_and_broadcasts_with_lyrics(api, monkeypatch):
    from backend import music

    with db.pool().connection() as conn:
        conn.execute("update rooms set audio_token_hash = %s where id = 'r1'", [auth.hash_secret("audio-1")])
        conn.execute("delete from now_playing")
        conn.execute("delete from lyrics")
    monkeypatch.setattr(music, "fetch_lyrics", lambda *a, **k: [[0.5, "First line"], [4.0, "Second line"]])
    song = {"title": "Song A", "artist": "Band", "album": "LP", "duration_s": 200, "position_s": 30, "playing": True}

    assert api["client"].post("/api/rooms/r1/nowplaying", json=song).status_code == 401
    ok = api["client"].post("/api/rooms/r1/nowplaying", json=song, headers={"X-Audio-Token": "audio-1"})
    assert ok.json()["status"] == "updated"
    topics = [m["topic"] for m in sent(api, "nowplaying.changed")]
    assert "room:r1" in topics and "device:s1" in topics

    same = api["client"].post("/api/rooms/r1/nowplaying", json={**song, "position_s": 30.2},
                              headers={"X-Audio-Token": "audio-1"}).json()
    assert same["status"] == "unchanged"  # small jitter isn't a change

    current = api["client"].get("/api/rooms/r1/nowplaying").json()
    assert current["now_playing"]["title"] == "Song A" and current["lyrics"][1] == [4.0, "Second line"]
    screen = api["client"].get("/api/device/nowplaying", headers={"Authorization": f"Bearer {TOKENS['s1']}"}).json()
    assert screen["now_playing"]["playing"] is True


def test_lrc_parsing():
    from backend.music import parse_lrc

    assert parse_lrc("[00:30.64] Like the legend\n[01:02.10]Of the phoenix\nnot a line") == [
        [30.64, "Like the legend"], [62.1, "Of the phoenix"]]


def test_events_keep_their_images_and_the_screen_shows_only_events_with_pictures(api, monkeypatch):
    from backend import events

    feed = fake_feed()
    feed["bwEventList"]["events"][0]["xproperties"] = [
        {"X-BEDEWORK-IMAGE": {"values": {"text": "/public/images/Pizza Night (1).jpg"}}}]
    feed["bwEventList"]["events"].append({**feed["bwEventList"]["events"][0], "guid": "g9", "summary": "No Picture Talk",
                                          "xproperties": []})
    monkeypatch.setattr(events, "fetch", lambda: feed)
    api["client"].post("/api/tick", headers={"X-Cron-Secret": "test-cron"})
    listed = {e["title"]: e for e in api["client"].get("/api/rooms/r1/events").json()["events"]}
    assert listed["Pizza & Pitch Night"]["image_url"] == \
        "https://events.columbia.edu/pubcaldav/public/images/Pizza%20Night%20(1).jpg"
    assert listed["Pizza & Pitch Night"]["blurb"] == "Free pizza for everyone"
    screen = api["client"].get("/api/device/screen", headers={"Authorization": f"Bearer {TOKENS['s1']}"}).json()
    assert [e["title"] for e in screen["events"]] == ["Pizza & Pitch Night"]
    assert "qr_svg" not in screen["events"][0]
    assert {"now", "today", "best_times"} <= screen["insights"].keys()
