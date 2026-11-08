"""The rules in tiger/migrations (the team's 001-002, plus 003-005), tested in SQL.

Room r1 from the seed: s1 is the real stall, s2 to s5 are simulated.
"""

import uuid

import psycopg
import pytest

A, B = "phone-a", "phone-b"


def set_state(conn, stall, state, minutes_ago=0):
    conn.execute("update stalls set state = %s, since = now() - make_interval(mins => %s) where id = %s",
                 [state, minutes_ago, stall])


def event(conn, stall, event_type, event_id=None, payload="{}"):
    return conn.execute("select record_device_event(%s, %s, now(), %s, %s::jsonb) as ok",
                        [stall, event_id or uuid.uuid4(), event_type, payload]).fetchone()["ok"]


def outbox(conn, event_name=None):
    rows = conn.execute("select topic, event, payload from outbox order by id").fetchall()
    return [r for r in rows if event_name is None or r["event"] == event_name]


def queue_status(conn, visitor):
    return conn.execute("select status, called_stall_id from queue where visitor_id = %s order by id desc limit 1",
                        [visitor]).fetchone()


def pick(conn):
    return conn.execute("select pick_notify_stall() as id").fetchone()["id"]


# 003: Notify prefers real stalls -------------------------------------------------------


def test_notify_picks_the_real_stall_even_when_a_simulated_one_has_been_in_use_longer(conn, room):
    set_state(conn, "s1", "in_use", 15)
    set_state(conn, "s3", "in_use", 45)
    assert pick(conn) == "s1"


def test_notify_falls_back_to_the_longest_simulated_session(conn, room):
    set_state(conn, "s3", "in_use", 20)
    set_state(conn, "s4", "finishing", 45)
    assert pick(conn) == "s4"


def test_notify_skips_sessions_shorter_than_the_setting(conn, room):
    set_state(conn, "s1", "in_use", 5)
    assert pick(conn) is None


# 003: only real stalls send "go!" ---------------------------------------------------------


def test_simulated_stall_freeing_up_calls_nobody(conn, room):
    set_state(conn, "s3", "in_use", 10)
    conn.execute("select join_queue(%s)", [A])
    event(conn, "s3", "session.end")
    assert queue_status(conn, A)["status"] == "waiting"
    assert outbox(conn, "queue.go") == []


def test_real_stall_freeing_up_calls_exactly_one_person(conn, room):
    set_state(conn, "s1", "in_use", 10)
    conn.execute("select join_queue(%s)", [A])
    conn.execute("select join_queue(%s)", [B])
    event(conn, "s1", "session.end")

    assert queue_status(conn, A) == {"status": "called", "called_stall_id": "s1"}
    assert queue_status(conn, B)["status"] == "waiting"
    assert [m["topic"] for m in outbox(conn, "queue.go")] == [f"browser:{A}"]
    assert [m["topic"] for m in outbox(conn, "stall.reserved")] == ["device:s1"]


# 005: the line ---------------------------------------------------------------------


def test_joining_twice_keeps_one_place(conn, room):
    first = conn.execute("select join_queue(%s) as r", [A]).fetchone()["r"]
    again = conn.execute("select join_queue(%s) as r", [A]).fetchone()["r"]
    assert first["queue_id"] == again["queue_id"] and first["position"] == 1


def test_a_passed_turn_goes_to_the_next_person_on_the_tick(conn, room):
    set_state(conn, "s1", "in_use", 10)
    conn.execute("select join_queue(%s)", [A])
    conn.execute("select join_queue(%s)", [B])
    event(conn, "s1", "session.end")
    queue_id = conn.execute("select id from queue where visitor_id = %s", [A]).fetchone()["id"]
    assert conn.execute("select leave_queue(%s, %s) as ok", [A, queue_id]).fetchone()["ok"]

    result = conn.execute("select api_tick() as r").fetchone()["r"]
    assert result["called"] == 1
    assert queue_status(conn, B) == {"status": "called", "called_stall_id": "s1"}


def test_an_unclaimed_turn_expires_and_passes_on(conn, room):
    set_state(conn, "s1", "in_use", 10)
    conn.execute("select join_queue(%s)", [A])
    conn.execute("select join_queue(%s)", [B])
    event(conn, "s1", "session.end")
    conn.execute("update queue set called_at = now() - interval '3 minutes' where visitor_id = %s", [A])

    conn.execute("select api_tick()")
    assert queue_status(conn, A)["status"] == "expired"
    assert queue_status(conn, B)["status"] == "called"
    assert [m["topic"] for m in outbox(conn, "queue.expired")] == [f"browser:{A}"]


# 005: Notify -----------------------------------------------------------------------


def test_notify_reaches_the_stall_and_has_a_cooldown(conn, room):
    set_state(conn, "s1", "in_use", 30)
    first = conn.execute("select request_notify(%s) as r", [A]).fetchone()["r"]
    assert first["status"] == "sent" and first["stall_id"] == "s1"
    assert [m["topic"] for m in outbox(conn, "notify.deliver")] == ["device:s1"]
    assert conn.execute("select request_notify(%s) as r", [A]).fetchone()["r"]["status"] == "cooldown"


def test_hand_hold_acknowledges_the_pop_up(conn, room):
    set_state(conn, "s1", "in_use", 30)
    conn.execute("select request_notify(%s)", [A])
    event(conn, "s1", "notify.delivered")
    event(conn, "s1", "input.hold")

    status = conn.execute("select status from notifies where requester_id = %s", [A]).fetchone()["status"]
    assert status == "acknowledged"
    receipts = [m["payload"]["status"] for m in outbox(conn, "notify.receipt") if m["topic"] == f"browser:{A}"]
    assert receipts == ["sent", "delivered", "acknowledged"]
    assert [m["topic"] for m in outbox(conn, "notify.closed")] == ["device:s1"]


# Device events and live updates -------------------------------------------------------------


def test_replayed_event_is_applied_once(conn, room):
    event_id = uuid.uuid4()
    assert event(conn, "s1", "session.start", event_id) is True
    assert event(conn, "s1", "session.start", event_id) is False


def test_state_change_goes_to_the_room_topic_and_history(conn, room):
    event(conn, "s1", "session.start")
    changed = outbox(conn, "stall.changed")
    assert [(m["topic"], m["payload"]["id"], m["payload"]["state"]) for m in changed] == [("room:r1", "s1", "in_use")]
    logged = conn.execute("select count(*) as n from stall_events where stall_id = 's1'"
                          " and state = 'in_use' and prev_state = 'free' and time = now()").fetchone()["n"]
    assert logged == 1


def test_a_touch_that_changes_nothing_visible_sends_nothing(conn, room):
    conn.execute("update stalls set name = name where id = 's1'")
    assert outbox(conn, "stall.changed") == []


def test_sensor_readings_and_heartbeats_are_stored(conn, room):
    event(conn, "s1", "reading", payload='{"presence": true, "distance_cm": 42.5, "water": true}')
    event(conn, "s1", "heartbeat", payload='{"cpu_temp": 51.2, "radar_ok": true}')
    reading = conn.execute("select presence, water from sensor_readings where stall_id = 's1' order by time desc limit 1").fetchone()
    device = conn.execute("select radar_ok, last_seen is not null as seen from devices where stall_id = 's1'").fetchone()
    assert reading == {"presence": True, "water": True}
    assert device == {"radar_ok": True, "seen": True}


def test_rolled_back_work_leaves_nothing_to_send(conn, room):
    conn.execute("select join_queue(%s)", [A])
    before = len(outbox(conn))
    try:
        with conn.transaction():
            event(conn, "s1", "session.start")
            raise RuntimeError("function died before commit")
    except RuntimeError:
        pass
    assert len(outbox(conn)) == before


def test_safety_alert_goes_only_to_the_admin_topic(conn, room):
    event(conn, "s1", "safety.alert")
    assert [m["topic"] for m in outbox(conn)] == ["admin:r1"]


def test_unknown_event_type_is_rejected(conn, room):
    with pytest.raises(psycopg.errors.RaiseException):
        with conn.transaction():
            event(conn, "s1", "dance")


# The team's simulator, driven by the tick ------------------------------------------------------


def test_tick_runs_the_simulator_only_when_it_is_on(conn, room):
    set_state(conn, "s2", "free", 2)
    for _ in range(5):
        conn.execute("select api_tick()")
    assert conn.execute("select state from stalls where id = 's2'").fetchone()["state"] == "free"

    conn.execute("update settings set simulator_on = true")
    for _ in range(40):  # 35% chance per tick; 40 misses in a row is about 1 in 30 million
        conn.execute("select api_tick()")
    assert conn.execute("select state from stalls where id = 's2'").fetchone()["state"] == "in_use"
    assert conn.execute("select state from stalls where id = 's1'").fetchone()["state"] == "free"  # real stall untouched
