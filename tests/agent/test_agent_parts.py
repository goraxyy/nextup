"""Presence, hand-hold and the offline queue from HC-SR04 distances: no hardware needed."""

import asyncio
import json

import httpx
import pytest

from device.nextup_agent.config import load
from device.nextup_agent.presence import Presence
from device.nextup_agent.sensor import Reading
from device.nextup_agent.uplink import Uplink

ENV = {"NEXTUP_API": "http://api.test", "NEXTUP_STALL": "s1", "NEXTUP_DEVICE_TOKEN": "tok",
       "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "pk"}

PERSON = Reading(70.0)     # someone in the stall
NOBODY = Reading(None)     # no echo: empty stall
WALL = Reading(180.0)      # the empty stall's far wall, beyond PRESENT_MAX_CM (120)
HAND = Reading(8.0)        # a hand held up near the sensor


def run(presence, reading, start, end, step=0.5):
    out, t = [], start
    while t <= end:
        out += [kind for kind, _ in presence.update(reading, t)]
        t += step
    return out


def test_settings_have_sensible_defaults():
    cfg = load(ENV)
    assert (cfg.trigger_pin, cfg.echo_pin, cfg.present_max_cm, cfg.hold_max_cm) == (23, 24, 120, 15)
    assert load({**ENV, "PRESENT_MAX_CM": "95", "ECHO_PIN": "27"}).present_max_cm == 95


def test_missing_settings_stop_the_agent_with_a_clear_message():
    with pytest.raises(SystemExit, match="NEXTUP_DEVICE_TOKEN"):
        load({k: v for k, v in ENV.items() if k != "NEXTUP_DEVICE_TOKEN"})


def test_in_use_after_20_seconds_and_free_after_30():
    p = Presence(load(ENV))
    assert "session.start" not in run(p, PERSON, 0, 19)
    assert run(p, PERSON, 19.5, 21).count("session.start") == 1
    assert "session.end" not in run(p, NOBODY, 22, 51)
    assert run(p, NOBODY, 51.5, 53).count("session.end") == 1


def test_a_short_absence_does_not_end_the_session():
    p = Presence(load(ENV))
    run(p, PERSON, 0, 21)
    run(p, NOBODY, 21.5, 40)
    assert "session.end" not in run(p, PERSON, 40.5, 100)


def test_the_empty_stalls_far_wall_is_not_a_person():
    assert "session.start" not in run(Presence(load(ENV)), WALL, 0, 60)


def test_hand_hold_fires_once_after_two_seconds_with_progress():
    p = Presence(load(ENV))
    events, t = [], 0.0
    while t <= 4:
        events += p.update(HAND, t)
        t += 0.1
    assert [k for k, _ in events].count("input.hold") == 1
    progress = [v for k, v in events if k == "hold.progress"]
    assert progress == sorted(progress) and progress[-1] == 1.0
    assert p.update(PERSON, 4.1) == []                         # one stray reading: still holding
    assert p.update(PERSON, 4.5) == [("hold.progress", 0.0)]   # hand down: reset


def test_a_stray_reading_does_not_break_a_hold():
    p = Presence(load(ENV))
    events, t = [], 0.0
    while t <= 2.6:
        events += p.update(PERSON if abs(t - 1.0) < 0.05 else HAND, t)  # one far reading at 1 s
        t += 0.1
    assert [k for k, _ in events].count("input.hold") == 1


def test_server_state_is_adopted_after_a_restart():
    p = Presence(load(ENV))
    p.force("in_use")
    assert run(p, NOBODY, 0, 31).count("session.end") == 1


# Offline queue -------------------------------------------------------------------------


def test_queue_survives_a_restart_and_sends_in_one_batch(tmp_path):
    first = Uplink("http://api.test", "tok", tmp_path)
    first.enqueue("session.start")
    first.enqueue("reading", presence=True, distance_cm=70.0)
    second = Uplink("http://api.test", "tok", tmp_path)  # the agent restarted
    assert second.pending() == 2

    seen = []

    def handler(request):
        seen.append((request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(200, json={"applied": 2, "replayed": 0, "errors": []})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await second.send_once(client)

    assert asyncio.run(go()) == 2
    assert second.pending() == 0
    auth, body = seen[0]
    assert auth == "Bearer tok" and [e["type"] for e in body["events"]] == ["session.start", "reading"]
    assert len({e["event_id"] for e in body["events"]}) == 2


def test_queue_keeps_events_when_the_api_is_down(tmp_path):
    uplink = Uplink("http://api.test", "tok", tmp_path)
    uplink.enqueue("session.end")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
            await uplink.send_once(client)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())
    assert uplink.pending() == 1
