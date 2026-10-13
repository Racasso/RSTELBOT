"""Private-chat user commands and access-code handling."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.config import settings
from bot.services import codes as code_service
from bot.services import content as content_service
from bot.services.codes import RedemptionStatus

logger = logging.getLogger(__name__)


def _private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private")


async def _remember(update: Update) -> None:
    user = update.effective_user
    if user is not None:
        await code_service.remember_user(user.id, user.username)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update) or update.effective_user is None:
        return
    await _remember(update)
    message = update.effective_message
    telegram_id = update.effective_user.id
    if not context.args:
        await message.reply_text(
            "Cześć! Tu RSTELBOT — bot do płatnych treści.\n"
            f"Twój Telegram ID: {telegram_id}\n\n"
            "Aby odblokować treść, podaj swój ID administratorowi po płatności. "
            "Otrzymany kod wyślij tutaj jako zwykłą wiadomość. Użyj /moje, "
            "aby ponownie otrzymać odblokowane treści."
        )
        return

    slug = context.args[0]
    try:
        content = await content_service.get_content_by_slug(slug)
    except ValueError:
        content = None
    if content is None or content.type.value != "paid":
        await message.reply_text("RSTELBOT: nie znaleziono takiej płatnej treści.")
        return

    teaser = content.teaser or "Szczegóły treści przekaże administrator."
    has_access = await content_service.user_has_access(telegram_id, content.id)
    access_note = (
        "Masz już dostęp — wpisz wcześniej otrzymany kod ponownie albo użyj /moje."
        if has_access
        else "Po płatności poza botem przekaż administratorowi swój Telegram ID."
    )
    await message.reply_text(
        f"RSTELBOT — {content.title}\n\n{teaser}\n\n"
        "Cena i płatność są ustalane poza RSTELBOT.\n"
        f"Twój Telegram ID: {telegram_id}\n{access_note}"
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update) or update.effective_user is None:
        return
    await _remember(update)
    await update.effective_message.reply_text(
        f"RSTELBOT: Twój Telegram ID to {update.effective_user.id}."
    )


async def my_contents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update) or update.effective_user is None:
        return
    await _remember(update)
    user_id = update.effective_user.id
    contents = await content_service.list_user_contents(user_id)
    if not contents:
        await update.effective_message.reply_text(
            "RSTELBOT: nie masz jeszcze odblokowanych treści."
        )
        return

    names = "\n".join(f"• {content.title} ({content.slug})" for content in contents)
    await update.effective_message.reply_text(
        f"RSTELBOT — Twoje odblokowane treści:\n{names}\n\nWysyłam je ponownie z ochroną przed przekazywaniem."
    )
    for content in contents:
        try:
            await content_service.deliver_content(context.bot, user_id, content)
        except TelegramError as exc:
            logger.warning("Nie udało się ponownie wysłać treści %s: %s", content.slug, exc)
            await context.bot.send_message(
                chat_id=user_id,
                text=f"RSTELBOT: nie udało się wysłać „{content.title}”. Spróbuj ponownie później.",
            )


async def handle_code_redemption(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _private(update) or update.effective_user is None:
        return
    user = update.effective_user
    message = update.effective_message
    if message is None or message.text is None:
        return

    raw_code = message.text.strip()
    # Admins also receive access codes (often while testing). Ignore their
    # unrelated text so it can still be staged as content by the admin handler.
    if user.id in settings.admin_ids and not code_service.normalize_code(
        raw_code
    ).startswith("BOX-"):
        return

    try:
        result = await code_service.redeem_code(raw_code, user.id, user.username)
    except Exception:
        logger.exception("Błąd przy weryfikacji kodu w RSTELBOT.")
        await message.reply_text("RSTELBOT: wystąpił błąd. Spróbuj ponownie później.")
        return

    if result.status == RedemptionStatus.RATE_LIMITED:
        await message.reply_text(
            "RSTELBOT: wykorzystano limit 5 błędnych prób w ciągu 10 minut. "
            "Spróbuj ponownie po upływie tego czasu."
        )
        return
    if result.status == RedemptionStatus.INVALID or result.content is None:
        await message.reply_text("Kod nieprawidłowy")
        return

    try:
        await content_service.deliver_content(context.bot, user.id, result.content)
        await message.reply_text(
            "RSTELBOT: treść została wysłana z ochroną przed przekazywaniem."
        )
    except TelegramError as exc:
        logger.warning("Dostęp zapisano, ale nie wysłano treści: %s", exc)
        await message.reply_text(
            "RSTELBOT: dostęp został zapisany, ale nie udało się wysłać treści. "
            "Użyj /moje, aby spróbować ponownie."
        )


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_command), group=1)
    application.add_handler(CommandHandler("id", id_command), group=1)
    application.add_handler(CommandHandler("moje", my_contents_command), group=1)
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_code_redemption,
        ),
        group=1,
    )
