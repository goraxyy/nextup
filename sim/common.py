"""Shared helpers for the simulator and sim/device.py."""

import datetime as dt
import json
import pathlib
import uuid

import httpx

DEVICES_FILE = pathlib.Path(__file__).resolve().parents[1] / "devices.local.json"


def load(path: pathlib.Path = DEVICES_FILE) -> dict:
    if not path.exists():
        raise SystemExit(f"{path.name} not found: run scripts/seed_demo.py first")
    return json.loads(path.read_text())


def event(event_type: str, **payload) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "type": event_type,
        "payload": payload,
    }


def post_events(client: httpx.Client, api: str, token: str, events: list[dict]) -> dict:
    response = client.post(
        f"{api.rstrip('/')}/api/device/events",
        json={"events": events},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
