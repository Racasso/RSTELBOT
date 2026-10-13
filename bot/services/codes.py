"""Secure access-code creation, revocation, grants, and redemption."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError

from bot.db import SessionLocal
from bot.models import Access, AccessCode, CodeAttempt, Content, ContentType, User
from bot.services.content import normalize_slug

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(r"^BOX-([ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4})-([ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4})$", re.IGNORECASE)
MAX_FAILED_ATTEMPTS = 5
ATTEMPT_WINDOW = timedelta(minutes=10)


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_code() -> str:
    first = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
    second = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
    return f"BOX-{first}-{second}"


def normalize_code(raw_code: str) -> str:
    return raw_code.strip().upper()


class RedemptionStatus(str, Enum):
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"
    GRANTED = "granted"
    ALREADY_HAS_ACCESS = "already_has_access"


@dataclass(frozen=True, slots=True)
class RedemptionResult:
    status: RedemptionStatus
    content: Content | None = None


async def remember_user(telegram_id: int, username: str | None) -> None:
    async with SessionLocal.begin() as session:
        statement = mysql_insert(User.__table__).values(
            telegram_id=telegram_id,
            username=username,
        )
        update_values = (
            {"username": username}
            if username is not None
            else {"telegram_id": telegram_id}
        )
        await session.execute(statement.on_duplicate_key_update(**update_values))


async def _upsert_user_for_lock(session, telegram_id: int, username: str | None) -> None:
    statement = mysql_insert(User.__table__).values(
        telegram_id=telegram_id,
        username=username,
    )
    update_values = (
        {"username": username}
        if username is not None
        else {"telegram_id": telegram_id}
    )
    await session.execute(statement.on_duplicate_key_update(**update_values))
    await session.scalar(
        select(User).where(User.telegram_id == telegram_id).with_for_update()
    )


async def create_access_code(
    slug: str,
    telegram_id: int,
    *,
    expires_at: datetime | None,
    max_uses: int,
    note: str | None = None,
) -> str:
    slug = normalize_slug(slug)
    if max_uses < 1:
        raise ValueError("Liczba użyć musi być większa od zera.")

    for _ in range(5):
        code = generate_code()
        try:
            async with SessionLocal.begin() as session:
                content = await session.scalar(
                    select(Content).where(
                        Content.slug == slug,
                        Content.type == ContentType.PAID,
                    )
                )
                if content is None:
                    raise ValueError(f"Nie znaleziono płatnej treści o slug '{slug}'.")
                session.add(
                    AccessCode(
                        code=code,
                        content_id=content.id,
                        telegram_id=telegram_id,
                        max_uses=max_uses,
                        uses=0,
                        expires_at=expires_at,
                        note=note,
                    )
                )
                await session.flush()
            return code
        except IntegrityError:
            # The primary key collision probability is tiny; a duplicate is
            # rolled back by the transaction context and retried with a new code.
            continue
    raise RuntimeError("Nie udało się wygenerować unikalnego kodu. Spróbuj ponownie.")


async def revoke_access_code(raw_code: str) -> bool:
    code = normalize_code(raw_code)
    async with SessionLocal.begin() as session:
        access_code = await session.scalar(
            select(AccessCode).where(AccessCode.code == code).with_for_update()
        )
        if access_code is None:
            return False
        access_code.revoked_at = utcnow_naive()
        return True


async def grant_access(slug: str, telegram_id: int) -> bool:
    slug = normalize_slug(slug)
    async with SessionLocal.begin() as session:
        content = await session.scalar(select(Content).where(Content.slug == slug))
        if content is None:
            raise ValueError(f"Nie znaleziono treści o slug '{slug}'.")
        await _upsert_user_for_lock(session, telegram_id, None)
        existing = await session.get(Access, (telegram_id, content.id))
        if existing is not None:
            return False
        session.add(Access(telegram_id=telegram_id, content_id=content.id, code=None))
        return True


async def ungrant_access(slug: str, telegram_id: int) -> bool:
    slug = normalize_slug(slug)
    async with SessionLocal.begin() as session:
        content = await session.scalar(select(Content).where(Content.slug == slug))
        if content is None:
            raise ValueError(f"Nie znaleziono treści o slug '{slug}'.")
        access = await session.get(Access, (telegram_id, content.id))
        if access is None:
            return False
        await session.delete(access)
        return True


async def redeem_code(
    raw_code: str,
    telegram_id: int,
    username: str | None,
) -> RedemptionResult:
    """Validate in the specified order and atomically grant at most once."""
    code = normalize_code(raw_code)
    now = utcnow_naive()
    cutoff = now - ATTEMPT_WINDOW

    async with SessionLocal.begin() as session:
        # Serializing attempts on the user's row makes the 5-attempt window
        # strict even if Telegram retries or concurrent updates arrive.
        await _upsert_user_for_lock(session, telegram_id, username)
        recent_attempts = await session.scalar(
            select(func.count(CodeAttempt.id)).where(
                CodeAttempt.telegram_id == telegram_id,
                CodeAttempt.at >= cutoff,
            )
        )
        if recent_attempts >= MAX_FAILED_ATTEMPTS:
            return RedemptionResult(RedemptionStatus.RATE_LIMITED)

        access_code = None
        if CODE_RE.fullmatch(code):
            access_code = await session.scalar(
                select(AccessCode)
                .where(AccessCode.code == code)
                .with_for_update()
            )

        if access_code is None or access_code.telegram_id != telegram_id:
            session.add(CodeAttempt(telegram_id=telegram_id, at=now))
            return RedemptionResult(RedemptionStatus.INVALID)

        if (
            access_code.revoked_at is not None
            or (access_code.expires_at is not None and access_code.expires_at <= now)
        ):
            session.add(CodeAttempt(telegram_id=telegram_id, at=now))
            return RedemptionResult(RedemptionStatus.INVALID)

        content = await session.get(Content, access_code.content_id)
        if content is None:
            session.add(CodeAttempt(telegram_id=telegram_id, at=now))
            return RedemptionResult(RedemptionStatus.INVALID)

        existing_access = await session.get(Access, (telegram_id, content.id))
        if existing_access is not None:
            return RedemptionResult(RedemptionStatus.ALREADY_HAS_ACCESS, content)

        if access_code.uses >= access_code.max_uses:
            session.add(CodeAttempt(telegram_id=telegram_id, at=now))
            return RedemptionResult(RedemptionStatus.INVALID)

        session.add(
            Access(
                telegram_id=telegram_id,
                content_id=content.id,
                code=access_code.code,
            )
        )
        access_code.uses += 1
        return RedemptionResult(RedemptionStatus.GRANTED, content)
