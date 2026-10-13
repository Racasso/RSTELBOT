"""Private-chat admin commands for RSTELBOT."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable

from sqlalchemy import func, select
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.config import settings
from bot.db import SessionLocal
from bot.models import Access, AccessCode, Content, ContentType, User
from bot.services import codes as code_service
from bot.services import content as content_service

logger = logging.getLogger(__name__)


def admin_only(handler: Callable):
    @wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or user is None or chat is None:
            return
        if chat.type != "private":
            return
        if user.id not in settings.admin_ids:
            await message.reply_text("RSTELBOT: brak uprawnień do tej komendy.")
            return
        await handler(update, context)

    return wrapped


def _admin_private_message(update: Update) -> bool:
    return bool(
        update.effective_user
        and update.effective_chat
        and update.effective_chat.type == "private"
        and update.effective_user.id in settings.admin_ids
    )


@admin_only
async def free_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    source = message.reply_to_message
    if source is None:
        await message.reply_text("Użycie: odpowiedz komendą /free na wiadomość z treścią.")
        return
    try:
        content, published_now = await content_service.create_and_publish_free_content(
            context.bot,
            source,
        )
        if published_now:
            await message.reply_text(
                f"RSTELBOT: opublikowano darmową treść na kanale. Slug: {content.slug}"
            )
        else:
            await message.reply_text(
                f"RSTELBOT: ta treść była już opublikowana. Slug: {content.slug}"
            )
    except (ValueError, TelegramError) as exc:
        logger.info("Nie udało się opublikować darmowej treści: %s", exc)
        await message.reply_text(f"RSTELBOT: nie udało się opublikować treści: {exc}")


@admin_only
async def paid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    source = message.reply_to_message
    if source is None or len(context.args) < 2:
        await message.reply_text("Użycie: odpowiedz /paid <slug> <tytuł> na wiadomość z treścią.")
        return
    slug = context.args[0]
    title = " ".join(context.args[1:])
    try:
        content = await content_service.create_paid_content(
            context.bot,
            source,
            slug,
            title,
        )
        await message.reply_text(
            f"RSTELBOT: zapisana płatna treść „{content.title}” (slug: {content.slug}). "
            "Użyj /teaser, aby opublikować zapowiedź."
        )
    except (ValueError, TelegramError) as exc:
        logger.info("Nie udało się zapisać płatnej treści: %s", exc)
        await message.reply_text(f"RSTELBOT: nie udało się zapisać treści: {exc}")


@admin_only
async def teaser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if len(context.args) < 2:
        await message.reply_text("Użycie: /teaser <slug> <opis>")
        return
    slug = context.args[0]
    teaser = " ".join(context.args[1:])
    try:
        content = await content_service.get_content_by_slug(slug)
        if content is None or content.type != ContentType.PAID:
            raise ValueError(f"Nie znaleziono płatnej treści o slug '{slug}'.")
        message_id = await content_service.publish_teaser(
            context.bot,
            content,
            teaser,
            context.application.bot_data.get("bot_username", ""),
        )
        await message.reply_text(
            f"RSTELBOT: zapowiedź opublikowana lub zaktualizowana na kanale "
            f"(wiadomość {message_id})."
        )
    except (ValueError, TelegramError) as exc:
        logger.info("Nie udało się opublikować zapowiedzi: %s", exc)
        await message.reply_text(f"RSTELBOT: nie udało się opublikować zapowiedzi: {exc}")


@admin_only
async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not 2 <= len(context.args) <= 4:
        await message.reply_text("Użycie: /code <slug> <telegram_id> [dni] [użycia]")
        return
    slug = context.args[0]
    try:
        telegram_id = int(context.args[1])
        if telegram_id <= 0:
            raise ValueError
        days = int(context.args[2]) if len(context.args) >= 3 else None
        max_uses = int(context.args[3]) if len(context.args) >= 4 else 1
        if days is not None and days <= 0:
            raise ValueError("Liczba dni musi być większa od zera.")
        if max_uses <= 0:
            raise ValueError("Liczba użyć musi być większa od zera.")
        expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)
            if days is not None
            else None
        )
        code = await code_service.create_access_code(
            slug,
            telegram_id,
            expires_at=expires_at,
            max_uses=max_uses,
        )
        expiry_text = (
            f" Ważny do: {expires_at:%Y-%m-%d %H:%M} UTC."
            if expires_at
            else " Bez terminu ważności."
        )
        await message.reply_text(
            f"RSTELBOT: kod dla ID {telegram_id}: {code}. "
            f"Limit użyć: {max_uses}.{expiry_text}"
        )
    except ValueError as exc:
        await message.reply_text(f"RSTELBOT: {exc or 'Podano nieprawidłowe argumenty.'}")


@admin_only
async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if len(context.args) != 1:
        await message.reply_text("Użycie: /revoke <kod>")
        return
    revoked = await code_service.revoke_access_code(context.args[0])
    await message.reply_text(
        "RSTELBOT: kod unieważniony." if revoked else "RSTELBOT: nie znaleziono takiego kodu."
    )


@admin_only
async def grant_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if len(context.args) != 2:
        await message.reply_text("Użycie: /grant <slug> <telegram_id>")
        return
    try:
        telegram_id = int(context.args[1])
        if telegram_id <= 0:
            raise ValueError("Telegram ID musi być dodatnie.")
        created = await code_service.grant_access(context.args[0], telegram_id)
        await message.reply_text(
            "RSTELBOT: nadano dostęp." if created else "RSTELBOT: ten dostęp już istnieje."
        )
    except ValueError as exc:
        await message.reply_text(f"RSTELBOT: {exc}")


@admin_only
async def ungrant_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if len(context.args) != 2:
        await message.reply_text("Użycie: /ungrant <slug> <telegram_id>")
        return
    try:
        telegram_id = int(context.args[1])
        if telegram_id <= 0:
            raise ValueError("Telegram ID musi być dodatnie.")
        removed = await code_service.ungrant_access(context.args[0], telegram_id)
        await message.reply_text(
            "RSTELBOT: odebrano dostęp." if removed else "RSTELBOT: użytkownik nie miał tego dostępu."
        )
    except ValueError as exc:
        await message.reply_text(f"RSTELBOT: {exc}")


async def _reply_in_chunks(message, text: str, limit: int = 3800) -> None:
    while text:
        if len(text) <= limit:
            chunk, text = text, ""
        else:
            split_at = text.rfind("\n", 0, limit)
            if split_at < 1:
                split_at = limit
            chunk, text = text[:split_at], text[split_at:].lstrip("\n")
        await message.reply_text(chunk)


@admin_only
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Content, func.count(Access.telegram_id))
                .outerjoin(Access, Access.content_id == Content.id)
                .group_by(Content.id)
                .order_by(Content.created_at.desc())
            )
        ).all()
    if not rows:
        await message.reply_text("RSTELBOT: nie ma jeszcze zapisanych treści.")
        return
    lines = ["Treści RSTELBOT (liczba osób z dostępem):"]
    for content, buyers in rows:
        lines.append(
            f"• {content.slug} | {content.type.value} | {content.title} | dostęp: {buyers}"
        )
    await _reply_in_chunks(message, "\n".join(lines))


@admin_only
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with SessionLocal() as session:
        users_count = await session.scalar(select(func.count(User.telegram_id)))
        contents_count = await session.scalar(select(func.count(Content.id)))
        free_count = await session.scalar(
            select(func.count(Content.id)).where(Content.type == ContentType.FREE)
        )
        paid_count = await session.scalar(
            select(func.count(Content.id)).where(Content.type == ContentType.PAID)
        )
        access_count = await session.scalar(select(func.count()).select_from(Access))
        codes_count = await session.scalar(select(func.count(AccessCode.code)))
        active_codes_count = await session.scalar(
            select(func.count(AccessCode.code)).where(AccessCode.revoked_at.is_(None))
        )
    await update.effective_message.reply_text(
        "RSTELBOT — statystyki\n"
        f"Użytkownicy: {users_count}\n"
        f"Treści: {contents_count} (darmowe: {free_count}, płatne: {paid_count})\n"
        f"Nadane dostępy: {access_count}\n"
        f"Kody: {codes_count} (bez unieważnienia: {active_codes_count})"
    )


async def stage_admin_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _admin_private_message(update):
        return
    message = update.effective_message
    # A code entered by an admin is for redemption, not a new staged asset.
    # The user handler runs in a later group and validates it against MySQL.
    if message.text is not None and code_service.normalize_code(
        message.text
    ).startswith("BOX-"):
        return
    try:
        await content_service.stage_admin_upload(
            context.bot,
            message,
        )
    except TelegramError as exc:
        logger.warning("Nie udało się skopiować wiadomości admina do magazynu: %s", exc)
        await message.reply_text(
            "RSTELBOT: nie udało się zapisać tej wiadomości w kanale-magazynie. "
            "Sprawdź uprawnienia bota i spróbuj wysłać ją ponownie."
        )


def register_handlers(application: Application) -> None:
    for command, callback in (
        ("free", free_command),
        ("paid", paid_command),
        ("teaser", teaser_command),
        ("code", code_command),
        ("revoke", revoke_command),
        ("grant", grant_command),
        ("ungrant", ungrant_command),
        ("list", list_command),
        ("stats", stats_command),
    ):
        application.add_handler(CommandHandler(command, callback), group=0)

    # Uploaded admin messages are copied to the storage channel immediately;
    # the later /free or /paid reply only needs to persist their message IDs.
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, stage_admin_message),
        group=0,
    )
