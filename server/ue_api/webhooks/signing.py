"""Standard Webhooks (https://www.standardwebhooks.com) signing and verification."""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime

SECRET_PREFIX = "whsec_"  # noqa: S105 - a format marker, not a secret
DEFAULT_TOLERANCE_SECONDS = 300


class InvalidSignatureError(ValueError):
    pass


def generate_secret() -> str:
    return SECRET_PREFIX + base64.b64encode(secrets.token_bytes(24)).decode()


def _key(secret: str) -> bytes:
    return base64.b64decode(secret.removeprefix(SECRET_PREFIX))


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(_key(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def headers(secret: str, msg_id: str, sent_at: datetime, body: bytes) -> dict[str, str]:
    timestamp = int(sent_at.timestamp())
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": sign(secret, msg_id, timestamp, body),
        "content-type": "application/json",
    }


def verify(
    secret: str, hdrs: dict[str, str], body: bytes, now: datetime, tolerance: int = DEFAULT_TOLERANCE_SECONDS
) -> None:
    """Raise unless one of the `v1` signatures matches and the timestamp is fresh."""
    try:
        msg_id = hdrs["webhook-id"]
        timestamp = int(hdrs["webhook-timestamp"])
        signatures = hdrs["webhook-signature"].split()
    except (KeyError, ValueError) as exc:
        raise InvalidSignatureError("missing or malformed webhook headers") from exc
    if abs(now.timestamp() - timestamp) > tolerance:
        raise InvalidSignatureError("timestamp outside the tolerance window")
    expected = sign(secret, msg_id, timestamp, body)
    # Several signatures can be present while a secret is being rotated.
    if not any(hmac.compare_digest(expected, s) for s in signatures if s.startswith("v1,")):
        raise InvalidSignatureError("no matching signature")
