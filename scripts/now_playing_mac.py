"""Follow Apple Music on this Mac and report what's playing to NextUp, for synced lyrics.

    NEXTUP_AUDIO_TOKEN=... python3 scripts/now_playing_mac.py [--room r1] [--latency-ms 200]

Play the demo playlist in the Music app, with this Mac's sound going to the Bluetooth speaker.
Once a second this asks the Music app for the current track, whether it's playing, and the player
position, and tells the API when the song changes, pauses, resumes or jumps, and every 10 s
otherwise. macOS asks once whether Terminal may control Music: allow it.

--latency-ms shifts the lyrics to match what you hear: Bluetooth speakers play about 0.1 to
0.3 s late. If the words on the screen run ahead of the singer, raise it; if behind, lower it.

Only the Python standard library is used, so any Mac with python3 can run it.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

JXA = """
const music = Application('Music');
if (!music.running()) {
  JSON.stringify({running: false});
} else {
  let track = null;
  try {
    const t = music.currentTrack();
    track = {title: t.name(), artist: t.artist(), album: t.album(), duration: t.duration()};
  } catch (e) {}
  JSON.stringify({running: true, state: music.playerState(), position: music.playerPosition(), track: track});
}
"""


def read_music() -> dict:
    out = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA], capture_output=True, text=True, timeout=5)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "osascript failed")
    return json.loads(out.stdout)


def post(api: str, room: str, token: str, body: dict) -> dict:
    req = urllib.request.Request(f"{api}/api/rooms/{room}/nowplaying", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Audio-Token": token}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=os.environ.get("NEXTUP_API", "https://nextup-khaki.vercel.app"))
    parser.add_argument("--room", default=os.environ.get("NEXTUP_ROOM", "r1"))
    parser.add_argument("--token", default=os.environ.get("NEXTUP_AUDIO_TOKEN"))
    parser.add_argument("--latency-ms", type=float, default=float(os.environ.get("AUDIO_LATENCY_MS", 200)))
    args = parser.parse_args()
    if not args.token:
        sys.exit("Set NEXTUP_AUDIO_TOKEN (from scripts/setup_room.py --only-audio) or pass --token")

    print(f"Following Apple Music for room {args.room} → {args.api} (latency {args.latency_ms:.0f} ms). Ctrl-C to stop.")
    last_sent = None          # (track title, playing) of the last report
    last_report_at = 0.0
    expected = None           # (monotonic time, position) of the last report, to spot jumps
    while True:
        try:
            m = read_music()
            track = m.get("track") if m.get("running") else None
            playing = bool(track) and m.get("state") == "playing"
            position = float(m.get("position") or 0) if track else 0.0
            now = time.monotonic()
            drift = abs((expected[1] + (now - expected[0])) - position) if (expected and playing) else 0.0
            key = (track or {}).get("title"), playing
            if key != last_sent or drift > 1.0 or now - last_report_at > 10:
                body = {"title": track["title"] if track else None, "artist": (track or {}).get("artist"),
                        "album": (track or {}).get("album"), "duration_s": (track or {}).get("duration"),
                        "position_s": position, "playing": playing, "latency_ms": args.latency_ms}
                result = post(args.api, args.room, args.token, body)
                if key != last_sent:
                    what = f"{track['title']} — {track.get('artist')}" if track else "nothing"
                    print(f"{'▶' if playing else '⏸'} {what} ({result.get('status')})")
                last_sent, last_report_at = key, now
                expected = (now, position)
        except urllib.error.HTTPError as exc:
            print(f"API refused the report: {exc.code} {exc.read()[:200]!r}")
            time.sleep(5)
        except (urllib.error.URLError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            print(f"retrying: {exc}")
            time.sleep(3)
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
