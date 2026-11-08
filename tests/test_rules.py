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
    conn.execute("update settings set sim_gap_override_minutes = 0.001")  # would start every free stall
    conn.execute("update settings set sim_last_run = now() - interval '60 seconds'")
    conn.execute("select api_tick()")
    assert conn.execute("select count(*) as n from stalls where state <> 'free'").fetchone()["n"] == 0  # off

    conn.execute("update settings set simulator_on = true, sim_last_run = now() - interval '60 seconds'")
    conn.execute("select api_tick()")
    states = dict(conn.execute("select id, state from stalls").fetchall() and
                  [(r["id"], r["state"]) for r in conn.execute("select id, state from stalls").fetchall()])
    assert states["s1"] == "free"  # the real stall is never simulated
    assert all(states[s] == "in_use" for s in ("s2", "s3", "s4", "s5"))


# 006: no floor code, Hurry on a chosen stall, the simulator ---------------------------------


def test_rooms_have_no_floor_code(conn, room):
    assert conn.execute("select join_code_hash from rooms where id = 'r1'").fetchone()["join_code_hash"] is None


def hurry(conn, phone, stall):
    return conn.execute("select request_hurry(%s, %s) as r", [phone, stall]).fetchone()["r"]


def test_hurry_reaches_the_chosen_stall_with_the_line_length(conn, room):
    set_state(conn, "s1", "in_use", 12)
    set_state(conn, "s2", "in_use", 30)  # longer, but not the one chosen
    conn.execute("select join_queue('someone-waiting')")
    result = hurry(conn, A, "s1")
    assert result["status"] == "sent" and result["stall_id"] == "s1"
    deliver = outbox(conn, "notify.deliver")
    assert [(m["topic"], m["payload"]["waiting"]) for m in deliver] == [("device:s1", 1)]
    assert [m["payload"]["stall_id"] for m in outbox(conn, "hurry.changed")] == ["s1"]


def test_hurry_rules(conn, room):
    assert hurry(conn, A, "s1")["status"] == "free"
    assert hurry(conn, A, "nope")["status"] == "unknown_stall"
    set_state(conn, "s1", "in_use", 2)
    assert hurry(conn, A, "s1")["status"] == "too_soon"  # under notify_min_session_minutes (10)
    set_state(conn, "s1", "in_use", 12)
    set_state(conn, "s3", "in_use", 12)
    assert hurry(conn, A, "s1")["status"] == "sent"
    assert hurry(conn, B, "s1")["status"] == "open"           # one open Hurry per stall
    assert hurry(conn, A, "s3")["status"] == "cooldown"       # this phone just hurried
    conn.execute("update notifies set status = 'expired', created_at = now() - interval '3 minutes'")
    assert hurry(conn, B, "s1")["status"] == "stall_cooldown"  # stall hurried 3 min ago (cooldown 5)
    assert hurry(conn, A, "s3")["status"] == "sent"            # phone cooldown (2 min) is over


def test_sim_gap_follows_the_new_york_clock(conn):
    gaps = {hour: conn.execute("select sim_gap_minutes(%s::timestamptz) as g",
                               [f"2026-11-09 {hour}:30 America/New_York"]).fetchone()["g"]
            for hour in ("03", "08", "13", "19", "22")}
    assert gaps == {"03": 60, "08": 2, "13": 25, "19": 10, "22": 3}


def run_simulator(conn, seconds_since_last=60):
    conn.execute("update settings set sim_last_run = now() - make_interval(secs => %s)", [seconds_since_last])
    conn.execute("select simulate_stalls()")


def test_simulated_showers_last_10_to_20_minutes_and_finish(conn, room):
    conn.execute("update settings set simulator_on = true, sim_gap_override_minutes = 0.001")  # start at once
    run_simulator(conn)
    rows = conn.execute("select id, state, extract(epoch from timer_end - now()) / 60 as minutes"
                        " from stalls where simulated order by id").fetchall()
    assert all(r["state"] == "in_use" and 10 <= r["minutes"] <= 20 for r in rows)
    assert conn.execute("select state from stalls where id = 's1'").fetchone()["state"] == "free"  # real stall untouched

    conn.execute("update stalls set timer_end = now() + interval '30 seconds' where id = 's2'")
    run_simulator(conn)
    assert conn.execute("select state, water from stalls where id = 's2'").fetchone() == {"state": "finishing", "water": False}
    conn.execute("update stalls set timer_end = now() - interval '1 second' where id = 's2'")
    run_simulator(conn)
    assert conn.execute("select state from stalls where id = 's2'").fetchone()["state"] == "free"


def test_the_simulator_pace_does_not_depend_on_how_often_it_runs(conn, room):
    conn.execute("update settings set simulator_on = true, sim_gap_override_minutes = 0.001")
    run_simulator(conn, seconds_since_last=0)  # called again straight away: no time has passed
    assert conn.execute("select count(*) as n from stalls where simulated and state <> 'free'").fetchone()["n"] == 0


def test_simulated_stalls_answer_a_hurry(conn, room):
    conn.execute("update settings set simulator_on = true")
    set_state(conn, "s2", "in_use", 12)
    conn.execute("update stalls set timer_end = now() + interval '8 minutes' where id = 's2'")
    notify_id = hurry(conn, A, "s2")["notify_id"]
    run_simulator(conn)
    assert conn.execute("select status from notifies where id = %s", [notify_id]).fetchone()["status"] == "delivered"

    conn.execute("update notifies set created_at = now() - interval '41 seconds' where id = %s", [notify_id])
    run_simulator(conn)
    status = conn.execute("select status from notifies where id = %s", [notify_id]).fetchone()["status"]
    if (notify_id * 7919) % 10 < 7:
        assert status == "acknowledged"
        left = conn.execute("select extract(epoch from timer_end - now()) as s from stalls where id = 's2'").fetchone()["s"]
        assert left <= 120  # wraps up within 2 minutes
    else:
        assert status == "delivered"  # this one lets it expire


def test_water_is_minutes_with_the_water_on_times_the_flow_rate(conn, room):
    from backend import insights

    conn.execute("update settings set water_gpm = 2.0")
    conn.execute("update stalls set state = 'in_use', since = now() - interval '5 minutes' where id = 's3'")
    conn.execute("update stalls set state = 'in_use', since = now() - interval '20 seconds' where id = 's4'")
    conn.execute("delete from stall_events")
    # Today: one 10-minute shower, then 2 minutes finishing (water off), and a 30-second blip.
    conn.execute("insert into stall_events (time, stall_id, state, prev_state, prev_duration_s) values"
                 " (now() - interval '4 seconds', 's2', 'in_use', 'free', 3600),"
                 " (now() - interval '3 seconds', 's2', 'finishing', 'in_use', 600),"
                 " (now() - interval '2 seconds', 's2', 'free', 'finishing', 120),"
                 " (now() - interval '1 second', 's5', 'free', 'in_use', 30)")
    now = conn.execute("select now() as now").fetchone()["now"]
    midnight = now.astimezone(insights.NY).replace(hour=0, minute=0, second=0, microsecond=0)
    live = min(5.0, (now - midnight).total_seconds() / 60)   # s3 has had the water on for 5 minutes

    body = insights.room_insights(conn, room)
    water, stats = body["water"], body["stats"]
    assert water["gpm"] == 2.0 and water["lpm"] == 7.6
    assert water["today_gallons"] == round((10 + live) * 2.0)      # not the blip, nor s4's 20 seconds
    assert water["per_shower_gallons"] == 20.0
    assert (water["running_now"], water["flowing_now_gpm"]) == (2, 4.0)   # flowing now is the live reading
    assert water["daily"][-1] == {"day": midnight.date().isoformat(), "gallons": water["today_gallons"], "showers": 2}
    assert stats["showers_today"] == 2                             # s2's finished shower and s3's running one
    assert stats["avg_shower_minutes"] == 12.0                     # 10 minutes in use + 2 finishing


def test_event_text_from_the_feed_is_unescaped_once_more():
    import datetime as dt

    from backend import events

    start = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    stamp = start.strftime("%Y%m%dT%H%M%SZ")
    feed = {"bwEventList": {"events": [{
        "summary": "Modeling &amp;amp; Valuation", "status": "CONFIRMED", "guid": "g", "recurrenceId": "",
        "start": {"allday": "false", "utcdate": stamp}, "end": {"allday": "false", "utcdate": stamp},
        "location": {}, "description": "<p>Valuation &amp;amp; Interview Prep</p>"}]}}
    [event] = events.parse(feed, dt.datetime.now(dt.timezone.utc))
    assert (event["title"], event["blurb"]) == ("Modeling & Valuation", "Valuation & Interview Prep")
