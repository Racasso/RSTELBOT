"""Private-chat admin commands for RSTELBOT."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.config import settings
from bot.db import SessionLocal
from bot.models import Access, AccessCode, Content, ContentType, StarPayment, User
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
    if source is None or len(context.args) < 3:
        await message.reply_text(
            "Użycie: odpowiedz /paid <slug> <gwiazdki> <tytuł> na materiał."
        )
        return
    slug = context.args[0]
    try:
        stars_price = int(context.args[1])
        title = " ".join(context.args[2:])
        content = await content_service.create_paid_content(
            context.bot,
            source,
            slug,
            stars_price,
            title,
        )
        await message.reply_text(
            f"RSTELBOT: zapisana płatna treść „{content.title}” (slug: {content.slug}). "
            f"Cena: {content.stars_price} ⭐. Użyj /teaser, aby opublikować zapowiedź."
        )
    except (ValueError, TelegramError) as exc:
        logger.info("Nie udało się zapisać płatnej treści: %s", exc)
        await message.reply_text(f"RSTELBOT: nie udało się zapisać treści: {exc}")


@admin_only
async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if len(context.args) != 2:
        await message.reply_text("Użycie: /price <slug> <liczba_gwiazdek>")
        return
    try:
        content = await content_service.set_content_price(
            context.args[0],
            int(context.args[1]),
        )
        await message.reply_text(
            f"RSTELBOT: cena „{content.title}” ustawiona na {content.stars_price} ⭐."
        )
    except ValueError as exc:
        await message.reply_text(f"RSTELBOT: {exc}")


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


def _parse_schedule_time(value: str) -> datetime:
    try:
        scheduled_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Podaj datę jako RRRR-MM-DDTHH:MM.") from exc
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=ZoneInfo(settings.timezone))
    scheduled_at = scheduled_at.astimezone(timezone.utc)
    if scheduled_at <= datetime.now(timezone.utc):
        raise ValueError("Termin publikacji musi być w przyszłości.")
    return scheduled_at


def _display_schedule_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local_value = value.astimezone(ZoneInfo(settings.timezone))
    return local_value.strftime("%Y-%m-%d %H:%M")


def _queue_publication(application: Application, publication) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        raise RuntimeError('Zainstaluj zależność python-telegram-bot[job-queue].')
    when = publication.scheduled_at.replace(tzinfo=timezone.utc)
    if when <= datetime.now(timezone.utc):
        when = timedelta(seconds=1)
    job_queue.run_once(
        scheduled_publication_job,
        when=when,
        data=publication.id,
        name=f"publication-{publication.id}",
    )


async def restore_scheduled_jobs(application: Application) -> None:
    for publication in await content_service.list_pending_publications():
        _queue_publication(application, publication)


async def scheduled_publication_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    publication_id = context.job.data
    try:
        await content_service.publish_scheduled_publication(
            context.bot,
            publication_id,
            context.application.bot_data.get("bot_username", ""),
        )
    except Exception:
        logger.exception("Nie udało się opublikować zaplanowanego wpisu %s.", publication_id)
        for admin_id in settings.admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"RSTELBOT: publikacja zaplanowana pod numerem {publication_id} "
                        "nie powiodła się. Sprawdź /scheduled."
                    ),
                )
            except TelegramError:
                logger.warning("Nie udało się powiadomić administratora %s.", admin_id)


@admin_only
async def schedule_teaser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if len(context.args) < 3:
        await message.reply_text(
            "Użycie: /schedule <slug> <RRRR-MM-DDTHH:MM> <opis zapowiedzi>"
        )
        return
    try:
        content = await content_service.get_content_by_slug(context.args[0])
        if content is None:
            raise ValueError(f"Nie znaleziono treści o slug '{context.args[0]}'.")
        scheduled_at = _parse_schedule_time(context.args[1])
        teaser = " ".join(context.args[2:])
        publication = await content_service.create_scheduled_publication(
            content.id,
            "teaser",
            scheduled_at,
            teaser=teaser,
        )
        _queue_publication(context.application, publication)
        await message.reply_text(
            f"RSTELBOT: zapowiedź zaplanowana na {_display_schedule_time(scheduled_at)} "
            f"({settings.timezone}). Numer: {publication.id}."
        )
    except (ValueError, RuntimeError) as exc:
        await message.reply_text(f"RSTELBOT: {exc}")


@admin_only
async def schedule_free_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    source = message.reply_to_message
    if source is None or len(context.args) != 1:
        await message.reply_text(
            "Użycie: odpowiedz /schedule_free <RRRR-MM-DDTHH:MM> na materiał."
        )
        return
    try:
        scheduled_at = _parse_schedule_time(context.args[0])
        content, _ = await content_service.create_free_content_record(context.bot, source)
        publication = await content_service.create_scheduled_publication(
            content.id,
            "free",
            scheduled_at,
        )
        _queue_publication(context.application, publication)
        await message.reply_text(
            f"RSTELBOT: bezpłatna treść zaplanowana na {_display_schedule_time(scheduled_at)} "
            f"({settings.timezone}). Numer: {publication.id}."
        )
    except (ValueError, RuntimeError, TelegramError) as exc:
        await message.reply_text(f"RSTELBOT: nie udało się zaplanować treści: {exc}")


@admin_only
async def scheduled_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await content_service.list_scheduled_publications()
    if not rows:
        await update.effective_message.reply_text("RSTELBOT: brak oczekujących lub nieudanych publikacji.")
        return
    lines = ["Zaplanowane publikacje RSTELBOT:"]
    for row in rows:
        content = await content_service.get_content_by_id(row.content_id)
        title = content.title if content else f"treść {row.content_id}"
        lines.append(
            f"• {row.id} | {row.status} | {row.kind} | {title} | "
            f"{_display_schedule_time(row.scheduled_at)} ({settings.timezone})"
        )
    await _reply_in_chunks(update.effective_message, "\n".join(lines))


@admin_only
async def cancel_scheduled_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        await update.effective_message.reply_text("Użycie: /schedule_cancel <numer>")
        return
    try:
        publication_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Numer publikacji musi być liczbą.")
        return
    cancelled = await content_service.cancel_scheduled_publication(publication_id)
    await update.effective_message.reply_text(
        "RSTELBOT: publikacja anulowana."
        if cancelled
        else "RSTELBOT: nie znaleziono oczekującej publikacji o tym numerze."
    )


@admin_only
async def preview_default_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message.reply_to_message is None:
        await message.reply_text("Odpowiedz komendą /preview_default na zdjęcie domyślne.")
        return
    try:
        await content_service.set_default_preview(context.bot, message.reply_to_message)
        await message.reply_text("RSTELBOT: ustawiono domyślny podgląd produktów.")
    except (ValueError, TelegramError) as exc:
        await message.reply_text(f"RSTELBOT: nie udało się ustawić podglądu: {exc}")


@admin_only
async def preview_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message.reply_to_message is None or len(context.args) != 1:
        await message.reply_text("Użycie: odpowiedz /preview <slug> na zdjęcie poglądowe.")
        return
    try:
        await content_service.set_content_preview(
            context.bot,
            context.args[0],
            message.reply_to_message,
        )
        await message.reply_text("RSTELBOT: zapisano podgląd dla produktu.")
    except (ValueError, TelegramError) as exc:
        await message.reply_text(f"RSTELBOT: nie udało się ustawić podglądu: {exc}")


@admin_only
async def preview_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1:
        await update.effective_message.reply_text("Użycie: /preview_clear <slug>")
        return
    try:
        await content_service.clear_content_preview(context.args[0])
        await update.effective_message.reply_text(
            "RSTELBOT: produkt będzie używać podglądu domyślnego."
        )
    except ValueError as exc:
        await update.effective_message.reply_text(f"RSTELBOT: {exc}")


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
        price = f" | cena: {content.stars_price} ⭐" if content.type == ContentType.PAID else ""
        lines.append(
            f"• {content.slug} | {content.type.value} | {content.title}{price} | dostęp: {buyers}"
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
        paid_orders = await session.scalar(
            select(func.count(StarPayment.payload)).where(StarPayment.status == "paid")
        )
        stars_revenue = await session.scalar(
            select(func.coalesce(func.sum(StarPayment.amount), 0)).where(
                StarPayment.status == "paid"
            )
        )
        active_codes_count = await session.scalar(
            select(func.count(AccessCode.code)).where(AccessCode.revoked_at.is_(None))
        )
    await update.effective_message.reply_text(
        "RSTELBOT — statystyki\n"
        f"Użytkownicy: {users_count}\n"
        f"Treści: {contents_count} (darmowe: {free_count}, płatne: {paid_count})\n"
        f"Nadane dostępy: {access_count}\n"
        f"Płatności Telegram Stars: {paid_orders} ({stars_revenue} ⭐)\n"
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
        ("price", price_command),
        ("teaser", teaser_command),
        ("schedule", schedule_teaser_command),
        ("schedule_free", schedule_free_command),
        ("scheduled", scheduled_command),
        ("schedule_cancel", cancel_scheduled_command),
        ("preview_default", preview_default_command),
        ("preview", preview_command),
        ("preview_clear", preview_clear_command),
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
        MessageHandler(
            filters.ChatType.PRIVATE
            & ~filters.COMMAND
            & ~filters.SUCCESSFUL_PAYMENT,
            stage_admin_message,
        ),
        group=0,
    )
