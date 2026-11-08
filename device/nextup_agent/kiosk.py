"""The stall screen's local side: a static web server and a websocket that pushes state.

Chromium in kiosk mode opens http://localhost:8080 (device/kiosk/index.html), which connects
to ws://localhost:8765 and redraws on every message. Everything stays on the Pi, so the
screen keeps working when the network doesn't.
"""

import asyncio
import functools
import json
import logging
import pathlib
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from websockets.asyncio.server import serve

log = logging.getLogger("nextup.kiosk")

STATIC = pathlib.Path(__file__).resolve().parents[1] / "kiosk"


class Kiosk:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.clients: set = set()
        self.state = {
            "stall": cfg.stall,
            "stall_name": None,
            "stall_state": "free",
            "since": None,
            "notify": None,        # {"notify_id", "status"} while a pop-up is open
            "thanks_until": 0,     # epoch seconds: show "They'll know you've seen it"
            "reserved_until": None,
            "hold": 0.0,
            "hand_hold": True,     # False when no sensor can see a hand-hold (ULTRASONIC=off)
            "sensor_ok": False,
            "online": False,
            "chime": 0,            # bumped to make the screen chime
            "events": [],          # campus events with pictures, for the cycling pages
            "insights": None,      # best times and today's busyness, for the "best time" page
            "music": None,         # {"now_playing", "lyrics"} from the Mac playing Apple Music
            "clock_offset_ms": 0,  # server clock minus this Pi's clock, to sync lyrics
            "waiting": 0,          # people in line
            "pages": {"status_s": cfg.page_status_s, "event_s": cfg.page_event_s},
            "hold_s": cfg.hold_s,    # seconds a hand must be held up, shown on the pop-up
            # Experimental shower music (MUSIC=spotify, stall_music.py); unused otherwise.
            "local_music": None,     # {"now_playing", "lyrics"} from Spotify on this Pi
            "gesture": 0.0,          # 0-1: a hand held up toward "next song"
            "music_feedback": None,  # {"action": paused|playing|next, "until": epoch s}
            "music_hint": None,      # None: music off; "": show "NextUp <stall name>"
        }

    def publish(self, **changes) -> None:
        self.state.update(changes)
        message = json.dumps(self.state, default=str)
        for ws in list(self.clients):
            asyncio.ensure_future(self._send(ws, message))

    async def _send(self, ws, message: str) -> None:
        try:
            await ws.send(message)
        except Exception:  # noqa: BLE001  the screen reloaded
            self.clients.discard(ws)

    async def _handler(self, ws) -> None:
        self.clients.add(ws)
        try:
            await ws.send(json.dumps(self.state, default=str))
            async for _ in ws:
                pass
        finally:
            self.clients.discard(ws)

    def _serve_static(self) -> None:
        media = self.cfg.data_dir / "media"

        class Handler(SimpleHTTPRequestHandler):
            def translate_path(self, path: str) -> str:
                # /media/<file> comes from the agent's cache of event images; the rest is device/kiosk.
                if path.startswith("/media/"):
                    return str(media / pathlib.PurePosixPath(path.split("?")[0]).name)
                return super().translate_path(path)

            def log_message(self, *args) -> None:
                pass

        handler = functools.partial(Handler, directory=str(STATIC))
        ThreadingHTTPServer((self.cfg.kiosk_host, self.cfg.kiosk_http_port), handler).serve_forever()

    async def run(self) -> None:
        threading.Thread(target=self._serve_static, daemon=True, name="kiosk-http").start()
        async with serve(self._handler, self.cfg.kiosk_host, self.cfg.kiosk_ws_port):
            log.info("stall screen on http://%s:%d", self.cfg.kiosk_host, self.cfg.kiosk_http_port)
            await asyncio.Future()
