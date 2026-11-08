"""Wires the parts together.

HC-SR04 distances → presence → events to the API (queued) and state to the screen
Supabase Realtime (device:<stall>) → pop-ups, re-chimes and reservations on the screen
"""

import asyncio
import logging
import os
import socket
import time

import httpx

from . import __version__
from .config import Config
from .kiosk import Kiosk
from .presence import Presence
from .sensor import FakeSensor, Reading, UltrasonicSensor
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


class Agent:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.kiosk = Kiosk(cfg)
        self.uplink = Uplink(cfg.api, cfg.device_token, cfg.data_dir)
        self.presence = Presence(cfg)
        self.sensor = (FakeSensor() if cfg.fake_sensor
                       else UltrasonicSensor(cfg.trigger_pin, cfg.echo_pin, cfg.range_cm, cfg.sample_hz))
        self.session = SupabaseSession(cfg.supabase_url, cfg.supabase_key, cfg.data_dir)
        self.channel = DeviceChannel(cfg, self.session)
        self.last_reading: Reading | None = None

    # Sensor → presence ------------------------------------------------------------

    def on_reading(self, reading: Reading, now: float) -> None:
        self.last_reading = reading
        for kind, value in self.presence.update(reading, now):
            if kind == "session.start":
                self.uplink.enqueue("session.start")
                self.kiosk.publish(stall_state="in_use", since=now_iso(), reserved_until=None)
            elif kind == "session.end":
                self.uplink.enqueue("session.end")
                self.kiosk.publish(stall_state="free", since=now_iso(), notify=None)
            elif kind == "hold.progress":
                self.kiosk.publish(hold=value)
            elif kind == "input.hold":
                self.uplink.enqueue("input.hold")
                if self.kiosk.state["notify"]:
                    self.kiosk.publish(notify=None, thanks_until=time.time() + 4)

    def sensor_ok(self) -> bool:
        return time.monotonic() - self.sensor.last_reading_at < 5

    # Supabase → screen ------------------------------------------------------------

    async def on_message(self, event: str, payload: dict) -> None:
        log.info("message %s %s", event, payload)
        if event == "notify.deliver":
            self.kiosk.publish(notify={"notify_id": payload.get("notify_id"), "status": "sent"},
                               chime=self.kiosk.state["chime"] + 1)
            self.uplink.enqueue("notify.delivered")
        elif event == "notify.rechime":
            self.kiosk.publish(chime=self.kiosk.state["chime"] + 1)
        elif event == "notify.closed":
            current = self.kiosk.state["notify"]
            if current and current.get("notify_id") == payload.get("notify_id"):
                self.kiosk.publish(notify=None)
        elif event == "stall.reserved":
            self.kiosk.publish(reserved_until=payload.get("until"))

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
        self.kiosk.publish(stall_name=stall.get("name"), stall_state=self.presence.state, since=stall.get("since"),
                           notify={"notify_id": notify["notify_id"], "status": notify["status"]} if notify else None,
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
                present = r.distance_cm is not None and r.distance_cm <= self.cfg.present_max_cm
                self.uplink.enqueue("reading", presence=present, distance_cm=r.distance_cm, water=False)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self.sensor.start(loop, self.on_reading)
        log.info("NextUp agent %s for stall %s → %s (%s)", __version__, self.cfg.stall, self.cfg.api,
                 "fake sensor" if self.cfg.fake_sensor else f"HC-SR04 TRIG {self.cfg.trigger_pin} ECHO {self.cfg.echo_pin}")
        async with httpx.AsyncClient() as client:
            await asyncio.gather(
                self.kiosk.run(),
                self.uplink.run(client),
                self.heartbeats(),
                self.readings(),
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
