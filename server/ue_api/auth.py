"""Organization API keys.

Keys are 32 random bytes, so a single SHA-256 is enough to store them safely: there is
nothing to brute-force, unlike a password. That keeps lookup to one indexed equality on
the hash instead of a slow KDF per request.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ue_api.db import get_session
from ue_api.models import ApiKey

KEY_PREFIX = "ue_sk_"


@dataclass(frozen=True, slots=True)
class IssuedKey:
    plaintext: str
    prefix: str
    key_hash: bytes


@dataclass(frozen=True, slots=True)
class Principal:
    org_id: uuid.UUID
    key_id: uuid.UUID


def hash_key(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode()).digest()


def issue_key() -> IssuedKey:
    plaintext = KEY_PREFIX + secrets.token_urlsafe(32)
    return IssuedKey(plaintext=plaintext, prefix=plaintext[:12], key_hash=hash_key(plaintext))


async def create_api_key(session: AsyncSession, org_id: uuid.UUID, name: str) -> str:
    issued = issue_key()
    session.add(ApiKey(org_id=org_id, name=name, prefix=issued.prefix, key_hash=issued.key_hash))
    await session.flush()
    return issued.plaintext


async def resolve_key(session: AsyncSession, plaintext: str) -> Principal | None:
    if not plaintext.startswith(KEY_PREFIX):
        return None
    row = (
        await session.execute(
            select(ApiKey.id, ApiKey.org_id).where(
                ApiKey.key_hash == hash_key(plaintext), ApiKey.revoked_at.is_(None)
            )
        )
    ).one_or_none()
    return None if row is None else Principal(org_id=row.org_id, key_id=row.id)


async def require_principal(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    principal = await resolve_key(session, token.strip()) if scheme.lower() == "bearer" else None
    if principal is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]
Session = Annotated[AsyncSession, Depends(get_session)]
