# Compute VM Hunt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace VPC address hunting with parallel Compute VM hunting: 8 VMs per cloud, SSH key per cloud, delete misses, keep matched VM and send key to Telegram.

**Architecture:** Add a focused Compute API client and extend VPC read-only subnet lookup. Keep existing hunt state and DB tables by storing VM resources as `vm:<instance_id>` in the existing `address_id` field, then update hunter cleanup, preflight, notifications, and docs around that compatibility layer.

**Tech Stack:** Python 3.11, asyncio, aiohttp-based YC REST client, aiogram notifications, unittest.

---

### Task 1: Compute API And VM Payload

**Files:**
- Create: `ycbot/yc/compute.py`
- Modify: `ycbot/yc/__init__.py`
- Modify: `ycbot/config.py`
- Test: `tests/test_compute_vm_hunt.py`

- [ ] **Step 1: Write failing tests for Compute API payload and IP extraction**

Add `tests/test_compute_vm_hunt.py` with:

```python
from __future__ import annotations

import unittest
from types import SimpleNamespace

from ycbot.yc.compute import ComputeApi


class FakeClient:
    def __init__(self):
        self.requests = []

    async def request_json(self, method, url, *, params=None, body=None, retries=None):
        self.requests.append((method, url, params, body, retries))
        return {"done": True, "response": {"id": "vm-1", "networkInterfaces": []}}

    async def poll_operation(self, operation_id, *, timeout_seconds=240, min_delay=1.0, max_delay=2.5):
        return {"done": True, "response": {"id": "vm-1"}}


class ComputeApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_instance_payload_matches_vm_hunt_config(self):
        client = FakeClient()
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
            logger=SimpleNamespace(),
        )

        await api.create_instance(
            folder_id="folder-1",
            name="hunter-vm-1",
            zone_id="ru-central1-a",
            subnet_id="subnet-a",
            image_id="image-1",
            ssh_username="user",
            ssh_public_key="ssh-ed25519 AAAA test",
        )

        method, url, params, body, retries = client.requests[0]
        assert method == "POST"
        assert url == "https://compute.example/instances"
        assert params is None
        assert retries is None
        assert body["folderId"] == "folder-1"
        assert body["zoneId"] == "ru-central1-a"
        assert body["platformId"] == "standard-v4a"
        assert body["resourcesSpec"] == {
            "cores": "2",
            "memory": str(1024**3),
            "coreFraction": "20",
        }
        assert body["bootDiskSpec"]["autoDelete"] is True
        assert body["bootDiskSpec"]["diskSpec"]["typeId"] == "network-hdd"
        assert body["bootDiskSpec"]["diskSpec"]["size"] == str(10 * 1024**3)
        assert body["bootDiskSpec"]["diskSpec"]["imageId"] == "image-1"
        assert body["networkInterfaceSpecs"] == [
            {
                "subnetId": "subnet-a",
                "primaryV4AddressSpec": {"oneToOneNatSpec": {"ipVersion": "IPV4"}},
            }
        ]
        assert body["schedulingPolicy"] == {"preemptible": True}
        assert body["metadata"] == {"ssh-keys": "user:ssh-ed25519 AAAA test"}

    def test_extract_external_ip_reads_one_to_one_nat(self):
        ip = ComputeApi.extract_external_ip(
            {
                "networkInterfaces": [
                    {
                        "primaryV4Address": {
                            "oneToOneNat": {"address": "84.201.10.20"}
                        }
                    }
                ]
            }
        )
        assert ip == "84.201.10.20"
```

- [ ] **Step 2: Run tests to verify RED**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.ComputeApiTests -v`

Expected: import failure because `ycbot.yc.compute` does not exist.

- [ ] **Step 3: Implement `ComputeApi` and settings**

Create `ComputeApi` with:

- dataclass `Instance(id: str, ip: str | None, zone_id: str | None = None)`;
- REST URLs from settings;
- create payload matching the test;
- `_resolve_operation` using `client.poll_operation`;
- `get_latest_image_by_family`;
- `list_instances`;
- `get_instance`;
- `delete_instance`;
- `wait_for_external_ip`;
- `extract_external_ip`.

Add settings fields listed in the spec to `ycbot/config.py`.

Export `ComputeApi` and `Instance` from `ycbot/yc/__init__.py`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.ComputeApiTests -v`

Expected: both tests pass.

### Task 2: VPC Subnet Lookup

**Files:**
- Modify: `ycbot/yc/vpc.py`
- Test: `tests/test_compute_vm_hunt.py`

- [ ] **Step 1: Write failing test for subnet listing**

Append:

```python
from ycbot.yc.vpc import VpcApi


class VpcSubnetTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_subnets_returns_id_and_zone(self):
        class Client:
            async def paginated(self, *, url, key, params=None):
                assert url == "https://vpc.example/subnets"
                assert key == "subnets"
                assert params == {"folderId": "folder-1"}
                return [
                    {"id": "subnet-a", "zoneId": "ru-central1-a"},
                    {"id": "subnet-d", "zoneId": "ru-central1-d"},
                ]

        api = VpcApi(
            client=Client(),
            settings=SimpleNamespace(yc_vpc_subnet_url="https://vpc.example/subnets"),
            logger=SimpleNamespace(),
        )

        subnets = await api.list_subnets("folder-1")

        assert [(item.id, item.zone_id) for item in subnets] == [
            ("subnet-a", "ru-central1-a"),
            ("subnet-d", "ru-central1-d"),
        ]
```

- [ ] **Step 2: Run test to verify RED**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.VpcSubnetTests -v`

Expected: fail because `list_subnets` or `Subnet` is missing.

- [ ] **Step 3: Implement `Subnet` and `list_subnets`**

Add dataclass `Subnet(id: str, zone_id: str)` and method `list_subnets(folder_id)`.

- [ ] **Step 4: Run test to verify GREEN**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.VpcSubnetTests -v`

Expected: pass.

### Task 3: SSH Key Generation

**Files:**
- Create: `ycbot/core/ssh_keys.py`
- Test: `tests/test_compute_vm_hunt.py`

- [ ] **Step 1: Write failing test for keypair format**

Append:

```python
from ycbot.core.ssh_keys import generate_ssh_keypair


class SshKeyTests(unittest.TestCase):
    def test_generate_ssh_keypair_returns_public_and_private_key(self):
        keypair = generate_ssh_keypair("user")

        assert keypair.username == "user"
        assert keypair.public_key.startswith("ssh-")
        assert "PRIVATE KEY" in keypair.private_key
        assert keypair.public_key.strip() in keypair.metadata_value
        assert keypair.metadata_value.startswith("user:ssh-")
```

- [ ] **Step 2: Run test to verify RED**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.SshKeyTests -v`

Expected: import failure because `ycbot.core.ssh_keys` does not exist.

- [ ] **Step 3: Implement key generation**

Use `ssh-keygen` via `asyncio` is not needed here; use `subprocess.run` with a temporary directory and `ssh-keygen -t ed25519 -N "" -C ychunter-<username> -f key`. Return dataclass `SshKeyPair(username, public_key, private_key, metadata_value)`.

- [ ] **Step 4: Run test to verify GREEN**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.SshKeyTests -v`

Expected: pass.

### Task 4: Hunter VM Batch

**Files:**
- Modify: `ycbot/core/hunter.py`
- Test: `tests/test_compute_vm_hunt.py`

- [ ] **Step 1: Write failing async tests for VM batch behavior**

Append tests that instantiate `HunterEngine` with fake state/db and call a new helper `_hunt_cloud_vm_batch(...)`:

- first test: fake Compute API creates 8 VM ids, returns 7 miss IPs and 1 matching `84.201.10.20`; assert 8 create calls, all miss VM ids deleted, matched VM id not deleted, `_accept_match` called with `address_id="vm:<matched-id>"`, and `ssh_private_key` passed to notification context.
- second test: all 8 VM IPs miss; assert all 8 deleted and helper returns `False`.

Keep DB out of this helper test by monkeypatching `_store_address_created`, `_store_address_deleted`, `_store_address_failed`, `_accept_match`, `_matched_prefix`, and `_target_per_cloud` with `AsyncMock`.

- [ ] **Step 2: Run tests to verify RED**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.HunterVmBatchTests -v`

Expected: fail because `_hunt_cloud_vm_batch` is missing.

- [ ] **Step 3: Implement VM batch helper and wire `_hunt_cloud_once`**

In `HunterEngine`:

- import `ComputeApi`, `Instance`, `Subnet`, `generate_ssh_keypair`;
- create resource id helpers `vm_record_id(instance_id)` and `strip_vm_record_id(resource_id)`;
- add `_hunt_cloud_vm_batch(job_id, scope, cloud, compute_api, vpc_api, stop_event)`;
- select allowed subnets from `settings.hunt_vm_zones`;
- get Ubuntu image once per batch;
- create `settings.hunt_vm_batch_size` tasks concurrently;
- store each created VM as `vm:<id>`;
- poll with `settings.hunt_vm_poll_seconds`;
- delete misses;
- keep matched VM and pass SSH fields to `_accept_match`;
- make `_hunt_cloud_once` call VM batch instead of VPC address cycles.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.HunterVmBatchTests -v`

Expected: pass.

### Task 5: Notification And Preflight

**Files:**
- Modify: `ycbot/core/hunter.py`
- Modify: `ycbot/bot/notifications.py`
- Modify: `ycbot/core/scheduler.py`
- Test: `tests/test_compute_vm_hunt.py`
- Test: `tests/test_prefix_picker_preflight.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

- `_match_text` includes VM id, zone, login `user`, and private key when notification has SSH fields;
- `scan_existing_prefix_ips` includes IPs from fake Compute instances in addition to VPC addresses.

- [ ] **Step 2: Run tests to verify RED**

Run:

`/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.NotificationVmTests tests.test_prefix_picker_preflight.SchedulerPrefixTests -v`

Expected: notification lacks VM/key fields and scheduler does not scan Compute instances.

- [ ] **Step 3: Implement notification fields and Compute preflight**

Extend `MatchNotification` with optional `resource_id`, `resource_type`, `ssh_username`, `ssh_private_key`, `zone_id`.

In `_accept_match`, accept optional SSH/resource args and include them in `MatchNotification`.

In notifications, render VM info and private key in `<pre>` when present.

In scheduler preflight, instantiate `ComputeApi`, list instances per folder, match external IPs against known prefixes, and include `resource_type="vm"`/`instance_id`.

- [ ] **Step 4: Run tests to verify GREEN**

Run same command.

Expected: pass.

### Task 6: Cleanup Routing And Docs

**Files:**
- Modify: `ycbot/core/scheduler.py`
- Modify: `README.md`
- Modify: `.env.example`
- Test: `tests/test_compute_vm_hunt.py`

- [ ] **Step 1: Write failing cleanup routing test**

Add a test for a helper that classifies `address_id`:

- `vm:abc` routes to Compute `delete_instance("abc")`;
- `addr-1` routes to VPC `delete_address("addr-1")`.

- [ ] **Step 2: Run test to verify RED**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.CleanupRoutingTests -v`

Expected: fail because helper/routing is missing.

- [ ] **Step 3: Implement cleanup routing and docs**

In scheduler cleanup, create both VPC and Compute clients and delete based on `vm:` prefix. Update README and `.env.example` with new Compute VM settings and mark old cycle settings as legacy.

- [ ] **Step 4: Run test to verify GREEN**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt.CleanupRoutingTests -v`

Expected: pass.

### Task 7: Full Verification

**Files:**
- All touched files

- [ ] **Step 1: Run focused tests**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_compute_vm_hunt tests.test_prefix_picker_preflight tests.test_branch_scope -v`

Expected: pass.

- [ ] **Step 2: Run full test suite**

Run: `/opt/homebrew/bin/python3.11 -m unittest`

Expected: pass.

- [ ] **Step 3: Review diff**

Run: `git diff -- ychunter`

Expected: changes are scoped to Compute VM hunt, docs, and tests.
