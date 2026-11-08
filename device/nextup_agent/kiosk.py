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
            "sensor_ok": False,
            "online": False,
            "chime": 0,            # bumped to make the screen chime
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
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(STATIC))
        handler.log_message = lambda *args: None
        ThreadingHTTPServer((self.cfg.kiosk_host, self.cfg.kiosk_http_port), handler).serve_forever()

    async def run(self) -> None:
        threading.Thread(target=self._serve_static, daemon=True, name="kiosk-http").start()
        async with serve(self._handler, self.cfg.kiosk_host, self.cfg.kiosk_ws_port):
            log.info("stall screen on http://%s:%d", self.cfg.kiosk_host, self.cfg.kiosk_http_port)
            await asyncio.Future()
