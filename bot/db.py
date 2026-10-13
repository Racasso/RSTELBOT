"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import SQLAlchemyError

from bot.config import settings

logger = logging.getLogger(__name__)

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
