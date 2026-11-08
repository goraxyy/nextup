"""HLK-LD2410C 24 GHz mmWave radar on the Pi's UART: is someone in the stall?

Ported from the team's Arduino test sketch (same frame parsing, same tuning). The radar
streams a report about 10 times a second at 256000 baud.

With its RX wired, the agent also sets the radar up when it opens the port (RadarSetup): the
size of its distance gates (0.75 m, or 0.2 m for finer range), the farthest gate it looks at
(from RADAR_MAX_CM), and a sensitivity per gate. The radar stores these, so they are only
written when they differ. It also turns on engineering mode, so each report carries the signal
energy at every gate: that is what the empty-stall calibration measures.

Wiring (physical pin numbers on the Pi 4's 40-pin header):
    VCC → 5V  (pin 4)
    GND → GND (pin 14)
    TX  → GPIO15 / RXD (pin 10). No voltage divider: the LD2410C's UART is 3.3 V.
    RX  → GPIO14 / TXD (pin 8). Needed for the setup commands and calibration.
    OUT → not used.

The UART must be on the GPIO pins with Bluetooth off (`enable_uart=1` and
`dtoverlay=disable-bt` in /boot/firmware/config.txt, no serial console in cmdline.txt), so
/dev/serial0 is /dev/ttyAMA0, and the agent's user must be in the `dialout` group.

Report frame (all multi-byte numbers little-endian):
    F4 F3 F2 F1 | length (2) | data (length bytes) | F8 F7 F6 F5
    data: type (02 basic, 01 engineering), AA, target state (bit 0 moving, bit 1 still),
          moving distance cm (2), moving energy, still distance cm (2), still energy,
          detection distance (2), then in engineering mode: the last moving and still gates
          (1 byte each, N), moving energy at gates 0..N, still energy at gates 0..N, ...

Command frame:  FD FC FB FA | length (2) | command word (2) + value | 04 03 02 01
The radar answers with the same framing: command word | 0x0100, a status word (0 = OK), data.
"""

import dataclasses
import datetime as dt
import json
import logging
import math
import pathlib
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

GATES = 9               # distance gates 0-8
GATE_SIZES = (75, 20)   # cm per gate: the radar's default, or the finer one (firmware 2.44+)
CMD_HEADER = bytes.fromhex("fdfcfbfa")
CMD_FOOTER = bytes.fromhex("04030201")
CAL_FILE = "radar-calibration.json"  # in the agent's data dir: per-gate sensitivities from calibration
CAL_TRIGGER = "radar-calibrate"      # create this file there to calibrate (calibrate.py --radar-auto does)
CAL_MARGIN = 10   # a gate's threshold = the strongest energy seen there in the empty stall + this
CAL_FLOOR = 10    # no gate gets a threshold below this


@dataclass(frozen=True)
class RadarFrame:
    state: int            # bit 0: moving target, bit 1: still target
    moving_cm: int
    moving_energy: int    # 0-100
    still_cm: int
    still_energy: int     # 0-100
    # Engineering mode only: the energy (0-100) at each distance gate, nearest first.
    moving_gates: tuple[int, ...] = ()
    still_gates: tuple[int, ...] = ()


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
                moving_gates = still_gates = ()
                if data[0] == 0x01 and len(data) >= 13:  # engineering mode: energies per gate
                    n_moving, n_still = data[11], data[12]
                    if n_moving < GATES and n_still < GATES and len(data) >= 15 + n_moving + n_still:
                        moving_gates = tuple(data[13 : 14 + n_moving])
                        still_gates = tuple(data[14 + n_moving : 15 + n_moving + n_still])
                frames.append(RadarFrame(
                    state=data[2],
                    moving_cm=data[3] | (data[4] << 8),
                    moving_energy=data[5],
                    still_cm=data[6] | (data[7] << 8),
                    still_energy=data[8],
                    moving_gates=moving_gates,
                    still_gates=still_gates,
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


# Setup commands -----------------------------------------------------------------------


class RadarError(Exception):
    pass


def command(word: int, value: bytes = b"") -> bytes:
    body = word.to_bytes(2, "little") + value
    return CMD_HEADER + len(body).to_bytes(2, "little") + body + CMD_FOOTER


def _param(word: int, value: int) -> bytes:
    return word.to_bytes(2, "little") + value.to_bytes(4, "little")


class AckParser:
    """Finds the radar's answers to commands in the byte stream: (command word, status, data)."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, int, bytes]]:
        self.buf += data
        out = []
        while True:
            start = self.buf.find(CMD_HEADER)
            if start < 0:
                del self.buf[: max(0, len(self.buf) - (len(CMD_HEADER) - 1))]
                return out
            del self.buf[:start]
            if len(self.buf) < 6:
                return out
            length = self.buf[4] | (self.buf[5] << 8)
            if not 4 <= length <= MAX_LEN:
                del self.buf[:1]
                continue
            end = 6 + length + 4
            if len(self.buf) < end:
                return out
            body = bytes(self.buf[6 : 6 + length])
            if bytes(self.buf[6 + length : end]) == CMD_FOOTER:
                out.append((body[0] | (body[1] << 8), body[2] | (body[3] << 8), body[4:]))
                del self.buf[:end]
            else:
                del self.buf[:1]


@dataclass(frozen=True)
class RadarParams:
    """What the radar has stored (its answer to command 0x0061)."""
    max_moving_gate: int
    max_still_gate: int
    moving_sens: tuple[int, ...]   # per gate: the energy needed to count (higher = less sensitive)
    still_sens: tuple[int, ...]
    nobody_s: int                  # how long the radar keeps reporting a target after it has gone


def parse_params(data: bytes) -> RadarParams:
    n = data[1]
    return RadarParams(data[2], data[3], tuple(data[4 : 5 + n]), tuple(data[5 + n : 6 + 2 * n]),
                       data[6 + 2 * n] | (data[7 + 2 * n] << 8))


@dataclass(frozen=True)
class RadarSetup:
    """What the agent writes to the radar when it opens the port."""
    gate_cm: int = 75                           # 75, or 20 for finer gates (out to ~1.8 m)
    max_cm: int = 150                           # sets the farthest gate the radar looks at
    moving_sens: tuple[int, ...] | None = None  # 9 values; None leaves the radar's own
    still_sens: tuple[int, ...] | None = None
    nobody_s: int | None = None

    def max_gate(self) -> int:
        """The last gate that starts inside max_cm (the radar allows 2 to 8)."""
        return max(2, min(GATES - 1, math.ceil(self.max_cm / self.gate_cm) - 1))


class RadarLink:
    """Send a command and wait for its answer, skipping the report frames in between."""

    def __init__(self, port, timeout: float = 1.0) -> None:
        self.port, self.timeout, self.acks = port, timeout, AckParser()

    def send(self, word: int, value: bytes = b"") -> bytes:
        self.port.write(command(word, value))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            for answered, status, data in self.acks.feed(self.port.read(64)):
                if answered == word | 0x0100:
                    if status:
                        raise RadarError(f"the radar refused command {word:#06x}")
                    return data
        raise RadarError(f"no answer to command {word:#06x}: is the radar's RX wired to GPIO14 (pin 8)?")


def configure(port, setup: RadarSetup, engineering: bool = True, sleep=time.sleep) -> tuple[int, RadarParams]:
    """Write `setup` to the radar (only what differs); returns (gate size in cm, its settings now)."""
    link = RadarLink(port)
    link.send(0x00FF, b"\x01\x00")  # enter setup mode
    try:
        gate_cm = 20 if link.send(0x00AB)[:1] == b"\x01" else 75
        if gate_cm != setup.gate_cm:
            link.send(0x00AA, (1 if setup.gate_cm == 20 else 0).to_bytes(2, "little"))
            link.send(0x00A3)  # restart the radar: the new gate size applies after it
            sleep(2.0)
            port.reset_input_buffer()
            link.acks = AckParser()
            link.send(0x00FF, b"\x01\x00")
            gate_cm = setup.gate_cm
        now = parse_params(link.send(0x0061))
        gate = setup.max_gate()
        nobody = now.nobody_s if setup.nobody_s is None else setup.nobody_s
        if (now.max_moving_gate, now.max_still_gate, now.nobody_s) != (gate, gate, nobody):
            link.send(0x0060, _param(0, gate) + _param(1, gate) + _param(2, nobody))
        for g in range(GATES):
            want = (setup.moving_sens[g] if setup.moving_sens else now.moving_sens[g],
                    setup.still_sens[g] if setup.still_sens else now.still_sens[g])
            if want != (now.moving_sens[g], now.still_sens[g]):
                try:
                    link.send(0x0064, _param(0, g) + _param(1, want[0]) + _param(2, want[1]))
                except RadarError as exc:  # some firmware won't take a still sensitivity for gates 0-1
                    log.warning("gate %d: %s", g, exc)
        link.send(0x0062 if engineering else 0x0063)
        return gate_cm, parse_params(link.send(0x0061))
    finally:
        try:
            link.send(0x00FE)  # leave setup mode: reports start again
        except RadarError:
            pass


class Calibration:
    """Records the strongest energy at each gate while the stall is empty."""

    def __init__(self, seconds: float, margin: int, now: float) -> None:
        self.seconds, self.margin, self.until = seconds, margin, now + seconds
        self.moving, self.still, self.frames = [0] * GATES, [0] * GATES, 0

    def add(self, frame: RadarFrame) -> None:
        if not frame.moving_gates:
            return
        self.frames += 1
        for g, e in enumerate(frame.moving_gates):
            self.moving[g] = max(self.moving[g], e)
        for g, e in enumerate(frame.still_gates):
            self.still[g] = max(self.still[g], e)

    def thresholds(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        def pick(peaks):
            return tuple(min(100, max(CAL_FLOOR, e + self.margin)) for e in peaks)
        return pick(self.moving), pick(self.still)


def gates_text(energies) -> str:
    return " ".join(f"{e:3d}" for e in energies)


class OccupancyFilter:
    """The sketch's score with hysteresis: call update() once per 100 ms sample.

    score_down and free_hold_s set how fast it lets go (RADAR_SCORE_DOWN, RADAR_FREE_HOLD_SECONDS):
    faster frees the stall sooner after someone leaves, but risks a "free" blip while someone
    stands very still.
    """

    def __init__(self, score_down: int = SCORE_DOWN, free_hold_s: float = FREE_HOLD_S) -> None:
        self.score_down, self.free_hold_s = score_down, free_hold_s
        self.score = 0
        self.occupied = False
        self.low_since: float | None = None

    def update(self, raw: bool, now: float) -> bool:
        self.score = max(0, min(100, self.score + (SCORE_UP if raw else -self.score_down)))
        if not self.occupied and self.score >= OCCUPIED_ABOVE:
            self.occupied, self.low_since = True, None
        elif self.occupied:
            if self.score <= EMPTY_BELOW:
                self.low_since = self.low_since if self.low_since is not None else now
                if now - self.low_since >= self.free_hold_s:
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
                 hold_min_energy: int = 50, setup: RadarSetup | None = None,
                 data_dir: pathlib.Path | None = None, score_down: int = SCORE_DOWN,
                 free_hold_s: float = FREE_HOLD_S) -> None:
        self.port, self.baud, self.max_cm, self.min_energy = port, baud, max_cm, min_energy
        self.setup, self.data_dir = setup, data_dir
        self.gate_cm: int | None = None
        self.calibration: Calibration | None = None
        self.lost = False  # went silent (unplugged): set it up again when it's back
        self.min_cm, self.debug = min_cm, debug
        self.hold, self.hold_max_cm, self.hold_min_energy = hold, hold_max_cm, hold_min_energy
        self.last_reading_at = 0.0
        self.frame: RadarFrame | None = None
        self.frame_at = 0.0
        self.filter = OccupancyFilter(score_down, free_hold_s)
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
            if f.moving_gates:
                log.info("radar gates: moving %s | still %s", gates_text(f.moving_gates), gates_text(f.still_gates))
        return Reading(distance_cm=nearest_cm(f), present=occupied, hold_near=self.hand_near(f) if self.hold else None)

    def hand_near(self, f: RadarFrame) -> bool:
        """A hand raised to the radar: strong movement within RADAR_HOLD_MAX_CM of it.

        The radar reports one moving target, the strongest, and with a hand up it jumps between
        the hand and the arm or body behind it. With 20 cm gates the energy at the nearest gates
        is steadier, so that decides; otherwise the reported target does.
        """
        if f.moving_gates and self.gate_cm and self.gate_cm <= self.hold_max_cm:
            return max(f.moving_gates[: math.ceil(self.hold_max_cm / self.gate_cm)]) >= self.hold_min_energy
        return bool(f.state & 1) and 0 < f.moving_cm <= self.hold_max_cm and f.moving_energy >= self.hold_min_energy

    # Setup and calibration ---------------------------------------------------------------

    def stored_calibration(self) -> dict | None:
        path = self.data_dir / CAL_FILE if self.data_dir else None
        if not path or not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return data if len(data.get("moving_sens", ())) == GATES == len(data.get("still_sens", ())) else None

    def apply_setup(self, port, setup: RadarSetup | None = None) -> None:
        """Write the setup to the radar. Sensitivities: RADAR_*_SENS if set, else the last calibration."""
        setup = setup or self.setup
        if setup is None:
            return
        cal = self.stored_calibration()
        if cal and setup.moving_sens is None and setup.still_sens is None:
            setup = dataclasses.replace(setup, moving_sens=tuple(cal["moving_sens"]), still_sens=tuple(cal["still_sens"]))
        try:
            self.gate_cm, params = configure(port, setup)
        except RadarError as exc:
            log.warning("couldn't set up the LD2410C (%s); it keeps its own settings", exc)
            return
        log.info("LD2410C set up: %d cm gates, out to gate %d (~%d cm), nobody after %d s", self.gate_cm,
                 params.max_moving_gate, (params.max_moving_gate + 1) * self.gate_cm, params.nobody_s)
        source = "calibrated" if cal and setup.moving_sens == tuple(cal["moving_sens"]) else "from settings"
        log.info("LD2410C sensitivity per gate (%s): moving %s | still %s", source,
                 gates_text(params.moving_sens), gates_text(params.still_sens))

    def check_calibration_request(self, now: float) -> None:
        trigger = self.data_dir / CAL_TRIGGER if self.data_dir else None
        if self.calibration or not trigger or not trigger.exists():
            return
        try:
            request = json.loads(trigger.read_text() or "{}")
        except (OSError, ValueError):
            request = {}
        trigger.unlink(missing_ok=True)
        self.calibration = Calibration(float(request.get("seconds", 20)), int(request.get("margin", CAL_MARGIN)), now)
        log.info("radar calibration: keep the stall empty for %.0f s", self.calibration.seconds)

    def finish_calibration(self, port) -> None:
        cal, self.calibration = self.calibration, None
        if not cal.frames:
            result = {"error": "no per-gate energies: the radar isn't in engineering mode (is its RX wired to GPIO14?)"}
            log.warning("radar calibration failed: %s", result["error"])
        else:
            moving, still = cal.thresholds()
            result = {"calibrated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                      "gate_cm": self.gate_cm, "seconds": cal.seconds, "margin": cal.margin, "frames": cal.frames,
                      "empty_moving_max": cal.moving, "empty_still_max": cal.still,
                      "moving_sens": list(moving), "still_sens": list(still)}
            log.info("radar calibration: empty stall peaks moving %s | still %s",
                     gates_text(cal.moving), gates_text(cal.still))
            if self.setup and (self.setup.moving_sens or self.setup.still_sens):
                log.warning("RADAR_MOVING_SENS / RADAR_STILL_SENS are set, so they replace this calibration at the"
                            " next restart; remove them to keep it")
        path = self.data_dir / CAL_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, indent=1))
        tmp.replace(path)
        if "error" not in result:
            self.apply_setup(port, dataclasses.replace(self.setup or RadarSetup(max_cm=self.max_cm),
                                                       moving_sens=moving, still_sens=still))

    def _run(self, loop, on_reading) -> None:
        import serial  # pyserial; only needed on the Pi

        while True:
            try:
                with serial.Serial(self.port, self.baud, timeout=SAMPLE_S / 2) as port:
                    log.info("LD2410C on %s at %d baud", self.port, self.baud)
                    self.apply_setup(port)
                    opened_at = time.monotonic()
                    parser, next_sample, warned_at, checked_at = FrameParser(), opened_at, 0.0, 0.0
                    while True:
                        got = False
                        for frame in parser.feed(port.read(256)):
                            self.frame, self.frame_at, got = frame, time.monotonic(), True
                            if self.calibration:
                                self.calibration.add(frame)
                        if got and self.lost:
                            # Power came back (re-plugged): its settings are in its memory, but
                            # engineering mode isn't, so set it up again.
                            self.lost = False
                            log.info("LD2410C is back on %s: setting it up again", self.port)
                            self.apply_setup(port)
                            parser = FrameParser()
                        now = time.monotonic()
                        if now - checked_at >= 1.0:
                            checked_at = now
                            self.check_calibration_request(now)
                        if self.calibration and now >= self.calibration.until:
                            self.finish_calibration(port)
                            parser = FrameParser()
                        if now < next_sample:
                            continue
                        next_sample = now + SAMPLE_S
                        reading = self.sample(now)
                        if reading is None:
                            # Warn only after STALE_S of real silence, not before the first frame arrives.
                            if self.frame_at and now - self.frame_at > STALE_S:
                                self.lost = True
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
