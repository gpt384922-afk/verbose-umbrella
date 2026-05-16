from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ycbot.config import Settings
from ycbot.db.base import Base


class Database:
    def __init__(self, settings: Settings) -> None:
        engine_options = {
            "echo": False,
            "pool_pre_ping": True,
        }
        if not settings.database_dsn.startswith("sqlite"):
            engine_options.update(
                {
                    "pool_size": 10,
                    "max_overflow": 20,
                }
            )
        self._engine = create_async_engine(
            settings.database_dsn,
            **engine_options,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def init_models(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if self._engine.dialect.name == "postgresql":
                await conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS center_proxy_url VARCHAR(512)"))
                await conn.execute(
                    text(
                        """
                        UPDATE accounts
                        SET center_proxy_url = proxy_url
                        WHERE center_proxy_url IS NULL AND proxy_url IS NOT NULL
                        """
                    )
                )
                await conn.execute(text("UPDATE accounts SET proxy_url = NULL WHERE center_proxy_url IS NOT NULL"))
                await conn.execute(
                    text(
                        """
                        ALTER TABLE hunt_jobs
                        ALTER COLUMN requested_by_chat_id TYPE BIGINT,
                        ALTER COLUMN progress_message_chat_id TYPE BIGINT,
                        ALTER COLUMN progress_message_id TYPE BIGINT
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        ALTER TABLE accounts
                        ADD COLUMN IF NOT EXISTS branch_id VARCHAR
                        REFERENCES bot_branches(id) ON DELETE SET NULL
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        ALTER TABLE hunt_jobs
                        ADD COLUMN IF NOT EXISTS branch_id VARCHAR
                        REFERENCES bot_branches(id) ON DELETE SET NULL
                        """
                    )
                )
                await conn.execute(text("ALTER TABLE hunt_jobs ADD COLUMN IF NOT EXISTS vm_config JSONB"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_branch_id ON accounts(branch_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_hunt_jobs_branch_id ON hunt_jobs(branch_id)"))
                await conn.execute(text("ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_name_key"))
                await conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_branch_name
                        ON accounts(COALESCE(branch_id, '__main__'), name)
                        """
                    )
                )
            elif self._engine.dialect.name == "sqlite":
                rows = await conn.execute(text("PRAGMA table_info(accounts)"))
                account_columns = {row[1] for row in rows}
                if "center_proxy_url" not in account_columns:
                    await conn.execute(text("ALTER TABLE accounts ADD COLUMN center_proxy_url VARCHAR(512)"))
                await conn.execute(
                    text(
                        """
                        UPDATE accounts
                        SET center_proxy_url = proxy_url
                        WHERE center_proxy_url IS NULL AND proxy_url IS NOT NULL
                        """
                    )
                )
                await conn.execute(text("UPDATE accounts SET proxy_url = NULL WHERE center_proxy_url IS NOT NULL"))

    @asynccontextmanager
    async def session(self) -> AsyncSession:
        async with self._session_factory() as session:
            yield session

    async def close(self) -> None:
        await self._engine.dispose()


def create_database(settings: Settings) -> Database:
    return Database(settings)
