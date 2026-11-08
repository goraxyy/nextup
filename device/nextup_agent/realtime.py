"""Messages for this stall from Supabase Realtime (topic device:<stall>).

1. Sign in to Supabase anonymously once; the session is saved, so restarts keep the same user.
2. Ask the API (with the device token) to link that user to this stall. The API sets
   app_metadata.device_id, and after a token refresh the private-topic policy lets us in.
3. Join the private topic over Realtime's websocket (Phoenix protocol), send heartbeats,
   refresh the token before it expires, and reconnect with backoff.
"""

import asyncio
import base64
import json
import logging
import pathlib
import time

import httpx
from websockets.asyncio.client import connect

log = logging.getLogger("nextup.realtime")

HEARTBEAT_S = 25


def jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class SupabaseSession:
    def __init__(self, supabase_url: str, key: str, data_dir: pathlib.Path) -> None:
        self.url, self.key = supabase_url, key
        self.file = data_dir / "supabase-session.json"
        self.session: dict | None = json.loads(self.file.read_text()) if self.file.exists() else None

    @property
    def access_token(self) -> str:
        return self.session["access_token"]

    @property
    def claims(self) -> dict:
        return jwt_claims(self.access_token)

    def _save(self, data: dict) -> None:
        data["expires_at"] = data.get("expires_at") or int(time.time()) + int(data.get("expires_in", 3600))
        self.session = {k: data[k] for k in ("access_token", "refresh_token", "expires_at")}
        self.file.write_text(json.dumps(self.session))
        self.file.chmod(0o600)

    async def sign_in(self, client: httpx.AsyncClient) -> None:
        response = await client.post(f"{self.url}/auth/v1/signup", headers={"apikey": self.key}, json={}, timeout=15)
        response.raise_for_status()
        self._save(response.json())
        log.info("signed in to Supabase anonymously as %s", self.claims["sub"])

    async def refresh(self, client: httpx.AsyncClient) -> None:
        response = await client.post(f"{self.url}/auth/v1/token?grant_type=refresh_token",
                                     headers={"apikey": self.key},
                                     json={"refresh_token": self.session["refresh_token"]}, timeout=15)
        if response.status_code in (400, 401, 403):  # refresh token gone: start over
            log.warning("Supabase refresh refused; signing in again")
            await self.sign_in(client)
            return
        response.raise_for_status()
        self._save(response.json())

    async def ensure(self, client: httpx.AsyncClient, min_valid_s: int = 300) -> None:
        if self.session is None:
            await self.sign_in(client)
        elif self.session["expires_at"] - time.time() < min_valid_s:
            await self.refresh(client)


class DeviceChannel:
    def __init__(self, cfg, session: SupabaseSession) -> None:
        self.cfg, self.session = cfg, session
        self.topic = f"device:{cfg.stall}"
        self.connected = False
        self._ref = 0

    def _next_ref(self) -> str:
        self._ref += 1
        return str(self._ref)

    async def _link(self, client: httpx.AsyncClient) -> None:
        if (self.session.claims.get("app_metadata") or {}).get("device_id") == self.cfg.stall:
            return
        response = await client.post(f"{self.cfg.api}/api/device/link",
                                     headers={"Authorization": f"Bearer {self.cfg.device_token}"},
                                     json={"access_token": self.session.access_token}, timeout=15)
        response.raise_for_status()
        await self.session.refresh(client)  # the new token carries app_metadata.device_id
        log.info("linked Supabase user to stall %s", self.cfg.stall)

    async def run(self, client: httpx.AsyncClient, on_message, on_connect, on_status=lambda ok: None) -> None:
        backoff = 1.0
        ws_url = (self.cfg.supabase_url.replace("https://", "wss://").replace("http://", "ws://")
                  + f"/realtime/v1/websocket?apikey={self.cfg.supabase_key}&vsn=1.0.0")
        while True:
            try:
                await self.session.ensure(client)
                await self._link(client)
                async with connect(ws_url, open_timeout=15, ping_interval=None) as ws:
                    join_ref = self._next_ref()
                    await ws.send(json.dumps({
                        "topic": f"realtime:{self.topic}", "event": "phx_join", "ref": join_ref, "join_ref": join_ref,
                        "payload": {"config": {"broadcast": {"self": False, "ack": False}, "presence": {"key": ""},
                                               "postgres_changes": [], "private": True},
                                    "access_token": self.session.access_token}}))
                    reply = await self._wait_reply(ws, join_ref)
                    if reply.get("status") != "ok":
                        raise RuntimeError(f"join refused: {reply.get('response')}")
                    log.info("listening on %s", self.topic)
                    self.connected, backoff = True, 1.0
                    on_status(True)
                    await on_connect()
                    await self._listen(ws, client, join_ref, on_message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  network, auth, server restarts
                log.warning("Realtime: %s; reconnecting in %.0f s", exc, backoff)
            self.connected = False
            on_status(False)
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

    async def _wait_reply(self, ws, ref: str) -> dict:
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if msg.get("event") == "phx_reply" and msg.get("ref") == ref:
                return msg.get("payload", {})

    async def _listen(self, ws, client: httpx.AsyncClient, join_ref: str, on_message) -> None:
        next_beat = time.monotonic() + HEARTBEAT_S
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, next_beat - time.monotonic()))
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": self._next_ref()}))
                next_beat = time.monotonic() + HEARTBEAT_S
                if self.session.session["expires_at"] - time.time() < 300:
                    await self.session.refresh(client)
                    await ws.send(json.dumps({"topic": f"realtime:{self.topic}", "event": "access_token",
                                              "ref": self._next_ref(), "join_ref": join_ref,
                                              "payload": {"access_token": self.session.access_token}}))
                continue
            msg = json.loads(raw)
            event = msg.get("event")
            if event == "broadcast":
                inner = msg.get("payload", {})
                await on_message(inner.get("event"), inner.get("payload") or {})
            elif event in ("phx_error", "phx_close"):
                raise RuntimeError(f"channel {event}")
            elif event == "system" and (msg.get("payload") or {}).get("status") == "error":
                raise RuntimeError(f"system error: {msg['payload'].get('message')}")
