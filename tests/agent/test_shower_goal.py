"""Experimental "How long will your shower be?" chosen with a raised hand. No hardware."""

import pytest

from device.nextup_agent.config import load
from device.nextup_agent.sensor import Reading
from device.nextup_agent.shower_goal import GoalChooser

ENV = {"NEXTUP_API": "http://api.test", "NEXTUP_STALL": "s1", "NEXTUP_DEVICE_TOKEN": "tok",
       "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "pk",
       "PRESENCE_SENSOR": "ld2410c", "ULTRASONIC": "off"}


def run(chooser, pattern, start=0.0, step=0.1):
    """pattern: [(seconds, near)] → every screen state shown, in order."""
    t, shown = start, []
    for seconds, near in pattern:
        end = t + seconds
        while t < end - 1e-9:
            out = chooser.update(near, t)
            shown += [out] if out else []
            t += step
    return shown


def chosen(shown):
    return next((s["chosen"] for s in shown if s.get("chosen")), None)


@pytest.mark.parametrize("hold_s, minutes", [(1.5, 5), (2.6, 10), (3.8, 15), (5.0, 5)])  # 5 s: back round to Quick
def test_hold_watch_release_picks_the_option_that_was_lit(hold_s, minutes):
    c = GoalChooser((5, 10, 15), ask_s=15)
    c.start(0.0, wall=1000.0)
    shown = run(c, [(1, False), (hold_s, True), (2, False)])
    assert chosen(shown) == minutes and c.goal == minutes and not c.asking


def test_the_highlight_walks_along_the_options_while_the_hand_is_up():
    c = GoalChooser((5, 10, 15), ask_s=15)
    c.start(0.0, wall=1000.0)
    lit = [s["highlight"] for s in run(c, [(4.4, True)]) if "highlight" in s]
    assert lit[0] == -1 and [h for i, h in enumerate(lit) if h >= 0 and (i == 0 or lit[i - 1] != h)] == [0, 1, 2]


def test_a_short_raise_picks_nothing_and_radar_flicker_does_not_end_the_hold():
    c = GoalChooser((5, 10, 15), ask_s=15)
    c.start(0.0, wall=1000.0)
    assert chosen(run(c, [(0.6, True), (2, False)])) is None and c.asking      # under the 1 s arm time
    shown = run(c, [(1.5, True), (0.5, False), (1.0, True), (2, False)], start=3.0)  # a 0.5 s gap mid-hold
    assert chosen(shown) == 10                                                   # one 3 s hold: Normal


def test_no_answer_skips_quietly():
    c = GoalChooser((5, 10, 15), ask_s=15)
    c.start(0.0, wall=1000.0)
    shown = run(c, [(16, False)])
    assert shown[-1] == {"skipped": True} and not c.asking and c.goal is None


def test_the_question_comes_with_the_shower_and_the_goal_reaches_the_celebration(tmp_path):
    import datetime as dt

    from device.nextup_agent.agent import Agent

    agent = Agent(load({**ENV, "NEXTUP_DATA_DIR": str(tmp_path)}))
    t = 0.0
    while "goal_ask" not in agent.kiosk.state or agent.kiosk.state["goal_ask"] is None:  # someone steps in
        agent.on_reading(Reading(80, present=True, hold_near=False), t)
        t += 0.1
        assert t < 30
    assert agent.kiosk.state["goal_ask"]["options"] == [5, 10, 15] and agent.goal.asking
    for _ in range(26):                                                          # hand up 2.6 s: Normal lit
        agent.on_reading(Reading(12, present=True, hold_near=True), t)
        t += 0.1
    for _ in range(12):
        agent.on_reading(Reading(80, present=True, hold_near=False), t)
        t += 0.1
    assert agent.kiosk.state["shower_goal"] == 10 and agent.kiosk.state["goal_ask"]["chosen"] == 10
    agent.kiosk.publish(since=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=9)).isoformat())
    agent.celebrate()
    assert agent.kiosk.state["celebrate"]["goal"] == 10


def test_the_question_can_be_turned_off_and_its_options_set(tmp_path):
    from device.nextup_agent.agent import Agent

    assert Agent(load({**ENV, "NEXTUP_DATA_DIR": str(tmp_path), "GOAL_ASK_SECONDS": "0"})).goal is None
    assert Agent(load({**ENV, "NEXTUP_DATA_DIR": str(tmp_path), "RADAR_HOLD": "off"})).goal is None  # no way to answer
    assert load({**ENV, "GOAL_OPTIONS": "4,8"}).goal_options == (4, 8)
    with pytest.raises(SystemExit, match="GOAL_OPTIONS"):
        load({**ENV, "GOAL_OPTIONS": "5,10,15,20,25"})
