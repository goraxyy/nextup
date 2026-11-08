"""Agent settings, from the environment (/etc/nextup/agent.env on the Pi)."""

import os
import pathlib
from dataclasses import dataclass

REQUIRED = ("NEXTUP_API", "NEXTUP_STALL", "NEXTUP_DEVICE_TOKEN", "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY")
PRESENCE_SENSORS = ("ultrasonic", "ld2410c")


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
    # Hand-hold: something this close to the sensor for this long. Holds only count while the
    # Hurry pop-up is on the screen.
    hold_max_cm: int = 15
    hold_s: float = 5.0
    sample_hz: float = 10.0
    # Which sensor decides presence: "ultrasonic" (HC-SR04) or "ld2410c" (radar on the UART).
    # With the radar, the HC-SR04 still does the hand-hold.
    presence_sensor: str = "ultrasonic"
    radar_port: str = "/dev/serial0"
    radar_baud: int = 256000
    radar_max_cm: int = 150       # radar targets farther than this are outside the stall (also the radar's last gate)
    radar_min_energy: int = 30    # weaker radar echoes (0-100) are ignored
    radar_min_cm: int = 0         # radar targets closer than this (e.g. a flexing plastic cover) are ignored
    radar_debug: bool = False     # log what the radar sees once a second, for tuning
    # Written to the radar at start (needs its RX wired): gate size (75 or 20 cm), a sensitivity
    # per gate (9 values, 0-100, higher = less sensitive; None = the last calibration, else the
    # radar's own), and how long it keeps reporting someone who has gone. RADAR_SETUP=off: never write.
    radar_setup: bool = True
    radar_gate_cm: int = 75
    radar_moving_sens: tuple[int, ...] | None = None
    radar_still_sens: tuple[int, ...] | None = None
    radar_nobody_s: int | None = None
    # Hand-hold on the radar: a moving target this close and this strong (while the pop-up shows).
    radar_hold: bool = True
    radar_hold_max_cm: int = 30
    radar_hold_min_energy: int = 50
    # With the radar, the HC-SR04 is only for the hand-hold. ULTRASONIC=off leaves it out entirely
    # (unplugged): no hand-hold, so the screen's Notify pop-up can't be acknowledged.
    ultrasonic: bool = True
    reading_every_s: float = 5.0
    # No readings from the presence sensor for this long (unplugged, loose wire) while the stall is
    # in use: end the session, so the stall doesn't stay "In use" forever. 0: never.
    sensor_lost_s: float = 60.0
    heartbeat_every_s: float = 30.0
    screen_every_s: float = 300.0  # how often to fetch the stall screen's content (events)
    # Experimental shower music (stall_music.py): "spotify" plays through go-librespot on this
    # Pi and lets radar gestures pause and skip. Empty or "off": no change from before.
    music: str = ""
    music_api: str = "http://127.0.0.1:3678"
    music_device_name: str = ""  # the Spotify Connect name, for the screen's hint
    spotify_client_id: str = ""   # MUSIC=spotify-web: the Spotify developer app's Client ID
    music_nudge_every: int = 5    # every this many songs in one shower, the lion asks them to hurry (0: never)
    music_shampoo_after: int = 2  # after this many songs, "Let's shampoo!" and a wave of bubbles (0: never)
    # Experimental, off unless set: when someone leaves after a real shower, the lion celebrates
    # for this long (CELEBRATE_SECONDS=10).
    celebrate_s: float = 0.0
    # Experimental, off unless set: when a shower starts, ask "How long will your shower be?" for
    # this long, answered with a hand raised to the sensor (shower_goal.py; GOAL_ASK_SECONDS=15).
    goal_ask_s: float = 0.0
    goal_options: tuple[int, ...] = (5, 10, 15)
    spotify_token_file: pathlib.Path | None = None  # its refresh token, from scripts/spotify_login.py
    page_status_s: float = 10.0    # cycling pages on the stall screen
    page_event_s: float = 8.0


def _num(env: dict, name: str, default, cast=float):
    value = env.get(name)
    return cast(value) if value not in (None, "") else default


def _gates(env: dict, name: str) -> tuple[int, ...] | None:
    """RADAR_MOVING_SENS=40 (every gate) or 50,50,40,30,20,15,15,15,15 (gates 0-8)."""
    value = env.get(name, "").strip()
    if not value:
        return None
    try:
        numbers = [int(v) for v in value.split(",")]
    except ValueError:
        raise SystemExit(f"{name} must be a number or 9 comma-separated numbers (got {value!r})") from None
    if len(numbers) == 1:
        numbers *= 9
    if len(numbers) != 9 or not all(0 <= n <= 100 for n in numbers):
        raise SystemExit(f"{name} needs 1 or 9 values from 0 to 100, one per gate (got {value!r})")
    return tuple(numbers)


def load(env: dict | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        raise SystemExit("missing settings: " + ", ".join(missing))
    presence_sensor = env.get("PRESENCE_SENSOR", "").strip().lower() or "ultrasonic"
    if presence_sensor not in PRESENCE_SENSORS:
        raise SystemExit(f"PRESENCE_SENSOR must be one of: {', '.join(PRESENCE_SENSORS)} (got {presence_sensor!r})")
    ultrasonic = env.get("ULTRASONIC", "").strip().lower() not in ("0", "off", "false", "no")
    if not ultrasonic and presence_sensor != "ld2410c":
        raise SystemExit("ULTRASONIC=off needs PRESENCE_SENSOR=ld2410c: without the radar, the HC-SR04 is the only sensor")
    gate_cm = _num(env, "RADAR_GATE_CM", 75, int)
    try:
        goal_options = tuple(int(v) for v in env.get("GOAL_OPTIONS", "5,10,15").split(",") if v.strip())
    except ValueError:
        raise SystemExit(f"GOAL_OPTIONS must be minutes separated by commas (got {env.get('GOAL_OPTIONS')!r})") from None
    if not 1 <= len(goal_options) <= 4 or min(goal_options) < 1:
        raise SystemExit("GOAL_OPTIONS needs 1 to 4 choices in minutes, like 5,10,15")
    music = env.get("MUSIC", "").strip().lower()
    music = "" if music in ("", "0", "off", "false", "no") else music
    if music not in ("", "spotify", "spotify-web"):
        raise SystemExit(f"MUSIC must be spotify-web, spotify or off (got {music!r})")
    if music == "spotify-web" and not env.get("SPOTIFY_CLIENT_ID", "").strip():
        raise SystemExit("MUSIC=spotify-web needs SPOTIFY_CLIENT_ID (the Spotify developer app's Client ID)")
    if gate_cm not in (75, 20):
        raise SystemExit(f"RADAR_GATE_CM must be 75 or 20 (got {gate_cm})")
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
        hold_s=_num(env, "HOLD_SECONDS", 5.0),
        sample_hz=_num(env, "SAMPLE_HZ", 10.0),
        reading_every_s=_num(env, "READING_EVERY_SECONDS", 5.0),
        sensor_lost_s=_num(env, "SENSOR_LOST_SECONDS", 60.0),
        heartbeat_every_s=_num(env, "HEARTBEAT_EVERY_SECONDS", 30.0),
        presence_sensor=presence_sensor,
        radar_port=env.get("RADAR_PORT") or "/dev/serial0",
        radar_baud=_num(env, "RADAR_BAUD", 256000, int),
        radar_max_cm=_num(env, "RADAR_MAX_CM", 150, int),
        radar_min_energy=_num(env, "RADAR_MIN_ENERGY", 30, int),
        radar_min_cm=_num(env, "RADAR_MIN_CM", 0, int),
        radar_debug=env.get("RADAR_DEBUG", "").lower() in ("1", "true", "yes"),
        radar_setup=env.get("RADAR_SETUP", "1").strip().lower() not in ("0", "off", "false", "no"),
        radar_gate_cm=gate_cm,
        radar_moving_sens=_gates(env, "RADAR_MOVING_SENS"),
        radar_still_sens=_gates(env, "RADAR_STILL_SENS"),
        radar_nobody_s=_num(env, "RADAR_NOBODY_SECONDS", None, int),
        radar_hold=env.get("RADAR_HOLD", "1").strip().lower() not in ("0", "off", "false", "no"),
        radar_hold_max_cm=_num(env, "RADAR_HOLD_MAX_CM", 30, int),
        radar_hold_min_energy=_num(env, "RADAR_HOLD_MIN_ENERGY", 50, int),
        screen_every_s=_num(env, "SCREEN_EVERY_SECONDS", 300.0),
        page_status_s=_num(env, "PAGE_STATUS_SECONDS", 10.0),
        page_event_s=_num(env, "PAGE_EVENT_SECONDS", 8.0),
        ultrasonic=ultrasonic,
        music=music,
        music_api=env.get("MUSIC_API", "http://127.0.0.1:3678"),
        music_device_name=env.get("MUSIC_DEVICE_NAME", ""),
        spotify_client_id=env.get("SPOTIFY_CLIENT_ID", "").strip(),
        music_nudge_every=_num(env, "MUSIC_NUDGE_EVERY", 5, int),
        music_shampoo_after=_num(env, "MUSIC_SHAMPOO_AFTER", 2, int),
        celebrate_s=_num(env, "CELEBRATE_SECONDS", 0.0),
        goal_ask_s=_num(env, "GOAL_ASK_SECONDS", 0.0),
        goal_options=goal_options,
        spotify_token_file=pathlib.Path(env.get("SPOTIFY_TOKEN_FILE")
                                        or pathlib.Path(env.get("NEXTUP_DATA_DIR", "/var/lib/nextup")) / "spotify-token.json"),
    )
