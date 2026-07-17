from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings
from app.core.logging import get_logger
from app.infrastructure.migrations import current_revision, upgrade_head_async

logger = get_logger(__name__)


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self.schema_revision: str | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database not initialized")
        return self._engine

    async def connect(self) -> None:
        connect_args: dict[str, object] = {}
        url = self._settings.database_url
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False

        engine_kwargs: dict[str, object] = {
            "echo": self._settings.debug and not self._settings.is_production,
            "pool_pre_ping": True,
            "connect_args": connect_args,
        }
        # SQLite does not support QueuePool sizing the same way.
        if not url.startswith("sqlite"):
            engine_kwargs.update(
                {
                    "pool_size": self._settings.db_pool_size,
                    "max_overflow": self._settings.db_max_overflow,
                    "pool_recycle": self._settings.db_pool_recycle_seconds,
                    "pool_timeout": self._settings.db_pool_timeout_seconds,
                }
            )

        self._engine = create_async_engine(url, **engine_kwargs)
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

        # Import models so metadata is populated for create_all fallback.
        from app.infrastructure import orm  # noqa: F401

        if self._settings.auto_migrate:
            await upgrade_head_async(url)
        elif self._settings.db_create_all:
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        try:
            self.schema_revision = await current_revision(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("schema_revision_unavailable", error=str(exc))
            self.schema_revision = None

        logger.info(
            "database_connected",
            url=url.split("@")[-1],
            revision=self.schema_revision,
            pool_size=self._settings.db_pool_size if not url.startswith("sqlite") else 1,
        )

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("database_disconnected")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("Database not initialized")
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def healthcheck(self) -> bool:
        try:
            async with self.session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("database_healthcheck_failed", error=str(exc))
            return False

    async def pool_status(self) -> dict[str, object]:
        if self._engine is None:
            return {"initialized": False}
        pool = self._engine.sync_engine.pool
        status: dict[str, object] = {"initialized": True, "dialect": self._engine.dialect.name}
        for attr in ("size", "checkedin", "checkedout", "overflow"):
            fn = getattr(pool, attr, None)
            if callable(fn):
                try:
                    status[attr] = fn()
                except Exception:  # noqa: BLE001
                    pass
        status["revision"] = self.schema_revision
        return status
