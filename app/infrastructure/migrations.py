from __future__ import annotations

import asyncio
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.core.logging import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parents[2]


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_head(database_url: str) -> None:
    """Apply all pending migrations (sync entrypoint for CLI / to_thread)."""
    cfg = alembic_config(database_url)
    command.upgrade(cfg, "head")
    logger.info("alembic_upgrade_complete", url=database_url.split("@")[-1])


async def upgrade_head_async(database_url: str) -> None:
    await asyncio.to_thread(upgrade_head, database_url)


async def current_revision(database_url: str) -> str | None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:

            def _get(sync_conn: object) -> str | None:
                return MigrationContext.configure(sync_conn).get_current_revision()  # type: ignore[arg-type]

            return await conn.run_sync(_get)
    finally:
        await engine.dispose()
