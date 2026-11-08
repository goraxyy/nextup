"""Experimental: the shower's own music on the stall screen, with lyrics and radar gestures.

Off unless MUSIC is set (see infra/pi/music/README.md). Two ways to follow the music:

- MUSIC=spotify-web: the official Spotify app plays (on the phone, to the speaker over
  Bluetooth) and the Pi follows the signed-in account through Spotify's Web API. This is the
  one that works: in November 2026 Spotify refuses audio to unofficial players.
- MUSIC=spotify: go-librespot on this Pi is a Spotify Connect speaker ("NextUp Stall 1").

Either way the agent:

- polls the player for what's playing and shows it on the screen's lyrics page, with synced
  lyrics fetched from LRCLIB (the same free source the backend uses);
- counts the songs started during each shower: when the third starts (MUSIC_SHAMPOO_AFTER=2)
  the lion says "Let's shampoo!" and bubbles fill the screen, and every MUSIC_NUDGE_EVERY
  songs (5 by default) it asks them to shower faster or lose control of the music;
- reads a hand raised to the radar as a gesture while music is on: up briefly, then away,
  plays or pauses; held for NEXT_S, skips to the next song. A Hurry pop-up always wins: while
  it shows, a raised hand answers the Hurry instead.

Nothing here talks to the NextUp API or database, so turning MUSIC off restores the old behavior.
"""

import asyncio
import json
import logging
import pathlib
import re
import time

import httpx

log = logging.getLogger("nextup.music")

LRCLIB = "https://lrclib.net/api"
LRC_STAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
DRIFT_S = 1.5          # a jump in position bigger than this is a seek; smaller is polling jitter
NUDGE_S = 9.0          # how long the "shower faster" pop-up stays up
SHAMPOO_S = 7.0        # the "Let's shampoo!" moment: cloud, bottle, then a wave of bubbles


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


def web_now_playing(data: dict | None) -> dict | None:
    """Spotify Web API GET /me/player/currently-playing → what the screen needs."""
    item = (data or {}).get("item")
    if not item or (data.get("currently_playing_type") or "track") != "track":
        return None
    return {
        "uri": item.get("uri"),
        "title": item.get("name") or "",
        "artist": ", ".join(a.get("name", "") for a in item.get("artists") or []),
        "album": (item.get("album") or {}).get("name"),
        "duration_s": (item.get("duration_ms") or 0) / 1000 or None,
        "position_s": (data.get("progress_ms") or 0) / 1000,
        "playing": bool(data.get("is_playing")),
    }


class MusicError(Exception):
    pass


class LibrespotPlayer:
    """go-librespot's local API on this Pi (MUSIC=spotify)."""

    poll_s = 1.0

    def __init__(self, api: str) -> None:
        self.api = api.rstrip("/")

    def describe(self) -> str:
        return f"go-librespot at {self.api}"

    async def status(self, client: httpx.AsyncClient) -> dict | None:
        response = await client.get(f"{self.api}/status", timeout=3)
        return now_playing(response.json()) if response.status_code == 200 else None

    async def command(self, client: httpx.AsyncClient, action: str, playing: bool) -> None:
        response = await client.post(f"{self.api}/player/{action}", timeout=3)
        response.raise_for_status()


class SpotifyWebPlayer:
    """Follows one Spotify account through the Web API (MUSIC=spotify-web).

    scripts/spotify_login.py signs the account in once and writes a refresh token to
    SPOTIFY_TOKEN_FILE. Spotify rotates refresh tokens, so each new one is saved back there.
    """

    API = "https://api.spotify.com/v1"
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    poll_s = 2.0  # the Web API is rate limited; gestures poll again right away

    def __init__(self, client_id: str, token_file: pathlib.Path) -> None:
        self.client_id, self.token_file = client_id, pathlib.Path(token_file)
        self.access: str | None = None
        self.expires = 0.0
        self.retry_at = 0.0

    def describe(self) -> str:
        return f"Spotify Web API (token in {self.token_file})"

    async def token(self, client: httpx.AsyncClient) -> str:
        if self.access and time.time() < self.expires - 60:
            return self.access
        try:
            saved = json.loads(self.token_file.read_text())
        except (OSError, ValueError):
            raise MusicError(f"not signed in to Spotify: run scripts/spotify_login.py and copy the token to "
                             f"{self.token_file}") from None
        response = await client.post(self.TOKEN_URL, timeout=10, data={
            "grant_type": "refresh_token", "refresh_token": saved["refresh_token"], "client_id": self.client_id})
        if response.status_code != 200:
            raise MusicError(f"Spotify sign-in was refused ({response.status_code}): sign in again with "
                             "scripts/spotify_login.py")
        body = response.json()
        self.access, self.expires = body["access_token"], time.time() + body.get("expires_in", 3600)
        if body.get("refresh_token") and body["refresh_token"] != saved["refresh_token"]:
            saved["refresh_token"] = body["refresh_token"]
            tmp = self.token_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved))
            tmp.chmod(0o600)
            tmp.replace(self.token_file)
        return self.access

    async def request(self, client: httpx.AsyncClient, method: str, path: str) -> httpx.Response:
        if time.time() < self.retry_at:
            raise MusicError("waiting out Spotify's rate limit")
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {await self.token(client)}"}
            response = await client.request(method, self.API + path, headers=headers, timeout=8)
            if response.status_code == 401 and attempt == 1:
                self.access = None  # expired early: refresh once and retry
                continue
            if response.status_code == 429:
                self.retry_at = time.time() + int(response.headers.get("Retry-After", "5"))
                raise MusicError("Spotify rate limit")
            return response
        return response

    async def status(self, client: httpx.AsyncClient) -> dict | None:
        response = await self.request(client, "GET", "/me/player/currently-playing")
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return web_now_playing(response.json())

    async def command(self, client: httpx.AsyncClient, action: str, playing: bool) -> None:
        method, path = (("POST", "/me/player/next") if action == "next"
                        else ("PUT", "/me/player/pause") if playing else ("PUT", "/me/player/play"))
        response = await self.request(client, method, path)
        if response.status_code >= 400:
            raise MusicError(f"Spotify said {response.status_code} to {path}: {response.text[:120]}")


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


class SongCounter:
    """Counts the songs started during one shower (for the shampoo moment and the nudge).

    Every new song counts, played to the end or skipped; a song on repeat counts once.
    song() returns the new count when a new song starts, else None.
    """

    def __init__(self) -> None:
        self.count = 0
        self.last_uri: str | None = None

    def reset(self) -> None:
        self.count, self.last_uri = 0, None

    def song(self, uri: str | None) -> int | None:
        if not uri or uri == self.last_uri:
            return None
        self.last_uri = uri
        self.count += 1
        return self.count


class StallMusic:
    """Keeps the screen's `local_music` in step with the player and sends it gesture commands."""

    def __init__(self, player, kiosk, in_use=lambda: True, nudge_every: int = 0, shampoo_after: int = 0) -> None:
        self.player, self.kiosk = player, kiosk
        self.in_use = in_use  # songs only count while someone's in the stall
        self.counter = SongCounter()
        self.nudge_every, self.shampoo_after = nudge_every, shampoo_after
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
        count = self.counter.song(np["uri"]) if self.in_use() else None
        if count and self.shampoo_after and count == self.shampoo_after + 1:
            log.info("music: shampoo time (song %d)", count)
            self.kiosk.publish(shampoo={"until": time.time() + SHAMPOO_S})
        if count and self.nudge_every and count % self.nudge_every == 0:
            log.info("music nudge: %d songs this shower", count)
            self.kiosk.publish(music_nudge={"songs": count, "level": count // self.nudge_every,
                                            "until": time.time() + NUDGE_S})
        if self.track != old:
            self.kiosk.publish(local_music={"now_playing": self.track, "lyrics": self.lyrics.get(np["uri"])})

    def new_shower(self) -> None:
        """A shower started or ended: count songs from zero, and drop any nudge on screen."""
        self.counter.reset()
        self.kiosk.publish(music_nudge=None, shampoo=None)

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
                np = await self.player.status(client)
                if np and np["uri"] not in self.lyrics:
                    asyncio.create_task(self.fetch_lyrics(np))
                self.publish(np)
                warned = False
            except (httpx.HTTPError, ValueError, KeyError, MusicError) as exc:
                if not warned:
                    log.warning("can't read the music player (%s): %s", self.player.describe(), exc)
                    warned = True
                self.publish(None)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), self.player.poll_s)
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
        playing = bool(self.track and self.track["playing"])
        feedback = "next" if action == "next" else ("paused" if playing else "playing")
        try:
            await self.player.command(self.client, action, playing)
        except (httpx.HTTPError, MusicError) as exc:
            log.warning("music %s failed: %s", action, exc)
            return
        log.info("music: %s (radar gesture)", action)
        self.kiosk.publish(music_feedback={"action": feedback, "until": time.time() + 1.8})
        self.wake.set()  # show the new state right away
