from __future__ import annotations

import asyncio
import contextlib
import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, TelegramObject

from ycbot.app_context import AppContext, build_context, shutdown_context
from ycbot.bot.handlers import router
from ycbot.bot.notifications import send_match_notification
from ycbot.bot.runtime import BotRuntimeScope
from ycbot.config import get_settings
from ycbot.core.scheduler import HuntScheduler
from ycbot.utils import log_error, log_event, setup_logging


class AccessMiddleware(BaseMiddleware):
    def __init__(self, scope: BotRuntimeScope, branch_manager: BranchBotManager | None = None) -> None:
        self.scope = scope
        self.allowed = set(scope.allowed_chat_ids)
        self.branch_manager = branch_manager

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[object]],
        event: TelegramObject,
        data: dict,
    ) -> object:
        chat_id: int | None = None
        if isinstance(event, Message):
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery) and event.message:
            chat_id = event.message.chat.id

        if chat_id is None or chat_id not in self.allowed:
            return None
        data["bot_scope"] = self.scope
        data["branch_manager"] = self.branch_manager
        return await handler(event, data)


class SchedulerMiddleware(BaseMiddleware):
    def __init__(self, scheduler: HuntScheduler) -> None:
        self.scheduler = scheduler

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[object]],
        event: TelegramObject,
        data: dict,
    ) -> object:
        data["scheduler"] = self.scheduler
        return await handler(event, data)


@dataclass(slots=True)
class BotRuntime:
    branch_id: str | None
    bot: Bot
    dispatcher: Dispatcher
    task: asyncio.Task[None]
    scope: BotRuntimeScope


class BranchBotManager:
    def __init__(self, *, settings, context: AppContext, logger) -> None:
        self.settings = settings
        self.context = context
        self.logger = logger
        self._runtimes: dict[str | None, BotRuntime] = {}
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self.start_main_bot()
        for branch in await self.context.scheduler.list_active_branches():
            try:
                await self.start_branch(branch)
            except Exception as exc:  # noqa: BLE001
                log_error(self.logger, "branch.start.error", exc, branch_id=branch["id"])

    async def start_main_bot(self) -> None:
        scope = BotRuntimeScope(
            branch_id=None,
            allowed_chat_ids=self.settings.allowed_chat_ids,
            can_manage_branches=True,
        )
        await self._start_runtime(
            branch_id=None,
            token=self.settings.tg_token.get_secret_value(),
            scope=scope,
        )

    async def start_branch(self, branch: dict) -> None:
        if not branch.get("is_active", True):
            return
        branch_id = branch["id"]
        scope = BotRuntimeScope(
            branch_id=branch_id,
            allowed_chat_ids=(int(branch["owner_chat_id"]),),
            can_manage_branches=False,
        )
        await self._start_runtime(
            branch_id=branch_id,
            token=branch["bot_token"],
            scope=scope,
        )
        log_event(self.logger, "branch.started", branch_id=branch_id, bot_username=branch.get("bot_username"))

    async def stop_branch(self, branch_id: str) -> None:
        async with self._lock:
            runtime = self._runtimes.pop(branch_id, None)
        if runtime is None:
            return
        runtime.task.cancel()
        await asyncio.gather(runtime.task, return_exceptions=True)
        await runtime.bot.session.close()
        log_event(self.logger, "branch.stopped", branch_id=branch_id)

    async def wait(self) -> None:
        await self._stop_event.wait()

    async def shutdown(self) -> None:
        async with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            runtime.task.cancel()
        if runtimes:
            await asyncio.gather(*(runtime.task for runtime in runtimes), return_exceptions=True)
        for runtime in runtimes:
            await runtime.bot.session.close()

    async def send_match_notification(self, notification) -> None:
        branch_id = getattr(notification, "branch_id", None)
        runtime = self._runtimes.get(branch_id)
        if runtime is None:
            return
        await send_match_notification(runtime.bot, self.settings, notification)

    async def _start_runtime(self, *, branch_id: str | None, token: str, scope: BotRuntimeScope) -> None:
        async with self._lock:
            existing = self._runtimes.get(branch_id)
            if existing and not existing.task.done():
                return
            if existing:
                await existing.bot.session.close()

            bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
            dispatcher = self._build_dispatcher(scope)
            task = asyncio.create_task(self._poll_runtime(branch_id, dispatcher, bot))
            self._runtimes[branch_id] = BotRuntime(
                branch_id=branch_id,
                bot=bot,
                dispatcher=dispatcher,
                task=task,
                scope=scope,
            )

    def _build_dispatcher(self, scope: BotRuntimeScope) -> Dispatcher:
        dp = Dispatcher(storage=MemoryStorage())
        access = AccessMiddleware(scope, self)
        scheduler_mw = SchedulerMiddleware(self.context.scheduler)
        runtime_router = copy.deepcopy(router)
        runtime_router.message.middleware(access)
        runtime_router.callback_query.middleware(access)
        runtime_router.message.middleware(scheduler_mw)
        runtime_router.callback_query.middleware(scheduler_mw)
        dp.include_router(runtime_router)
        return dp

    async def _poll_runtime(self, branch_id: str | None, dispatcher: Dispatcher, bot: Bot) -> None:
        try:
            await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log_error(self.logger, "bot.polling.error", exc, branch_id=branch_id)
            if branch_id is None:
                self._stop_event.set()
            else:
                async with self._lock:
                    self._runtimes.pop(branch_id, None)
                with contextlib.suppress(Exception):
                    await bot.session.close()


async def run_bot() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    context = await build_context(settings)
    await context.scheduler.start_cleanup()
    logger = context.logger if hasattr(context, "logger") else __import__("logging").getLogger("ycbot")
    manager = BranchBotManager(settings=settings, context=context, logger=logger)
    context.hunter.set_match_notifier(manager.send_match_notification)

    try:
        await manager.start()
        await manager.wait()
    finally:
        await manager.shutdown()
        await shutdown_context(context)


def main() -> None:
    asyncio.run(run_bot())
