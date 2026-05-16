from __future__ import annotations

import logging
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ycbot.bot.notifications import _match_text
from ycbot.config import Settings
from ycbot.core.hunter import DeletingCloud, HunterEngine, ManagedCloud, PendingCloudSlot, ScopeDescriptor
from ycbot.core.hunter import MatchNotification
from ycbot.core.ssh_keys import generate_ssh_keypair
from ycbot.core.vm_config import VmHuntConfig
from ycbot.core.scheduler import HuntScheduler, HuntStartScope
from ycbot.core.state_manager import CloudLifecycle, CloudState, MatchState, StateManager
from ycbot.db.enums import CloudState as DbCloudState
from ycbot.yc import Address, Organization, YcApiError
from ycbot.yc.compute import ComputeApi, Instance
from ycbot.yc.vpc import VpcApi


class FakeComputeClient:
    def __init__(self):
        self.requests = []

    async def request_json(self, method, url, *, params=None, body=None, retries=None, log_errors=True):
        self.requests.append((method, url, params, body, retries, log_errors))
        return {"done": True, "response": {"id": "vm-1", "networkInterfaces": []}}

    async def poll_operation(self, operation_id, *, timeout_seconds=240, min_delay=1.0, max_delay=2.5):
        return {"done": True, "response": {"id": "vm-1"}}


class FakeCloudsApi:
    def __init__(self, deleting=False, **kwargs):
        self.deleting = deleting

    async def list_clouds(self, organization_id):
        return [
            SimpleNamespace(
                id="cloud-1",
                name="Cloud",
                state=DbCloudState.DELETING if self.deleting else DbCloudState.ACTIVE,
                deleting=self.deleting,
            )
        ]

    async def list_folders(self, cloud_id):
        return [SimpleNamespace(id="folder-1", name="Folder")]


class ComputeApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_instance_payload_matches_vm_hunt_config(self) -> None:
        client = FakeComputeClient()
        api = ComputeApi(
            client=client,
            settings=SimpleNamespace(
                yc_compute_instance_url="https://compute.example/instances",
                yc_compute_image_url="https://compute.example/images",
                hunt_vm_platform_id="standard-v4a",
                hunt_vm_disk_type_id="network-hdd",
                hunt_vm_disk_size_gb=10,
                hunt_vm_cores=2,
                hunt_vm_core_fraction=20,
                hunt_vm_memory_gb=1,
            ),
            logger=logging.getLogger("test"),
        )

        await api.create_instance(
            folder_id="folder-1",
            name="hunter-vm-1",
            zone_id="ru-central1-a",
            subnet_id="subnet-a",
            image_id="image-1",
            ssh_username="user",
            ssh_public_key="ssh-ed25519 AAAA test",
            vm_config=VmHuntConfig(
                platform_id="standard-v3",
                cores=4,
                core_fraction=50,
                memory_gb=2,
                disk_type_id="network-ssd",
                disk_size_gb=20,
            ),
        )

        method, url, params, body, retries, log_errors = client.requests[0]
        self.assertEqual("POST", method)
        self.assertEqual("https://compute.example/instances", url)
        self.assertIsNone(params)
        self.assertIsNone(retries)
        self.assertFalse(log_errors)
        self.assertEqual("folder-1", body["folderId"])
        self.assertEqual("ru-central1-a", body["zoneId"])
        self.assertEqual("standard-v3", body["platformId"])
        self.assertEqual(
            {
                "cores": "4",
                "memory": str(2 * 1024**3),
                "coreFraction": "50",
            },
            body["resourcesSpec"],
        )
        self.assertTrue(body["bootDiskSpec"]["autoDelete"])
        self.assertEqual("network-ssd", body["bootDiskSpec"]["diskSpec"]["typeId"])
        self.assertEqual(str(20 * 1024**3), body["bootDiskSpec"]["diskSpec"]["size"])
        self.assertEqual("image-1", body["bootDiskSpec"]["diskSpec"]["imageId"])
        self.assertEqual(
            [
                {
                    "subnetId": "subnet-a",
                    "primaryV4AddressSpec": {"oneToOneNatSpec": {"ipVersion": "IPV4"}},
                }
            ],
            body["networkInterfaceSpecs"],
        )

    async def test_create_instance_payload_supports_minimal_cascade_lake_config(self) -> None:
        client = FakeComputeClient()
        api = ComputeApi(
            client=client,
            settings=SimpleNamespace(
                yc_compute_instance_url="https://compute.example/instances",
                yc_compute_image_url="https://compute.example/images",
                hunt_vm_platform_id="standard-v2",
                hunt_vm_disk_type_id="network-hdd",
                hunt_vm_disk_size_gb=5,
                hunt_vm_cores=2,
                hunt_vm_core_fraction=5,
                hunt_vm_memory_gb=0.5,
            ),
            logger=logging.getLogger("test"),
        )

        await api.create_instance(
            folder_id="folder-1",
            name="hunter-vm-1",
            zone_id="ru-central1-a",
            subnet_id="subnet-a",
            image_id="image-1",
            ssh_username="user",
            ssh_public_key="ssh-ed25519 AAAA test",
            vm_config=VmHuntConfig(
                platform_id="standard-v2",
                cores=2,
                core_fraction=5,
                memory_gb=0.5,
                disk_type_id="network-hdd",
                disk_size_gb=5,
            ),
        )

        body = client.requests[0][3]
        self.assertEqual("standard-v2", body["platformId"])
        self.assertEqual(
            {
                "cores": "2",
                "memory": str(512 * 1024**2),
                "coreFraction": "5",
            },
            body["resourcesSpec"],
        )
        self.assertEqual("network-hdd", body["bootDiskSpec"]["diskSpec"]["typeId"])
        self.assertEqual(str(5 * 1024**3), body["bootDiskSpec"]["diskSpec"]["size"])
        self.assertEqual({"preemptible": False}, body["schedulingPolicy"])
        self.assertEqual("user:ssh-ed25519 AAAA test", body["metadata"]["ssh-keys"])
        user_data = body["metadata"]["user-data"]
        self.assertIn("#cloud-config", user_data)
        self.assertIn("name: user", user_data)
        self.assertIn("ssh_authorized_keys:", user_data)
        self.assertIn("ssh-ed25519 AAAA test", user_data)

    def test_extract_external_ip_reads_one_to_one_nat(self) -> None:
        ip = ComputeApi.extract_external_ip(
            {
                "networkInterfaces": [
                    {
                        "primaryV4Address": {
                            "oneToOneNat": {"address": "84.201.10.20"},
                        }
                    }
                ]
            }
        )

        self.assertEqual("84.201.10.20", ip)


class VpcSubnetTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_subnets_returns_id_and_zone(self) -> None:
        test_case = self

        class Client:
            async def paginated(self, *, url, key, params=None):
                test_case.assertEqual("https://vpc.example/subnets", url)
                test_case.assertEqual("subnets", key)
                test_case.assertEqual({"folderId": "folder-1"}, params)
                return [
                    {"id": "subnet-a", "zoneId": "ru-central1-a"},
                    {"id": "subnet-d", "zoneId": "ru-central1-d"},
                ]

        api = VpcApi(
            client=Client(),
            settings=SimpleNamespace(yc_vpc_subnet_url="https://vpc.example/subnets"),
            logger=logging.getLogger("test"),
        )

        subnets = await api.list_subnets("folder-1")

        self.assertEqual(
            [
                ("subnet-a", "ru-central1-a"),
                ("subnet-d", "ru-central1-d"),
            ],
            [(item.id, item.zone_id) for item in subnets],
        )

    async def test_ensure_subnets_creates_network_and_missing_zone_subnets(self) -> None:
        class Client:
            def __init__(self):
                self.posts = []

            async def paginated(self, *, url, key, params=None):
                if key == "subnets":
                    return []
                if key == "networks":
                    return []
                raise AssertionError(key)

            async def request_json(self, method, url, *, params=None, body=None, retries=None, log_errors=True):
                self.posts.append((method, url, body))
                if url.endswith("/networks"):
                    return {"done": True, "response": {"id": "net-1", "name": body["name"]}}
                if url.endswith("/subnets"):
                    return {
                        "done": True,
                        "response": {
                            "id": f"subnet-{body['zoneId'][-1]}",
                            "zoneId": body["zoneId"],
                            "networkId": body["networkId"],
                        },
                    }
                raise AssertionError(url)

        client = Client()
        api = VpcApi(
            client=client,
            settings=SimpleNamespace(
                yc_vpc_subnet_url="https://vpc.example/subnets",
                yc_vpc_network_url="https://vpc.example/networks",
            ),
            logger=logging.getLogger("test"),
        )

        subnets = await api.ensure_subnets(
            folder_id="folder-1",
            zones=["ru-central1-a", "ru-central1-d"],
            cidr_blocks=["10.10.0.0/24", "10.20.0.0/24"],
        )

        self.assertEqual(["ru-central1-a", "ru-central1-d"], [item.zone_id for item in subnets])
        self.assertEqual("net-1", subnets[0].network_id)
        self.assertEqual(
            [
                ("POST", "https://vpc.example/networks", "ycbot-net-folder1"),
                ("POST", "https://vpc.example/subnets", "ycbot-subnet-a-folder1"),
                ("POST", "https://vpc.example/subnets", "ycbot-subnet-d-folder1"),
            ],
            [(method, url, body["name"]) for method, url, body in client.posts],
        )


class SshKeyTests(unittest.TestCase):
    def test_generate_ssh_keypair_returns_public_and_private_key(self) -> None:
        keypair = generate_ssh_keypair("user")

        self.assertEqual("user", keypair.username)
        self.assertTrue(keypair.public_key.startswith("ssh-"))
        self.assertIn("PRIVATE KEY", keypair.private_key)
        self.assertIn(keypair.public_key.strip(), keypair.metadata_value)
        self.assertTrue(keypair.metadata_value.startswith("user:ssh-"))

    def test_generate_ssh_keypair_does_not_require_ssh_keygen_binary(self) -> None:
        with patch("ycbot.core.ssh_keys.subprocess.run", side_effect=FileNotFoundError):
            keypair = generate_ssh_keypair("user")

        self.assertEqual("user", keypair.username)
        self.assertTrue(keypair.public_key.startswith("ssh-ed25519 "))
        self.assertIn("OPENSSH PRIVATE KEY", keypair.private_key)


class VmImageSettingsTests(unittest.TestCase):
    def test_default_vm_image_family_is_debian_12(self) -> None:
        with patch.dict(os.environ, {"TG_TOKEN": "123456:ABCDEF"}, clear=True):
            settings = Settings(_env_file=None)

        self.assertEqual("debian-12", settings.hunt_vm_image_family)


class FakeVmBatchComputeApi:
    def __init__(
        self,
        ips: dict[str, str | None],
        *,
        create_errors: dict[int, Exception] | None = None,
        listed_instances: list[Instance] | None = None,
        create_delay: float = 0.0,
    ):
        self.ips = ips
        self.create_errors = create_errors or {}
        self.listed_instances = listed_instances
        self.create_delay = create_delay
        self.create_attempts = 0
        self.create_in_flight = 0
        self.max_create_in_flight = 0
        self.created = []
        self.deleted = []
        self.stopped = []
        self.delete_in_flight = 0
        self.max_delete_in_flight = 0
        self.list_instances_called = False
        self.refresh_called = False

    async def get_latest_image_by_family(self, folder_id: str, family: str) -> str:
        return "image-1"

    async def create_instance(
        self,
        *,
        folder_id: str,
        name: str,
        zone_id: str,
        subnet_id: str,
        image_id: str,
        ssh_username: str,
        ssh_public_key: str,
        vm_config: VmHuntConfig,
    ) -> Instance:
        self.create_attempts += 1
        instance_index = self.create_attempts
        instance_id = f"vm-{instance_index}"
        self.create_in_flight += 1
        self.max_create_in_flight = max(self.max_create_in_flight, self.create_in_flight)
        try:
            if self.create_delay:
                await asyncio.sleep(self.create_delay)
            self.created.append(
                {
                    "instance_id": instance_id,
                    "folder_id": folder_id,
                    "name": name,
                    "zone_id": zone_id,
                    "subnet_id": subnet_id,
                    "image_id": image_id,
                    "ssh_username": ssh_username,
                    "ssh_public_key": ssh_public_key,
                    "vm_config": vm_config,
                }
            )
            if instance_index in self.create_errors:
                raise self.create_errors[instance_index]
            return Instance(id=instance_id, ip=None, zone_id=zone_id, name=name)
        finally:
            self.create_in_flight -= 1

    async def list_instances(self, folder_id: str) -> list[Instance]:
        self.list_instances_called = True
        if self.listed_instances is not None:
            return self.listed_instances
        raise AssertionError("VM batch must not reuse existing instances")

    async def refresh_instance_dynamic_ip(
        self,
        instance_id: str,
        *,
        poll_seconds: int,
        timeout_seconds: int,
    ) -> Instance | None:
        self.refresh_called = True
        raise AssertionError("VM batch must not start stopped instances")

    async def wait_for_external_ip(
        self,
        instance_id: str,
        *,
        poll_seconds: int,
        timeout_seconds: int,
    ) -> Instance | None:
        return Instance(id=instance_id, ip=self.ips.get(instance_id), zone_id="ru-central1-a")

    async def delete_instance(self, instance_id: str) -> None:
        self.delete_in_flight += 1
        self.max_delete_in_flight = max(self.max_delete_in_flight, self.delete_in_flight)
        await asyncio.sleep(0.01)
        self.deleted.append(instance_id)
        self.delete_in_flight -= 1

    async def stop_instance(self, instance_id: str) -> None:
        self.stopped.append(instance_id)


class FakeVmBatchComputeApiWithExisting(FakeVmBatchComputeApi):
    async def list_instances(self, folder_id: str) -> list[Instance]:
        self.list_instances_called = True
        return [Instance(id="existing-vm", ip=None, zone_id="ru-central1-a", name="hunter-vm-existing")]


class FakeVmBatchVpcApi:
    def __init__(self, addresses: list[Address] | None = None):
        self.addresses = addresses or []

    async def ensure_subnets(self, *, folder_id: str, zones: list[str], cidr_blocks: list[str]):
        return [
            SimpleNamespace(id="subnet-a", zone_id="ru-central1-a"),
            SimpleNamespace(id="subnet-d", zone_id="ru-central1-d"),
        ]

    async def list_addresses(self, folder_id: str) -> list[Address]:
        return self.addresses


class HunterVmBatchTests(unittest.IsolatedAsyncioTestCase):
    def _build_hunter(self) -> HunterEngine:
        state = SimpleNamespace()
        state.add_checked_ip = AsyncMock()
        state.set_cloud_lifecycle = AsyncMock()
        state.increment_cloud_attempt = AsyncMock()
        state.cloud_match_count = AsyncMock(return_value=0)
        return HunterEngine(
            settings=SimpleNamespace(
                hunt_vm_batch_size=8,
                hunt_vm_delete_delay_seconds=0.0,
                hunt_vm_zones=["ru-central1-a", "ru-central1-d"],
                hunt_vm_image_folder_id="standard-images",
                hunt_vm_image_family="debian-12",
                hunt_vm_username="user",
                hunt_vm_poll_seconds=5,
                hunt_vm_poll_timeout_seconds=30,
                hunt_vm_subnet_cidr_blocks=["10.10.0.0/24", "10.20.0.0/24"],
            ),
            db=SimpleNamespace(),
            state=state,
            semaphore=asyncio.Semaphore(8),
            logger=logging.getLogger("test"),
        )

    @staticmethod
    def _cloud() -> ManagedCloud:
        return ManagedCloud(
            account_id="acc-1",
            organization_id="org-1",
            cloud_id="cloud-1",
            cloud_name="Cloud",
            folder_id="folder-1",
            billing_account_id="billing-1",
        )

    async def test_vm_batch_ignores_existing_stopped_vms_and_creates_fresh_batch(self) -> None:
        hunter = self._build_hunter()
        hunter._store_address_created = AsyncMock()
        hunter._store_address_deleted = AsyncMock()
        hunter._store_address_failed = AsyncMock()
        hunter._accept_match = AsyncMock(return_value=True)
        hunter._matched_prefix = AsyncMock(return_value=None)
        hunter._target_per_cloud = AsyncMock(return_value=1)
        compute_api = FakeVmBatchComputeApiWithExisting({f"vm-{index}": f"8.8.8.{index}" for index in range(1, 9)})

        matched = await hunter._hunt_cloud_vm_batch(
            "job-1",
            ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
            self._cloud(),
            compute_api,
            FakeVmBatchVpcApi(),
            asyncio.Event(),
            VmHuntConfig.default(),
        )

        self.assertFalse(matched)
        self.assertFalse(compute_api.list_instances_called)
        self.assertFalse(compute_api.refresh_called)
        self.assertEqual(8, len(compute_api.created))
        self.assertNotIn("existing-vm", compute_api.deleted)
        self.assertEqual(
            ["vm-1", "vm-2", "vm-3", "vm-4", "vm-5", "vm-6", "vm-7", "vm-8"],
            compute_api.deleted,
        )
        self.assertEqual([], compute_api.stopped)

    async def test_vm_batch_deletes_misses_and_keeps_matched_vm(self) -> None:
        hunter = self._build_hunter()
        hunter._store_address_created = AsyncMock()
        hunter._store_address_deleted = AsyncMock()
        hunter._store_address_failed = AsyncMock()
        hunter._accept_match = AsyncMock(return_value=True)
        hunter._matched_prefix = AsyncMock(
            side_effect=lambda job_id, ip: "84.201" if ip.startswith("84.201.") else None
        )
        hunter._target_per_cloud = AsyncMock(return_value=1)
        compute_api = FakeVmBatchComputeApi(
            {
                **{f"vm-{index}": f"8.8.8.{index}" for index in range(1, 9)},
                "vm-5": "84.201.10.20",
            }
        )

        matched = await hunter._hunt_cloud_vm_batch(
            "job-1",
            ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
            self._cloud(),
            compute_api,
            FakeVmBatchVpcApi(),
            asyncio.Event(),
            VmHuntConfig.default(),
        )

        self.assertTrue(matched)
        self.assertEqual(8, len(compute_api.created))
        self.assertEqual(
            ["vm-1", "vm-2", "vm-3", "vm-4", "vm-6", "vm-7", "vm-8"],
            compute_api.deleted,
        )
        self.assertEqual([], compute_api.stopped)
        self.assertEqual(1, compute_api.max_delete_in_flight)
        self.assertEqual(7, hunter._store_address_deleted.await_count)
        hunter._store_address_failed.assert_not_awaited()
        hunter._accept_match.assert_awaited_once()
        kwargs = hunter._accept_match.await_args.kwargs
        self.assertEqual("vm:vm-5", kwargs["address_id"])
        self.assertEqual("84.201.10.20", kwargs["ip"])
        self.assertEqual("84.201", kwargs["prefix"])
        self.assertEqual("vm-5", kwargs["resource_id"])
        self.assertEqual("vm", kwargs["resource_type"])
        self.assertEqual("user", kwargs["ssh_username"])
        self.assertTrue(kwargs["ssh_public_key"].startswith("ssh-ed25519 "))
        self.assertIn("PRIVATE KEY", kwargs["ssh_private_key"])
        self.assertEqual("ru-central1-a", kwargs["zone_id"])

    async def test_vm_batch_deletes_all_vms_when_all_ips_miss(self) -> None:
        hunter = self._build_hunter()
        hunter._store_address_created = AsyncMock()
        hunter._store_address_deleted = AsyncMock()
        hunter._store_address_failed = AsyncMock()
        hunter._accept_match = AsyncMock(return_value=True)
        hunter._matched_prefix = AsyncMock(return_value=None)
        hunter._target_per_cloud = AsyncMock(return_value=1)
        compute_api = FakeVmBatchComputeApi({f"vm-{index}": f"8.8.8.{index}" for index in range(1, 9)})

        matched = await hunter._hunt_cloud_vm_batch(
            "job-1",
            ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
            self._cloud(),
            compute_api,
            FakeVmBatchVpcApi(),
            asyncio.Event(),
            VmHuntConfig.default(),
        )

        self.assertFalse(matched)
        self.assertEqual(8, len(compute_api.created))
        self.assertEqual(
            ["vm-1", "vm-2", "vm-3", "vm-4", "vm-5", "vm-6", "vm-7", "vm-8"],
            compute_api.deleted,
        )
        self.assertEqual([], compute_api.stopped)
        self.assertEqual(1, compute_api.max_delete_in_flight)
        self.assertEqual(8, hunter._store_address_deleted.await_count)
        hunter._store_address_failed.assert_not_awaited()
        hunter._accept_match.assert_not_awaited()

    async def test_vm_batch_creates_vms_concurrently(self) -> None:
        hunter = self._build_hunter()
        hunter._store_address_created = AsyncMock()
        hunter._store_address_deleted = AsyncMock()
        hunter._store_address_failed = AsyncMock()
        hunter._accept_match = AsyncMock(return_value=True)
        hunter._matched_prefix = AsyncMock(return_value=None)
        hunter._target_per_cloud = AsyncMock(return_value=1)
        compute_api = FakeVmBatchComputeApi(
            {f"vm-{index}": f"8.8.8.{index}" for index in range(1, 9)},
            create_delay=0.01,
        )

        await hunter._hunt_cloud_vm_batch(
            "job-1",
            ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
            self._cloud(),
            compute_api,
            FakeVmBatchVpcApi(),
            asyncio.Event(),
            VmHuntConfig.default(),
        )

        self.assertGreater(compute_api.max_create_in_flight, 1)

    async def test_quota_error_preserves_cloud_when_existing_vm_has_public_ip(self) -> None:
        quota_error = YcApiError(
            status=400,
            message="request failed",
            payload='{"code":8,"message":"Quota exceeded"}',
        )
        hunter = self._build_hunter()
        hunter.state.set_cloud_lifecycle = AsyncMock()
        hunter.state.increment_cloud_attempt = AsyncMock()
        hunter.state.cloud_match_count = AsyncMock(return_value=0)
        hunter._set_cloud_progress = AsyncMock()
        hunter._store_address_created = AsyncMock()
        hunter._store_address_deleted = AsyncMock()
        hunter._store_address_failed = AsyncMock()
        hunter._accept_match = AsyncMock(return_value=True)
        hunter._matched_prefix = AsyncMock(return_value=None)
        hunter._target_per_cloud = AsyncMock(return_value=1)
        compute_api = FakeVmBatchComputeApi(
            {},
            create_errors={1: quota_error},
            listed_instances=[Instance(id="existing-vm", ip="158.160.10.20")],
        )

        matched = await hunter._hunt_cloud_once(
            "job-1",
            ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
            self._cloud(),
            FakeCloudsApi(),
            compute_api,
            FakeVmBatchVpcApi(),
            asyncio.Event(),
            VmHuntConfig.default(),
        )

        self.assertTrue(matched)
        self.assertEqual([], compute_api.deleted)
        self.assertTrue(compute_api.list_instances_called)
        hunter.state.set_cloud_lifecycle.assert_any_await("job-1", "cloud-1", CloudLifecycle.SUCCESS)

    async def test_quota_error_marks_cloud_failed_when_no_public_ip_exists(self) -> None:
        quota_error = YcApiError(
            status=400,
            message="request failed",
            payload='{"code":8,"message":"Quota exceeded"}',
        )
        hunter = self._build_hunter()
        hunter.state.set_cloud_lifecycle = AsyncMock()
        hunter.state.increment_cloud_attempt = AsyncMock()
        hunter.state.cloud_match_count = AsyncMock(return_value=0)
        hunter._set_cloud_progress = AsyncMock()
        hunter._store_address_created = AsyncMock()
        hunter._store_address_deleted = AsyncMock()
        hunter._store_address_failed = AsyncMock()
        hunter._accept_match = AsyncMock(return_value=True)
        hunter._matched_prefix = AsyncMock(return_value=None)
        hunter._target_per_cloud = AsyncMock(return_value=1)
        compute_api = FakeVmBatchComputeApi(
            {},
            create_errors={1: quota_error},
            listed_instances=[],
        )

        matched = await hunter._hunt_cloud_once(
            "job-1",
            ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
            self._cloud(),
            FakeCloudsApi(),
            compute_api,
            FakeVmBatchVpcApi(),
            asyncio.Event(),
            VmHuntConfig.default(),
        )

        self.assertFalse(matched)
        self.assertTrue(compute_api.list_instances_called)
        lifecycle_calls = hunter.state.set_cloud_lifecycle.await_args_list
        self.assertEqual(("job-1", "cloud-1", CloudLifecycle.FAILED), lifecycle_calls[-1].args[:3])

    async def test_vm_create_permission_denied_preserves_cloud(self) -> None:
        permission_error = YcApiError(
            status=403,
            message="request failed",
            payload='{"code":7,"message":"Permission denied to resource-manager.folder folder-1"}',
        )
        hunter = self._build_hunter()
        hunter.state.set_cloud_lifecycle = AsyncMock()
        hunter.state.increment_cloud_attempt = AsyncMock()
        hunter.state.cloud_match_count = AsyncMock(return_value=0)
        hunter._set_cloud_progress = AsyncMock()
        hunter._store_address_created = AsyncMock()
        hunter._store_address_deleted = AsyncMock()
        hunter._store_address_failed = AsyncMock()
        hunter._accept_match = AsyncMock(return_value=True)
        hunter._matched_prefix = AsyncMock(return_value=None)
        hunter._target_per_cloud = AsyncMock(return_value=1)
        compute_api = FakeVmBatchComputeApi(
            {},
            create_errors={index: permission_error for index in range(1, 9)},
            listed_instances=[],
        )

        matched = await hunter._hunt_cloud_once(
            "job-1",
            ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
            self._cloud(),
            FakeCloudsApi(),
            compute_api,
            FakeVmBatchVpcApi(),
            asyncio.Event(),
            VmHuntConfig.default(),
        )

        self.assertTrue(matched)
        self.assertEqual([], compute_api.deleted)
        hunter.state.set_cloud_lifecycle.assert_any_await("job-1", "cloud-1", CloudLifecycle.SUCCESS)

    async def test_vm_create_permission_denied_logs_once(self) -> None:
        permission_error = YcApiError(
            status=403,
            message="request failed",
            payload='{"code":7,"message":"Permission denied to resource-manager.folder folder-1"}',
        )
        hunter = self._build_hunter()
        hunter._set_cloud_progress = AsyncMock()
        hunter._store_address_created = AsyncMock()
        hunter._store_address_deleted = AsyncMock()
        hunter._store_address_failed = AsyncMock()
        hunter._accept_match = AsyncMock(return_value=True)
        hunter._matched_prefix = AsyncMock(return_value=None)
        hunter._target_per_cloud = AsyncMock(return_value=1)
        compute_api = FakeVmBatchComputeApi(
            {},
            create_errors={index: permission_error for index in range(1, 9)},
            listed_instances=[],
        )

        with patch("ycbot.core.hunter.log_error") as log_error_mock:
            matched = await hunter._hunt_cloud_once(
                "job-1",
                ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
                self._cloud(),
                FakeCloudsApi(),
                compute_api,
                FakeVmBatchVpcApi(),
                asyncio.Event(),
                VmHuntConfig.default(),
            )

        self.assertTrue(matched)
        self.assertFalse(
            any(call.args and call.args[1] == "vm.create.error" for call in log_error_mock.call_args_list)
        )

    async def test_hunt_cloud_once_skips_deleting_cloud(self) -> None:
        hunter = self._build_hunter()
        hunter._set_cloud_progress = AsyncMock()
        hunter._store_address_created = AsyncMock()
        hunter._store_address_deleted = AsyncMock()
        hunter._store_address_failed = AsyncMock()
        hunter._accept_match = AsyncMock(return_value=True)
        hunter._matched_prefix = AsyncMock(return_value=None)
        hunter._target_per_cloud = AsyncMock(return_value=1)
        compute_api = FakeVmBatchComputeApi({}, create_errors={}, listed_instances=[])

        matched = await hunter._hunt_cloud_once(
            "job-1",
            ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
            self._cloud(),
            FakeCloudsApi(deleting=True),
            compute_api,
            FakeVmBatchVpcApi(),
            asyncio.Event(),
            VmHuntConfig.default(),
        )

        self.assertFalse(matched)
        # Should not create any VMs
        self.assertEqual(0, len(compute_api.created))
        lifecycle_calls = hunter.state.set_cloud_lifecycle.await_args_list
        self.assertEqual(("job-1", "cloud-1", CloudLifecycle.FAILED), lifecycle_calls[-1].args[:3])


class HunterScopeConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_scope_targets_use_global_job_match_count(self) -> None:
        state = StateManager()
        await state.create_hunt("job-1", ["84.201"], 2)
        await state.register_cloud(
            "job-1",
            CloudState(
                account_id="acc-1",
                organization_id="org-1",
                cloud_id="cloud-1",
                cloud_name="Cloud 1",
                folder_id="folder-1",
                billing_account_id="billing-1",
            ),
        )
        await state.register_cloud(
            "job-1",
            CloudState(
                account_id="acc-1",
                organization_id="org-2",
                cloud_id="cloud-2",
                cloud_name="Cloud 2",
                folder_id="folder-2",
                billing_account_id="billing-1",
            ),
        )
        await state.add_match(
            "job-1",
            MatchState(
                account_id="acc-1",
                organization_id="org-1",
                cloud_id="cloud-1",
                address_id="vm:vm-1",
                ip="84.201.10.1",
                prefix="84.201",
                preexisting=False,
            ),
        )
        await state.add_match(
            "job-1",
            MatchState(
                account_id="acc-1",
                organization_id="org-1",
                cloud_id="cloud-1",
                address_id="vm:vm-2",
                ip="84.201.10.2",
                prefix="84.201",
                preexisting=False,
            ),
        )
        accepted_after_target = await state.add_match(
            "job-1",
            MatchState(
                account_id="acc-1",
                organization_id="org-2",
                cloud_id="cloud-2",
                address_id="vm:vm-3",
                ip="84.201.10.3",
                prefix="84.201",
                preexisting=False,
            ),
        )
        hunter = HunterEngine(
            settings=SimpleNamespace(hunt_cloud_target_count=5),
            db=SimpleNamespace(),
            state=state,
            semaphore=asyncio.Semaphore(16),
            logger=logging.getLogger("test"),
        )

        reached = await hunter._all_scope_targets_reached(
            "job-1",
            [
                ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
                ScopeDescriptor(account_id="acc-1", organization_id="org-2"),
            ],
        )

        self.assertFalse(accepted_after_target)
        self.assertTrue(reached)

    async def test_ensure_scope_clouds_creates_parallel_hunt_cloud_pool(self) -> None:
        state = SimpleNamespace()
        state.get_hunt = AsyncMock(return_value=SimpleNamespace(cloud_states={}))
        state.register_cloud = AsyncMock()
        hunter = HunterEngine(
            settings=SimpleNamespace(
                hunt_cloud_target_count=5,
                hunt_parallel_cloud_count=5,
            ),
            db=SimpleNamespace(),
            state=state,
            semaphore=asyncio.Semaphore(16),
            logger=logging.getLogger("test"),
        )
        hunter._upsert_cloud_row = AsyncMock()
        hunter._cloud_has_saved_match = AsyncMock(return_value=False)

        class FakeCloudsApi:
            def __init__(self):
                self.created = []

            async def list_billing_accounts(self):
                return [SimpleNamespace(id="billing-1", active=True)]

            async def resolve_cloud_billing_map(self, active_billing):
                return {}

            async def resolve_organization_billing_map(self, active_billing):
                return {"org-1": "billing-1"}

            async def list_clouds(self, organization_id):
                return []

            async def create_cloud_with_folder(self, *, organization_id, name, billing_account_id):
                cloud = SimpleNamespace(
                    id=f"cloud-{len(self.created) + 1}",
                    name=name,
                    organization_id=organization_id,
                    state=DbCloudState.ACTIVE,
                    deleting=False,
                )
                folder = SimpleNamespace(id=f"folder-{len(self.created) + 1}", name="Folder")
                self.created.append((cloud, folder, billing_account_id))
                return cloud, folder

        clouds_api = FakeCloudsApi()

        candidates, pending_slots = await hunter._ensure_scope_clouds(
            "job-1",
            ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
            clouds_api,
            SimpleNamespace(),
            SimpleNamespace(),
            asyncio.Event(),
        )

        self.assertEqual(5, len(candidates))
        self.assertEqual([f"cloud-{index}" for index in range(1, 6)], [item.cloud_id for item in candidates])
        self.assertEqual(5, len(clouds_api.created))
        self.assertEqual([], pending_slots)

    async def test_run_scope_hunts_ready_clouds_in_parallel(self) -> None:
        state = SimpleNamespace()
        state.set_cloud_lifecycle = AsyncMock()
        state.get_hunt = AsyncMock(
            return_value=SimpleNamespace(
                cloud_states={
                    f"cloud-{index}": SimpleNamespace(lifecycle=CloudLifecycle.SUCCESS)
                    for index in range(3)
                }
            )
        )
        hunter = HunterEngine(
            settings=SimpleNamespace(
                hunt_cycles_per_cloud=1,
                hunt_cloud_target_count=5,
                hunt_parallel_cloud_count=5,
                hunt_organization_rotation_enabled=False,
            ),
            db=SimpleNamespace(),
            state=state,
            semaphore=asyncio.Semaphore(16),
            logger=logging.getLogger("test"),
        )
        clouds = [
            ManagedCloud(
                account_id="acc-1",
                organization_id="org-1",
                cloud_id=f"cloud-{index}",
                cloud_name=f"Cloud {index}",
                folder_id=f"folder-{index}",
                billing_account_id="billing-1",
            )
            for index in range(3)
        ]
        in_flight = 0
        max_in_flight = 0
        completed = 0

        async def fake_hunt(*args, **kwargs):
            nonlocal in_flight, max_in_flight, completed
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            completed += 1
            return True

        async def scope_targets_reached(*args, **kwargs):
            return completed >= len(clouds)

        hunter._get_account = AsyncMock(return_value=SimpleNamespace(oauth_token="token", proxy_url=None))
        hunter._ensure_scope_clouds = AsyncMock(return_value=(clouds, []))
        hunter._collect_replacements = AsyncMock(return_value=[])
        hunter._hunt_cloud_once = AsyncMock(side_effect=fake_hunt)
        hunter._scope_targets_reached = AsyncMock(side_effect=scope_targets_reached)
        hunter._set_cloud_progress = AsyncMock()
        hunter._cloud_has_saved_match = AsyncMock(return_value=False)
        hunter._delete_cloud_for_replacement = AsyncMock(return_value=True)

        class FakeYcClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        class FakeApi:
            def __init__(self, **kwargs):
                pass

        with (
            patch("ycbot.core.hunter.YcClient", FakeYcClient),
            patch("ycbot.core.hunter.CloudsApi", FakeApi),
            patch("ycbot.core.hunter.ComputeApi", FakeApi),
            patch("ycbot.core.hunter.VpcApi", FakeApi),
        ):
            await hunter._run_scope(
                "job-1",
                ScopeDescriptor(account_id="acc-1", organization_id="org-1"),
                asyncio.Event(),
                VmHuntConfig.default(),
            )

        self.assertEqual(3, hunter._hunt_cloud_once.await_count)
        self.assertEqual(3, max_in_flight)
        hunter._delete_cloud_for_replacement.assert_not_awaited()

    async def test_organization_rotation_waits_deletions_then_creates_first_cloud(self) -> None:
        events: list[str] = []
        organization_created = False
        state = SimpleNamespace(register_cloud=AsyncMock())
        hunter = HunterEngine(
            settings=SimpleNamespace(
                hunt_cloud_target_count=5,
                hunt_parallel_cloud_count=5,
                hunt_cycles_per_cloud=1,
                hunt_organization_rotation_enabled=True,
                hunt_organization_rotation_cloud_miss_count=5,
                yc_center_wait_seconds=1,
                yc_center_org_name_prefix="ycbot-org",
            ),
            db=SimpleNamespace(),
            state=state,
            semaphore=asyncio.Semaphore(16),
            logger=logging.getLogger("test"),
        )
        hunter._upsert_cloud_row = AsyncMock()
        hunter._set_cloud_progress = AsyncMock()
        hunter._replace_account_organizations = AsyncMock()

        async def create_organization(name, *, current_organization_name):
            nonlocal organization_created
            events.append("selenium-create-org")
            organization_created = True
            self.assertEqual("Old Org", current_organization_name)
            return "New Org"

        hunter._create_organization_via_selenium = AsyncMock(side_effect=create_organization)

        async def delete_task():
            events.append("delete-start")
            await asyncio.sleep(0)
            events.append("deleted-cloud")
            return True

        async def pending_task():
            events.append("pending-start")
            await asyncio.sleep(0)
            events.append("deleted-pending")

        deleting = [
            DeletingCloud(
                cloud=ManagedCloud(
                    account_id="acc-1",
                    organization_id="org-old",
                    cloud_id="cloud-old",
                    cloud_name="Old Cloud",
                    folder_id="folder-old",
                    billing_account_id="billing-1",
                ),
                task=asyncio.create_task(delete_task()),
            )
        ]
        pending_slots = [
            PendingCloudSlot(
                cloud_id="cloud-pending",
                billing_account_id="billing-1",
                task=asyncio.create_task(pending_task()),
            )
        ]

        test_case = self

        class FakeCloudsApi:
            async def list_organizations(self):
                organizations = [Organization(id="org-old", name="Old Org", state="ACTIVE")]
                if organization_created:
                    organizations.append(Organization(id="org-new", name="New Org", state="ACTIVE"))
                return organizations

            async def create_cloud_with_folder(self, *, organization_id, name, billing_account_id):
                events.append(f"create-cloud:{organization_id}")
                create_order = events.index("create-cloud:org-new")
                test_case.assertLess(events.index("deleted-cloud"), create_order)
                test_case.assertLess(events.index("deleted-pending"), create_order)
                cloud_number = events.count("create-cloud:org-new")
                cloud = SimpleNamespace(
                    id=f"cloud-new-{cloud_number}",
                    name=name,
                    organization_id=organization_id,
                    state=DbCloudState.ACTIVE,
                    deleting=False,
                )
                folder = SimpleNamespace(id="folder-new", name="Folder")
                return cloud, folder

        new_scope, candidates, new_pending = await hunter._rotate_scope_organization_after_misses(
            job_id="job-1",
            scope=ScopeDescriptor(account_id="acc-1", organization_id="org-old"),
            deleting=deleting,
            pending_slots=pending_slots,
            clouds_api=FakeCloudsApi(),
            billing_account_id="billing-1",
            stop_event=asyncio.Event(),
        )

        self.assertEqual("org-new", new_scope.organization_id)
        self.assertEqual([f"cloud-new-{index}" for index in range(1, 6)], [item.cloud_id for item in candidates])
        self.assertEqual([], new_pending)
        self.assertEqual([], deleting)
        self.assertEqual([], pending_slots)
        self.assertLess(events.index("deleted-cloud"), events.index("selenium-create-org"))
        self.assertLess(events.index("deleted-pending"), events.index("selenium-create-org"))
        hunter._replace_account_organizations.assert_awaited_once()


class NotificationVmTests(unittest.TestCase):
    def test_match_text_includes_vm_login_and_private_key(self) -> None:
        text = _match_text(
            MatchNotification(
                chat_id=123,
                branch_id=None,
                job_id="job-1",
                account_id="acc-1",
                account_name="Main",
                organization_id="org-1",
                cloud_id="cloud-1",
                cloud_name="Cloud",
                folder_id="folder-1",
                address_id="vm:vm-1",
                ip="84.201.10.20",
                prefix="84.201",
                preexisting=False,
                resource_id="vm-1",
                resource_type="vm",
                ssh_username="user",
                ssh_public_key="ssh-ed25519 AAAA test",
                ssh_private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nkey\n-----END OPENSSH PRIVATE KEY-----",
                zone_id="ru-central1-a",
            ),
            custom_emoji=False,
        )

        self.assertIn("vm-1", text)
        self.assertIn("ru-central1-a", text)
        self.assertIn("user", text)
        self.assertIn("ssh-ed25519 AAAA test", text)
        self.assertIn("PRIVATE KEY", text)


class SchedulerComputePreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_existing_prefix_ips_reports_compute_instance_ips(self) -> None:
        scheduler = HuntScheduler(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=asyncio.Semaphore(1),
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
                return [Address(id="addr-1", ip="8.8.8.8")]

        class FakeComputeApi:
            def __init__(self, **kwargs):
                pass

            async def list_instances(self, folder_id):
                return [Instance(id="vm-1", ip="51.250.10.20", zone_id="ru-central1-d", name="VM")]

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
        self.assertEqual("51.250.10.20", result["existing_ips"][0]["ip"])
        self.assertEqual("51.250", result["existing_ips"][0]["prefix"])
        self.assertEqual("vm", result["existing_ips"][0]["resource_type"])
        self.assertEqual("vm-1", result["existing_ips"][0]["instance_id"])


class CleanupRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_tracked_resource_routes_vm_records_to_compute(self) -> None:
        scheduler = HuntScheduler(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=asyncio.Semaphore(1),
            logger=logging.getLogger("test"),
        )
        scheduler._address_is_matched = AsyncMock(return_value=False)
        scheduler._mark_deleted = AsyncMock()
        vpc_api = SimpleNamespace(delete_address=AsyncMock())
        compute_api = SimpleNamespace(stop_instance=AsyncMock())

        deleted = await scheduler._delete_tracked_resource("acc-1", "vm:vm-1", vpc_api, compute_api)

        self.assertTrue(deleted)
        compute_api.stop_instance.assert_awaited_once_with("vm-1")
        vpc_api.delete_address.assert_not_awaited()
        scheduler._mark_deleted.assert_awaited_once_with("acc-1", "vm:vm-1")

    async def test_delete_tracked_resource_routes_legacy_addresses_to_vpc(self) -> None:
        scheduler = HuntScheduler(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=asyncio.Semaphore(1),
            logger=logging.getLogger("test"),
        )
        scheduler._address_is_matched = AsyncMock(return_value=False)
        scheduler._mark_deleted = AsyncMock()
        vpc_api = SimpleNamespace(delete_address=AsyncMock())
        compute_api = SimpleNamespace(stop_instance=AsyncMock())

        deleted = await scheduler._delete_tracked_resource("acc-1", "addr-1", vpc_api, compute_api)

        self.assertTrue(deleted)
        vpc_api.delete_address.assert_awaited_once_with("addr-1")
        compute_api.stop_instance.assert_not_awaited()
        scheduler._mark_deleted.assert_awaited_once_with("acc-1", "addr-1")
