from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ycbot.config import Settings
from ycbot.core import HunterEngine, HuntScheduler, StateManager
from ycbot.db.session import Database, create_database


@dataclass(slots=True)
class AppContext:
    settings: Settings
    db: Database
    semaphore: asyncio.Semaphore
    state: StateManager
    hunter: HunterEngine
    scheduler: HuntScheduler
    logger: object


async def build_context(settings: Settings) -> AppContext:
    db = create_database(settings)
    await db.init_models()

    semaphore = asyncio.Semaphore(settings.yc_max_concurrency)
    state = StateManager()

    import logging

    logger = logging.getLogger("ycbot")
    hunter = HunterEngine(
        settings=settings,
        db=db,
        state=state,
        semaphore=semaphore,
        logger=logger,
    )
    scheduler = HuntScheduler(
        settings=settings,
        db=db,
        state=state,
        hunter=hunter,
        semaphore=semaphore,
        logger=logger,
    )
    return AppContext(
        settings=settings,
        db=db,
        semaphore=semaphore,
        state=state,
        hunter=hunter,
        scheduler=scheduler,
        logger=logger,
    )


async def shutdown_context(context: AppContext) -> None:
    await context.scheduler.stop_cleanup()
    await context.db.close()
