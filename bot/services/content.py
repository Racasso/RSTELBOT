"""Content intake, channel publishing, and protected content delivery."""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import BadRequest

from bot.config import settings
from bot.db import SessionLocal
from bot.models import Content, ContentType, StagedUpload, utcnow_naive

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ContentError(ValueError):
    """A user-correctable content operation error."""


def normalize_slug(value: str) -> str:
    slug = value.strip().lower()
    if not SLUG_RE.fullmatch(slug):
        raise ContentError(
            "Slug może zawierać 1–64 znaki: małe litery, cyfry, myślnik i podkreślenie."
        )
    return slug


async def stage_admin_upload(bot, message: Message) -> int:
    """Copy an admin's private message into the storage channel once."""
    source_key = (message.chat_id, message.message_id)
    async with SessionLocal() as session:
        existing = await session.get(StagedUpload, source_key)
        if existing is not None:
            return existing.storage_message_id

    copied = await bot.copy_message(
        chat_id=settings.storage_chat_id,
        from_chat_id=message.chat_id,
        message_id=message.message_id,
    )
    storage_msg_id = copied.message_id
    async with SessionLocal.begin() as session:
        session.add(
            StagedUpload(
                source_chat_id=message.chat_id,
                source_message_id=message.message_id,
                media_group_id=message.media_group_id,
                storage_chat_id=settings.storage_chat_id,
                storage_message_id=storage_msg_id,
            )
        )
        await session.execute(
            delete(StagedUpload).where(
                StagedUpload.created_at < utcnow_naive() - timedelta(days=7)
            )
        )
    return storage_msg_id


async def storage_ids_for_reply(
    bot,
    source_message: Message,
) -> list[int]:
    """Return ordered storage IDs for a replied-to message or media album."""
    async with SessionLocal.begin() as session:
        if source_message.media_group_id:
            staged = list(
                (
                    await session.scalars(
                        select(StagedUpload)
                        .where(
                            StagedUpload.source_chat_id == source_message.chat_id,
                            StagedUpload.media_group_id == source_message.media_group_id,
                        )
                        .order_by(StagedUpload.source_message_id)
                        .with_for_update()
                    )
                ).all()
            )
            if any(item.source_message_id == source_message.message_id for item in staged):
                result = [item.storage_message_id for item in staged]
                for item in staged:
                    await session.delete(item)
                return result
        else:
            staged = []

        source_upload = await session.get(
            StagedUpload,
            (source_message.chat_id, source_message.message_id),
        )
        if source_upload is not None:
            result = [source_upload.storage_message_id]
            await session.delete(source_upload)
            return result

    # This fallback covers a bot restart before the admin message was staged,
    # or an upload type whose initial copy failed.
    return [await stage_admin_upload(bot, source_message)]


async def get_content_by_slug(slug: str) -> Content | None:
    slug = normalize_slug(slug)
    async with SessionLocal() as session:
        return await session.scalar(select(Content).where(Content.slug == slug))


async def create_paid_content(
    bot,
    source_message: Message,
    slug: str,
    title: str,
) -> Content:
    slug = normalize_slug(slug)
    title = title.strip()
    if not title:
        raise ContentError("Podaj tytuł płatnej treści.")
    if len(title) > 255:
        raise ContentError("Tytuł może mieć maksymalnie 255 znaków.")

    async with SessionLocal() as session:
        existing = await session.scalar(select(Content).where(Content.slug == slug))
    if existing:
        raise ContentError(f"Treść o slug '{slug}' już istnieje.")

    storage_ids = await storage_ids_for_reply(bot, source_message)
    content = Content(
        slug=slug,
        type=ContentType.PAID,
        title=title,
        storage_chat_id=settings.storage_chat_id,
        storage_msg_ids=storage_ids,
    )
    try:
        async with SessionLocal.begin() as session:
            session.add(content)
            await session.flush()
    except IntegrityError as exc:
        raise ContentError(f"Treść o slug '{slug}' już istnieje.") from exc
    return content


async def create_and_publish_free_content(
    bot,
    source_message: Message,
) -> tuple[Content, bool]:
    # A source Telegram message has a stable chat/message ID, so retrying /free
    # after a transient channel error can resume the same database record.
    slug = f"free-{source_message.chat_id}-{source_message.message_id}"
    async with SessionLocal() as session:
        existing = await session.scalar(select(Content).where(Content.slug == slug))
    if existing is not None:
        if existing.type != ContentType.FREE:
            raise ContentError("Ta wiadomość źródłowa jest już użyta przez inną treść.")
        if existing.channel_msg_id is not None:
            return existing, False
        storage_ids = existing.storage_msg_ids
        content_id = existing.id
    else:
        storage_ids = await storage_ids_for_reply(bot, source_message)
        content_id = None

    async with SessionLocal.begin() as session:
        content = await session.scalar(select(Content).where(Content.slug == slug))
        if content is None and content_id is None:
            content = Content(
                slug=slug,
                type=ContentType.FREE,
                title="Bezpłatna treść",
                storage_chat_id=settings.storage_chat_id,
                storage_msg_ids=storage_ids,
            )
            session.add(content)
            await session.flush()
        elif content is not None and content.type != ContentType.FREE:
            raise ContentError("Ta wiadomość źródłowa jest już użyta przez inną treść.")
        if content is not None:
            content_id = content.id
            storage_ids = content.storage_msg_ids
            if content.channel_msg_id is not None:
                return content, False

    published_ids = await copy_stored_messages(
        bot,
        settings.channel_id,
        settings.storage_chat_id,
        storage_ids,
        protect_content=False,
    )
    async with SessionLocal.begin() as session:
        content = await session.get(Content, content_id)
        if content is None:
            raise ContentError("Nie znaleziono rekordu opublikowanej treści.")
        content.channel_msg_id = published_ids[0]
    async with SessionLocal() as session:
        content = await session.get(Content, content_id)
        return content, True


async def publish_teaser(bot, content: Content, teaser: str, bot_username: str) -> int:
    teaser = teaser.strip()
    if not teaser:
        raise ContentError("Opis zapowiedzi nie może być pusty.")
    if len(teaser) > 3500:
        raise ContentError("Opis zapowiedzi jest za długi (maksymalnie 3500 znaków).")
    if not bot_username:
        raise ContentError("Nie udało się pobrać nazwy użytkownika RSTELBOT.")

    text = f"{content.title}\n\n{teaser}"
    button = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Odblokuj", url=f"https://t.me/{bot_username}?start={content.slug}")]]
    )
    message_id: int
    if content.channel_msg_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=settings.channel_id,
                message_id=content.channel_msg_id,
                text=text,
                reply_markup=button,
            )
            message_id = content.channel_msg_id
        except BadRequest:
            published = await bot.send_message(
                chat_id=settings.channel_id,
                text=text,
                reply_markup=button,
            )
            message_id = published.message_id
    else:
        published = await bot.send_message(
            chat_id=settings.channel_id,
            text=text,
            reply_markup=button,
        )
        message_id = published.message_id

    async with SessionLocal.begin() as session:
        stored_content = await session.get(Content, content.id)
        if stored_content is None:
            raise ContentError("Nie znaleziono treści do zapisania zapowiedzi.")
        stored_content.teaser = teaser
        stored_content.channel_msg_id = message_id
    return message_id


async def copy_stored_messages(
    bot,
    target_chat_id: int,
    storage_chat_id: int,
    storage_msg_ids: list[int],
    *,
    protect_content: bool,
) -> list[int]:
    """Copy each stored message in source order, without downloading files."""
    if not storage_msg_ids:
        raise ContentError("Treść nie zawiera wiadomości w kanale-magazynie.")
    result_ids: list[int] = []
    for storage_msg_id in storage_msg_ids:
        copied = await bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=storage_chat_id,
            message_id=storage_msg_id,
            protect_content=protect_content,
        )
        result_ids.append(copied.message_id)
    return result_ids


async def deliver_content(bot, target_chat_id: int, content: Content) -> list[int]:
    return await copy_stored_messages(
        bot,
        target_chat_id,
        content.storage_chat_id,
        content.storage_msg_ids,
        protect_content=True,
    )


async def list_user_contents(telegram_id: int) -> list[Content]:
    from bot.models import Access

    async with SessionLocal() as session:
        result = await session.scalars(
            select(Content)
            .join(Access, Access.content_id == Content.id)
            .where(Access.telegram_id == telegram_id)
            .order_by(Access.granted_at.desc())
        )
        return list(result.all())


async def user_has_access(telegram_id: int, content_id: int) -> bool:
    from bot.models import Access

    async with SessionLocal() as session:
        access = await session.get(Access, (telegram_id, content_id))
        return access is not None
