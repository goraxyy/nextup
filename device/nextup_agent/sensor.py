"""HC-SR04 ultrasonic distance sensor on GPIO, plus a fake sensor for testing.

With PRESENCE_SENSOR=ld2410c, the LD2410C radar (ld2410.py) decides presence and the HC-SR04
is only used for the hand-hold.

Wiring (BCM pin numbers, the defaults):
    VCC  → 5V (pin 2)
    GND  → GND (pin 6)
    TRIG → GPIO23 (pin 16)
    ECHO → GPIO24 (pin 18) through a voltage divider. ECHO outputs 5 V and the Pi's pins
           only take 3.3 V:  ECHO ──1 kΩ──┬── GPIO24
                                          └──2 kΩ── GND

Readings come from gpiozero's DistanceSensor (the lgpio pin factory on Bookworm), sampled
about 10 times a second. No echo within range reads as "nothing there" (None).
"""

import logging
import sys
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("nextup.sensor")


@dataclass(frozen=True)
class Reading:
    distance_cm: float | None  # None: nothing within range
    # Set by the LD2410C radar (ld2410.py), which decides presence itself. None means an HC-SR04
    # distance: presence comes from PRESENT_MAX_CM, and it can also be a hand-hold.
    present: bool | None = None


class UltrasonicSensor:
    def __init__(self, trigger_pin: int = 23, echo_pin: int = 24, max_cm: int = 300, hz: float = 10) -> None:
        self.trigger_pin, self.echo_pin, self.max_cm, self.hz = trigger_pin, echo_pin, max_cm, hz
        self.last_reading_at = 0.0

    def start(self, loop, on_reading) -> None:
        threading.Thread(target=self._run, args=(loop, on_reading), daemon=True, name="hc-sr04").start()

    def _run(self, loop, on_reading) -> None:
        import warnings

        from gpiozero import DistanceSensor  # from apt on the Pi; not needed for tests

        warnings.filterwarnings("ignore", module="gpiozero")  # "no echo received" is normal: nothing in range
        max_m = self.max_cm / 100
        while True:
            try:
                sensor = DistanceSensor(echo=self.echo_pin, trigger=self.trigger_pin, max_distance=max_m, queue_len=3)
                log.info("HC-SR04 on TRIG GPIO%d, ECHO GPIO%d", self.trigger_pin, self.echo_pin)
                while True:
                    meters = sensor.distance
                    distance = None if meters >= max_m * 0.99 else round(meters * 100, 1)
                    self.last_reading_at = time.monotonic()
                    loop.call_soon_threadsafe(on_reading, Reading(distance), self.last_reading_at)
                    time.sleep(1 / self.hz)
            except Exception as exc:  # noqa: BLE001  wiring, permissions, pin in use
                log.warning("HC-SR04 error: %s; retrying in 2 s", exc)
                time.sleep(2)


class FakeSensor:
    """Stands in for the HC-SR04: type in, out or hold (then Enter) on stdin.

    in    someone in the stall, 70 cm away
    out   nobody
    hold  a hand 8 cm away for 2.5 s, then back to "in"
    """

    def __init__(self) -> None:
        self.last_reading_at = 0.0
        self.mode = "out"
        self.hold_until = 0.0

    def start(self, loop, on_reading) -> None:
        threading.Thread(target=self._read_commands, daemon=True, name="fake-sensor-input").start()
        threading.Thread(target=self._emit, args=(loop, on_reading), daemon=True, name="fake-sensor").start()

    def _read_commands(self) -> None:
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd == "hold":
                self.hold_until, self.mode = time.monotonic() + 2.5, "in"
            elif cmd in ("in", "out"):
                self.mode = cmd
            else:
                continue
            log.info("fake sensor: %s", cmd)

    def _reading(self) -> Reading:
        if time.monotonic() < self.hold_until:
            return Reading(8.0)
        return Reading(70.0 if self.mode == "in" else None)

    def _emit(self, loop, on_reading) -> None:
        while True:
            self.last_reading_at = time.monotonic()
            loop.call_soon_threadsafe(on_reading, self._reading(), self.last_reading_at)
            time.sleep(0.1)
