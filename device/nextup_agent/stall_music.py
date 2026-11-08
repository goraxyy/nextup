"""Experimental: the shower's own music, played by this Pi, with lyrics and radar gestures.

Off unless MUSIC=spotify. Then the person showering picks songs in the Spotify app on their
phone (Connect device "NextUp Stall 1", served by go-librespot on this Pi: see
infra/pi/music/README.md), and the agent:

- polls go-librespot's local API for what's playing and shows it on the screen's lyrics page,
  with synced lyrics fetched from LRCLIB (the same free source the backend uses);
- reads a hand raised to the radar as a gesture while music is on: up briefly, then away,
  plays or pauses; held for NEXT_S, skips to the next song. A Hurry pop-up always wins: while
  it shows, a raised hand answers the Hurry instead.

Nothing here talks to the NextUp API or database, so turning MUSIC off restores the old behavior.
"""

import asyncio
import logging
import re
import time

import httpx

log = logging.getLogger("nextup.music")

LRCLIB = "https://lrclib.net/api"
LRC_STAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
POLL_S = 1.0
DRIFT_S = 1.5          # a jump in position bigger than this is a seek; smaller is polling jitter


def parse_lrc(text: str) -> list[list]:
    """LRC text → [[seconds, line], ...] sorted by time (a line may carry several stamps)."""
    lines = []
    for raw in text.splitlines():
        stamps = LRC_STAMP.findall(raw)
        words = LRC_STAMP.sub("", raw).strip()
        lines += [[round(int(m) * 60 + float(s), 2), words] for m, s in stamps]
    return sorted(lines, key=lambda line: line[0])


def now_playing(status: dict | None) -> dict | None:
    """go-librespot's GET /status → what the screen needs, or None when nothing is loaded."""
    track = (status or {}).get("track")
    if not status or status.get("stopped") or not track:
        return None
    return {
        "uri": track.get("uri"),
        "title": track.get("name") or "",
        "artist": ", ".join(track.get("artist_names") or []),
        "album": track.get("album_name") or None,
        "duration_s": (track.get("duration") or 0) / 1000 or None,
        "position_s": (track.get("position") or 0) / 1000,
        "playing": not status.get("paused") and not status.get("buffering"),
    }


class Gesture:
    """A hand raised to the radar, read as a music control.

    update() takes whether a hand is near (Reading.hold_near) and returns (action, progress):
    action is "playpause" (hand up at least TAP_MIN_S, then away), "next" (held NEXT_S) or
    None; progress (0-1) is how far toward "next" the hold is, for the screen. The radar loses
    the hand for moments, so it only counts as away after GRACE_S without it.
    """

    TAP_MIN_S = 0.5
    NEXT_S = 2.5
    GRACE_S = 0.8

    def __init__(self) -> None:
        self.since: float | None = None
        self.last_near = 0.0
        self.fired = False

    def reset(self) -> None:
        self.since, self.fired = None, False

    def update(self, near: bool, now: float) -> tuple[str | None, float]:
        if near:
            self.last_near = now
            if self.since is None:
                self.since, self.fired = now, False
            held = now - self.since
            if held >= self.NEXT_S and not self.fired:
                self.fired = True
                return "next", 1.0
            return None, 1.0 if self.fired else min(1.0, held / self.NEXT_S)
        if self.since is None:
            return None, 0.0
        if now - self.last_near <= self.GRACE_S:  # probably the radar flickering: keep the hold
            return None, 1.0 if self.fired else min(1.0, (self.last_near - self.since) / self.NEXT_S)
        held, fired = self.last_near - self.since, self.fired
        self.reset()
        return ("playpause" if not fired and held >= self.TAP_MIN_S else None), 0.0


class StallMusic:
    """Keeps the screen's `local_music` in step with go-librespot and sends it gesture commands."""

    def __init__(self, api: str, kiosk) -> None:
        self.api, self.kiosk = api.rstrip("/"), kiosk
        self.gesture = Gesture()
        self.client: httpx.AsyncClient | None = None
        self.track: dict | None = None      # what the screen shows now
        self.lyrics: dict[str, list | None] = {}
        self.wake = asyncio.Event()
        self.last_progress = 0.0

    # Screen ------------------------------------------------------------------------------

    def publish(self, np: dict | None) -> None:
        if np is None:
            if self.track is not None:
                self.track = None
                self.kiosk.publish(local_music=None)
            return
        now = time.time()
        started_ms = round((now - np["position_s"]) * 1000)
        old = self.track
        # Keep the start time steady unless the song, the play state or a seek changed it.
        if (old and old["uri"] == np["uri"] and old["playing"] == np["playing"]
                and abs(old["started_at_ms"] - started_ms) < DRIFT_S * 1000):
            started_ms = old["started_at_ms"]
        self.track = {**np, "started_at_ms": started_ms, "local": True}
        if self.track != old:
            self.kiosk.publish(local_music={"now_playing": self.track, "lyrics": self.lyrics.get(np["uri"])})

    async def fetch_lyrics(self, np: dict) -> None:
        uri = np["uri"]
        if uri in self.lyrics:
            return
        self.lyrics[uri] = None  # don't ask twice while this one is in flight
        lines = None
        try:
            params = {"track_name": np["title"], "artist_name": np["artist"]}
            if np["album"]:
                params["album_name"] = np["album"]
            if np["duration_s"]:
                params["duration"] = round(np["duration_s"])
            response = await self.client.get(f"{LRCLIB}/get", params=params, timeout=8)
            if response.status_code == 200 and response.json().get("syncedLyrics"):
                lines = parse_lrc(response.json()["syncedLyrics"])
            else:  # album names and lengths often differ a little between catalogs
                response = await self.client.get(f"{LRCLIB}/search", timeout=8,
                                                 params={"track_name": np["title"], "artist_name": np["artist"]})
                for hit in response.json() if response.status_code == 200 else []:
                    if hit.get("syncedLyrics") and (not np["duration_s"]
                                                    or abs((hit.get("duration") or 0) - np["duration_s"]) <= 5):
                        lines = parse_lrc(hit["syncedLyrics"])
                        break
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("couldn't fetch lyrics for %s: %s", np["title"], exc)
            del self.lyrics[uri]  # try again next time this song comes up
            return
        self.lyrics[uri] = lines
        log.info("lyrics for %s - %s: %s", np["artist"], np["title"], f"{len(lines)} lines" if lines else "none")
        if self.track and self.track["uri"] == uri:
            self.kiosk.publish(local_music={"now_playing": self.track, "lyrics": lines})

    async def run(self, client: httpx.AsyncClient) -> None:
        self.client = client
        warned = False
        while True:
            try:
                response = await client.get(f"{self.api}/status", timeout=3)
                np = now_playing(response.json()) if response.status_code == 200 else None
                if np and np["uri"] not in self.lyrics:
                    asyncio.create_task(self.fetch_lyrics(np))
                self.publish(np)
                warned = False
            except (httpx.HTTPError, ValueError) as exc:
                if not warned:
                    log.warning("music player not reachable at %s (%s): is nextup-spotify running?", self.api, exc)
                    warned = True
                self.publish(None)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), POLL_S)
            except asyncio.TimeoutError:
                pass

    # Gestures -----------------------------------------------------------------------------

    def on_hand(self, near: bool, now: float, hurry_showing: bool) -> None:
        """Called with every radar reading. Gestures only count while a song is loaded."""
        if hurry_showing or self.track is None:
            if self.gesture.since is not None or self.last_progress:
                self.gesture.reset()
                self.show_progress(0.0)
            return
        action, progress = self.gesture.update(near, now)
        self.show_progress(progress)
        if action:
            asyncio.get_running_loop().create_task(self.command(action))

    def show_progress(self, progress: float) -> None:
        if abs(progress - self.last_progress) >= 0.05 or (progress in (0.0, 1.0) and progress != self.last_progress):
            self.last_progress = progress
            self.kiosk.publish(gesture=round(progress, 2))

    async def command(self, action: str) -> None:
        if self.client is None:
            return
        feedback = "next" if action == "next" else ("paused" if self.track and self.track["playing"] else "playing")
        try:
            response = await self.client.post(f"{self.api}/player/{action}", timeout=3)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("music %s failed: %s", action, exc)
            return
        log.info("music: %s (radar gesture)", action)
        self.kiosk.publish(music_feedback={"action": feedback, "until": time.time() + 1.8})
        self.wake.set()  # show the new state right away
