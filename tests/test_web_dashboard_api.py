from __future__ import annotations

import unittest
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from ycbot.web.app import create_app


class FakeDb:
    @asynccontextmanager
    async def session(self):
        yield object()


class FakeDashboardService:
    async def build(self, _session):
        return {
            "overview": {
                "active_hunts": 1,
                "matched_vm": 2,
                "managed_clouds": 3,
                "checked_ip": 4,
            },
            "accounts": [
                {
                    "id": "acc-1",
                    "name": "main",
                    "email": "flow@example.test",
                    "branch_id": None,
                    "is_active": True,
                    "has_proxy": False,
                    "organization_count": 1,
                    "cloud_count": 3,
                    "active_billing_count": 1,
                    "created_at": None,
                }
            ],
            "organizations": [],
            "clouds": [],
            "hunts": [],
            "matches": [],
            "branches": [],
            "settings": {
                "platform_id": "standard-v2",
                "image_family": "debian-12",
                "cores": 2,
                "core_fraction": 5,
                "memory_gb": 0.5,
                "disk_type_id": "network-hdd",
                "disk_size_gb": 5,
                "vm_batch_size": 8,
                "cloud_target_count": 5,
                "username": "user",
                "zones": ["ru-central1-a"],
            },
        }


class FakeAccountsService:
    def __init__(self) -> None:
        self.created = None
        self.synced_account_id = None

    async def create(self, _session, payload):
        self.created = payload
        return "acc-new"

    async def sync(self, account_id):
        if account_id == "broken":
            raise RuntimeError("sync failed")
        self.synced_account_id = account_id
        return {"organizations": 2, "billing_accounts": 1}


class FakeHuntsService:
    def __init__(self) -> None:
        self.started = None
        self.preflighted = None

    async def start(self, payload):
        self.started = payload
        return "job-new"

    async def preflight(self, payload):
        self.preflighted = payload
        return {
            "ready": True,
            "summary": "Готово искать 2 нужные VM в 1 организации",
            "capacity": {
                "selected_organizations": 1,
                "target_count": 2,
                "max_vm_per_cloud": 5,
                "estimated_vm_limit": 2,
            },
            "checks": [
                {
                    "key": "scope",
                    "label": "Область запуска выбрана",
                    "status": "ready",
                    "detail": "1 организация выбрана",
                },
                {
                    "key": "target_count",
                    "label": "Нужных VM",
                    "status": "ready",
                    "detail": "Остановиться после 2 нужных VM",
                }
            ],
            "vm_profile": "Intel Cascade Lake · 2 vCPU · 0.5 GB RAM · network-hdd 5 GB · debian-12",
        }


class WebDashboardApiTests(unittest.TestCase):
    def test_dashboard_endpoint_returns_payload_without_secret_fields(self) -> None:
        app = create_app(db=FakeDb(), dashboard_service=FakeDashboardService())

        response = TestClient(app).get("/api/dashboard")

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual(1, payload["overview"]["active_hunts"])
        self.assertEqual("debian-12", payload["settings"]["image_family"])
        self.assertNotIn("oauth_token", str(payload))
        self.assertNotIn("password", str(payload))

    def test_create_account_endpoint_persists_account_without_echoing_secret_fields(self) -> None:
        accounts_service = FakeAccountsService()
        app = create_app(
            db=FakeDb(),
            dashboard_service=FakeDashboardService(),
            accounts_service=accounts_service,
        )

        response = TestClient(app).post(
            "/api/accounts",
            json={
                "name": "flow",
                "oauth_token": "oauth-secret",
                "email": "flow@example.test",
                "password": "password-secret",
                "secret": "second-factor",
                "proxy_url": None,
            },
        )

        self.assertEqual(201, response.status_code)
        payload = response.json()
        self.assertEqual({"id": "acc-new"}, payload)
        self.assertEqual("flow", accounts_service.created.name)
        self.assertNotIn("oauth-secret", str(payload))

    def test_sync_account_endpoint_refreshes_directory(self) -> None:
        accounts_service = FakeAccountsService()
        app = create_app(
            db=FakeDb(),
            dashboard_service=FakeDashboardService(),
            accounts_service=accounts_service,
        )

        response = TestClient(app).post("/api/accounts/acc-1/sync")

        self.assertEqual(200, response.status_code)
        self.assertEqual("acc-1", accounts_service.synced_account_id)
        self.assertEqual({"organizations": 2, "billing_accounts": 1}, response.json())

    def test_sync_account_endpoint_returns_bad_gateway_on_yc_error(self) -> None:
        app = create_app(
            db=FakeDb(),
            dashboard_service=FakeDashboardService(),
            accounts_service=FakeAccountsService(),
        )

        response = TestClient(app).post("/api/accounts/broken/sync")

        self.assertEqual(502, response.status_code)
        self.assertIn("sync failed", response.json()["detail"])

    def test_start_hunt_endpoint_creates_job_without_echoing_tokens(self) -> None:
        hunts_service = FakeHuntsService()
        app = create_app(
            db=FakeDb(),
            dashboard_service=FakeDashboardService(),
            accounts_service=FakeAccountsService(),
            hunts_service=hunts_service,
        )

        response = TestClient(app).post(
            "/api/hunts",
            json={
                "scopes": [{"account_id": "acc-1", "organization_id": "org-1"}],
                "prefixes": ["84.201"],
                "target_count": 2,
                "vm_config": {"platform_id": "standard-v2", "cores": 2},
            },
        )

        self.assertEqual(201, response.status_code)
        self.assertEqual({"job_id": "job-new"}, response.json())
        self.assertEqual(["84.201"], hunts_service.started.prefixes)
        self.assertNotIn("oauth", str(response.json()).lower())

    def test_hunt_preflight_endpoint_returns_readiness_without_starting_job(self) -> None:
        hunts_service = FakeHuntsService()
        app = create_app(
            db=FakeDb(),
            dashboard_service=FakeDashboardService(),
            accounts_service=FakeAccountsService(),
            hunts_service=hunts_service,
        )

        response = TestClient(app).post(
            "/api/hunts/preflight",
            json={
                "scopes": [{"account_id": "acc-1", "organization_id": "org-1"}],
                "prefixes": ["84.201"],
                "target_count": 2,
                "vm_config": {"platform_id": "standard-v2", "cores": 2},
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ready"])
        self.assertIn("нужные VM", payload["summary"])
        self.assertEqual(2, payload["capacity"]["target_count"])
        self.assertIn("Нужных VM", [check["label"] for check in payload["checks"]])
        self.assertEqual(["84.201"], hunts_service.preflighted.prefixes)
        self.assertIsNone(hunts_service.started)
        self.assertNotIn("oauth", str(payload).lower())


if __name__ == "__main__":
    unittest.main()
