"""Experimental: "How long will your shower be?" chosen with a hand raised to the sensor.

When a shower starts, the screen asks for a goal (Quick 5 min, Normal 10, Long 15 by default,
GOAL_OPTIONS). The person raises a hand; after ARM_S a highlight moves along the options, one
every STEP_S, wrapping around; they lower the hand when their pick is lit. Doing nothing for
GOAL_ASK_SECONDS skips it quietly. The goal then drives the screen's countdown and the exit
celebration. GOAL_ASK_SECONDS=0 turns it off.

Like the music gestures, the hand comes from Reading.hold_near (the radar) and gaps up to
GRACE_S are forgiven, because the radar loses a hand for moments.
"""

import time

ARM_S = 1.0      # hold this long before the highlight starts, so moving around doesn't pick anything
STEP_S = 1.2     # then each option is lit this long
GRACE_S = 0.8    # the hand only counts as lowered after this long without it
CONFIRM_S = 3.0  # "10 minutes, got it!" stays up this long


class GoalChooser:
    """update() returns the new screen state (`goal_ask`) when it changes, else None."""

    def __init__(self, options: tuple[int, ...], ask_s: float) -> None:
        self.options, self.ask_s = options, ask_s
        self.asking = False
        self.until = 0.0
        self.since: float | None = None   # when the hand went up
        self.last_near = 0.0
        self.goal: int | None = None       # minutes, once chosen
        self.shown: dict | None = None

    def start(self, now: float, wall: float | None = None) -> dict | None:
        """A shower started: ask. `wall` is time.time(), for the screen's countdown."""
        self.asking, self.goal, self.since = bool(self.ask_s and self.options), None, None
        self.until = now + self.ask_s
        self.wall_offset = (wall if wall is not None else time.time()) - now
        return self._show(self._state(now)) if self.asking else None

    def stop(self) -> None:
        self.asking, self.goal, self.since, self.shown = False, None, None, None

    def _highlight(self, held: float) -> int:
        return -1 if held < ARM_S else int((held - ARM_S) / STEP_S) % len(self.options)

    def _state(self, now: float, highlight: int = -1, arming: float = 0.0) -> dict:
        return {"options": list(self.options), "highlight": highlight, "arming": round(arming, 1),
                "until": round(self.until + self.wall_offset, 1), "chosen": None}

    def _show(self, state: dict) -> dict | None:
        if state == self.shown:
            return None
        self.shown = state
        return state

    def update(self, near: bool, now: float) -> dict | None:
        if not self.asking:
            return None
        if near:
            self.last_near = now
            if self.since is None:
                self.since = now
            held = now - self.since
            return self._show(self._state(now, self._highlight(held), min(1.0, held / ARM_S)))
        if self.since is not None and now - self.last_near <= GRACE_S:  # a radar gap: still holding
            return None
        if self.since is not None:  # the hand came down: whatever was lit when it was last seen
            pick = self._highlight(self.last_near - self.since)
            self.since = None
            if pick >= 0:
                self.goal, self.asking = self.options[pick], False
                return self._show({**self._state(now), "chosen": self.goal,
                                   "until": round(now + CONFIRM_S + self.wall_offset, 1)})
            return self._show(self._state(now))
        if now >= self.until:  # nobody answered: skip quietly
            self.asking = False
            self.shown = None
            return {"skipped": True}
        return None
