"""RSTELBOT entry point: Telegram long polling and handler registration."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, ContextTypes

from bot.config import settings
from bot.db import close_db, init_db
from bot.handlers import admin, user

BOT_NAME = "RSTELBOT"


async def post_init(application: Application) -> None:
    await init_db()
    bot_user = await application.bot.get_me()
    application.bot_data["bot_username"] = bot_user.username
    logging.getLogger(__name__).info(
        "%s uruchomiony jako @%s.", BOT_NAME, bot_user.username or "unknown"
    )


async def post_shutdown(application: Application) -> None:
    await close_db()


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger = logging.getLogger(__name__)
    if context.error is not None:
        logger.error(
            "Nieobsłużony błąd aktualizacji Telegrama.",
            exc_info=(type(context.error), context.error, context.error.__traceback__),
        )
    else:
        logger.error("Nieobsłużony błąd aktualizacji Telegrama bez wyjątku w kontekście.")
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                "RSTELBOT: wystąpił nieoczekiwany błąd. Spróbuj ponownie później."
            )
        except Exception:
            logger.debug("Nie udało się wysłać komunikatu o błędzie.")


def build_application() -> Application:
    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(False)
        .build()
    )
    admin.register_handlers(application)
    # This registers the private non-command text handler that redeems codes.
    user.register_handlers(application)
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    application = build_application()
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        bootstrap_retries=5,
    )


if __name__ == "__main__":
    main()
