from __future__ import annotations

import unittest
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ycbot.bot.app import AccessMiddleware, BranchBotManager, SchedulerMiddleware
from ycbot.bot.handlers import _hunt_detail_text
from ycbot.bot.keyboards import account_detail_keyboard
from ycbot.bot.runtime import BotRuntimeScope
from ycbot.core.scheduler import HuntScheduler
from ycbot.bot.ui import main_menu_text
from ycbot.db.repositories import BranchRepository
from ycbot.yc.center import CloudCenterOrganizationCreator


class BranchScopeUiTests(unittest.TestCase):
    def test_main_menu_hides_branches_for_branch_runtime(self) -> None:
        text = main_menu_text(can_manage_branches=False)

        self.assertNotIn("Филиалы", text)

    def test_main_menu_shows_branches_for_main_runtime(self) -> None:
        text = main_menu_text(can_manage_branches=True)

        self.assertIn("Филиалы", text)

    def test_account_detail_has_test_organization_button(self) -> None:
        markup = account_detail_keyboard("acc-1").as_markup()
        buttons = [button for row in markup.inline_keyboard for button in row]

        self.assertTrue(any(button.text == "Тест: создать организацию" for button in buttons))
        self.assertTrue(any(button.callback_data == "accounts:org_test_ask:acc-1" for button in buttons))
        self.assertTrue(any(button.text == "Загрузить cookies" for button in buttons))
        self.assertTrue(any(button.callback_data == "accounts:cookies_ask:acc-1" for button in buttons))
        self.assertTrue(any(button.text == "Указать прокси" for button in buttons))
        self.assertTrue(any(button.callback_data == "accounts:proxy_ask:acc-1" for button in buttons))

    def test_hunt_detail_text_shows_runtime_and_ip_metrics_without_deletion_words(self) -> None:
        text = _hunt_detail_text(
            {
                "job_id": "job-1",
                "status": "running",
                "prefixes": ["84.201"],
                "target_count": 1,
                "target_total": 1,
                "match_count": 1,
                "runtime_seconds": 125,
                "checked_ip_count": 17,
                "active_cloud_count": 3,
                "error": None,
                "matches": [
                    {
                        "ip": "84.201.1.2",
                        "prefix": "84.201",
                        "cloud_id": "cloud-1",
                    }
                ],
                "clouds": [{"cloud_id": "cloud-dead", "status": "deleting"}],
            }
        )
        lowered = text.lower()

        self.assertIn("время работы", lowered)
        self.assertIn("перебрано ip", lowered)
        self.assertIn("активных облаков", lowered)
        self.assertIn("1/1 vm с нужным префиксом", lowered)
        self.assertNotIn("удален", lowered)
        self.assertNotIn("удал", lowered)

    def test_branch_repository_is_available(self) -> None:
        self.assertIsNotNone(BranchRepository)

    def test_runtime_router_receives_scope_and_scheduler_middlewares(self) -> None:
        settings = SimpleNamespace(
            allowed_chat_ids=(123,),
            tg_token=SimpleNamespace(get_secret_value=lambda: "123456:ABCDEF"),
        )
        context = SimpleNamespace(scheduler=object())
        manager = BranchBotManager(settings=settings, context=context, logger=logging.getLogger("test"))

        dispatcher = manager._build_dispatcher(BotRuntimeScope(None, (123,), True))
        runtime_router = dispatcher.sub_routers[0]
        message_middlewares = list(runtime_router.message.middleware._middlewares)
        callback_middlewares = list(runtime_router.callback_query.middleware._middlewares)

        self.assertTrue(any(isinstance(item, AccessMiddleware) for item in message_middlewares))
        self.assertTrue(any(isinstance(item, SchedulerMiddleware) for item in message_middlewares))
        self.assertTrue(any(isinstance(item, AccessMiddleware) for item in callback_middlewares))
        self.assertTrue(any(isinstance(item, SchedulerMiddleware) for item in callback_middlewares))

    def test_runtime_routers_keep_independent_scopes(self) -> None:
        settings = SimpleNamespace(
            allowed_chat_ids=(111,),
            tg_token=SimpleNamespace(get_secret_value=lambda: "123456:ABCDEF"),
        )
        context = SimpleNamespace(scheduler=object())
        manager = BranchBotManager(settings=settings, context=context, logger=logging.getLogger("test"))

        main_dispatcher = manager._build_dispatcher(BotRuntimeScope(None, (111,), True))
        branch_dispatcher = manager._build_dispatcher(BotRuntimeScope("branch-2", (222,), False))

        main_middlewares = list(main_dispatcher.sub_routers[0].message.middleware._middlewares)
        branch_middlewares = list(branch_dispatcher.sub_routers[0].message.middleware._middlewares)
        main_access = [item for item in main_middlewares if isinstance(item, AccessMiddleware)]
        branch_access = [item for item in branch_middlewares if isinstance(item, AccessMiddleware)]

        self.assertEqual(1, len(main_access))
        self.assertEqual(1, len(branch_access))
        self.assertIsNone(main_access[0].scope.branch_id)
        self.assertEqual("branch-2", branch_access[0].scope.branch_id)
        self.assertIsNot(
            main_dispatcher.sub_routers[0].message.middleware,
            branch_dispatcher.sub_routers[0].message.middleware,
        )


class ClosingSession:
    def __init__(self) -> None:
        self.closed = False
        self.committed = False
        self.deactivated_account_id: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    async def commit(self) -> None:
        if self.closed:
            raise RuntimeError("commit after session context closed")
        self.committed = True


class FakeDb:
    def __init__(self, session: ClosingSession) -> None:
        self._session = session

    def session(self) -> ClosingSession:
        return self._session


class FakeAccountRepository:
    def __init__(self, session: ClosingSession) -> None:
        self.session = session

    async def deactivate_account(self, account_id: str) -> None:
        self.session.deactivated_account_id = account_id


class SchedulerAccountDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_account_commits_before_session_context_closes(self) -> None:
        session = ClosingSession()
        scheduler = HuntScheduler(
            settings=SimpleNamespace(),
            db=FakeDb(session),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=SimpleNamespace(),
            logger=logging.getLogger("test"),
        )
        scheduler._get_account_for_branch = AsyncMock(return_value=SimpleNamespace(is_active=True))
        scheduler._account_has_active_hunt = AsyncMock(return_value=False)

        with patch("ycbot.core.scheduler.AccountRepository", FakeAccountRepository):
            deleted = await scheduler.delete_account("acc-1", branch_id="branch-1")

        self.assertTrue(deleted)
        self.assertEqual("acc-1", session.deactivated_account_id)
        self.assertTrue(session.committed)


class SchedulerOrganizationTestTests(unittest.TestCase):
    def test_next_test_organization_name_uses_center_prefix(self) -> None:
        scheduler = HuntScheduler(
            settings=SimpleNamespace(yc_center_org_name_prefix="demo-org"),
            db=SimpleNamespace(),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=SimpleNamespace(),
            logger=logging.getLogger("test"),
        )

        name = scheduler._next_test_organization_name()

        self.assertTrue(name.startswith("demo-org-test-"))


class CloudCenterCookieTests(unittest.TestCase):
    def test_parse_json_cookie_export(self) -> None:
        cookies = CloudCenterOrganizationCreator._parse_cookies(
            '[{"domain": ".yandex.ru", "name": "Session_id", "value": "abc", "path": "/", "secure": true, "expirationDate": 1893456000}]'
        )

        self.assertEqual(1, len(cookies))
        self.assertEqual("Session_id", cookies[0]["name"])
        self.assertEqual(".yandex.ru", cookies[0]["domain"])
        self.assertEqual(1893456000, cookies[0]["expiry"])

    def test_parse_netscape_cookie_export(self) -> None:
        cookies = CloudCenterOrganizationCreator._parse_cookies(
            ".yandex.ru\tTRUE\t/\tTRUE\t1893456000\tSession_id\tabc"
        )

        self.assertEqual(1, len(cookies))
        self.assertEqual("Session_id", cookies[0]["name"])
        self.assertEqual("abc", cookies[0]["value"])

    def test_parse_netscape_http_only_cookie_export(self) -> None:
        cookies = CloudCenterOrganizationCreator._parse_cookies(
            "#HttpOnly_.yandex.ru\tTRUE\t/\tTRUE\t1893456000\tSession_id\tabc"
        )

        self.assertEqual(1, len(cookies))
        self.assertTrue(cookies[0]["httpOnly"])


if __name__ == "__main__":
    unittest.main()
