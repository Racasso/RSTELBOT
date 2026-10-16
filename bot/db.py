"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import inspect

from bot.config import settings

logger = logging.getLogger(__name__)


def _migrate_existing_schema(connection) -> None:
    """Add the new nullable fields to an existing MVP database in place."""
    additions = {
        "users": {
            "terms_accepted_at": "DATETIME NULL",
            "terms_version": "VARCHAR(32) NULL",
        },
        "contents": {
            "stars_price": "INT NULL",
            "preview_storage_msg_id": "INT NULL",
        },
        "star_payments": {
            "prechecked_at": "DATETIME NULL",
        },
    }
    inspector = inspect(connection)
    for table_name, columns_to_add in additions.items():
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, sql_type in columns_to_add.items():
            if column_name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {sql_type}"
                )

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db(max_attempts: int = 10) -> None:
    """Create the MVP schema, retrying briefly while MySQL starts up."""
    from bot.models import Base

    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.run_sync(_migrate_existing_schema)
            logger.info("Schemat MySQL RSTELBOT jest gotowy.")
            return
        except SQLAlchemyError:
            if attempt == max_attempts:
                raise
            delay = min(attempt * 2, 10)
            logger.warning(
                "MySQL niedostępny (próba %s/%s); ponawiam za %s s.",
                attempt,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)


async def close_db() -> None:
    await engine.dispose()
