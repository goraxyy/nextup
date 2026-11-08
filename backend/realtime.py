"""Send outbox rows to Supabase Realtime.

Rules in Tiger write messages to `outbox` in the same transaction as the change. After
every request, and on every tick, unsent rows go out in one call to Realtime's REST
broadcast endpoint. Delivery is at least once: each message carries its outbox id as `msg_id`
(never `id`, which payloads use for the stall), so clients ignore repeats.
"""

import logging

import httpx

from . import db
from .config import settings

log = logging.getLogger("nextup.realtime")

BATCH = 100


class RealtimeNotConfigured(RuntimeError):
    pass


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=5)
    return _client


def broadcast(messages: list[dict]) -> None:
    s = settings()
    if not (s.supabase_url and s.supabase_secret_key):
        raise RealtimeNotConfigured("Set SUPABASE_URL and SUPABASE_SECRET_KEY")
    response = _http().post(
        f"{s.supabase_url}/realtime/v1/api/broadcast",
        headers={"apikey": s.supabase_secret_key, "Content-Type": "application/json"},
        json={"messages": messages},
    )
    response.raise_for_status()


def flush_outbox(max_batches: int = 5) -> int:
    """Send unsent outbox rows. Rows stay unsent if the broadcast fails."""
    sent = 0
    with db.pool().connection() as conn:
        for _ in range(max_batches):
            with conn.transaction():
                rows = conn.execute(
                    "select id, topic, event, payload from outbox"
                    " where sent_at is null order by id limit %s for update skip locked",
                    [BATCH],
                ).fetchall()
                if not rows:
                    break
                broadcast([
                    {
                        "topic": row["topic"],
                        "event": row["event"],
                        "payload": {**row["payload"], "msg_id": row["id"]},
                        "private": True,
                    }
                    for row in rows
                ])
                conn.execute("update outbox set sent_at = now() where id = any(%s)", [[r["id"] for r in rows]])
            sent += len(rows)
            if len(rows) < BATCH:
                break
    return sent


def flush_quietly() -> int:
    """Flush after a request; a failure never fails the request (the tick retries)."""
    try:
        return flush_outbox()
    except Exception:  # noqa: BLE001
        log.exception("outbox flush failed; the next tick will retry")
        return 0
