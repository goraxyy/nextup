"""Agent settings, from the environment (/etc/nextup/agent.env on the Pi)."""

import os
import pathlib
from dataclasses import dataclass

REQUIRED = ("NEXTUP_API", "NEXTUP_STALL", "NEXTUP_DEVICE_TOKEN", "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY")


@dataclass(frozen=True)
class Config:
    api: str                      # e.g. https://nextup-khaki.vercel.app
    stall: str                    # e.g. s1
    device_token: str             # from scripts/setup_room.py
    supabase_url: str
    supabase_key: str             # publishable key: safe on the device
    trigger_pin: int = 23         # HC-SR04 TRIG (BCM numbering)
    echo_pin: int = 24            # HC-SR04 ECHO, through a voltage divider
    fake_sensor: bool = False     # read in/out/hold commands from stdin instead of the sensor
    data_dir: pathlib.Path = pathlib.Path("/var/lib/nextup")
    kiosk_host: str = "127.0.0.1"
    kiosk_http_port: int = 8080
    kiosk_ws_port: int = 8765
    # Presence: in use after this long present, free after this long absent.
    enter_s: float = 20.0
    leave_s: float = 30.0
    range_cm: int = 300           # farther than this, or no echo, counts as nothing there
    present_max_cm: int = 120     # closer than this: someone is in the stall (calibrate per stall)
    # Hand-hold: something this close to the sensor for this long.
    hold_max_cm: int = 15
    hold_s: float = 2.0
    sample_hz: float = 10.0
    reading_every_s: float = 5.0
    heartbeat_every_s: float = 30.0


def _num(env: dict, name: str, default, cast=float):
    value = env.get(name)
    return cast(value) if value not in (None, "") else default


def load(env: dict | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        raise SystemExit("missing settings: " + ", ".join(missing))
    return Config(
        api=env["NEXTUP_API"].rstrip("/"),
        stall=env["NEXTUP_STALL"],
        device_token=env["NEXTUP_DEVICE_TOKEN"],
        supabase_url=env["SUPABASE_URL"].rstrip("/"),
        supabase_key=env["SUPABASE_PUBLISHABLE_KEY"],
        trigger_pin=_num(env, "TRIG_PIN", 23, int),
        echo_pin=_num(env, "ECHO_PIN", 24, int),
        fake_sensor=env.get("FAKE_SENSOR", "").lower() in ("1", "true", "yes"),
        data_dir=pathlib.Path(env.get("NEXTUP_DATA_DIR", "/var/lib/nextup")),
        kiosk_host=env.get("KIOSK_HOST", "127.0.0.1"),
        kiosk_http_port=_num(env, "KIOSK_HTTP_PORT", 8080, int),
        kiosk_ws_port=_num(env, "KIOSK_WS_PORT", 8765, int),
        enter_s=_num(env, "ENTER_SECONDS", 20.0),
        leave_s=_num(env, "LEAVE_SECONDS", 30.0),
        range_cm=_num(env, "RANGE_CM", 300, int),
        present_max_cm=_num(env, "PRESENT_MAX_CM", 120, int),
        hold_max_cm=_num(env, "HOLD_MAX_CM", 15, int),
        hold_s=_num(env, "HOLD_SECONDS", 2.0),
        sample_hz=_num(env, "SAMPLE_HZ", 10.0),
        reading_every_s=_num(env, "READING_EVERY_SECONDS", 5.0),
        heartbeat_every_s=_num(env, "HEARTBEAT_EVERY_SECONDS", 30.0),
    )
