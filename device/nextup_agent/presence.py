"""Presence (FREE ↔ IN USE with hysteresis) and the hand-hold detector, from distances.

The HC-SR04 points into the stall. An empty stall reads far (or no echo); a person reads
closer than PRESENT_MAX_CM; a hand held up near the sensor reads closer than HOLD_MAX_CM.
Calibrate both per stall with `python -m device.nextup_agent.calibrate`.
"""

from .config import Config
from .sensor import Reading

HOLD_GRACE_S = 0.3  # one stray reading doesn't break a hand-hold


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
        self.hold_since: float | None = None
        self.hold_last_near = 0.0
        self.hold_fired = False
        self.last_progress = 0.0

    def force(self, state: str) -> None:
        """Adopt the server's state after a restart, so a stale IN USE still ends."""
        self.state = "free" if state == "free" else "in_use"

    def update(self, reading: Reading, now: float) -> list[tuple[str, float | None]]:
        out: list[tuple[str, float | None]] = []
        cfg = self.cfg
        d = reading.distance_cm

        present = d is not None and d <= cfg.present_max_cm
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

        near = d is not None and d <= cfg.hold_max_cm
        if near:
            self.hold_last_near = now
            self.hold_since = self.hold_since if self.hold_since is not None else now
            progress = min(1.0, (now - self.hold_since) / cfg.hold_s)
            if progress - self.last_progress >= 0.05 or (progress == 1.0 and self.last_progress < 1.0):
                self.last_progress = progress
                out.append(("hold.progress", round(progress, 2)))
            if progress >= 1.0 and not self.hold_fired:
                self.hold_fired = True
                out.append(("input.hold", None))
        elif self.hold_since is not None and now - self.hold_last_near > HOLD_GRACE_S:
            # The hand went away: reset, so the next hold needs a full 2 s again.
            self.hold_since, self.hold_fired, self.last_progress = None, False, 0.0
            out.append(("hold.progress", 0.0))
        return out
