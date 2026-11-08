"""Events to the API, through a SQLite queue that survives outages and restarts.

Every event gets a UUID when it's queued, so the API applies a replay only once.
"""

import asyncio
import datetime as dt
import json
import logging
import pathlib
import sqlite3
import uuid

import httpx

log = logging.getLogger("nextup.uplink")

BATCH = 100


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Uplink:
    def __init__(self, api: str, token: str, data_dir: pathlib.Path) -> None:
        self.url = f"{api}/api/device/events"
        self.headers = {"Authorization": f"Bearer {token}"}
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "queue.db")
        self.db.execute("create table if not exists events (id integer primary key autoincrement, body text not null)")
        self.db.commit()
        self.wake = asyncio.Event()
        self.last_ok: float | None = None

    def enqueue(self, event_type: str, **payload) -> None:
        body = {"event_id": str(uuid.uuid4()), "ts": now_iso(), "type": event_type, "payload": payload}
        self.db.execute("insert into events (body) values (?)", [json.dumps(body)])
        self.db.commit()
        self.wake.set()

    def pending(self) -> int:
        return self.db.execute("select count(*) from events").fetchone()[0]

    async def send_once(self, client: httpx.AsyncClient) -> int:
        """Send one batch. Returns how many were sent; raises if the API can't take them."""
        rows = self.db.execute("select id, body from events order by id limit ?", [BATCH]).fetchall()
        if not rows:
            return 0
        response = await client.post(self.url, headers=self.headers,
                                      json={"events": [json.loads(body) for _, body in rows]}, timeout=15)
        if response.status_code == 401:
            raise PermissionError("the API rejected this device's token")
        if response.status_code == 422:
            # A malformed event would block the queue forever: log it and drop the batch.
            log.error("API rejected a batch as invalid: %s", response.text[:300])
        else:
            response.raise_for_status()
            for error in response.json().get("errors", []):
                log.error("API couldn't apply event %s: %s", error.get("event_id"), error.get("error"))
        self.db.execute(f"delete from events where id <= {rows[-1][0]}")
        self.db.commit()
        return len(rows)

    async def run(self, client: httpx.AsyncClient) -> None:
        backoff = 1.0
        while True:
            try:
                await asyncio.sleep(0.3)  # let events that happen together go in one request
                while await self.send_once(client) == BATCH:
                    pass
                self.last_ok = asyncio.get_running_loop().time()
                backoff = 1.0
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
            except PermissionError as exc:
                log.error("%s; check NEXTUP_DEVICE_TOKEN. Retrying in 60 s", exc)
                await asyncio.sleep(60)
            except (httpx.HTTPError, OSError) as exc:
                log.warning("API unreachable (%s); %d events queued; retrying in %.0f s", exc, self.pending(), backoff)
                await asyncio.sleep(backoff)
                backoff = min(60.0, backoff * 2)
