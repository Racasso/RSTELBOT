"""Environment based configuration for RSTELBOT."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Brak wymaganej zmiennej środowiskowej {name}.")
    return value


def _parse_int(name: str) -> int:
    value = _required(name)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Zmienna {name} musi być liczbą całkowitą.") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    channel_id: int
    storage_chat_id: int
    database_url: str


def load_settings() -> Settings:
    raw_admin_ids = _required("ADMIN_IDS")
    try:
        admin_ids = frozenset(
            int(value.strip())
            for value in raw_admin_ids.split(",")
            if value.strip()
        )
    except ValueError as exc:
        raise RuntimeError("ADMIN_IDS musi być listą liczb rozdzielonych przecinkami.") from exc
    if not admin_ids:
        raise RuntimeError("ADMIN_IDS musi zawierać co najmniej jedno Telegram ID.")

    database_url = _required("DATABASE_URL")
    if not database_url.startswith("mysql+aiomysql://"):
        raise RuntimeError("DATABASE_URL musi używać sterownika mysql+aiomysql.")

    return Settings(
        bot_token=_required("BOT_TOKEN"),
        admin_ids=admin_ids,
        channel_id=_parse_int("CHANNEL_ID"),
        storage_chat_id=_parse_int("STORAGE_CHAT_ID"),
        database_url=database_url,
    )


settings = load_settings()
