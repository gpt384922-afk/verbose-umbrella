from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ycbot.bot.handlers import _hunt_confirm_text, _hunt_prefixes_text
from ycbot.bot.keyboards import hunt_prefixes_keyboard
from ycbot.core.hunter import HunterEngine, ManagedCloud
from ycbot.core.prefixes import KNOWN_PREFIX_VALUES, match_known_prefix, validate_hunt_prefixes
from ycbot.core.scheduler import HuntScheduler, HuntStartRequest, HuntStartScope
from ycbot.core.state_manager import CloudLifecycle
from ycbot.db.enums import CloudState as DbCloudState, HuntCloudStatus
from ycbot.yc import Address


class PrefixCatalogUiTests(unittest.TestCase):
    def test_known_prefixes_are_fixed_and_match_ip_addresses(self) -> None:
        self.assertEqual(["158.160", "84.201", "51.250", "87.250.247-254", "77.88.21"], KNOWN_PREFIX_VALUES)
        self.assertEqual("158.160", match_known_prefix("158.160.10.20"))
        self.assertEqual("84.201", match_known_prefix("84.201.1.2"))
        self.assertEqual("51.250", match_known_prefix("51.250.99.1"))
        self.assertEqual("87.250.247-254", match_known_prefix("87.250.247.1"))
        self.assertEqual("87.250.247-254", match_known_prefix("87.250.254.200"))
        self.assertEqual("77.88.21", match_known_prefix("77.88.21.9"))
        self.assertIsNone(match_known_prefix("87.250.246.255"))
        self.assertIsNone(match_known_prefix("87.250.255.1"))
        self.assertIsNone(match_known_prefix("77.88.210.1"))
        self.assertIsNone(match_known_prefix("8.8.8.8"))

    def test_validate_hunt_prefixes_rejects_unknown_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported prefix"):
            validate_hunt_prefixes(["84.201", "1.2"])

    def test_hunt_prefixes_text_contains_operator_descriptions(self) -> None:
        text = _hunt_prefixes_text()

        self.assertIn("158.160", text)
        self.assertIn("84.201", text)
        self.assertIn("51.250", text)
        self.assertIn("87.250.247-254", text)
        self.assertIn("77.88.21", text)
        self.assertIn("для всех операторов", text)
        self.assertIn("только для Мегафон", text)

    def test_hunt_prefixes_keyboard_marks_selected_prefixes(self) -> None:
        markup = hunt_prefixes_keyboard({"84.201"}).as_markup()
        buttons = [button for row in markup.inline_keyboard for button in row]
        by_callback = {button.callback_data: button.text for button in buttons}

        self.assertIn("🟢", by_callback["hunt:prefix:84.201"])
        self.assertIn("⚪", by_callback["hunt:prefix:158.160"])
        self.assertIn("Дальше", by_callback["hunt:prefix_done"])

    def test_hunt_confirm_text_shows_preflight_existing_ips(self) -> None:
        text = _hunt_confirm_text(
            {
                "accounts": [{"id": "acc-1", "name": "Main"}],
                "selected_accounts": ["acc-1"],
                "organization_options": [
                    {
                        "key": "1",
                        "account_id": "acc-1",
                        "account_name": "Main",
                        "organization_id": "org-1",
                        "organization_name": "Org",
                    }
                ],
                "selected_orgs": ["1"],
                "prefixes": ["84.201", "51.250"],
                "target_count": 1,
                "preflight_existing_ips": [
                    {
                        "ip": "158.160.10.20",
                        "prefix": "158.160",
                        "account_name": "Main",
                        "organization_name": "Org",
                        "cloud_id": "cloud-abcdef",
                    }
                ],
                "preflight_errors": [],
            }
        )

        self.assertIn("Уже есть IP", text)
        self.assertIn("158.160.10.20", text)
        self.assertIn("cloud-a", text)

    def test_hunt_confirm_text_shows_empty_preflight_result(self) -> None:
        text = _hunt_confirm_text(
            {
                "accounts": [{"id": "acc-1", "name": "Main"}],
                "selected_accounts": ["acc-1"],
                "organization_options": [
                    {
                        "key": "1",
                        "account_id": "acc-1",
                        "account_name": "Main",
                        "organization_id": "org-1",
                        "organization_name": "Org",
                    }
                ],
                "selected_orgs": ["1"],
                "prefixes": ["84.201"],
                "target_count": 1,
                "preflight_existing_ips": [],
                "preflight_errors": [],
            }
        )

        self.assertIn("Существующие IP из известных префиксов не найдены", text)


class SchedulerPrefixTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_hunt_rejects_unknown_prefix_before_database_work(self) -> None:
        scheduler = HuntScheduler(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=SimpleNamespace(),
            logger=logging.getLogger("test"),
        )

        with self.assertRaisesRegex(ValueError, "unsupported prefix"):
            await scheduler.start_hunt(
                HuntStartRequest(
                    requested_by_chat_id=123,
                    branch_id=None,
                    prefixes=["84.201", "1.2"],
                    target_count=1,
                    scopes=[HuntStartScope(account_id="acc-1", organization_id="org-1")],
                )
            )

    async def test_scan_existing_prefix_ips_reports_known_prefixes(self) -> None:
        scheduler = HuntScheduler(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=SimpleNamespace(),
            logger=logging.getLogger("test"),
        )
        scheduler._get_account_for_branch = AsyncMock(
            return_value=SimpleNamespace(
                id="acc-1",
                name="Main",
                oauth_token="token",
                proxy_url=None,
            )
        )
        scheduler.list_account_organizations = AsyncMock(
            return_value=[
                {
                    "account_id": "acc-1",
                    "account_name": "Main",
                    "organization_id": "org-1",
                    "organization_name": "Org",
                }
            ]
        )

        class FakeYcClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        class FakeCloudsApi:
            def __init__(self, **kwargs):
                pass

            async def list_clouds(self, organization_id):
                return [
                    SimpleNamespace(
                        id="cloud-1",
                        name="Cloud",
                        state=DbCloudState.ACTIVE,
                        deleting=False,
                    )
                ]

            async def list_folders(self, cloud_id):
                return [SimpleNamespace(id="folder-1", name="Folder")]

        class FakeVpcApi:
            def __init__(self, **kwargs):
                pass

            async def list_addresses(self, folder_id):
                return [Address(id="addr-1", ip="158.160.10.20"), Address(id="addr-2", ip="8.8.8.8")]

        class FakeComputeApi:
            def __init__(self, **kwargs):
                pass

            async def list_instances(self, folder_id):
                return []

        with (
            patch("ycbot.core.scheduler.YcClient", FakeYcClient),
            patch("ycbot.core.scheduler.CloudsApi", FakeCloudsApi),
            patch("ycbot.core.scheduler.VpcApi", FakeVpcApi),
            patch("ycbot.core.scheduler.ComputeApi", FakeComputeApi),
        ):
            result = await scheduler.scan_existing_prefix_ips(
                [HuntStartScope(account_id="acc-1", organization_id="org-1")],
                branch_id=None,
            )

        self.assertEqual([], result["errors"])
        self.assertEqual(1, len(result["existing_ips"]))
        self.assertEqual("158.160.10.20", result["existing_ips"][0]["ip"])
        self.assertEqual("158.160", result["existing_ips"][0]["prefix"])

    async def test_scan_existing_prefix_ips_includes_unknown_non_deleting_clouds(self) -> None:
        scheduler = HuntScheduler(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=SimpleNamespace(),
            logger=logging.getLogger("test"),
        )
        scheduler._get_account_for_branch = AsyncMock(
            return_value=SimpleNamespace(
                id="acc-1",
                name="Main",
                oauth_token="token",
                proxy_url=None,
            )
        )
        scheduler.list_account_organizations = AsyncMock(
            return_value=[
                {
                    "account_id": "acc-1",
                    "account_name": "Main",
                    "organization_id": "org-1",
                    "organization_name": "Org",
                }
            ]
        )

        class FakeYcClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        class FakeCloudsApi:
            def __init__(self, **kwargs):
                pass

            async def list_clouds(self, organization_id):
                return [
                    SimpleNamespace(
                        id="cloud-unknown",
                        name="Cloud Without Status",
                        state=DbCloudState.UNKNOWN,
                        deleting=False,
                    )
                ]

            async def list_folders(self, cloud_id):
                return [SimpleNamespace(id="folder-1", name="Folder")]

        class FakeVpcApi:
            def __init__(self, **kwargs):
                pass

            async def list_addresses(self, folder_id):
                return [Address(id="addr-1", ip="84.201.10.20")]

        class FakeComputeApi:
            def __init__(self, **kwargs):
                pass

            async def list_instances(self, folder_id):
                return []

        with (
            patch("ycbot.core.scheduler.YcClient", FakeYcClient),
            patch("ycbot.core.scheduler.CloudsApi", FakeCloudsApi),
            patch("ycbot.core.scheduler.VpcApi", FakeVpcApi),
            patch("ycbot.core.scheduler.ComputeApi", FakeComputeApi),
        ):
            result = await scheduler.scan_existing_prefix_ips(
                [HuntStartScope(account_id="acc-1", organization_id="org-1")],
                branch_id=None,
            )

        self.assertEqual([], result["errors"])
        self.assertEqual(1, len(result["existing_ips"]))
        self.assertEqual("84.201.10.20", result["existing_ips"][0]["ip"])


class HunterKnownPrefixProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_known_prefix_protects_cloud_even_when_not_selected(self) -> None:
        state = SimpleNamespace()
        state.add_checked_ip = AsyncMock()
        state.set_cloud_lifecycle = AsyncMock()
        state.get_prefixes = AsyncMock(return_value=["84.201"])
        hunter = HunterEngine(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=state,
            semaphore=SimpleNamespace(),
            logger=logging.getLogger("test"),
        )
        hunter._set_cloud_progress = AsyncMock()
        vpc_api = SimpleNamespace(list_addresses=AsyncMock(return_value=[Address(id="addr-1", ip="158.160.10.20")]))
        cloud = ManagedCloud(
            account_id="acc-1",
            organization_id="org-1",
            cloud_id="cloud-1",
            cloud_name="Cloud",
            folder_id="folder-1",
            billing_account_id="billing-1",
        )

        protected = await hunter._scan_existing_matches("job-1", cloud, vpc_api)

        self.assertTrue(protected)
        state.set_cloud_lifecycle.assert_awaited_once_with("job-1", "cloud-1", CloudLifecycle.IDLE)
        hunter._set_cloud_progress.assert_awaited_once()
        args = hunter._set_cloud_progress.await_args.args
        self.assertEqual(HuntCloudStatus.SKIPPED, args[3])
