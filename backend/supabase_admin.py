"""Supabase Auth admin calls (secret key, server only)."""

import httpx

from .config import settings


class AdminNotConfigured(RuntimeError):
    pass


def set_device_claim(user_id: str, stall_id: str) -> dict:
    """Stamp app_metadata.device_id on a Supabase user, so its tokens can read device:<stall>."""
    s = settings()
    if not (s.supabase_url and s.supabase_secret_key):
        raise AdminNotConfigured("Set SUPABASE_URL and SUPABASE_SECRET_KEY")
    response = httpx.put(
        f"{s.supabase_url}/auth/v1/admin/users/{user_id}",
        headers={"apikey": s.supabase_secret_key, "Authorization": f"Bearer {s.supabase_secret_key}"},
        json={"app_metadata": {"device_id": stall_id}},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
