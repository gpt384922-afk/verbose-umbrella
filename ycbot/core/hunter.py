from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from ycbot.config import Settings
from ycbot.core.prefixes import match_known_prefix, match_prefix
from ycbot.core.ssh_keys import SshKeyPair, generate_ssh_keypair
from ycbot.core.vm_config import VmHuntConfig
from ycbot.core.state_manager import CloudLifecycle, CloudState, MatchState, StateManager, TaskLifecycle
from ycbot.db.enums import CloudState as DbCloudState
from ycbot.db.enums import HuntCloudStatus, HuntStatus
from ycbot.db.repositories import AccountRepository, AddressRepository, HuntRepository
from ycbot.db.session import Database
from ycbot.utils import log_error, log_event
from ycbot.yc import Cloud, CloudsApi, ComputeApi, VpcApi, YcClient


VM_RECORD_PREFIX = "vm:"


def vm_record_id(instance_id: str) -> str:
    return f"{VM_RECORD_PREFIX}{instance_id}"


def strip_vm_record_id(resource_id: str) -> str:
    if resource_id.startswith(VM_RECORD_PREFIX):
        return resource_id[len(VM_RECORD_PREFIX):]
    return resource_id


@dataclass(slots=True)
class ManagedCloud:
    account_id: str
    organization_id: str
    cloud_id: str
    cloud_name: str
    folder_id: str
    billing_account_id: str


@dataclass(slots=True)
class ScopeDescriptor:
    account_id: str
    organization_id: str


@dataclass(slots=True)
class DeletingCloud:
    cloud: ManagedCloud
    task: asyncio.Task[bool]


@dataclass(slots=True)
class PendingCloudSlot:
    cloud_id: str
    billing_account_id: str
    task: asyncio.Task[None]


@dataclass(slots=True)
class MatchNotification:
    chat_id: int
    branch_id: str | None
    job_id: str
    account_id: str
    account_name: str
    organization_id: str
    cloud_id: str
    cloud_name: str
    folder_id: str
    address_id: str
    ip: str
    prefix: str
    preexisting: bool
    resource_id: str | None = None
    resource_type: str | None = None
    ssh_username: str | None = None
    ssh_public_key: str | None = None
    ssh_private_key: str | None = None
    zone_id: str | None = None


MatchNotifier = Callable[[MatchNotification], Awaitable[None]]


class HunterEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        state: StateManager,
        semaphore: asyncio.Semaphore,
        logger,
    ) -> None:
        self.settings = settings
        self.db = db
        self.state = state
        self.semaphore = semaphore
        self.logger = logger
        self._match_notifier: MatchNotifier | None = None
        self._vm_keypairs: dict[tuple[str, str], SshKeyPair] = {}

    def set_match_notifier(self, notifier: MatchNotifier | None) -> None:
        self._match_notifier = notifier

    async def run_job(
        self,
        job_id: str,
        stop_event: asyncio.Event,
        vm_config: VmHuntConfig | None = None,
    ) -> None:
        vm_config = vm_config or VmHuntConfig.from_settings(self.settings)
        job, scopes = await self._load_job(job_id)
        if job is None:
            raise RuntimeError(f"hunt job not found: {job_id}")
        if not scopes:
            raise RuntimeError(f"hunt scopes not found: {job_id}")

        await self.state.create_hunt(job_id, list(job.target_prefixes), job.requested_ip_count)
        await self.state.set_task_status(job_id, TaskLifecycle.RUNNING)
        await self._set_job_status(job_id, HuntStatus.RUNNING)

        scope_descriptors = [
            ScopeDescriptor(scope.account_id, scope.organization_external_id)
            for scope in scopes
        ]
        scope_tasks = [
            asyncio.create_task(self._run_scope(job_id, scope, stop_event, vm_config))
            for scope in scope_descriptors
        ]

        errors: list[str] = []
        results = await asyncio.gather(*scope_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                errors.append(str(result))
                log_error(self.logger, "hunt.scope.error", result)

        if await self._all_scope_targets_reached(job_id, scope_descriptors):
            await self.state.set_task_status(job_id, TaskLifecycle.COMPLETED)
            await self._set_job_status(job_id, HuntStatus.COMPLETED)
            return

        if stop_event.is_set():
            await self.state.set_task_status(job_id, TaskLifecycle.CANCELLED)
            await self._set_job_status(job_id, HuntStatus.CANCELLED)
            return

        error_text = "; ".join(errors[:3]) if errors else "target ip count was not reached"
        await self.state.set_task_status(job_id, TaskLifecycle.FAILED, error=error_text)
        await self._set_job_status(job_id, HuntStatus.FAILED, error=error_text)

    async def _run_scope(
        self,
        job_id: str,
        scope: ScopeDescriptor,
        stop_event: asyncio.Event,
        vm_config: VmHuntConfig,
    ) -> None:
        account = await self._get_account(scope.account_id)
        if account is None:
            raise RuntimeError(f"account not found: {scope.account_id}")

        async with YcClient(
            settings=self.settings,
            oauth_token=account.oauth_token,
            proxy_url=account.proxy_url,
            semaphore=self.semaphore,
            logger=self.logger,
        ) as client:
            clouds_api = CloudsApi(client=client, settings=self.settings, logger=self.logger)
            compute_api = ComputeApi(client=client, settings=self.settings, logger=self.logger)
            vpc_api = VpcApi(client=client, settings=self.settings, logger=self.logger)

            candidates, pending_slots = await self._ensure_scope_clouds(
                job_id,
                scope,
                clouds_api,
                compute_api,
                vpc_api,
                stop_event,
            )
            deleting: list[DeletingCloud] = []

            try:
                while not stop_event.is_set() and not await self._scope_targets_reached(job_id, scope):
                    ready = await self._collect_replacements(
                        job_id=job_id,
                        scope=scope,
                        deleting=deleting,
                        pending_slots=pending_slots,
                        clouds_api=clouds_api,
                        stop_event=stop_event,
                        block=False,
                    )
                    if ready:
                        candidates.extend(ready)

                    if not candidates:
                        candidates = await self._collect_replacements(
                            job_id=job_id,
                            scope=scope,
                            deleting=deleting,
                            pending_slots=pending_slots,
                            clouds_api=clouds_api,
                            stop_event=stop_event,
                            block=True,
                        )
                        if not candidates and not deleting and not pending_slots:
                            await asyncio.sleep(2)
                            candidates, new_slots = await self._ensure_scope_clouds(
                                job_id,
                                scope,
                                clouds_api,
                                compute_api,
                                vpc_api,
                                stop_event,
                            )
                            pending_slots.extend(new_slots)
                            if (
                                not candidates
                                and not new_slots
                                and await self._scope_has_match(job_id, scope)
                            ):
                                log_event(
                                    self.logger,
                                    "hunt.scope.exhausted_preserving_matches",
                                    job_id=job_id,
                                    account_id=scope.account_id,
                                    org_id=scope.organization_id,
                                )
                                return
                        continue

                    batch = candidates
                    candidates = []
                    hunt_tasks = [
                        (
                            cloud,
                            asyncio.create_task(
                                self._hunt_cloud_once(
                                    job_id,
                                    scope,
                                    cloud,
                                    compute_api,
                                    vpc_api,
                                    stop_event,
                                    vm_config,
                                )
                            ),
                        )
                        for cloud in batch
                    ]
                    hunt_results = await asyncio.gather(
                        *(task for _, task in hunt_tasks),
                        return_exceptions=True,
                    )
                    for (cloud, _), result in zip(hunt_tasks, hunt_results):
                        if isinstance(result, asyncio.CancelledError):
                            raise result
                        if isinstance(result, Exception):
                            log_error(self.logger, "hunt.cloud.error", result, cloud_id=cloud.cloud_id)
                            matched = False
                        else:
                            matched = bool(result)

                        if matched or stop_event.is_set() or await self._scope_targets_reached(job_id, scope):
                            continue
                        hunt_state = await self.state.get_hunt(job_id)
                        cloud_state = hunt_state.cloud_states.get(cloud.cloud_id) if hunt_state else None
                        if cloud_state and cloud_state.lifecycle != CloudLifecycle.FAILED:
                            candidates.append(cloud)
                            continue
                        if await self._cloud_has_saved_match(scope.account_id, cloud.cloud_id):
                            await self._skip_protected_cloud_delete(scope.account_id, cloud.cloud_id, reason="post_hunt")
                            continue

                        await self.state.set_cloud_lifecycle(job_id, cloud.cloud_id, CloudLifecycle.DELETING)
                        await self._set_cloud_progress(
                            job_id,
                            scope,
                            cloud.cloud_id,
                            HuntCloudStatus.FAILED,
                            self.settings.hunt_cycles_per_cloud,
                            notes="cloud failed; deleting",
                        )
                        deleting.append(
                            DeletingCloud(
                                cloud=cloud,
                                task=asyncio.create_task(self._delete_cloud_for_replacement(scope, cloud, clouds_api)),
                            )
                        )
            finally:
                if deleting:
                    await asyncio.gather(*(item.task for item in deleting), return_exceptions=True)
                for item in pending_slots:
                    if not item.task.done():
                        item.task.cancel()
                if pending_slots:
                    await asyncio.gather(*(item.task for item in pending_slots), return_exceptions=True)

    async def _ensure_scope_clouds(
        self,
        job_id: str,
        scope: ScopeDescriptor,
        clouds_api: CloudsApi,
        compute_api: ComputeApi,
        vpc_api: VpcApi,
        stop_event: asyncio.Event,
    ) -> tuple[list[ManagedCloud], list[PendingCloudSlot]]:
        billing_accounts = await clouds_api.list_billing_accounts()
        active_billing = [item for item in billing_accounts if item.active]
        if not active_billing:
            raise RuntimeError(f"no active billing account for account={scope.account_id}")

        billing_map = await clouds_api.resolve_cloud_billing_map(active_billing)
        org_billing_map = await clouds_api.resolve_organization_billing_map(active_billing)
        clouds = await clouds_api.list_clouds(scope.organization_id)
        primary_billing = self._select_billing_for_organization(
            organization_id=scope.organization_id,
            active_billing=active_billing,
            clouds=clouds,
            billing_map=billing_map,
            org_billing_map=org_billing_map,
        )
        if primary_billing is None:
            raise RuntimeError(f"no active billing account linked to organization={scope.organization_id}")

        keep_clouds: list[ManagedCloud] = []
        blocked_slots = 0
        protected_slots = 0
        blocked_cloud_ids: set[str] = set()
        preexisting_cloud_ids: set[str] = set()

        # Cleanup and normalize cloud states before hunt start.
        for cloud in clouds:
            billing_id = billing_map.get(cloud.id)
            if cloud.deleting or cloud.state == DbCloudState.DELETING:
                blocked_slots += 1
                blocked_cloud_ids.add(cloud.id)
                if await self._cloud_has_saved_match(scope.account_id, cloud.id):
                    log_event(
                        self.logger,
                        "cloud.protected.already_deleting",
                        account_id=scope.account_id,
                        cloud_id=cloud.id,
                    )
                else:
                    await self._remove_cloud_row(scope.account_id, cloud.id)
                log_event(self.logger, "cloud.skip.deleting", cloud_id=cloud.id)
                continue
            if cloud.state == DbCloudState.BLOCKED:
                if await self._cloud_has_saved_match(scope.account_id, cloud.id):
                    protected_slots += 1
                    await self._skip_protected_cloud_delete(scope.account_id, cloud.id, reason="blocked")
                    continue
                await clouds_api.delete_cloud(cloud.id, force_now=True)
                blocked_slots += 1
                blocked_cloud_ids.add(cloud.id)
                await self._remove_cloud_row(scope.account_id, cloud.id)
                log_event(self.logger, "cloud.deleted.blocked", cloud_id=cloud.id)
                continue
            if not billing_id:
                if await self._cloud_has_saved_match(scope.account_id, cloud.id):
                    protected_slots += 1
                    await self._skip_protected_cloud_delete(scope.account_id, cloud.id, reason="no_billing")
                    continue
                await clouds_api.delete_cloud(cloud.id, force_now=True)
                blocked_slots += 1
                blocked_cloud_ids.add(cloud.id)
                await self._remove_cloud_row(scope.account_id, cloud.id)
                log_event(self.logger, "cloud.deleted.no_billing", cloud_id=cloud.id)
                continue
            if billing_id != primary_billing.id:
                await clouds_api.bind_cloud_to_billing(cloud.id, primary_billing.id)
                billing_id = primary_billing.id

            try:
                folder = await clouds_api.ensure_folder(cloud.id)
            except Exception as exc:  # noqa: BLE001
                log_error(self.logger, "folder.ensure.error", exc, cloud_id=cloud.id)
                if await self._cloud_has_saved_match(scope.account_id, cloud.id):
                    protected_slots += 1
                    await self._skip_protected_cloud_delete(
                        scope.account_id,
                        cloud.id,
                        reason="folder_unavailable",
                    )
                    continue
                await clouds_api.delete_cloud(cloud.id, force_now=True)
                blocked_slots += 1
                blocked_cloud_ids.add(cloud.id)
                await self._remove_cloud_row(scope.account_id, cloud.id)
                log_event(self.logger, "cloud.deleted.folder_unavailable", cloud_id=cloud.id)
                continue
            managed = ManagedCloud(
                account_id=scope.account_id,
                organization_id=scope.organization_id,
                cloud_id=cloud.id,
                cloud_name=cloud.name,
                folder_id=folder.id,
                billing_account_id=billing_id,
            )
            await self._upsert_cloud_row(managed)
            keep_clouds.append(managed)

            if await self.state.cloud_match_count(job_id, managed.cloud_id):
                preexisting_cloud_ids.add(managed.cloud_id)
                continue

            await self.state.register_cloud(
                job_id,
                CloudState(
                    account_id=scope.account_id,
                    organization_id=scope.organization_id,
                    cloud_id=managed.cloud_id,
                    cloud_name=managed.cloud_name,
                    folder_id=managed.folder_id,
                    billing_account_id=managed.billing_account_id,
                    lifecycle=CloudLifecycle.INIT,
                ),
            )

            # Existing matching IP should not be touched.
            existing_match = await self._scan_existing_matches(job_id, managed, vpc_api, compute_api)
            if existing_match:
                preexisting_cloud_ids.add(managed.cloud_id)

            if stop_event.is_set():
                return keep_clouds, []

        # YC counts deleting clouds against the org cloud quota. Create only free slots.
        target = self.settings.hunt_cloud_target_count
        total_slots = len(keep_clouds) + blocked_slots + protected_slots
        if len(keep_clouds) < target and total_slots < target:
            missing = target - total_slots
            await self._create_scope_clouds(
                job_id=job_id,
                scope=scope,
                clouds_api=clouds_api,
                billing_account_id=primary_billing.id,
                keep_clouds=keep_clouds,
                missing=missing,
            )

        if len(keep_clouds) < target and blocked_cloud_ids:
            log_event(
                self.logger,
                "cloud.capacity.wait_deleting",
                account_id=scope.account_id,
                org_id=scope.organization_id,
                active_clouds=len(keep_clouds),
                deleting_slots=blocked_slots,
                target=target,
            )
            pending_slots = self._watch_cloud_slots(
                clouds_api=clouds_api,
                cloud_ids=blocked_cloud_ids,
                billing_account_id=primary_billing.id,
            )
        else:
            pending_slots = []

        # Clouds with preexisting target prefixes stay untouched and are excluded from worker loop.
        candidates = [item for item in keep_clouds if item.cloud_id not in preexisting_cloud_ids]
        return candidates, pending_slots

    async def _create_scope_clouds(
        self,
        *,
        job_id: str,
        scope: ScopeDescriptor,
        clouds_api: CloudsApi,
        billing_account_id: str,
        keep_clouds: list[ManagedCloud],
        missing: int,
    ) -> None:
        if missing <= 0:
            return

        used_names = {item.cloud_name for item in keep_clouds}
        for index in range(missing):
            name = self._next_cloud_name(scope.organization_id, used_names, index)
            try:
                created, folder = await clouds_api.create_cloud_with_folder(
                    organization_id=scope.organization_id,
                    name=name,
                    billing_account_id=billing_account_id,
                )
            except Exception as exc:  # noqa: BLE001
                log_error(self.logger, "cloud.prepare.error", exc, org_id=scope.organization_id)
                break

            managed = ManagedCloud(
                account_id=scope.account_id,
                organization_id=scope.organization_id,
                cloud_id=created.id,
                cloud_name=created.name,
                folder_id=folder.id,
                billing_account_id=billing_account_id,
            )
            keep_clouds.append(managed)
            used_names.add(created.name)
            await self._upsert_cloud_row(managed)
            await self.state.register_cloud(
                job_id,
                CloudState(
                    account_id=scope.account_id,
                    organization_id=scope.organization_id,
                    cloud_id=managed.cloud_id,
                    cloud_name=managed.cloud_name,
                    folder_id=managed.folder_id,
                    billing_account_id=managed.billing_account_id,
                    lifecycle=CloudLifecycle.INIT,
                ),
            )

    def _watch_cloud_slots(
        self,
        *,
        clouds_api: CloudsApi,
        cloud_ids: set[str],
        billing_account_id: str,
    ) -> list[PendingCloudSlot]:
        return [
            PendingCloudSlot(
                cloud_id=cloud_id,
                billing_account_id=billing_account_id,
                task=asyncio.create_task(
                    clouds_api.wait_cloud_deleted(
                        cloud_id,
                        timeout_seconds=self.settings.yc_cloud_slot_wait_timeout_seconds,
                        poll_interval_seconds=self.settings.yc_cloud_slot_wait_poll_seconds,
                    )
                ),
            )
            for cloud_id in sorted(cloud_ids)
        ]

    async def _hunt_cloud_once(
        self,
        job_id: str,
        scope: ScopeDescriptor,
        cloud: ManagedCloud,
        compute_api: ComputeApi,
        vpc_api: VpcApi,
        stop_event: asyncio.Event,
        vm_config: VmHuntConfig,
    ) -> bool:
        await self.state.set_cloud_lifecycle(job_id, cloud.cloud_id, CloudLifecycle.HUNTING)
        await self._set_cloud_progress(job_id, scope, cloud.cloud_id, HuntCloudStatus.RUNNING, 0, notes="hunting vm batch")
        await self.state.increment_cloud_attempt(job_id, cloud.cloud_id)
        try:
            matched = await self._hunt_cloud_vm_batch(
                job_id,
                scope,
                cloud,
                compute_api,
                vpc_api,
                stop_event,
                vm_config,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log_error(self.logger, "vm.batch.error", exc, cloud_id=cloud.cloud_id)
            await self.state.set_cloud_lifecycle(job_id, cloud.cloud_id, CloudLifecycle.FAILED, error=str(exc))
            await self._set_cloud_progress(
                job_id,
                scope,
                cloud.cloud_id,
                HuntCloudStatus.FAILED,
                1,
                notes="vm batch failed",
                error=str(exc),
            )
            return False
        if stop_event.is_set():
            return matched

        cloud_matches = await self.state.cloud_match_count(job_id, cloud.cloud_id)
        if cloud_matches:
            await self.state.set_cloud_lifecycle(job_id, cloud.cloud_id, CloudLifecycle.SUCCESS)
            await self._set_cloud_progress(
                job_id,
                scope,
                cloud.cloud_id,
                HuntCloudStatus.COMPLETED,
                1,
                notes=f"matched {cloud_matches}/{await self._target_per_cloud(job_id)}; preserved",
                completed=True,
            )
            log_event(
                self.logger,
                "cloud.preserved.partial_match",
                job_id=job_id,
                account_id=scope.account_id,
                cloud_id=cloud.cloud_id,
                matches=cloud_matches,
            )
            return True

        await self.state.set_cloud_lifecycle(job_id, cloud.cloud_id, CloudLifecycle.IDLE)
        await self._set_cloud_progress(
            job_id,
            scope,
            cloud.cloud_id,
            HuntCloudStatus.RUNNING,
            1,
            notes="vm batch missed; stopped for next ip cycle",
        )
        return False

    async def _hunt_cloud_vm_batch(
        self,
        job_id: str,
        scope: ScopeDescriptor,
        cloud: ManagedCloud,
        compute_api: ComputeApi,
        vpc_api: VpcApi,
        stop_event: asyncio.Event,
        vm_config: VmHuntConfig,
    ) -> bool:
        if stop_event.is_set():
            return False

        allowed_zones = self.settings.hunt_vm_zones or ["ru-central1-a", "ru-central1-d"]
        subnets = await vpc_api.ensure_subnets(
            folder_id=cloud.folder_id,
            zones=allowed_zones,
            cidr_blocks=self.settings.hunt_vm_subnet_cidr_blocks,
        )
        if not subnets:
            raise RuntimeError(f"no subnet in allowed VM zones: folder={cloud.folder_id}")

        keypair_key = (job_id, cloud.cloud_id)
        keypair = self._vm_keypairs.get(keypair_key)
        if keypair is None:
            keypair = generate_ssh_keypair(self.settings.hunt_vm_username)
            self._vm_keypairs[keypair_key] = keypair
        image_id = await compute_api.get_latest_image_by_family(
            self.settings.hunt_vm_image_folder_id,
            self.settings.hunt_vm_image_family,
        )
        batch_size = max(1, self.settings.hunt_vm_batch_size)

        existing_instances = [
            item
            for item in await compute_api.list_instances(cloud.folder_id)
            if (item.name or "").startswith("hunter-vm-")
        ][:batch_size]

        create_tasks = []
        for index in range(len(existing_instances), batch_size):
            subnet = subnets[index % len(subnets)]
            name = self._next_vm_name(cloud.cloud_id, index)
            create_tasks.append(
                asyncio.create_task(
                    compute_api.create_instance(
                        folder_id=cloud.folder_id,
                        name=name,
                        zone_id=subnet.zone_id,
                        subnet_id=subnet.id,
                        image_id=image_id,
                        ssh_username=keypair.username,
                        ssh_public_key=keypair.public_key,
                        vm_config=vm_config,
                    )
                )
            )

        creation_results = await asyncio.gather(*create_tasks, return_exceptions=True)
        instances = list(existing_instances)
        for result in creation_results:
            if isinstance(result, Exception):
                log_error(self.logger, "vm.create.error", result, cloud_id=cloud.cloud_id)
                continue
            instances.append(result)
            await self._store_address_created(job_id, cloud, vm_record_id(result.id), result.ip)

        if not instances:
            return False

        poll_tasks = []
        for instance in instances:
            if instance.id in {item.id for item in existing_instances}:
                poll_tasks.append(
                    asyncio.create_task(
                        compute_api.refresh_instance_dynamic_ip(
                            instance.id,
                            poll_seconds=self.settings.hunt_vm_poll_seconds,
                            timeout_seconds=self.settings.hunt_vm_poll_timeout_seconds,
                        )
                    )
                )
            else:
                poll_tasks.append(
                    asyncio.create_task(
                        compute_api.wait_for_external_ip(
                            instance.id,
                            poll_seconds=self.settings.hunt_vm_poll_seconds,
                            timeout_seconds=self.settings.hunt_vm_poll_timeout_seconds,
                        )
                    )
                )
        poll_results = await asyncio.gather(*poll_tasks, return_exceptions=True)

        keep_instance_ids: set[str] = set()
        target = max(1, await self._target_per_cloud(job_id))
        for result in poll_results:
            if isinstance(result, Exception) or result is None or not result.ip:
                continue

            await self._store_address_created(job_id, cloud, vm_record_id(result.id), result.ip)
            await self.state.add_checked_ip(job_id, cloud.cloud_id, result.ip)
            prefix = await self._matched_prefix(job_id, result.ip)
            if not prefix or len(keep_instance_ids) >= target:
                continue

            accepted = await self._accept_match(
                job_id,
                scope,
                cloud,
                address_id=vm_record_id(result.id),
                ip=result.ip,
                prefix=prefix,
                preexisting=False,
                resource_id=result.id,
                resource_type="vm",
                ssh_username=keypair.username,
                ssh_public_key=keypair.public_key,
                ssh_private_key=keypair.private_key,
                zone_id=result.zone_id,
            )
            if accepted:
                keep_instance_ids.add(result.id)

        stop_instances = [instance for instance in instances if instance.id not in keep_instance_ids]
        stop_results = await asyncio.gather(
            *(compute_api.stop_instance(instance.id) for instance in stop_instances),
            return_exceptions=True,
        )
        for instance, result in zip(stop_instances, stop_results):
            if isinstance(result, Exception):
                log_error(self.logger, "vm.stop.error", result, instance_id=instance.id)
                await self._store_address_failed(scope.account_id, vm_record_id(instance.id))

        return bool(keep_instance_ids)

    async def _delete_cloud_for_replacement(
        self,
        scope: ScopeDescriptor,
        cloud: ManagedCloud,
        clouds_api: CloudsApi,
    ) -> bool:
        if await self._cloud_has_saved_match(scope.account_id, cloud.cloud_id):
            await self._skip_protected_cloud_delete(scope.account_id, cloud.cloud_id, reason="replacement")
            return False
        await clouds_api.delete_cloud(cloud.cloud_id, force_now=True)
        await clouds_api.wait_cloud_deleted(cloud.cloud_id)
        await self._remove_cloud_row(scope.account_id, cloud.cloud_id)
        return True

    async def _collect_replacements(
        self,
        *,
        job_id: str,
        scope: ScopeDescriptor,
        deleting: list[DeletingCloud],
        pending_slots: list[PendingCloudSlot],
        clouds_api: CloudsApi,
        stop_event: asyncio.Event,
        block: bool,
    ) -> list[ManagedCloud]:
        if stop_event.is_set():
            return []

        watched_tasks = [item.task for item in deleting] + [item.task for item in pending_slots]
        if not watched_tasks:
            return []

        if block:
            done, _ = await asyncio.wait(watched_tasks, return_when=asyncio.FIRST_COMPLETED)
        else:
            done = {task for task in watched_tasks if task.done()}
            if not done:
                return []

        replacements: list[ManagedCloud] = []
        used_names: set[str] | None = None

        async def create_replacement(
            *,
            billing_account_id: str,
            lifecycle: CloudLifecycle,
            status: HuntCloudStatus,
            notes: str,
        ) -> ManagedCloud:
            nonlocal used_names
            if used_names is None:
                used_names = {cloud.name for cloud in await clouds_api.list_clouds(scope.organization_id)}
            return await self._create_replacement_cloud(
                job_id=job_id,
                scope=scope,
                clouds_api=clouds_api,
                billing_account_id=billing_account_id,
                used_names=used_names,
                lifecycle=lifecycle,
                status=status,
                notes=notes,
            )

        completed = [item for item in deleting if item.task in done]
        for item in completed:
            deleting.remove(item)
            try:
                deleted = await item.task
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log_error(self.logger, "cloud.delete.error", exc, cloud_id=item.cloud.cloud_id)
                await self._set_cloud_progress(
                    job_id,
                    scope,
                    item.cloud.cloud_id,
                    HuntCloudStatus.FAILED,
                    self.settings.hunt_cycles_per_cloud,
                    notes="delete failed",
                    error=str(exc),
                )
                continue
            if not deleted:
                continue

            try:
                replacement = await create_replacement(
                    billing_account_id=item.cloud.billing_account_id,
                    lifecycle=CloudLifecycle.RECREATED,
                    status=HuntCloudStatus.REPLACED,
                    notes="cloud recreated",
                )
            except Exception as exc:  # noqa: BLE001
                log_error(self.logger, "cloud.replacement.create.error", exc, cloud_id=item.cloud.cloud_id)
                await self._set_cloud_progress(
                    job_id,
                    scope,
                    item.cloud.cloud_id,
                    HuntCloudStatus.FAILED,
                    self.settings.hunt_cycles_per_cloud,
                    notes="replacement create failed",
                    error=str(exc),
                )
                continue
            replacements.append(replacement)

        ready_slots = [item for item in pending_slots if item.task in done]
        for item in ready_slots:
            pending_slots.remove(item)
            try:
                await item.task
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log_error(
                    self.logger,
                    "cloud.capacity.wait_deleting.error",
                    exc,
                    account_id=scope.account_id,
                    org_id=scope.organization_id,
                    cloud_id=item.cloud_id,
                )
                continue

            try:
                replacement = await create_replacement(
                    billing_account_id=item.billing_account_id,
                    lifecycle=CloudLifecycle.INIT,
                    status=HuntCloudStatus.PENDING,
                    notes="cloud created after slot freed",
                )
            except Exception as exc:  # noqa: BLE001
                log_error(
                    self.logger,
                    "cloud.slot.create.error",
                    exc,
                    account_id=scope.account_id,
                    org_id=scope.organization_id,
                    cloud_id=item.cloud_id,
                )
                continue
            replacements.append(replacement)
        return replacements

    async def _create_replacement_cloud(
        self,
        *,
        job_id: str,
        scope: ScopeDescriptor,
        clouds_api: CloudsApi,
        billing_account_id: str,
        used_names: set[str],
        lifecycle: CloudLifecycle,
        status: HuntCloudStatus,
        notes: str,
    ) -> ManagedCloud:
        name = self._next_cloud_name(scope.organization_id, used_names, 0)
        created, folder = await clouds_api.create_cloud_with_folder(
            organization_id=scope.organization_id,
            name=name,
            billing_account_id=billing_account_id,
        )

        replacement = ManagedCloud(
            account_id=scope.account_id,
            organization_id=scope.organization_id,
            cloud_id=created.id,
            cloud_name=created.name,
            folder_id=folder.id,
            billing_account_id=billing_account_id,
        )
        used_names.add(created.name)
        await self._upsert_cloud_row(replacement)
        await self.state.register_cloud(
            job_id,
            CloudState(
                account_id=replacement.account_id,
                organization_id=replacement.organization_id,
                cloud_id=replacement.cloud_id,
                cloud_name=replacement.cloud_name,
                folder_id=replacement.folder_id,
                billing_account_id=replacement.billing_account_id,
                lifecycle=lifecycle,
            ),
        )
        await self._set_cloud_progress(
            job_id,
            scope,
            replacement.cloud_id,
            status,
            0,
            notes=notes,
        )
        return replacement

    async def _scan_existing_matches(
        self,
        job_id: str,
        cloud: ManagedCloud,
        vpc_api: VpcApi,
        compute_api: ComputeApi | None = None,
    ) -> bool:
        async def protect(ip: str, *, resource_id: str | None = None) -> bool:
            await self.state.add_checked_ip(job_id, cloud.cloud_id, ip)
            selected_prefix = await self._matched_prefix(job_id, ip)
            known_prefix = match_known_prefix(ip)
            prefix = selected_prefix or known_prefix
            if not prefix:
                return False
            await self.state.set_cloud_lifecycle(job_id, cloud.cloud_id, CloudLifecycle.IDLE)
            await self._set_cloud_progress(
                job_id,
                ScopeDescriptor(account_id=cloud.account_id, organization_id=cloud.organization_id),
                cloud.cloud_id,
                HuntCloudStatus.SKIPPED,
                0,
                notes=f"preexisting known prefix {prefix}; cloud untouched",
                completed=True,
            )
            log_event(
                self.logger,
                "cloud.skipped.preexisting_prefix",
                job_id=job_id,
                account_id=cloud.account_id,
                cloud_id=cloud.cloud_id,
                resource_id=resource_id,
                ip=ip,
                prefix=prefix,
            )
            return True

        addresses = await vpc_api.list_addresses(cloud.folder_id)
        for address in addresses:
            if not address.ip:
                continue
            if await protect(address.ip, resource_id=address.id):
                return True

        if compute_api is not None:
            instances = await compute_api.list_instances(cloud.folder_id)
            for instance in instances:
                if not instance.ip:
                    continue
                if await protect(instance.ip, resource_id=vm_record_id(instance.id)):
                    return True
        return False

    async def _accept_match(
        self,
        job_id: str,
        scope: ScopeDescriptor,
        cloud: ManagedCloud,
        *,
        address_id: str,
        ip: str,
        prefix: str,
        preexisting: bool,
        resource_id: str | None = None,
        resource_type: str | None = None,
        ssh_username: str | None = None,
        ssh_public_key: str | None = None,
        ssh_private_key: str | None = None,
        zone_id: str | None = None,
    ) -> bool:
        match = MatchState(
            account_id=scope.account_id,
            organization_id=scope.organization_id,
            cloud_id=cloud.cloud_id,
            address_id=address_id,
            ip=ip,
            prefix=prefix,
            preexisting=preexisting,
        )
        accepted = await self.state.add_match(job_id, match)
        if not accepted:
            return False

        notification: MatchNotification | None = None
        async with self.db.session() as session:
            hunt_repo = HuntRepository(session)
            address_repo = AddressRepository(session)
            account_repo = AccountRepository(session)
            _, created = await hunt_repo.add_match(
                job_id=job_id,
                account_id=scope.account_id,
                organization_external_id=scope.organization_id,
                cloud_external_id=cloud.cloud_id,
                address_id=address_id,
                ip_address=ip,
                matched_prefix=prefix,
                preexisting=preexisting,
            )
            if created:
                await hunt_repo.increment_match_count(job_id)
            await address_repo.mark_match(scope.account_id, address_id, ip)
            job = await hunt_repo.get_job(job_id)
            account = await account_repo.get_account(scope.account_id)
            if job is not None:
                notification = MatchNotification(
                    chat_id=job.requested_by_chat_id,
                    branch_id=job.branch_id,
                    job_id=job_id,
                    account_id=scope.account_id,
                    account_name=account.name if account else scope.account_id,
                    organization_id=scope.organization_id,
                    cloud_id=cloud.cloud_id,
                    cloud_name=cloud.cloud_name,
                    folder_id=cloud.folder_id,
                    address_id=address_id,
                    ip=ip,
                    prefix=prefix,
                    preexisting=preexisting,
                    resource_id=resource_id,
                    resource_type=resource_type,
                    ssh_username=ssh_username,
                    ssh_public_key=ssh_public_key,
                    ssh_private_key=ssh_private_key,
                    zone_id=zone_id,
                )
            await session.commit()

        log_event(
            self.logger,
            "ip.matched",
            job_id=job_id,
            account_id=scope.account_id,
            cloud_id=cloud.cloud_id,
            address_id=address_id,
            ip=ip,
            prefix=prefix,
            preexisting=preexisting,
        )
        if notification is not None:
            await self._notify_match(notification)
        return True

    async def _notify_match(self, notification: MatchNotification) -> None:
        if self._match_notifier is None:
            return
        try:
            await self._match_notifier(notification)
        except Exception as exc:  # noqa: BLE001
            log_error(self.logger, "match.notification.error", exc, job_id=notification.job_id, ip=notification.ip)

    async def _cleanup_addresses(
        self,
        account_id: str,
        addresses: list[str],
        keep_ids: set[str],
        vpc_api: VpcApi,
    ) -> None:
        to_delete = [item for item in addresses if item not in keep_ids]
        if not to_delete:
            return

        to_delete = [item for item in to_delete if not await self._address_is_matched(account_id, item)]
        if not to_delete:
            return

        tasks = [asyncio.create_task(vpc_api.delete_address(address_id)) for address_id in to_delete]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for address_id, result in zip(to_delete, results):
            if isinstance(result, Exception):
                log_error(self.logger, "ip.delete.error", result, address_id=address_id)
                await self._store_address_failed(account_id, address_id)
                continue
            await self._store_address_deleted(account_id, address_id)

    async def _store_address_created(self, job_id: str, cloud: ManagedCloud, address_id: str, ip: str | None) -> None:
        async with self.db.session() as session:
            repo = AddressRepository(session)
            await repo.mark_created(
                account_id=cloud.account_id,
                job_id=job_id,
                cloud_external_id=cloud.cloud_id,
                folder_external_id=cloud.folder_id,
                address_id=address_id,
                ip_address=ip,
            )
            await session.commit()

    async def _store_address_deleted(self, account_id: str, address_id: str) -> None:
        async with self.db.session() as session:
            repo = AddressRepository(session)
            await repo.mark_deleted(account_id, address_id)
            await session.commit()

    async def _store_address_failed(self, account_id: str, address_id: str) -> None:
        async with self.db.session() as session:
            repo = AddressRepository(session)
            await repo.mark_failed(account_id, address_id)
            await session.commit()

    async def _set_cloud_progress(
        self,
        job_id: str,
        scope: ScopeDescriptor,
        cloud_id: str,
        status: HuntCloudStatus,
        cycles: int,
        *,
        notes: str | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> None:
        async with self.db.session() as session:
            repo = HuntRepository(session)
            await repo.upsert_cloud_progress(
                job_id=job_id,
                account_id=scope.account_id,
                organization_external_id=scope.organization_id,
                cloud_external_id=cloud_id,
                status=status,
                cycles_attempted=cycles,
                notes=notes,
                last_error=error,
                completed=completed,
            )
            await session.commit()

    async def _load_job(self, job_id: str):
        async with self.db.session() as session:
            repo = HuntRepository(session)
            job = await repo.get_job(job_id)
            scopes = await repo.list_job_scopes(job_id)
            return job, scopes

    async def _set_job_status(self, job_id: str, status: HuntStatus, error: str | None = None) -> None:
        async with self.db.session() as session:
            repo = HuntRepository(session)
            await repo.set_job_status(job_id, status=status, error_text=error)
            if status == HuntStatus.RUNNING:
                await repo.set_job_running(job_id, progress_message_chat_id=None, progress_message_id=None)
            await session.commit()

    async def _get_account(self, account_id: str):
        async with self.db.session() as session:
            repo = AccountRepository(session)
            return await repo.get_account(account_id)

    async def _upsert_cloud_row(self, cloud: ManagedCloud) -> None:
        async with self.db.session() as session:
            repo = AccountRepository(session)
            await repo.upsert_cloud(
                account_id=cloud.account_id,
                organization_external_id=cloud.organization_id,
                external_id=cloud.cloud_id,
                name=cloud.cloud_name,
                state=DbCloudState.ACTIVE,
                billing_account_external_id=cloud.billing_account_id,
                folder_external_id=cloud.folder_id,
                marked_for_deletion=False,
            )
            await session.commit()

    async def _remove_cloud_row(self, account_id: str, cloud_id: str) -> None:
        async with self.db.session() as session:
            repo = AccountRepository(session)
            await repo.remove_cloud(account_id, cloud_id)
            await session.commit()

    async def _address_is_matched(self, account_id: str, address_id: str) -> bool:
        async with self.db.session() as session:
            repo = AddressRepository(session)
            return await repo.address_is_matched(account_id, address_id)

    async def _cloud_has_saved_match(self, account_id: str, cloud_id: str) -> bool:
        async with self.db.session() as session:
            repo = AddressRepository(session)
            return await repo.cloud_has_matched_address(account_id, cloud_id)

    async def _skip_protected_cloud_delete(self, account_id: str, cloud_id: str, *, reason: str) -> None:
        log_event(
            self.logger,
            "cloud.delete.skipped.matched_ip",
            account_id=account_id,
            cloud_id=cloud_id,
            reason=reason,
        )

    async def _scope_has_match(self, job_id: str, scope: ScopeDescriptor) -> bool:
        hunt = await self.state.get_hunt(job_id)
        if hunt is None:
            return False
        return any(
            item.account_id == scope.account_id and item.organization_id == scope.organization_id
            for item in hunt.matches
        )

    async def _all_cloud_targets_reached(self, job_id: str) -> bool:
        return await self.state.all_cloud_targets_reached(job_id)

    async def _all_scope_targets_reached(self, job_id: str, scopes: list[ScopeDescriptor]) -> bool:
        for scope in scopes:
            if not await self._scope_targets_reached(job_id, scope):
                return False
        return True

    async def _scope_targets_reached(self, job_id: str, scope: ScopeDescriptor) -> bool:
        return await self.state.scope_targets_reached(
            job_id,
            account_id=scope.account_id,
            organization_id=scope.organization_id,
            target_cloud_count=self.settings.hunt_cloud_target_count,
        )

    async def _cloud_target_reached(self, job_id: str, cloud_id: str) -> bool:
        return await self.state.cloud_target_reached(job_id, cloud_id)

    async def _target_per_cloud(self, job_id: str) -> int:
        state = await self.state.get_hunt(job_id)
        if state is None:
            return 0
        return state.target_count

    async def _matched_prefix(self, job_id: str, ip: str) -> str | None:
        prefixes = await self.state.get_prefixes(job_id)
        return match_prefix(ip, prefixes)

    @staticmethod
    def _select_billing_for_organization(
        *,
        organization_id: str,
        active_billing: list,
        clouds: list[Cloud],
        billing_map: dict[str, str],
        org_billing_map: dict[str, str],
    ):
        active_by_id = {item.id: item for item in active_billing}

        org_billing_id = org_billing_map.get(organization_id)
        if org_billing_id in active_by_id:
            return active_by_id[org_billing_id]

        cloud_billing_ids = {
            billing_map[cloud.id]
            for cloud in clouds
            if billing_map.get(cloud.id) in active_by_id
        }
        if len(cloud_billing_ids) == 1:
            return active_by_id[next(iter(cloud_billing_ids))]

        return None

    @staticmethod
    def _next_cloud_name(organization_id: str, existing_names: set[str], salt: int) -> str:
        suffix = re.sub(r"[^a-z0-9]", "", organization_id.lower())[-6:] or "org"
        seq = max(1, salt + 1)
        for _ in range(300):
            candidate = f"hunter-{suffix}-{seq:03d}"
            if candidate not in existing_names:
                return candidate
            seq += 1
        ts = int(datetime.now(tz=timezone.utc).timestamp())
        return f"hunter-{suffix}-{ts}"

    @staticmethod
    def _next_vm_name(cloud_id: str, index: int) -> str:
        suffix = re.sub(r"[^a-z0-9]", "", cloud_id.lower())[-8:] or "cloud"
        return f"hunter-vm-{suffix}-{index + 1:02d}"
