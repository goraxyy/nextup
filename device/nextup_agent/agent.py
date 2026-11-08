"""Wires the parts together.

HC-SR04 distances (or the LD2410C radar for presence) → presence → events to the API (queued)
and state to the screen
Supabase Realtime (device:<stall>) → pop-ups, re-chimes and reservations on the screen
"""

import asyncio
import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
import socket
import time

import httpx

from . import __version__
from .config import Config
from .kiosk import Kiosk
from .ld2410 import LD2410Sensor, RadarSetup
from .presence import Presence
from .sensor import FakeSensor, Reading, UltrasonicSensor
from .stall_music import LibrespotPlayer, SpotifyWebPlayer, StallMusic
from .realtime import DeviceChannel, SupabaseSession
from .uplink import Uplink, now_iso

log = logging.getLogger("nextup.agent")


def local_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def cpu_temp() -> float | None:
    try:
        return int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
    except (OSError, ValueError):
        return None


def uptime_s() -> int | None:
    try:
        return int(float(open("/proc/uptime").read().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def radar_setup(cfg: Config) -> RadarSetup | None:
    if not cfg.radar_setup:
        return None
    return RadarSetup(gate_cm=cfg.radar_gate_cm, max_cm=cfg.radar_max_cm, moving_sens=cfg.radar_moving_sens,
                      still_sens=cfg.radar_still_sens, nobody_s=cfg.radar_nobody_s)


class Agent:
    def __init__(self, cfg: Config) -> None:
        if cfg.fake_sensor:  # the fake sensor plays the HC-SR04 for presence and hand-hold
            cfg = dataclasses.replace(cfg, presence_sensor="ultrasonic", ultrasonic=True)
        self.cfg = cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.kiosk = Kiosk(cfg)
        # The screen only asks for a hand-hold if a sensor can see one: the HC-SR04, or the radar with RADAR_HOLD.
        self.kiosk.publish(hand_hold=cfg.ultrasonic or (cfg.presence_sensor == "ld2410c" and cfg.radar_hold))
        self.uplink = Uplink(cfg.api, cfg.device_token, cfg.data_dir)
        self.presence = Presence(cfg)
        # self.sensor decides presence (and drives the screen's "sensor" dot); every sensor in
        # self.sensors feeds on_reading. With the radar, the HC-SR04 is only there for the hand-hold,
        # and ULTRASONIC=off leaves it out (its GPIO pins are never touched).
        ultrasonic = (FakeSensor() if cfg.fake_sensor
                      else UltrasonicSensor(cfg.trigger_pin, cfg.echo_pin, cfg.range_cm, cfg.sample_hz)
                      if cfg.ultrasonic else None)
        if cfg.presence_sensor == "ld2410c":
            self.sensor = LD2410Sensor(cfg.radar_port, cfg.radar_baud, cfg.radar_max_cm, cfg.radar_min_energy,
                                       cfg.radar_min_cm, cfg.radar_debug, cfg.radar_hold, cfg.radar_hold_max_cm,
                                       cfg.radar_hold_min_energy, setup=radar_setup(cfg), data_dir=cfg.data_dir)
            self.sensors = [self.sensor] + ([ultrasonic] if ultrasonic else [])
        else:
            self.sensor = ultrasonic
            self.sensors = [ultrasonic]
        self.session = SupabaseSession(cfg.supabase_url, cfg.supabase_key, cfg.data_dir)
        self.channel = DeviceChannel(cfg, self.session)
        self.last_reading: Reading | None = None
        self.music_changed = asyncio.Event()
        # Experimental: the shower's own music (MUSIC=spotify). None leaves everything as before.
        player = (SpotifyWebPlayer(cfg.spotify_client_id, cfg.spotify_token_file) if cfg.music == "spotify-web"
                  else LibrespotPlayer(cfg.music_api) if cfg.music == "spotify" else None)
        self.stall_music = StallMusic(player, self.kiosk) if player else None
        if self.stall_music:  # the hint on the screen: how to get your music on it
            self.kiosk.publish(music_hint="Spotify (lyrics show here)" if cfg.music == "spotify-web"
                               else f"Spotify → {cfg.music_device_name}" if cfg.music_device_name else "")

    def set_notify(self, notify: dict | None, **changes) -> None:
        """Show or clear the Hurry pop-up; hand-holds only count while it's showing."""
        self.presence.hold_armed = notify is not None
        self.kiosk.publish(notify=notify, **changes)

    # Sensor → presence ------------------------------------------------------------

    def on_reading(self, reading: Reading, now: float) -> None:
        if (reading.present is not None) == (self.cfg.presence_sensor == "ld2410c"):
            self.last_reading = reading  # the presence sensor's latest, for the history readings
        for kind, value in self.presence.update(reading, now):
            if kind == "session.start":
                self.uplink.enqueue("session.start")
                self.kiosk.publish(stall_state="in_use", since=now_iso(), reserved_until=None)
            elif kind == "session.end":
                self.uplink.enqueue("session.end")
                self.set_notify(None, stall_state="free", since=now_iso())
            elif kind == "hold.progress":
                self.kiosk.publish(hold=value)
            elif kind == "input.hold":
                self.uplink.enqueue("input.hold")
                if self.kiosk.state["notify"]:
                    self.set_notify(None, thanks_until=time.time() + 4)
        if self.stall_music and reading.hold_near is not None:  # radar gestures for the music
            self.stall_music.on_hand(reading.hold_near, now, hurry_showing=self.presence.hold_armed)

    def sensor_ok(self) -> bool:
        return time.monotonic() - self.sensor.last_reading_at < 5

    # Supabase → screen ------------------------------------------------------------

    async def on_message(self, event: str, payload: dict) -> None:
        log.info("message %s %s", event, payload)
        if event == "notify.deliver":
            waiting = payload.get("waiting", self.kiosk.state["waiting"])
            self.set_notify({"notify_id": payload.get("notify_id"), "status": "sent"},
                            waiting=waiting, chime=self.kiosk.state["chime"] + 1)
            self.uplink.enqueue("notify.delivered")
        elif event == "notify.rechime":
            self.kiosk.publish(chime=self.kiosk.state["chime"] + 1)
        elif event == "notify.closed":
            current = self.kiosk.state["notify"]
            if current and current.get("notify_id") == payload.get("notify_id"):
                self.set_notify(None)
        elif event == "stall.reserved":
            self.kiosk.publish(reserved_until=payload.get("until"))
        elif event == "nowplaying.changed":
            self.music_changed.set()

    async def resync(self, client: httpx.AsyncClient) -> None:
        """After (re)connecting, catch up on anything missed while offline."""
        try:
            response = await client.get(f"{self.cfg.api}/api/device/state",
                                        headers={"Authorization": f"Bearer {self.cfg.device_token}"}, timeout=15)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("couldn't fetch stall state: %s", exc)
            return
        data = response.json()
        stall = data.get("stall") or {}
        if self.uplink.pending() == 0 and stall.get("state"):
            self.presence.force(stall["state"])
        notify = data.get("notify")
        reserved = data.get("reserved")
        self.set_notify({"notify_id": notify["notify_id"], "status": notify["status"]} if notify else None,
                        stall_name=stall.get("name"), stall_state=self.presence.state, since=stall.get("since"),
                        reserved_until=reserved["until"] if reserved else None)
        if notify and notify["status"] == "sent":
            self.uplink.enqueue("notify.delivered")

    # Periodic reports -------------------------------------------------------------

    async def heartbeats(self) -> None:
        while True:
            self.uplink.enqueue("heartbeat", version=__version__, ip=local_ip(), cpu_temp=cpu_temp(),
                                uptime_s=uptime_s(), radar_ok=self.sensor_ok())  # devices.radar_ok means "sensor OK"
            self.kiosk.publish(sensor_ok=self.sensor_ok())
            await asyncio.sleep(self.cfg.heartbeat_every_s)

    async def readings(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.reading_every_s)
            r = self.last_reading
            if r is not None and self.sensor_ok():
                present = (r.present if r.present is not None
                           else r.distance_cm is not None and r.distance_cm <= self.cfg.present_max_cm)
                self.uplink.enqueue("reading", presence=present, distance_cm=r.distance_cm, water=False)

    async def cache_image(self, client: httpx.AsyncClient, url: str | None) -> str | None:
        """Download an event picture once and serve it locally (/media/...), so it shows offline."""
        if not url:
            return None
        media = self.cfg.data_dir / "media"
        media.mkdir(exist_ok=True)
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + (pathlib.PurePosixPath(url.split("?")[0]).suffix.lower() or ".jpg")
        path = media / name
        if not path.exists():
            try:
                response = await client.get(url, timeout=30, follow_redirects=True)
                response.raise_for_status()
                path.write_bytes(response.content)
            except httpx.HTTPError as exc:
                log.warning("couldn't download an event image: %s", exc)
                return None
        return f"/media/{name}"

    def publish_screen(self, data: dict) -> None:
        stall = data.get("stall") or {}
        offset = 0
        if data.get("server_time"):
            offset = (dt.datetime.fromisoformat(data["server_time"]).timestamp() - time.time()) * 1000
        self.kiosk.publish(events=data.get("events", []), waiting=data.get("waiting", 0),
                           insights=data.get("insights"), music=data.get("music"), clock_offset_ms=round(offset),
                           stall_name=stall.get("name") or self.kiosk.state["stall_name"])

    async def music_updates(self, client: httpx.AsyncClient) -> None:
        """When the song changes, fetch it (with its lyrics) for the lyrics page."""
        while True:
            await self.music_changed.wait()
            self.music_changed.clear()
            try:
                response = await client.get(f"{self.cfg.api}/api/device/nowplaying",
                                            headers={"Authorization": f"Bearer {self.cfg.device_token}"}, timeout=15)
                response.raise_for_status()
                data = response.json()
                offset = (dt.datetime.fromisoformat(data["server_time"]).timestamp() - time.time()) * 1000
                self.kiosk.publish(music=data, clock_offset_ms=round(offset))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.warning("couldn't fetch now playing: %s", exc)

    async def screen_content(self, client: httpx.AsyncClient) -> None:
        """Events, rush hours and music for the cycling pages; the last copy is kept for offline."""
        cache = self.cfg.data_dir / "screen.json"
        if cache.exists():
            try:
                self.publish_screen(json.loads(cache.read_text()))
            except (OSError, ValueError):
                pass
        while True:
            try:
                response = await client.get(f"{self.cfg.api}/api/device/screen",
                                            headers={"Authorization": f"Bearer {self.cfg.device_token}"}, timeout=20)
                response.raise_for_status()
                data = response.json()
                for e in data.get("events", []):
                    e["image"] = await self.cache_image(client, e.get("image_url"))
                data["events"] = [e for e in data.get("events", []) if e.get("image")]
                cache.write_text(json.dumps(data))
                self.publish_screen(data)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("couldn't fetch screen content: %s (showing the last copy)", exc)
            await asyncio.sleep(self.cfg.screen_every_s)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sensor in self.sensors:
            sensor.start(loop, self.on_reading)
        cfg = self.cfg
        hc_sr04 = f"HC-SR04 TRIG {cfg.trigger_pin} ECHO {cfg.echo_pin}" if cfg.ultrasonic else None
        holds = [h for h in (hc_sr04, "radar" if cfg.radar_hold else None) if h] or ["off"]
        sensors = ("fake sensor" if cfg.fake_sensor
                   else f"presence: LD2410C on {cfg.radar_port}, hand-hold: {' + '.join(holds)}"
                   if cfg.presence_sensor == "ld2410c" else hc_sr04)
        log.info("NextUp agent %s for stall %s → %s (%s)", __version__, cfg.stall, cfg.api, sensors)
        if self.stall_music:
            log.info("shower music (experimental): %s, radar gestures %s", self.stall_music.player.describe(),
                     "on" if cfg.presence_sensor == "ld2410c" and cfg.radar_hold else "off (needs the radar with RADAR_HOLD)")
        async with httpx.AsyncClient() as client:
            await asyncio.gather(
                self.kiosk.run(),
                self.uplink.run(client),
                self.heartbeats(),
                self.readings(),
                self.screen_content(client),
                self.music_updates(client),
                *([self.stall_music.run(client)] if self.stall_music else []),
                self.channel.run(client, self.on_message, lambda: self.resync(client),
                                 on_status=lambda ok: self.kiosk.publish(online=ok)),
            )


def main() -> None:
    from .config import load

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "websockets"):  # one line per request or screen reload
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        asyncio.run(Agent(load()).run())
    except KeyboardInterrupt:
        pass
