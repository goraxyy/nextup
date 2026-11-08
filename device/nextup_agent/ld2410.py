"""HLK-LD2410C 24 GHz mmWave radar on the Pi's UART: is someone in the stall?

Ported from the team's Arduino test sketch (same frame parsing, same tuning). The radar
streams a report about 10 times a second at 256000 baud; nothing needs to be sent to it.

Wiring (physical pin numbers on the Pi 4's 40-pin header):
    VCC → 5V  (pin 4)
    GND → GND (pin 14)
    TX  → GPIO15 / RXD (pin 10). No voltage divider: the LD2410C's UART is 3.3 V.
    RX  → GPIO14 / TXD (pin 8). Optional; only needed to send setup commands.
    OUT → not used.

The UART must be on the GPIO pins with Bluetooth off (`enable_uart=1` and
`dtoverlay=disable-bt` in /boot/firmware/config.txt, no serial console in cmdline.txt), so
/dev/serial0 is /dev/ttyAMA0, and the agent's user must be in the `dialout` group.

Report frame (all multi-byte numbers little-endian):
    F4 F3 F2 F1 | length (2) | data (length bytes) | F8 F7 F6 F5
    data: type (02 basic, 01 engineering), AA, target state (bit 0 moving, bit 1 still),
          moving distance cm (2), moving energy, still distance cm (2), still energy, ...
"""

import logging
import threading
import time
from dataclasses import dataclass

from .sensor import Reading

log = logging.getLogger("nextup.ld2410")

HEADER = bytes.fromhex("f4f3f2f1")
FOOTER = bytes.fromhex("f8f7f6f5")
MIN_LEN, MAX_LEN = 9, 64

# Occupancy filter, from the Arduino sketch: every 100 ms the score rises by SCORE_UP when the
# radar sees a target and falls by SCORE_DOWN when it doesn't. Occupied once the score reaches
# OCCUPIED_ABOVE; free again once it has stayed at or below EMPTY_BELOW for FREE_HOLD_S.
SAMPLE_S = 0.1
SCORE_UP = 10
SCORE_DOWN = 4
OCCUPIED_ABOVE = 70
EMPTY_BELOW = 10
FREE_HOLD_S = 1.0
STALE_S = 2.0  # no frame for this long: the radar is unplugged or the wiring is loose


@dataclass(frozen=True)
class RadarFrame:
    state: int            # bit 0: moving target, bit 1: still target
    moving_cm: int
    moving_energy: int    # 0-100
    still_cm: int
    still_energy: int     # 0-100


class FrameParser:
    """Feed it raw bytes in any chunk sizes; it returns the complete, valid frames."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[RadarFrame]:
        self.buf += data
        frames = []
        while True:
            start = self.buf.find(HEADER)
            if start < 0:
                del self.buf[: max(0, len(self.buf) - (len(HEADER) - 1))]  # keep a possible partial header
                return frames
            del self.buf[:start]
            if len(self.buf) < 6:
                return frames
            length = self.buf[4] | (self.buf[5] << 8)
            if not MIN_LEN <= length <= MAX_LEN:
                del self.buf[:1]  # not a real header: look for the next one
                continue
            end = 6 + length + 4
            if len(self.buf) < end:
                return frames
            data, footer = bytes(self.buf[6 : 6 + length]), bytes(self.buf[6 + length : end])
            if footer == FOOTER and data[1] == 0xAA:
                frames.append(RadarFrame(
                    state=data[2],
                    moving_cm=data[3] | (data[4] << 8),
                    moving_energy=data[5],
                    still_cm=data[6] | (data[7] << 8),
                    still_energy=data[8],
                ))
                del self.buf[:end]
            else:
                del self.buf[:1]


def sees_someone(frame: RadarFrame, max_cm: int, min_energy: int, min_cm: int = 0) -> bool:
    """A moving or still target inside the stall and strong enough (the sketch's `raw`).

    min_cm ignores targets right in front of the radar, such as a plastic cover that flexes.
    """
    moving = (bool(frame.state & 1) and min_cm <= frame.moving_cm <= max_cm
              and frame.moving_energy >= min_energy)
    still = bool(frame.state & 2) and min_cm <= frame.still_cm <= max_cm and frame.still_energy >= min_energy
    return moving or still


def nearest_cm(frame: RadarFrame) -> int | None:
    distances = [d for bit, d in ((1, frame.moving_cm), (2, frame.still_cm)) if frame.state & bit]
    return min(distances) if distances else None


class OccupancyFilter:
    """The sketch's score with hysteresis: call update() once per 100 ms sample."""

    def __init__(self) -> None:
        self.score = 0
        self.occupied = False
        self.low_since: float | None = None

    def update(self, raw: bool, now: float) -> bool:
        self.score = max(0, min(100, self.score + (SCORE_UP if raw else -SCORE_DOWN)))
        if not self.occupied and self.score >= OCCUPIED_ABOVE:
            self.occupied, self.low_since = True, None
        elif self.occupied:
            if self.score <= EMPTY_BELOW:
                self.low_since = self.low_since if self.low_since is not None else now
                if now - self.low_since >= FREE_HOLD_S:
                    self.occupied, self.low_since = False, None
            else:
                self.low_since = None
        return self.occupied


class LD2410Sensor:
    """Reads the radar in a thread and reports a presence Reading every 100 ms.

    Readings stop when no frame has arrived for STALE_S, so the agent's "sensor" dot goes off.
    """

    def __init__(self, port: str = "/dev/serial0", baud: int = 256000, max_cm: int = 150, min_energy: int = 30,
                 min_cm: int = 0, debug: bool = False, hold: bool = False, hold_max_cm: int = 30,
                 hold_min_energy: int = 50) -> None:
        self.port, self.baud, self.max_cm, self.min_energy = port, baud, max_cm, min_energy
        self.min_cm, self.debug = min_cm, debug
        self.hold, self.hold_max_cm, self.hold_min_energy = hold, hold_max_cm, hold_min_energy
        self.last_reading_at = 0.0
        self.frame: RadarFrame | None = None
        self.frame_at = 0.0
        self.filter = OccupancyFilter()
        self.debug_at = 0.0

    def start(self, loop, on_reading) -> None:
        threading.Thread(target=self._run, args=(loop, on_reading), daemon=True, name="ld2410c").start()

    def sample(self, now: float) -> Reading | None:
        """One 100 ms step of the filter, or None while the radar is silent."""
        if self.frame is None or now - self.frame_at > STALE_S:
            return None
        f = self.frame
        raw = sees_someone(f, self.max_cm, self.min_energy, self.min_cm)
        occupied = self.filter.update(raw, now)
        if self.debug and now - self.debug_at >= 1.0:  # RADAR_DEBUG=1: one line a second, for tuning
            self.debug_at = now
            moving = f"{f.moving_cm:4d} cm E{f.moving_energy:3d}" if f.state & 1 else "   -          "
            still = f"{f.still_cm:4d} cm E{f.still_energy:3d}" if f.state & 2 else "   -          "
            log.info("radar: moving %s | still %s | counts %-3s | score %3d %s",
                     moving, still, "yes" if raw else "no", self.filter.score, "OCCUPIED" if occupied else "free")
        hold_near = None
        if self.hold:  # a hand raised to the radar: a strong moving target very close to it
            hold_near = bool(f.state & 1) and 0 < f.moving_cm <= self.hold_max_cm and f.moving_energy >= self.hold_min_energy
        return Reading(distance_cm=nearest_cm(f), present=occupied, hold_near=hold_near)

    def _run(self, loop, on_reading) -> None:
        import serial  # pyserial; only needed on the Pi

        while True:
            try:
                with serial.Serial(self.port, self.baud, timeout=SAMPLE_S / 2) as port:
                    log.info("LD2410C on %s at %d baud", self.port, self.baud)
                    opened_at = time.monotonic()
                    parser, next_sample, warned_at = FrameParser(), opened_at, 0.0
                    while True:
                        for frame in parser.feed(port.read(256)):
                            self.frame, self.frame_at = frame, time.monotonic()
                        now = time.monotonic()
                        if now < next_sample:
                            continue
                        next_sample = now + SAMPLE_S
                        reading = self.sample(now)
                        if reading is None:
                            # Warn only after STALE_S of real silence, not before the first frame arrives.
                            if now - max(opened_at, self.frame_at) > STALE_S and now - warned_at > 30:
                                log.warning("no data from the LD2410C on %s: check TX → GPIO15 (pin 10), 5V and GND",
                                            self.port)
                                warned_at = now
                            continue
                        self.last_reading_at = now
                        loop.call_soon_threadsafe(on_reading, reading, now)
            except Exception as exc:  # noqa: BLE001  port missing, permissions, unplugged
                log.warning("LD2410C error on %s: %s; retrying in 2 s", self.port, exc)
                time.sleep(2)
