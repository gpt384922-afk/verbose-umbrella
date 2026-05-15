from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ycbot.config import Settings
from ycbot.core.hunter import HunterEngine
from ycbot.core.scheduler import HuntScheduler
from ycbot.core.state_manager import StateManager
from ycbot.db.repositories import AccountRepository
from ycbot.db.session import Database
from ycbot.web.schemas import AccountCreateRequest


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned in {"", "-", "skip", "/skip"}:
        return None
    return cleaned


class AccountsService:
    def __init__(self, *, settings: Settings | None = None, db: Database | None = None) -> None:
        self.settings = settings
        self.db = db
        self.logger = logging.getLogger("ycbot.web.accounts")

    async def create(self, session: AsyncSession, payload: AccountCreateRequest) -> str:
        name = payload.name.strip()
        oauth_token = payload.oauth_token.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Account name is required")
        if not oauth_token:
            raise HTTPException(status_code=422, detail="OAuth token is required")

        repo = AccountRepository(session)
        account = await repo.create_account(
            branch_id=None,
            name=name,
            oauth_token=oauth_token,
            email=_optional(payload.email),
            password=_optional(payload.password),
            secret=_optional(payload.secret),
            proxy_url=_optional(payload.proxy_url),
        )
        await session.commit()
        return account.id

    async def sync(self, account_id: str) -> dict[str, int]:
        if self.settings is None or self.db is None:
            raise RuntimeError("AccountsService sync requires settings and database")

        semaphore = asyncio.Semaphore(self.settings.yc_max_concurrency)
        state = StateManager()
        hunter = HunterEngine(
            settings=self.settings,
            db=self.db,
            state=state,
            semaphore=semaphore,
            logger=self.logger,
        )
        scheduler = HuntScheduler(
            settings=self.settings,
            db=self.db,
            state=state,
            hunter=hunter,
            semaphore=semaphore,
            logger=self.logger,
        )
        return await scheduler.refresh_account_directory(account_id, branch_id=None)
