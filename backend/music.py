"""Apple Music now playing (reported by scripts/now_playing_mac.py) and synced lyrics from LRCLIB.

NextUp doesn't play music: the Music app on a Mac does, through the Bluetooth speaker. The Mac
script reports the track and the player position; here that becomes a start time on the server's
clock, so phones and stall screens can show the same lyric line as the speaker.
"""

import datetime as dt
import json
import logging
import re

import httpx

log = logging.getLogger("nextup.music")

LRCLIB = "https://lrclib.net/api"
LRC_LINE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s?(.*)$")
DRIFT_S = 0.75   # smaller jitter in the reported position doesn't count as a change


def track_key(title: str, artist: str | None, duration_s: float | None) -> str:
    return f"{(artist or '').strip().lower()}|{title.strip().lower()}|{round(duration_s or 0)}"


def parse_lrc(text: str) -> list[list]:
    lines = []
    for raw in text.splitlines():
        m = LRC_LINE.match(raw.strip())
        if m:
            lines.append([round(int(m.group(1)) * 60 + float(m.group(2)), 2), m.group(3).strip()])
    return sorted(lines, key=lambda line: line[0])


def fetch_lyrics(title: str, artist: str | None, album: str | None, duration_s: float | None,
                 client: httpx.Client | None = None) -> list[list] | None:
    """Synced lyrics from LRCLIB, or None if it has none for this track."""
    client = client or httpx.Client(timeout=6, headers={"User-Agent": "NextUp (MakeCU project)"})
    params = {"track_name": title, "artist_name": artist or ""}
    if album:
        params["album_name"] = album
    if duration_s:
        params["duration"] = round(duration_s)
    response = client.get(f"{LRCLIB}/get", params=params)
    if response.status_code == 200 and response.json().get("syncedLyrics"):
        return parse_lrc(response.json()["syncedLyrics"])
    # Fall back to a search: album names and lengths often differ a little between catalogs.
    response = client.get(f"{LRCLIB}/search", params={"track_name": title, "artist_name": artist or ""})
    if response.status_code == 200:
        for hit in response.json():
            if hit.get("syncedLyrics") and (not duration_s or abs((hit.get("duration") or 0) - duration_s) <= 5):
                return parse_lrc(hit["syncedLyrics"])
    return None


def lyrics_for(conn, key: str, title: str, artist: str | None, album: str | None, duration_s: float | None,
               fetch=None) -> list[list] | None:
    row = conn.execute("select found, lines from lyrics where track_key = %s", [key]).fetchone()
    if row is not None:
        return row["lines"] if row["found"] else None
    try:
        lines = (fetch or fetch_lyrics)(title, artist, album, duration_s)
    except httpx.HTTPError as exc:
        log.warning("LRCLIB unavailable: %s", exc)
        return None  # not cached: try again on the next report
    conn.execute(
        "insert into lyrics (track_key, found, lines) values (%s, %s, %s::jsonb) on conflict (track_key) do nothing",
        [key, lines is not None, json.dumps(lines) if lines else None],
    )
    return lines


def report(conn, room: str, title: str | None, artist: str | None, album: str | None,
           duration_s: float | None, position_s: float | None, playing: bool, latency_ms: float = 200,
           fetch=None) -> dict:
    """Store what's playing. Writes (and so broadcasts) only when something really changed."""
    now = conn.execute("select now() as now").fetchone()["now"]
    if not title:
        conn.execute("update now_playing set playing = false, position_s = null, updated_at = now()"
                     " where room_id = %s and playing", [room])
        return {"status": "stopped"}

    key = track_key(title, artist, duration_s)
    position = max(0.0, position_s or 0.0)
    started_at = now - dt.timedelta(seconds=position) + dt.timedelta(milliseconds=latency_ms)
    current = conn.execute("select * from now_playing where room_id = %s", [room]).fetchone()

    changed = (current is None or current["track_key"] != key or current["playing"] != playing
               or (playing and abs((current["started_at"] - started_at).total_seconds()) > DRIFT_S)
               or (not playing and abs((current["position_s"] or 0) - position) > DRIFT_S))
    if changed:
        conn.execute(
            "insert into now_playing (room_id, track_key, title, artist, album, duration_s, started_at, playing,"
            " position_s, updated_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())"
            " on conflict (room_id) do update set track_key = excluded.track_key, title = excluded.title,"
            " artist = excluded.artist, album = excluded.album, duration_s = excluded.duration_s,"
            " started_at = excluded.started_at, playing = excluded.playing, position_s = excluded.position_s,"
            " updated_at = now()",
            [room, key, title, artist, album, duration_s, started_at, playing, None if playing else position],
        )
    if current is None or current["track_key"] != key:
        lyrics_for(conn, key, title, artist, album, duration_s, fetch=fetch)
    return {"status": "updated" if changed else "unchanged", "track_key": key}


def current(conn, room: str) -> dict:
    row = conn.execute("select track_key, title, artist, album, duration_s, started_at, playing, position_s,"
                       " updated_at from now_playing where room_id = %s", [room]).fetchone()
    now = conn.execute("select now() as now").fetchone()["now"]
    if row is None or (not row["playing"] and now - row["updated_at"] > dt.timedelta(minutes=30)):
        return {"now_playing": None, "lyrics": None, "server_time": now}
    found = conn.execute("select found, lines from lyrics where track_key = %s", [row["track_key"]]).fetchone()
    return {"now_playing": row, "lyrics": found["lines"] if found and found["found"] else None, "server_time": now}
