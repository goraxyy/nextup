"""Presence (FREE ↔ IN USE with hysteresis) and the hand-hold detector.

With PRESENCE_SENSOR=ultrasonic (the default), the HC-SR04 does both: an empty stall reads far
(or no echo), a person reads closer than PRESENT_MAX_CM, and a hand held up near the sensor
reads closer than HOLD_MAX_CM. Calibrate with `python -m device.nextup_agent.calibrate`.

With PRESENCE_SENSOR=ld2410c, the radar's readings (Reading.present set) decide presence and
the HC-SR04's readings are only used for the hand-hold. Radar distances never count as a hold.
"""

from .config import Config
from .sensor import Reading

HOLD_GRACE_S = 0.3        # HC-SR04: one stray reading doesn't break a hand-hold
# The radar's target jumps between the hand and the arm or body behind it, and it sometimes
# reports no moving target for a moment while the hand is still there: forgive longer gaps.
RADAR_HOLD_GRACE_S = 1.0
# After the grace, the ring drains back at twice the speed it filled, instead of starting over:
# a slip costs a little of the hold, not all of it.
HOLD_DRAIN = 2.0


class Presence:
    """Feed it readings; it returns what happened.

    Outputs are (kind, value) pairs: ("session.start", None), ("session.end", None),
    ("hold.progress", 0.0-1.0) and ("input.hold", None).
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.state = "free"
        self.present_since: float | None = None
        self.absent_since: float | None = None
        self.hold_progress = 0.0   # 0-1: how much of HOLD_SECONDS the hand has been held
        self.hold_at: float | None = None  # time of the last reading seen by _hold
        self.hold_last_near = 0.0
        self.hold_fired = False
        self.last_progress = 0.0   # the last progress sent to the screen
        # Holds only count while the Hurry pop-up is on the screen (the agent arms this), so moving
        # around in the shower can't acknowledge anything by accident.
        self.hold_armed = False

    def force(self, state: str) -> None:
        """Adopt the server's state after a restart, so a stale IN USE still ends."""
        self.state = "free" if state == "free" else "in_use"

    def update(self, reading: Reading, now: float) -> list[tuple[str, float | None]]:
        out: list[tuple[str, float | None]] = []
        radar = reading.present is not None
        if radar or self.cfg.presence_sensor != "ld2410c":
            out += self._presence(reading, now)
        if radar:
            if reading.hold_near is not None:  # RADAR_HOLD: a hand raised to the radar
                out += self._hold(reading.hold_near, now, RADAR_HOLD_GRACE_S)
        else:
            d = reading.distance_cm
            out += self._hold(d is not None and d <= self.cfg.hold_max_cm, now, HOLD_GRACE_S)
        return out

    def _presence(self, reading: Reading, now: float) -> list[tuple[str, float | None]]:
        out: list[tuple[str, float | None]] = []
        cfg = self.cfg
        d = reading.distance_cm

        present = reading.present if reading.present is not None else (d is not None and d <= cfg.present_max_cm)
        if present:
            self.absent_since = None
            self.present_since = self.present_since if self.present_since is not None else now
        else:
            self.present_since = None
            self.absent_since = self.absent_since if self.absent_since is not None else now

        if self.state == "free" and present and now - self.present_since >= cfg.enter_s:
            self.state = "in_use"
            out.append(("session.start", None))
        elif self.state == "in_use" and not present and now - self.absent_since >= cfg.leave_s:
            self.state = "free"
            out.append(("session.end", None))
        return out

    def _hold(self, near: bool, now: float, grace: float) -> list[tuple[str, float | None]]:
        out: list[tuple[str, float | None]] = []
        dt = 0.0 if self.hold_at is None else max(0.0, min(0.5, now - self.hold_at))  # a long gap counts as 0.5 s
        self.hold_at = now
        if not self.hold_armed:
            if self.hold_progress or self.last_progress:
                self.hold_progress, self.hold_fired, self.last_progress = 0.0, False, 0.0
                out.append(("hold.progress", 0.0))
            return out
        if near:
            self.hold_last_near = now
            self.hold_progress = min(1.0, self.hold_progress + dt / self.cfg.hold_s)
        elif now - self.hold_last_near > grace:  # the hand went away: drain, don't start over
            self.hold_progress = max(0.0, self.hold_progress - dt * HOLD_DRAIN / self.cfg.hold_s)
        progress = 1.0 if self.hold_progress > 1 - 1e-6 else round(self.hold_progress, 2)
        if progress == 0.0:
            self.hold_fired = False  # fully drained: the next hold can count again
        if abs(progress - self.last_progress) >= 0.05 or (progress in (0.0, 1.0) and progress != self.last_progress):
            self.last_progress = progress
            out.append(("hold.progress", progress))
        if progress == 1.0 and not self.hold_fired:
            self.hold_fired = True
            out.append(("input.hold", None))
        return out
