"""Who is calling: phones (Supabase tokens), stall Pis (device tokens), Supabase Cron."""

import hashlib
import hmac
import uuid

import jwt
from fastapi import Header, HTTPException

from . import db
from .config import settings

_jwks: jwt.PyJWKClient | None = None


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def _jwks_client() -> jwt.PyJWKClient:
    global _jwks
    if _jwks is None:
        base = settings().supabase_url
        if not base:
            raise HTTPException(503, "SUPABASE_URL is not set")
        _jwks = jwt.PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json", cache_keys=True)
    return _jwks


def verify_supabase_token(token: str) -> dict:
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            issuer=f"{settings().supabase_url}/auth/v1",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc


def browser_id(authorization: str | None = Header(default=None)) -> uuid.UUID:
    """The anonymous Supabase user id of the browser making the request."""
    claims = verify_supabase_token(_bearer(authorization))
    return uuid.UUID(claims["sub"])


def device_stall(authorization: str | None = Header(default=None)) -> str:
    """The stall whose Pi is calling, from its device token."""
    token = _bearer(authorization)
    with db.pool().connection() as conn:
        row = conn.execute("select stall_id from devices where token_hash = %s", [hash_secret(token)]).fetchone()
    if row is None:
        raise HTTPException(401, "unknown device")
    return row["stall_id"]


def require_cron(x_cron_secret: str | None = Header(default=None)) -> None:
    secret = settings().cron_secret
    if not secret or not x_cron_secret or not hmac.compare_digest(secret, x_cron_secret):
        raise HTTPException(401, "bad cron secret")
