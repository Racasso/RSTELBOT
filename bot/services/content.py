"""Content intake, channel publishing, and protected content delivery."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import BadRequest

from bot.config import settings
from bot.db import SessionLocal
from bot.models import (
    Access,
    Content,
    ContentType,
    DefaultPreview,
    ScheduledPublication,
    StarPayment,
    StagedUpload,
    User,
    utcnow_naive,
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ContentError(ValueError):
    """A user-correctable content operation error."""


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    payload: str
    amount: int
    title: str
    description: str


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).replace(tzinfo=None)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


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


async def get_content_by_id(content_id: int) -> Content | None:
    async with SessionLocal() as session:
        return await session.get(Content, content_id)


def _is_image_message(message: Message) -> bool:
    if message.photo:
        return True
    document = message.document
    return bool(document and document.mime_type and document.mime_type.startswith("image/"))


async def set_default_preview(bot, source_message: Message) -> int:
    if not _is_image_message(source_message):
        raise ContentError("Podgląd domyślny musi być zdjęciem lub plikiem graficznym.")
    stored_ids = await storage_ids_for_reply(bot, source_message)
    async with SessionLocal.begin() as session:
        preview = await session.get(DefaultPreview, 1)
        if preview is None:
            session.add(
                DefaultPreview(
                    id=1,
                    storage_chat_id=settings.storage_chat_id,
                    storage_message_id=stored_ids[0],
                )
            )
        else:
            preview.storage_chat_id = settings.storage_chat_id
            preview.storage_message_id = stored_ids[0]
            preview.updated_at = utcnow_naive()
    return stored_ids[0]


async def set_content_preview(bot, slug: str, source_message: Message) -> int:
    slug = normalize_slug(slug)
    if not _is_image_message(source_message):
        raise ContentError("Podgląd produktu musi być zdjęciem lub plikiem graficznym.")
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(Content).where(
                Content.slug == slug,
                Content.type == ContentType.PAID,
            )
        )
    if existing is None:
        raise ContentError(f"Nie znaleziono płatnej treści o slug '{slug}'.")
    stored_ids = await storage_ids_for_reply(bot, source_message)
    async with SessionLocal.begin() as session:
        content = await session.scalar(
            select(Content).where(Content.slug == slug).with_for_update()
        )
        if content is None or content.type != ContentType.PAID:
            raise ContentError(f"Nie znaleziono płatnej treści o slug '{slug}'.")
        content.preview_storage_msg_id = stored_ids[0]
    return stored_ids[0]


async def clear_content_preview(slug: str) -> None:
    slug = normalize_slug(slug)
    async with SessionLocal.begin() as session:
        content = await session.scalar(
            select(Content).where(Content.slug == slug).with_for_update()
        )
        if content is None or content.type != ContentType.PAID:
            raise ContentError(f"Nie znaleziono płatnej treści o slug '{slug}'.")
        content.preview_storage_msg_id = None


async def copy_content_preview(bot, target_chat_id: int, content: Content) -> bool:
    """Copy the product-specific preview or global default, if configured."""
    storage_chat_id = content.storage_chat_id
    message_id = content.preview_storage_msg_id
    if message_id is None:
        async with SessionLocal() as session:
            fallback = await session.get(DefaultPreview, 1)
            if fallback is None:
                return False
            storage_chat_id = fallback.storage_chat_id
            message_id = fallback.storage_message_id
    await bot.copy_message(
        chat_id=target_chat_id,
        from_chat_id=storage_chat_id,
        message_id=message_id,
        protect_content=False,
    )
    return True


async def create_paid_content(
    bot,
    source_message: Message,
    slug: str,
    stars_price: int,
    title: str,
) -> Content:
    slug = normalize_slug(slug)
    title = title.strip()
    if stars_price < 1:
        raise ContentError("Cena musi wynosić co najmniej 1 gwiazdkę Telegrama.")
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
        stars_price=stars_price,
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


async def set_content_price(slug: str, stars_price: int) -> Content:
    slug = normalize_slug(slug)
    if stars_price < 1:
        raise ContentError("Cena musi wynosić co najmniej 1 gwiazdkę Telegrama.")
    async with SessionLocal.begin() as session:
        content = await session.scalar(
            select(Content).where(Content.slug == slug).with_for_update()
        )
        if content is None or content.type != ContentType.PAID:
            raise ContentError(f"Nie znaleziono płatnej treści o slug '{slug}'.")
        content.stars_price = stars_price
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


async def create_free_content_record(
    bot,
    source_message: Message,
) -> tuple[Content, bool]:
    """Create a free content record without publishing it yet."""
    slug = f"free-{source_message.chat_id}-{source_message.message_id}"
    async with SessionLocal() as session:
        existing = await session.scalar(select(Content).where(Content.slug == slug))
    if existing is not None:
        if existing.type != ContentType.FREE:
            raise ContentError("Ta wiadomość źródłowa jest już użyta przez inną treść.")
        return existing, False

    storage_ids = await storage_ids_for_reply(bot, source_message)
    try:
        async with SessionLocal.begin() as session:
            content = await session.scalar(
                select(Content).where(Content.slug == slug).with_for_update()
            )
            if content is None:
                content = Content(
                    slug=slug,
                    type=ContentType.FREE,
                    title="Bezpłatna treść",
                    storage_chat_id=settings.storage_chat_id,
                    storage_msg_ids=storage_ids,
                )
                session.add(content)
                await session.flush()
            elif content.type != ContentType.FREE:
                raise ContentError("Ta wiadomość źródłowa jest już użyta przez inną treść.")
            content_id = content.id
    except IntegrityError as exc:
        raise ContentError("Nie udało się zapisać bezpłatnej treści.") from exc
    async with SessionLocal() as session:
        content = await session.get(Content, content_id)
        if content is None:
            raise ContentError("Nie znaleziono zapisanej bezpłatnej treści.")
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
        await copy_content_preview(bot, settings.channel_id, content)
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


async def create_scheduled_publication(
    content_id: int,
    kind: str,
    scheduled_at: datetime,
    *,
    teaser: str | None = None,
) -> ScheduledPublication:
    if kind not in {"free", "teaser"}:
        raise ContentError("Nieobsługiwany typ zaplanowanej publikacji.")
    scheduled_at_utc = _utc_naive(scheduled_at)
    if scheduled_at_utc <= utcnow_naive():
        raise ContentError("Termin publikacji musi być w przyszłości.")

    async with SessionLocal.begin() as session:
        content = await session.scalar(
            select(Content).where(Content.id == content_id).with_for_update()
        )
        if content is None:
            raise ContentError("Nie znaleziono treści do zaplanowania.")
        pending = await session.scalar(
            select(ScheduledPublication.id)
            .where(
                ScheduledPublication.content_id == content_id,
                ScheduledPublication.status.in_(("pending", "publishing")),
            )
            .limit(1)
        )
        if pending is not None:
            raise ContentError("Dla tej treści istnieje już oczekująca publikacja.")
        if kind == "free" and content.type != ContentType.FREE:
            raise ContentError("/schedule_free można użyć tylko dla darmowej treści.")
        if kind == "teaser":
            if content.type != ContentType.PAID:
                raise ContentError("Zapowiedź można zaplanować tylko dla płatnej treści.")
            if not content.stars_price:
                raise ContentError("Najpierw ustaw cenę treści w gwiazdkach komendą /price.")
            teaser = (teaser or "").strip()
            if not teaser:
                raise ContentError("Podaj opis zapowiedzi.")
            if len(teaser) > 3500:
                raise ContentError("Opis zapowiedzi jest za długi (maksymalnie 3500 znaków).")
            content.teaser = teaser
        elif content.channel_msg_id is not None:
            raise ContentError("Ta darmowa treść została już opublikowana.")

        publication = ScheduledPublication(
            content_id=content_id,
            kind=kind,
            teaser=teaser,
            scheduled_at=scheduled_at_utc,
            status="pending",
        )
        session.add(publication)
        await session.flush()
        publication_id = publication.id

    async with SessionLocal() as session:
        publication = await session.get(ScheduledPublication, publication_id)
        if publication is None:
            raise ContentError("Nie udało się odczytać zaplanowanej publikacji.")
        return publication


async def list_pending_publications() -> list[ScheduledPublication]:
    async with SessionLocal.begin() as session:
        # If the process stopped during publication, retry that row after restart.
        await session.execute(
            update(ScheduledPublication)
            .where(ScheduledPublication.status == "publishing")
            .values(status="pending", error=None)
        )
        rows = await session.scalars(
            select(ScheduledPublication)
            .where(ScheduledPublication.status == "pending")
            .order_by(ScheduledPublication.scheduled_at, ScheduledPublication.id)
        )
        return list(rows.all())


async def cancel_scheduled_publication(publication_id: int) -> bool:
    async with SessionLocal.begin() as session:
        publication = await session.scalar(
            select(ScheduledPublication)
            .where(
                ScheduledPublication.id == publication_id,
                ScheduledPublication.status == "pending",
            )
            .with_for_update()
        )
        if publication is None:
            return False
        publication.status = "cancelled"
        return True


async def list_scheduled_publications() -> list[ScheduledPublication]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(ScheduledPublication)
            .where(ScheduledPublication.status.in_(("pending", "failed")))
            .order_by(ScheduledPublication.scheduled_at, ScheduledPublication.id)
        )
        return list(rows.all())


async def publish_scheduled_publication(bot, publication_id: int, bot_username: str) -> int | None:
    async with SessionLocal.begin() as session:
        publication = await session.scalar(
            select(ScheduledPublication)
            .where(ScheduledPublication.id == publication_id)
            .with_for_update()
        )
        if publication is None or publication.status != "pending":
            return None
        content = await session.get(Content, publication.content_id)
        if content is None:
            publication.status = "failed"
            publication.error = "Treść została usunięta."
            return None
        publication.status = "publishing"
        kind = publication.kind
        teaser = publication.teaser
        content_id = content.id

    try:
        if kind == "free":
            published_ids = await copy_stored_messages(
                bot,
                settings.channel_id,
                content.storage_chat_id,
                content.storage_msg_ids,
                protect_content=False,
            )
            message_id = published_ids[0]
            async with SessionLocal.begin() as session:
                current_content = await session.get(Content, content_id)
                if current_content is not None:
                    current_content.channel_msg_id = message_id
        elif kind == "teaser" and teaser:
            message_id = await publish_teaser(bot, content, teaser, bot_username)
        else:
            raise ContentError("Zaplanowana publikacja nie ma poprawnej treści.")
    except Exception as exc:
        async with SessionLocal.begin() as session:
            publication = await session.get(ScheduledPublication, publication_id)
            if publication is not None:
                publication.status = "failed"
                publication.error = str(exc)[:500]
        raise

    async with SessionLocal.begin() as session:
        publication = await session.get(ScheduledPublication, publication_id)
        if publication is not None:
            publication.status = "published"
            publication.channel_msg_id = message_id
            publication.published_at = utcnow_naive()
            publication.error = None
    return message_id


async def accept_terms(telegram_id: int) -> None:
    async with SessionLocal.begin() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            user = User(telegram_id=telegram_id)
            session.add(user)
        user.terms_accepted_at = utcnow_naive()
        user.terms_version = settings.terms_version


async def create_star_payment_intent(
    content_id: int,
    telegram_id: int,
) -> PaymentIntent:
    async with SessionLocal.begin() as session:
        user = await session.get(User, telegram_id)
        if (
            user is None
            or user.terms_accepted_at is None
            or user.terms_version != settings.terms_version
        ):
            raise ContentError("Najpierw zaakceptuj aktualny regulamin RSTELBOT.")
        content = await session.scalar(
            select(Content).where(Content.id == content_id).with_for_update()
        )
        if content is None or content.type != ContentType.PAID:
            raise ContentError("Nie znaleziono płatnej treści.")
        if not content.stars_price or content.stars_price < 1:
            raise ContentError("Cena tej treści nie została jeszcze ustawiona.")
        if await session.get(Access, (telegram_id, content.id)) is not None:
            raise ContentError("Masz już dostęp do tej treści.")
        payload = f"rstel:{secrets.token_urlsafe(24)}"
        session.add(
            StarPayment(
                payload=payload,
                telegram_id=telegram_id,
                content_id=content.id,
                amount=content.stars_price,
                currency="XTR",
                status="pending",
            )
        )
        return PaymentIntent(
            payload=payload,
            amount=content.stars_price,
            title=content.title[:32],
            description=(content.teaser or f"Dostęp do: {content.title}")[:255],
        )


async def validate_star_precheckout(
    payload: str,
    telegram_id: int,
    amount: int,
    currency: str,
) -> bool:
    async with SessionLocal.begin() as session:
        user = await session.get(User, telegram_id)
        if (
            user is None
            or user.terms_accepted_at is None
            or user.terms_version != settings.terms_version
        ):
            return False
        payment = await session.scalar(
            select(StarPayment)
            .where(StarPayment.payload == payload)
            .with_for_update()
        )
        if (
            payment is None
            or payment.status != "pending"
            or payment.telegram_id != telegram_id
            or payment.amount != amount
            or payment.currency != currency
            or currency != "XTR"
        ):
            return False
        content_lock = await session.scalar(
            select(Content.id)
            .where(Content.id == payment.content_id)
            .with_for_update()
        )
        if content_lock is None:
            return False
        now = utcnow_naive()
        stale_cutoff = now - timedelta(minutes=30)
        await session.execute(
            update(StarPayment)
            .where(
                StarPayment.telegram_id == telegram_id,
                StarPayment.content_id == payment.content_id,
                StarPayment.status == "prechecked",
                StarPayment.prechecked_at < stale_cutoff,
            )
            .values(status="expired")
        )
        other_precheckout = await session.scalar(
            select(StarPayment.payload)
            .where(
                StarPayment.telegram_id == telegram_id,
                StarPayment.content_id == payment.content_id,
                StarPayment.status == "prechecked",
                StarPayment.payload != payload,
            )
            .limit(1)
        )
        if other_precheckout is not None:
            return False
        if await session.get(Access, (telegram_id, payment.content_id)) is not None:
            return False
        payment.status = "prechecked"
        payment.prechecked_at = now
        return True


async def complete_star_payment(
    payload: str,
    telegram_id: int,
    amount: int,
    currency: str,
    telegram_charge_id: str,
) -> Content:
    async with SessionLocal.begin() as session:
        payment = await session.scalar(
            select(StarPayment).where(StarPayment.payload == payload).with_for_update()
        )
        if (
            payment is None
            or payment.telegram_id != telegram_id
            or payment.amount != amount
            or payment.currency != currency
            or currency != "XTR"
        ):
            raise ContentError("Nie udało się zweryfikować płatności Telegram Stars.")
        if payment.status == "paid":
            if payment.telegram_charge_id != telegram_charge_id:
                raise ContentError("Płatność ma już inny identyfikator transakcji.")
            content = await session.get(Content, payment.content_id)
            if content is None:
                raise ContentError("Nie znaleziono zakupionej treści.")
            return content
        if payment.status != "prechecked":
            raise ContentError("Płatność nie przeszła weryfikacji przed zakupem.")

        content = await session.get(Content, payment.content_id)
        if content is None:
            raise ContentError("Nie znaleziono zakupionej treści.")
        if await session.get(Access, (telegram_id, content.id)) is None:
            session.add(Access(telegram_id=telegram_id, content_id=content.id, code=None))
        payment.status = "paid"
        payment.telegram_charge_id = telegram_charge_id
        payment.paid_at = utcnow_naive()
        return content


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
