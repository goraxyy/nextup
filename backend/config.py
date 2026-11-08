"""Settings from environment variables (Vercel project settings, or .env.local locally)."""

import functools
import os
from dataclasses import dataclass


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    supabase_url: str | None
    supabase_secret_key: str | None
    cron_secret: str | None


@functools.lru_cache
def settings() -> Settings:
    return Settings(
        # Serverless functions use Tiger's transaction pooler; the direct URL also works.
        database_url=_env("TIGER_POOLER_URL", "TIGER_DATABASE_URL", "DATABASE_URL"),
        supabase_url=(_env("SUPABASE_URL") or "").rstrip("/") or None,
        supabase_secret_key=_env("SUPABASE_SECRET_KEY"),
        cron_secret=_env("CRON_SECRET"),
    )
