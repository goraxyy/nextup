"""LD2410C radar: frame parsing, the occupancy filter, and presence with the radar. No hardware needed."""

import json

import pytest

from device.nextup_agent.config import load
from device.nextup_agent.ld2410 import FrameParser, LD2410Sensor, OccupancyFilter, RadarFrame, nearest_cm, sees_someone
from device.nextup_agent.presence import Presence
from device.nextup_agent.sensor import Reading

ENV = {"NEXTUP_API": "http://api.test", "NEXTUP_STALL": "s1", "NEXTUP_DEVICE_TOKEN": "tok",
       "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "pk"}
RADAR_ENV = {**ENV, "PRESENCE_SENSOR": "ld2410c"}


def frame_bytes(state=3, moving_cm=80, moving_e=60, still_cm=90, still_e=50, kind=0x02) -> bytes:
    """A basic report frame as the LD2410C sends it."""
    data = bytes([kind, 0xAA, state, moving_cm & 0xFF, moving_cm >> 8, moving_e,
                  still_cm & 0xFF, still_cm >> 8, still_e, 90, 0, 0x55, 0x00])
    return bytes.fromhex("f4f3f2f1") + len(data).to_bytes(2, "little") + data + bytes.fromhex("f8f7f6f5")


# Frames ---------------------------------------------------------------------------


def test_parses_a_basic_report():
    # The LD2410 datasheet's example report
    raw = bytes.fromhex("f4 f3 f2 f1 0d 00 02 aa 02 51 00 00 00 00 3b 00 00 55 00 f8 f7 f6 f5".replace(" ", ""))
    assert FrameParser().feed(raw) == [RadarFrame(state=2, moving_cm=81, moving_energy=0, still_cm=0, still_energy=0x3B)]


def test_parses_frames_split_across_reads_with_junk_between():
    data = b"\x00\xf4\x12" + frame_bytes(moving_cm=300) + b"\xff\xf4\xf3" + frame_bytes(state=2, still_cm=120)
    parser, frames = FrameParser(), []
    for i in range(0, len(data), 5):  # serial reads arrive in small chunks
        frames += parser.feed(data[i:i + 5])
    assert [(f.moving_cm, f.still_cm) for f in frames] == [(300, 90), (80, 120)]
    assert frames[1].state == 2


def test_rejects_a_frame_with_a_bad_footer_and_recovers():
    bad = frame_bytes()[:-1] + b"\x00"
    assert FrameParser().feed(bad + frame_bytes(moving_cm=42))[-1].moving_cm == 42
    assert len(FrameParser().feed(bad)) == 0


def test_engineering_mode_frames_are_read_too():
    assert FrameParser().feed(frame_bytes(kind=0x01, moving_cm=55))[0].moving_cm == 55


def test_targets_right_in_front_of_the_radar_can_be_ignored():
    cover = RadarFrame(1, 20, 100, 0, 0)            # plastic cover flexing 20 cm away
    person = RadarFrame(3, 80, 60, 90, 50)
    assert sees_someone(cover, max_cm=150, min_energy=30)                   # counts by default
    assert not sees_someone(cover, max_cm=150, min_energy=30, min_cm=40)
    assert sees_someone(person, max_cm=150, min_energy=30, min_cm=40)
    assert load({**RADAR_ENV, "RADAR_MIN_CM": "40", "RADAR_DEBUG": "1"}).radar_min_cm == 40


def test_debug_logging_shows_what_the_radar_sees(caplog):
    s = LD2410Sensor(min_cm=40, debug=True)
    s.frame, s.frame_at = RadarFrame(1, 20, 100, 0, 0), 10.0
    with caplog.at_level("INFO", logger="nextup.ld2410"):
        s.sample(10.0)
    assert "moving   20 cm E100" in caplog.text and "counts no" in caplog.text


def test_a_target_must_be_close_and_strong_enough():
    assert sees_someone(RadarFrame(1, 100, 60, 0, 0), max_cm=150, min_energy=30)
    assert not sees_someone(RadarFrame(1, 200, 60, 0, 0), max_cm=150, min_energy=30)   # beyond the stall
    assert not sees_someone(RadarFrame(1, 100, 10, 0, 0), max_cm=150, min_energy=30)   # too weak
    assert sees_someone(RadarFrame(2, 0, 0, 120, 45), max_cm=150, min_energy=30)       # standing still
    assert not sees_someone(RadarFrame(0, 100, 60, 100, 60), max_cm=150, min_energy=30)  # state says no target
    assert nearest_cm(RadarFrame(3, 140, 60, 95, 40)) == 95 and nearest_cm(RadarFrame(0, 1, 1, 1, 1)) is None


# Occupancy filter (same numbers as the Arduino sketch) ------------------------------


def samples(f, raw, start, n):
    return [f.update(raw, start + i * 0.1) for i in range(n)]


def test_occupied_after_seven_samples_and_free_about_three_seconds_after_leaving():
    f = OccupancyFilter()
    assert samples(f, True, 0, 7) == [False] * 6 + [True]     # score 70 after 0.7 s
    samples(f, True, 0.7, 10)                                  # score tops out at 100
    out = samples(f, False, 2.0, 40)
    first_free = out.index(False)
    assert 30 <= first_free <= 35                              # ~2.3 s to fall to 10, then held 1 s


def test_brief_dropouts_keep_the_stall_occupied():
    f = OccupancyFilter()
    samples(f, True, 0, 20)
    for i in range(100):  # 10 s of a still person the radar sees 3 times out of 4
        assert f.update(i % 4 != 0, 2.0 + i * 0.1)


def test_sensor_waits_for_frames_and_goes_quiet_when_they_stop():
    s = LD2410Sensor()
    assert s.sample(10.0) is None
    s.frame, s.frame_at = RadarFrame(3, 80, 60, 90, 50), 10.0
    readings = [s.sample(10.0 + i * 0.1) for i in range(8)]
    assert readings[-1] == Reading(distance_cm=80, present=True)
    assert s.sample(13.0) is None                              # no frame for more than 2 s


# Presence with the radar ------------------------------------------------------------


RADAR_IN = Reading(90, present=True)
RADAR_OUT = Reading(None, present=False)
HAND = Reading(8.0)          # HC-SR04
PERSON_CLOSE = Reading(70.0)  # HC-SR04 sees someone, but the radar decides presence


def kinds(presence, reading, start, end, step=0.5):
    out, t = [], start
    while t <= end:
        out += [k for k, _ in presence.update(reading, t)]
        t += step
    return out


def test_settings_pick_the_presence_sensor():
    cfg = load(RADAR_ENV)
    assert (cfg.presence_sensor, cfg.radar_port, cfg.radar_baud, cfg.radar_max_cm, cfg.radar_min_energy) == \
        ("ld2410c", "/dev/serial0", 256000, 150, 30)
    assert load(ENV).presence_sensor == "ultrasonic"
    with pytest.raises(SystemExit, match="PRESENCE_SENSOR"):
        load({**ENV, "PRESENCE_SENSOR": "lidar"})


def test_the_hc_sr04_can_be_left_out_when_the_radar_decides_presence(tmp_path):
    from device.nextup_agent.agent import Agent

    env = {**RADAR_ENV, "ULTRASONIC": "off", "NEXTUP_DATA_DIR": str(tmp_path)}
    agent = Agent(load(env))
    assert [type(s).__name__ for s in agent.sensors] == ["LD2410Sensor"]   # its GPIO pins are never opened
    assert agent.kiosk.state["hand_hold"] is True                           # the radar can see a raised hand
    no_hold = Agent(load({**env, "RADAR_HOLD": "off"}))
    assert no_hold.kiosk.state["hand_hold"] is False                        # the pop-up won't ask for a hold
    assert Agent(load({**RADAR_ENV, "NEXTUP_DATA_DIR": str(tmp_path)})).kiosk.state["hand_hold"] is True
    with pytest.raises(SystemExit, match="ULTRASONIC=off needs PRESENCE_SENSOR=ld2410c"):
        load({**ENV, "ULTRASONIC": "off"})


def test_radar_starts_and_ends_sessions_with_the_same_timers():
    p = Presence(load(RADAR_ENV))
    assert "session.start" not in kinds(p, RADAR_IN, 0, 19)
    assert kinds(p, RADAR_IN, 19.5, 21).count("session.start") == 1
    assert "session.end" not in kinds(p, RADAR_OUT, 22, 51)
    assert kinds(p, RADAR_OUT, 51.5, 53).count("session.end") == 1


def test_with_the_radar_the_hc_sr04_only_does_the_hand_hold():
    p = Presence(load(RADAR_ENV))
    p.hold_armed = True  # the Hurry pop-up is showing
    assert "session.start" not in kinds(p, PERSON_CLOSE, 0, 40)  # HC-SR04 alone can't start a session
    assert kinds(p, HAND, 41, 47, step=0.1).count("input.hold") == 1  # HOLD_SECONDS is 5 now


def test_radar_distances_never_count_as_a_hand_hold():
    p = Presence(load(RADAR_ENV))
    assert "input.hold" not in kinds(p, Reading(5, present=True), 0, 5, step=0.1)


def test_hc_sr04_readings_between_radar_readings_do_not_end_a_session():
    p = Presence(load(RADAR_ENV))
    t, out = 0.0, []
    while t < 60:  # the two sensors' readings interleave, as they do on the Pi
        out += [k for k, _ in p.update(RADAR_IN, t)]
        out += [k for k, _ in p.update(Reading(None), t + 0.05)]
        t += 0.1
    assert out.count("session.start") == 1 and "session.end" not in out


def test_a_hand_raised_to_the_radar_acknowledges_the_pop_up():
    from device.nextup_agent.ld2410 import LD2410Sensor, RadarFrame

    radar = LD2410Sensor(hold=True, hold_max_cm=30, hold_min_energy=50)
    radar.frame, radar.frame_at = RadarFrame(state=3, moving_cm=12, moving_energy=80, still_cm=90, still_energy=40), 0.0
    near = radar.sample(0.05)
    assert near.hold_near is True
    radar.frame = RadarFrame(state=3, moving_cm=70, moving_energy=80, still_cm=90, still_energy=40)  # showering, not a hand
    assert radar.sample(0.1).hold_near is False

    p = Presence(load({**RADAR_ENV, "ULTRASONIC": "off"}))
    p.hold_armed = True
    hand = Reading(12, present=True, hold_near=True)
    assert kinds(p, hand, 0, 5.2, step=0.1).count("input.hold") == 1


# Radar setup and calibration (needs the radar's RX) ----------------------------------


from device.nextup_agent.ld2410 import (CAL_FILE, CAL_TRIGGER, Calibration, RadarError, RadarSetup,  # noqa: E402
                                        command, configure)


def engineering_frame(moving_gates, still_gates, state=3) -> bytes:
    n = len(moving_gates) - 1
    data = bytes([0x01, 0xAA, state, 80, 0, 60, 90, 0, 50, 90, 0, n, n, *moving_gates, *still_gates, 0, 0, 0x55, 0x00])
    return bytes.fromhex("f4f3f2f1") + len(data).to_bytes(2, "little") + data + bytes.fromhex("f8f7f6f5")


class FakeRadar:
    """Plays the LD2410C's side of the serial port: answers setup commands, streams reports."""

    def __init__(self, wired=True):
        self.wired, self.out = wired, bytearray()
        self.gate_cm, self.max_gates, self.nobody = 75, (8, 8), 5
        self.moving = [50, 50, 40, 30, 20, 15, 15, 15, 15]
        self.still = [0, 0, 40, 40, 30, 30, 20, 20, 20]
        self.writes, self.restarts, self.engineering = [], 0, False

    def ack(self, word, data=b"", status=0):
        body = (word | 0x0100).to_bytes(2, "little") + status.to_bytes(2, "little") + data
        self.out += engineering_frame([1] * 9, [2] * 9)  # reports keep coming in between
        self.out += bytes.fromhex("fdfcfbfa") + len(body).to_bytes(2, "little") + body + bytes.fromhex("04030201")

    def write(self, frame):
        if not self.wired:
            return
        word, value = frame[6] | frame[7] << 8, frame[8:-4]
        num = lambda i: int.from_bytes(value[i + 2 : i + 6], "little")  # noqa: E731
        self.writes.append(word)
        if word == 0x0061:
            self.ack(word, bytes([0xAA, 8, *self.max_gates, *self.moving, *self.still]) + self.nobody.to_bytes(2, "little"))
        elif word == 0x00AB:
            self.ack(word, (1 if self.gate_cm == 20 else 0).to_bytes(2, "little"))
        elif word == 0x00AA:
            self.pending_gate = 20 if value[0] == 1 else 75
            self.ack(word)
        elif word == 0x00A3:
            self.gate_cm, self.restarts = self.pending_gate, self.restarts + 1
            self.ack(word)
        elif word == 0x0060:
            self.max_gates, self.nobody = (num(0), num(6)), num(12)
            self.ack(word)
        elif word == 0x0064:
            gate = num(0)
            self.moving[gate], self.still[gate] = num(6), num(12)
            self.ack(word)
        else:
            self.engineering = {0x0062: True, 0x0063: False}.get(word, self.engineering)
            self.ack(word)

    def read(self, n):
        data, self.out[:] = bytes(self.out[:n]), self.out[n:]
        return data

    def reset_input_buffer(self):
        self.out.clear()


def test_command_frames_match_the_datasheet():
    assert command(0x00FF, b"\x01\x00").hex() == "fdfcfbfa0400ff00010004030201"   # enable configuration
    assert command(0x00FE).hex() == "fdfcfbfa0200fe0004030201"                     # end configuration


def test_engineering_frames_carry_the_energy_at_each_gate():
    frame = FrameParser().feed(engineering_frame([5, 60, 30, 0, 0, 0, 0, 0, 1], [9, 40, 20, 3, 0, 0, 0, 0, 2]))[0]
    assert frame.moving_gates == (5, 60, 30, 0, 0, 0, 0, 0, 1) and frame.still_gates[1] == 40
    assert (frame.moving_cm, frame.still_energy) == (80, 50)
    assert FrameParser().feed(frame_bytes())[0].moving_gates == ()   # basic frames have none


def test_setup_narrows_the_radar_and_only_writes_what_changed():
    radar = FakeRadar()
    sens = (60, 50, 40, 40, 40, 40, 40, 40, 40)
    gate_cm, params = configure(radar, RadarSetup(gate_cm=20, max_cm=120, moving_sens=sens), sleep=lambda s: None)
    assert gate_cm == 20 and radar.restarts == 1                       # 20 cm gates need a radar restart
    assert (params.max_moving_gate, params.max_still_gate) == (5, 5)   # 6 gates x 20 cm = 0-120 cm
    assert params.moving_sens == sens and params.still_sens[2] == 40   # still sensitivity left alone
    assert radar.engineering and radar.writes[-1] == 0x00FE            # left setup mode
    radar.writes.clear()
    configure(radar, RadarSetup(gate_cm=20, max_cm=120, moving_sens=sens), sleep=lambda s: None)
    assert 0x0060 not in radar.writes and 0x0064 not in radar.writes and 0x00AA not in radar.writes


def test_max_gate_follows_radar_max_cm():
    assert RadarSetup(gate_cm=75, max_cm=150).max_gate() == 2          # the radar's minimum
    assert RadarSetup(gate_cm=75, max_cm=300).max_gate() == 3
    assert RadarSetup(gate_cm=20, max_cm=150).max_gate() == 7
    assert RadarSetup(gate_cm=20, max_cm=400).max_gate() == 8


def test_setup_without_the_rx_wire_fails_cleanly():
    with pytest.raises(RadarError, match="RX"):
        configure(FakeRadar(wired=False), RadarSetup())


def test_calibration_puts_each_threshold_above_the_empty_stall():
    cal = Calibration(seconds=1, margin=10, now=0)
    for frame in FrameParser().feed(engineering_frame([3, 70, 5, 0, 0, 0, 0, 0, 0], [20, 30, 8, 0, 0, 0, 0, 0, 95])
                                    + engineering_frame([9, 40, 2, 0, 0, 0, 0, 0, 0], [5, 35, 1, 0, 0, 0, 0, 0, 0])):
        cal.add(frame)
    moving, still = cal.thresholds()
    assert moving == (19, 80, 15, 10, 10, 10, 10, 10, 10)               # peak + 10, never below 10
    assert still == (30, 45, 18, 10, 10, 10, 10, 10, 100)               # capped at 100


def test_calibration_runs_inside_the_agent_and_is_reused(tmp_path):
    radar = FakeRadar()
    sensor = LD2410Sensor(setup=RadarSetup(gate_cm=75, max_cm=150), data_dir=tmp_path)
    (tmp_path / CAL_TRIGGER).write_text('{"seconds": 2, "margin": 5}')
    sensor.check_calibration_request(now=100.0)
    assert sensor.calibration and not (tmp_path / CAL_TRIGGER).exists()
    for frame in FrameParser().feed(engineering_frame([0, 50, 20, 0, 0, 0, 0, 0, 0], [0, 30, 10, 0, 0, 0, 0, 0, 0])):
        sensor.calibration.add(frame)
    sensor.finish_calibration(radar)
    saved = json.loads((tmp_path / CAL_FILE).read_text())
    assert saved["moving_sens"][:3] == [10, 55, 25] and radar.moving[:3] == [10, 55, 25]
    # After a restart the saved calibration is written again, unless RADAR_*_SENS are set.
    fresh = FakeRadar()
    LD2410Sensor(setup=RadarSetup(), data_dir=tmp_path).apply_setup(fresh)
    assert fresh.moving[:3] == [10, 55, 25] and fresh.still[:3] == [10, 35, 15]
    explicit = FakeRadar()
    LD2410Sensor(setup=RadarSetup(moving_sens=(70,) * 9), data_dir=tmp_path).apply_setup(explicit)
    assert explicit.moving == [70] * 9


def test_radar_setup_settings():
    cfg = load({**RADAR_ENV, "RADAR_GATE_CM": "20", "RADAR_MOVING_SENS": "40", "RADAR_STILL_SENS": "0,0,40,40,30,30,20,20,20"})
    assert cfg.radar_gate_cm == 20 and cfg.radar_moving_sens == (40,) * 9 and cfg.radar_still_sens[2] == 40
    assert load(RADAR_ENV).radar_setup and load({**RADAR_ENV, "RADAR_SETUP": "off"}).radar_setup is False
    with pytest.raises(SystemExit, match="RADAR_GATE_CM"):
        load({**RADAR_ENV, "RADAR_GATE_CM": "50"})
    with pytest.raises(SystemExit, match="RADAR_MOVING_SENS"):
        load({**RADAR_ENV, "RADAR_MOVING_SENS": "1,2,3"})


# A hand held up to the radar (what the Pi logged on 2026-11-08) -------------------------


def test_with_20_cm_gates_the_hand_is_read_from_the_nearest_gates():
    radar = LD2410Sensor(hold=True, hold_max_cm=30, hold_min_energy=50)
    radar.gate_cm = 20
    # The reported target says the arm at 40 cm, but gates 0-1 (0-40 cm) still see the hand.
    arm = RadarFrame(3, 40, 100, 40, 100, moving_gates=(100, 90, 100, 30, 10, 5, 5, 5, 5), still_gates=(0,) * 9)
    assert radar.hand_near(arm)
    # Someone 60 cm away, no hand: gates 0-1 stay low.
    person = RadarFrame(3, 60, 52, 54, 100, moving_gates=(13, 13, 21, 52, 25, 12, 7, 6, 4), still_gates=(0,) * 9)
    assert not radar.hand_near(person)
    radar.gate_cm = 75  # coarse gates: back to the reported target
    assert not radar.hand_near(arm) and radar.hand_near(RadarFrame(1, 12, 80, 0, 0, moving_gates=(80,) * 9))


def test_a_radar_hold_survives_the_target_flicking_away():
    p = Presence(load({**RADAR_ENV, "ULTRASONIC": "off"}))
    p.hold_armed = True
    out, t = [], 0.0
    while t < 9:  # 1.2 s of hand, then 0.5 s where the radar loses it, over and over
        near = (t % 1.7) < 1.2
        out += [k for k, _ in p.update(Reading(12, present=True, hold_near=near), t)]
        t += 0.1
    assert out.count("input.hold") == 1


def test_a_lowered_hand_drains_the_ring_instead_of_restarting_it():
    p = Presence(load({**RADAR_ENV, "ULTRASONIC": "off"}))
    p.hold_armed = True
    hand, away = Reading(12, present=True, hold_near=True), Reading(70, present=True, hold_near=False)
    kinds(p, hand, 0, 3, step=0.1)                      # 3 s of the 5 s hold
    kinds(p, away, 3.1, 4.5, step=0.1)                   # hand down 1.5 s: 1 s grace, then 0.5 s drains 0.2
    assert 0.3 < p.hold_progress < 0.45                  # about 0.6 - 0.2: kept most of it
    assert kinds(p, hand, 4.6, 7.0, step=0.1).count("input.hold") == 0   # 2.4 s more: not yet
    assert kinds(p, hand, 7.1, 8.2, step=0.1).count("input.hold") == 1   # about 3 s more, not 5: no restart


# A sensor that goes silent (unplugged) --------------------------------------------------


def test_a_silent_sensor_ends_the_session_instead_of_staying_in_use(tmp_path):
    import time as _time

    from device.nextup_agent.agent import Agent

    agent = Agent(load({**RADAR_ENV, "ULTRASONIC": "off", "NEXTUP_DATA_DIR": str(tmp_path)}))
    now = _time.monotonic()
    agent.presence.force("in_use")
    agent.kiosk.publish(stall_state="in_use")
    agent.sensor.last_reading_at = now - 30                  # quiet for 30 s: maybe a brief glitch, wait
    agent.check_sensor_lost(now)
    assert agent.presence.state == "in_use"
    agent.sensor.last_reading_at = now - 61                  # over a minute: the sensor is gone
    agent.check_sensor_lost(now)
    assert agent.presence.state == "free" and agent.kiosk.state["stall_state"] == "free"
    assert agent.uplink.pending() >= 1                        # session.end goes to the API too
    off = Agent(load({**RADAR_ENV, "ULTRASONIC": "off", "NEXTUP_DATA_DIR": str(tmp_path), "SENSOR_LOST_SECONDS": "0"}))
    off.presence.force("in_use")
    off.sensor.last_reading_at = now - 600
    off.check_sensor_lost(now)
    assert off.presence.state == "in_use"


def test_the_filter_can_let_go_faster():
    fast, normal = OccupancyFilter(score_down=10, free_hold_s=0.5), OccupancyFilter()
    for f in (fast, normal):
        samples(f, True, 0, 20)                                   # someone there: score 100
    fast_out, normal_out = samples(fast, False, 2.0, 40), samples(normal, False, 2.0, 40)
    assert 12 <= fast_out.index(False) <= 16                      # ~1.4 s to free
    assert 30 <= normal_out.index(False) <= 35                    # the default: ~3.3 s, as before
    cfg = load({**RADAR_ENV, "RADAR_SCORE_DOWN": "10", "RADAR_FREE_HOLD_SECONDS": "0.5"})
    assert (cfg.radar_score_down, cfg.radar_free_hold_s) == (10, 0.5)
    assert (load(RADAR_ENV).radar_score_down, load(RADAR_ENV).radar_free_hold_s) == (4, 1.0)
