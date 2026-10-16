"""Private-chat user commands and access-code handling."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

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
            "Otwórz link do produktu, aby zobaczyć podgląd i kupić go za gwiazdki Telegrama. "
            "Użyj /moje, aby ponownie otrzymać odblokowane treści. "
            "Regulamin: /terms. Pomoc w sprawie płatności: /paysupport."
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

    has_access = await content_service.user_has_access(telegram_id, content.id)
    if has_access:
        await message.reply_text(
            f"RSTELBOT: masz już dostęp do „{content.title}”. Wysyłam materiał ponownie."
        )
        try:
            await content_service.deliver_content(context.bot, telegram_id, content)
        except TelegramError:
            logger.exception("Nie udało się wysłać ponownie treści %s.", content.slug)
            await message.reply_text("RSTELBOT: nie udało się wysłać materiału. Użyj /moje.")
        return

    preview_sent = False
    try:
        preview_sent = await content_service.copy_content_preview(
            context.bot,
            telegram_id,
            content,
        )
    except TelegramError:
        logger.warning("Nie udało się wysłać podglądu treści %s.", content.slug)

    description = content.teaser or "Podgląd produktu."
    price_text = (
        f"Cena: {content.stars_price} ⭐"
        if content.stars_price
        else "Cena nie została jeszcze ustawiona."
    )
    lines = [f"RSTELBOT — {content.title}", description, price_text]
    if content.stars_price and settings.terms_url and settings.support_contact:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"Akceptuję regulamin i kupuję za {content.stars_price} ⭐",
                        callback_data=f"buy:{content.id}",
                    )
                ],
                [InlineKeyboardButton("Regulamin", url=settings.terms_url)],
            ]
        )
    else:
        keyboard = None
        lines.append(
            "Zakup jest chwilowo niedostępny. Administrator musi skonfigurować "
            "cenę, regulamin i kontakt wsparcia."
        )
    if not preview_sent:
        lines.append("Podgląd produktu nie został jeszcze skonfigurowany.")
    await message.reply_text("\n\n".join(lines), reply_markup=keyboard)


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


async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update):
        return
    if settings.terms_url:
        await update.effective_message.reply_text(
            f"Regulamin sprzedaży cyfrowej RSTELBOT: {settings.terms_url}"
        )
    else:
        await update.effective_message.reply_text(
            "RSTELBOT: regulamin nie został jeszcze skonfigurowany."
        )


async def paysupport_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private(update):
        return
    if settings.support_contact:
        await update.effective_message.reply_text(
            f"RSTELBOT — pomoc w sprawie płatności: {settings.support_contact}"
        )
    else:
        await update.effective_message.reply_text(
            "RSTELBOT: kontakt wsparcia nie został jeszcze skonfigurowany."
        )


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not _private(update):
        if query is not None:
            await query.answer("Zakup jest dostępny w prywatnym czacie z RSTELBOT.", show_alert=True)
        return
    await query.answer()
    if not settings.terms_url or not settings.support_contact:
        await context.bot.send_message(
            chat_id=user.id,
            text="RSTELBOT: zakup jest chwilowo niedostępny; skontaktuj się z administratorem.",
        )
        return
    try:
        content_id = int(query.data.split(":", 1)[1])
        content = await content_service.get_content_by_id(content_id)
        if content is None or content.type.value != "paid":
            raise ValueError("Nie znaleziono płatnej treści.")
        if await content_service.user_has_access(user.id, content.id):
            await context.bot.send_message(
                chat_id=user.id,
                text=f"RSTELBOT: masz już dostęp do „{content.title}”. Wysyłam materiał.",
            )
            await content_service.deliver_content(context.bot, user.id, content)
            return
        await content_service.accept_terms(user.id)
        intent = await content_service.create_star_payment_intent(content.id, user.id)
        await context.bot.send_invoice(
            chat_id=user.id,
            title=intent.title,
            description=intent.description,
            payload=intent.payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=intent.title, amount=intent.amount)],
            start_parameter="rstelbot",
            protect_content=True,
        )
    except (ValueError, TelegramError) as exc:
        logger.info("Nie udało się rozpocząć płatności Stars: %s", exc)
        await context.bot.send_message(chat_id=user.id, text=f"RSTELBOT: {exc}")
    except Exception:
        logger.exception("Błąd podczas tworzenia faktury Telegram Stars.")
        await context.bot.send_message(
            chat_id=user.id,
            text="RSTELBOT: nie udało się rozpocząć płatności. Spróbuj później.",
        )


async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if query is None:
        return
    try:
        valid = await content_service.validate_star_precheckout(
            query.invoice_payload,
            query.from_user.id,
            query.total_amount,
            query.currency,
        )
    except Exception:
        logger.exception("Nie udało się zweryfikować pre-checkout Telegram Stars.")
        valid = False
    if valid:
        await query.answer(ok=True)
    else:
        await query.answer(
            ok=False,
            error_message="Zamówienie jest nieaktualne. Wróć do RSTELBOT i rozpocznij zakup ponownie.",
        )


async def successful_payment_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user
    payment = message.successful_payment if message else None
    if message is None or user is None or payment is None:
        return
    try:
        content = await content_service.complete_star_payment(
            payment.invoice_payload,
            user.id,
            payment.total_amount,
            payment.currency,
            payment.telegram_payment_charge_id,
        )
        await content_service.deliver_content(context.bot, user.id, content)
        await message.reply_text(
            f"RSTELBOT: płatność przyjęta. Odblokowano „{content.title}”. "
            "Materiał jest chroniony przed przekazywaniem; ponowny dostęp: /moje."
        )
    except Exception:
        logger.exception("Płatność Stars otrzymana, ale nie udało się zrealizować zamówienia.")
        await message.reply_text(
            "RSTELBOT: otrzymaliśmy płatność, ale wystąpił problem z dostarczeniem treści. "
            "Użyj /moje lub skontaktuj się przez /paysupport."
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
    application.add_handler(CommandHandler("terms", terms_command), group=1)
    application.add_handler(CommandHandler("paysupport", paysupport_command), group=1)
    application.add_handler(CallbackQueryHandler(buy_callback, pattern=r"^buy:\d+$"), group=1)
    application.add_handler(PreCheckoutQueryHandler(precheckout_handler), group=1)
    application.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler),
        group=1,
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_code_redemption,
        ),
        group=1,
    )
