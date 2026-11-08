"""LD2410C radar: frame parsing, the occupancy filter, and presence with the radar. No hardware needed."""

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


def test_radar_starts_and_ends_sessions_with_the_same_timers():
    p = Presence(load(RADAR_ENV))
    assert "session.start" not in kinds(p, RADAR_IN, 0, 19)
    assert kinds(p, RADAR_IN, 19.5, 21).count("session.start") == 1
    assert "session.end" not in kinds(p, RADAR_OUT, 22, 51)
    assert kinds(p, RADAR_OUT, 51.5, 53).count("session.end") == 1


def test_with_the_radar_the_hc_sr04_only_does_the_hand_hold():
    p = Presence(load(RADAR_ENV))
    assert "session.start" not in kinds(p, PERSON_CLOSE, 0, 40)  # HC-SR04 alone can't start a session
    assert kinds(p, HAND, 41, 44, step=0.1).count("input.hold") == 1


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
