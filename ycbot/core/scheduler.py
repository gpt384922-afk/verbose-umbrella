from __future__ import annotations

import asyncio
import contextlib
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from ycbot.config import Settings
from ycbot.core.prefixes import match_known_prefix, validate_hunt_prefixes
from ycbot.core.hunter import HunterEngine, ManagedCloud, VM_RECORD_PREFIX, strip_vm_record_id
from ycbot.core.state_manager import CloudLifecycle, HuntState, StateManager, TaskLifecycle
from ycbot.core.vm_config import VmHuntConfig
from ycbot.db.enums import CloudState as DbCloudState
from ycbot.db.enums import HuntStatus
from ycbot.db.repositories import (
    AccountRepository,
    AddressRepository,
    BranchRepository,
    HuntRepository,
    HuntScopeInput,
    StatsRepository,
)
from ycbot.db.session import Database
from ycbot.utils import log_error, log_event
from ycbot.yc import CloudsApi, ComputeApi, VpcApi, YcClient
from ycbot.yc.center import CloudCenterOrganizationCreator


@dataclass(slots=True)
class HuntStartScope:
    account_id: str
    organization_id: str


@dataclass(slots=True)
class HuntStartRequest:
    requested_by_chat_id: int
    branch_id: str | None
    prefixes: list[str]
    target_count: int
    scopes: list[HuntStartScope]
    vm_config: dict | VmHuntConfig | None = None


@dataclass(slots=True)
class SchedulerTask:
    job_id: str
    task: asyncio.Task[None]
    stop_event: asyncio.Event


ACTIVE_HUNT_STATUSES = {TaskLifecycle.PENDING, TaskLifecycle.RUNNING}


class HuntScheduler:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        state: StateManager,
        hunter: HunterEngine,
        semaphore: asyncio.Semaphore,
        logger,
    ) -> None:
        self.settings = settings
        self.db = db
        self.state = state
        self.hunter = hunter
        self.semaphore = semaphore
        self.logger = logger

        self._tasks: dict[str, SchedulerTask] = {}
        self._account_sync_tasks: set[asyncio.Task[None]] = set()
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

        self._cleanup_task: asyncio.Task | None = None
        self._cleanup_stop = asyncio.Event()

    async def start_cleanup(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            return
        self._cleanup_stop.clear()
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup(self) -> None:
        self._cleanup_stop.set()
        if self._cleanup_task:
            self._cleanup_task.cancel()
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
            self._cleanup_task = None

    async def add_account(
        self,
        *,
        branch_id: str | None,
        name: str,
        oauth_token: str,
        email: str | None,
        password: str | None,
        secret: str | None,
        proxy_url: str | None,
    ) -> str:
        async with self.db.session() as session:
            repo = AccountRepository(session)
            account = await repo.create_account(
                branch_id=branch_id,
                name=name,
                oauth_token=oauth_token,
                email=email,
                password=password,
                secret=secret,
                proxy_url=proxy_url,
            )
            await session.commit()

        return account.id

    def schedule_account_sync(self, account_id: str, *, ensure_capacity: bool, branch_id: str | None) -> None:
        task = asyncio.create_task(self._run_account_sync(account_id, ensure_capacity=ensure_capacity, branch_id=branch_id))
        self._account_sync_tasks.add(task)
        task.add_done_callback(self._account_sync_done)

    async def _run_account_sync(self, account_id: str, *, ensure_capacity: bool, branch_id: str | None) -> None:
        await self.sync_account(account_id, ensure_capacity=ensure_capacity, branch_id=branch_id)

    def _account_sync_done(self, task: asyncio.Task[None]) -> None:
        self._account_sync_tasks.discard(task)
        if task.cancelled():
            return
        with contextlib.suppress(Exception):
            exc = task.exception()
            if exc:
                log_error(self.logger, "account.background_sync.error", exc)

    async def sync_account(self, account_id: str, *, ensure_capacity: bool, branch_id: str | None) -> dict[str, int]:
        account = await self._get_account_for_branch(account_id, branch_id)
        if account is None:
            raise RuntimeError(f"account not found: {account_id}")

        if ensure_capacity and await self._account_has_active_hunt(account_id):
            log_event(self.logger, "account.sync.skipped.active_hunt", account_id=account_id)
            return {
                "organizations": 0,
                "billing_accounts": 0,
                "active_clouds": 0,
                "deleted_clouds": 0,
            }

        async with self._account_lock(account_id):
            return await self._sync_account_locked(account, ensure_capacity=ensure_capacity)

    async def _sync_account_locked(self, account, *, ensure_capacity: bool) -> dict[str, int]:

        async with YcClient(
            settings=self.settings,
            oauth_token=account.oauth_token,
            proxy_url=account.proxy_url,
            semaphore=self.semaphore,
            logger=self.logger,
        ) as client:
            clouds_api = CloudsApi(client=client, settings=self.settings, logger=self.logger)

            organizations = await clouds_api.list_organizations()
            billing_accounts = await clouds_api.list_billing_accounts()
            active_billing = [item for item in billing_accounts if item.active]
            billing_map = await clouds_api.resolve_cloud_billing_map(active_billing)
            org_billing_map = await clouds_api.resolve_organization_billing_map(active_billing)

            async with self.db.session() as session:
                repo = AccountRepository(session)
                await repo.replace_organizations(account.id, [asdict(item) for item in organizations])
                await repo.replace_billing_accounts(account.id, [asdict(item) for item in billing_accounts])
                await session.commit()

            active_clouds = 0
            deleted_clouds = 0
            for organization in organizations:
                normalized = await self._normalize_organization_clouds(
                    account_id=account.id,
                    organization_id=organization.id,
                    clouds_api=clouds_api,
                    active_billing=active_billing,
                    billing_map=billing_map,
                    org_billing_map=org_billing_map,
                    ensure_capacity=ensure_capacity,
                )
                active_clouds += normalized["active_clouds"]
                deleted_clouds += normalized["deleted_clouds"]

        result = {
            "organizations": len(organizations),
            "billing_accounts": len(billing_accounts),
            "active_clouds": active_clouds,
            "deleted_clouds": deleted_clouds,
        }
        log_event(self.logger, "account.synced", account_id=account.id, **result)
        return result

    async def refresh_account_directory(self, account_id: str, *, branch_id: str | None) -> dict[str, int]:
        account = await self._get_account_for_branch(account_id, branch_id)
        if account is None:
            raise RuntimeError(f"account not found: {account_id}")

        async with YcClient(
            settings=self.settings,
            oauth_token=account.oauth_token,
            proxy_url=account.proxy_url,
            semaphore=self.semaphore,
            logger=self.logger,
        ) as client:
            clouds_api = CloudsApi(client=client, settings=self.settings, logger=self.logger)
            organizations = await clouds_api.list_organizations()
            billing_accounts = await clouds_api.list_billing_accounts()

        async with self.db.session() as session:
            repo = AccountRepository(session)
            await repo.replace_organizations(account.id, [asdict(item) for item in organizations])
            await repo.replace_billing_accounts(account.id, [asdict(item) for item in billing_accounts])
            await session.commit()

        result = {
            "organizations": len(organizations),
            "billing_accounts": len(billing_accounts),
        }
        log_event(self.logger, "account.directory_refreshed", account_id=account.id, **result)
        return result

    async def create_test_organization(self, account_id: str, *, branch_id: str | None) -> dict[str, str | int]:
        account = await self._get_account_for_branch(account_id, branch_id)
        if account is None:
            raise RuntimeError(f"account not found: {account_id}")

        async with self._account_lock(account_id):
            async with YcClient(
                settings=self.settings,
                oauth_token=account.oauth_token,
                proxy_url=account.proxy_url,
                semaphore=self.semaphore,
                logger=self.logger,
            ) as client:
                clouds_api = CloudsApi(client=client, settings=self.settings, logger=self.logger)
                before = await clouds_api.list_organizations()

            existing_ids = {item.id for item in before}
            current_org_name = before[0].name if before else None
            name = self._next_test_organization_name()
            creator = CloudCenterOrganizationCreator(settings=self.settings, logger=self.logger)
            created_name = await asyncio.to_thread(
                creator.create_organization,
                name,
                current_organization_name=current_org_name,
                proxy_url=account.proxy_url,
            )

            async with YcClient(
                settings=self.settings,
                oauth_token=account.oauth_token,
                proxy_url=account.proxy_url,
                semaphore=self.semaphore,
                logger=self.logger,
            ) as client:
                clouds_api = CloudsApi(client=client, settings=self.settings, logger=self.logger)
                organization = await self._wait_for_created_organization(
                    clouds_api=clouds_api,
                    name=created_name,
                    existing_ids=existing_ids,
                )
                organizations = await clouds_api.list_organizations()
                billing_accounts = await clouds_api.list_billing_accounts()

            async with self.db.session() as session:
                repo = AccountRepository(session)
                await repo.replace_organizations(account.id, [asdict(item) for item in organizations])
                await repo.replace_billing_accounts(account.id, [asdict(item) for item in billing_accounts])
                await session.commit()

        result = {
            "id": organization.id,
            "name": organization.name,
            "organizations": len(organizations),
            "billing_accounts": len(billing_accounts),
        }
        log_event(self.logger, "account.test_organization.created", account_id=account.id, **result)
        return result

    async def import_center_cookies(
        self,
        account_id: str,
        *,
        branch_id: str | None,
        cookie_text: str,
    ) -> dict[str, object]:
        account = await self._get_account_for_branch(account_id, branch_id)
        if account is None:
            raise RuntimeError(f"account not found: {account_id}")
        if not (
            self.settings.yc_center_chrome_user_data_dir
            or self.settings.yc_center_chrome_debugger_address
            or self.settings.yc_center_selenium_remote_url
        ):
            raise RuntimeError("set YC_CENTER_CHROME_USER_DATA_DIR or YC_CENTER_CHROME_DEBUGGER_ADDRESS first")

        async with self._account_lock(account_id):
            creator = CloudCenterOrganizationCreator(settings=self.settings, logger=self.logger)
            result = await asyncio.to_thread(creator.import_cookies, cookie_text, proxy_url=account.proxy_url)

        log_event(
            self.logger,
            "account.center_cookies.imported",
            account_id=account.id,
            imported=result.get("imported"),
            failed=result.get("failed"),
        )
        return result

    async def _wait_for_created_organization(
        self,
        *,
        clouds_api: CloudsApi,
        name: str,
        existing_ids: set[str],
    ):
        deadline = time.monotonic() + self.settings.yc_center_wait_seconds
        last_seen = []
        while time.monotonic() <= deadline:
            last_seen = await clouds_api.list_organizations()
            for organization in last_seen:
                if organization.name == name and organization.id not in existing_ids:
                    return organization
            await asyncio.sleep(3)

        known = ", ".join(f"{item.name}:{item.id}" for item in last_seen[:10])
        raise RuntimeError(f"created organization not found by name={name!r}; seen={known}")

    def _next_test_organization_name(self) -> str:
        prefix = self.settings.yc_center_org_name_prefix.strip() or "ycbot-org"
        return f"{prefix}-test-{int(time.time())}"

    async def list_accounts(self, *, branch_id: str | None) -> list[dict]:
        async with self.db.session() as session:
            repo = AccountRepository(session)
            stats_repo = StatsRepository(session)
            accounts = await repo.list_accounts(branch_id=branch_id, include_inactive=False)

            rows = []
            for account in accounts:
                overview = await stats_repo.account_overview(account.id)
                rows.append(
                    {
                        "id": account.id,
                        "branch_id": account.branch_id,
                        "name": account.name,
                        "email": account.email,
                        "proxy_url": account.proxy_url,
                        "organizations": overview["organizations"],
                        "clouds": overview["clouds"],
                        "active_billing": overview["active_billing"],
                    }
                )
            return rows

    async def _list_all_accounts(self) -> list[dict]:
        async with self.db.session() as session:
            repo = AccountRepository(session)
            accounts = await repo.list_all_accounts(include_inactive=False)
            return [
                {
                    "id": account.id,
                    "branch_id": account.branch_id,
                    "name": account.name,
                }
                for account in accounts
            ]

    async def account_details(self, account_id: str, *, branch_id: str | None) -> dict | None:
        async with self.db.session() as session:
            repo = AccountRepository(session)
            account = await repo.get_account_for_branch(account_id, branch_id)
            if account is None or not account.is_active:
                return None
            organizations = await repo.list_organizations(account_id)
            clouds = await repo.list_clouds(account_id)

        active_hunts = await self.state.list_hunts()
        cloud_runtime_status: dict[str, CloudLifecycle] = {}
        for hunt in active_hunts:
            for cloud_id, state in hunt.cloud_states.items():
                cloud_runtime_status[cloud_id] = state.lifecycle

        return {
            "id": account.id,
            "name": account.name,
            "oauth_token": account.oauth_token,
            "email": account.email,
            "password": account.password,
            "secret": account.secret,
            "proxy_url": account.proxy_url,
            "organizations": [
                {"id": org.external_id, "name": org.name, "state": org.state}
                for org in organizations
            ],
            "clouds": [
                {
                    "id": cloud.external_id,
                    "name": cloud.name,
                    "state": cloud.state.value,
                    "billing": cloud.billing_account_external_id,
                    "runtime": (cloud_runtime_status.get(cloud.external_id) or CloudLifecycle.IDLE).value,
                }
                for cloud in clouds
            ],
        }

    async def delete_account(self, account_id: str, *, branch_id: str | None) -> bool:
        account = await self._get_account_for_branch(account_id, branch_id)
        if account is None or not account.is_active:
            return False
        if await self._account_has_active_hunt(account_id):
            raise RuntimeError("нельзя удалить аккаунт, пока по нему идет активный хант")

        async with self.db.session() as session:
            repo = AccountRepository(session)
            await repo.deactivate_account(account_id)
            await session.commit()
        return True

    async def update_account_proxy(self, account_id: str, *, branch_id: str | None, proxy_url: str | None) -> bool:
        account = await self._get_account_for_branch(account_id, branch_id)
        if account is None or not account.is_active:
            return False
        async with self.db.session() as session:
            repo = AccountRepository(session)
            updated = await repo.update_account_proxy(account_id, proxy_url)
            await session.commit()
        return updated

    async def add_branch(
        self,
        *,
        name: str,
        bot_token: str,
        owner_chat_id: int,
        bot_username: str | None,
    ) -> dict:
        async with self.db.session() as session:
            repo = BranchRepository(session)
            branch = await repo.create_branch(
                name=name,
                bot_token=bot_token,
                owner_chat_id=owner_chat_id,
                bot_username=bot_username,
            )
            await session.commit()
            return self._branch_dict(branch)

    async def list_branches(self) -> list[dict]:
        async with self.db.session() as session:
            repo = BranchRepository(session)
            branches = await repo.list_branches(include_inactive=True)
            return [self._branch_dict(branch) for branch in branches]

    async def list_active_branches(self) -> list[dict]:
        async with self.db.session() as session:
            repo = BranchRepository(session)
            branches = await repo.active_branches()
            return [self._branch_dict(branch) for branch in branches]

    async def branch_details(self, branch_id: str) -> dict | None:
        async with self.db.session() as session:
            repo = BranchRepository(session)
            branch = await repo.get_branch(branch_id)
            if branch is None:
                return None
            return self._branch_dict(branch)

    async def disable_branch(self, branch_id: str) -> bool:
        async with self.db.session() as session:
            repo = BranchRepository(session)
            disabled = await repo.deactivate_branch(branch_id)
            await session.commit()
            return disabled

    @staticmethod
    def _branch_dict(branch) -> dict:
        return {
            "id": branch.id,
            "name": branch.name,
            "bot_token": branch.bot_token,
            "owner_chat_id": branch.owner_chat_id,
            "bot_username": branch.bot_username,
            "is_active": branch.is_active,
        }

    async def list_account_organizations(self, account_ids: list[str], *, branch_id: str | None) -> list[dict]:
        async with self.db.session() as session:
            repo = AccountRepository(session)
            pairs = await repo.list_account_organization_pairs(account_ids, branch_id=branch_id)

        return [
            {
                "account_id": account.id,
                "account_name": account.name,
                "organization_id": org.external_id,
                "organization_name": org.name,
            }
            for account, org in pairs
        ]

    async def scan_existing_prefix_ips(
        self,
        scopes: list[HuntStartScope],
        *,
        branch_id: str | None,
    ) -> dict[str, list[dict]]:
        if not scopes:
            return {"existing_ips": [], "errors": []}

        account_ids = sorted({item.account_id for item in scopes})
        org_names: dict[tuple[str, str], dict] = {}
        try:
            for row in await self.list_account_organizations(account_ids, branch_id=branch_id):
                org_names[(row["account_id"], row["organization_id"])] = row
        except Exception as exc:  # noqa: BLE001
            log_error(self.logger, "hunt.preflight.directory.error", exc)

        existing_ips: list[dict] = []
        errors: list[dict] = []
        scopes_by_account: dict[str, list[HuntStartScope]] = {}
        for scope in scopes:
            scopes_by_account.setdefault(scope.account_id, []).append(scope)

        for account_id, account_scopes in scopes_by_account.items():
            account = await self._get_account_for_branch(account_id, branch_id)
            if account is None:
                for scope in account_scopes:
                    names = org_names.get((scope.account_id, scope.organization_id), {})
                    errors.append(
                        {
                            "account_id": scope.account_id,
                            "account_name": names.get("account_name"),
                            "organization_id": scope.organization_id,
                            "organization_name": names.get("organization_name"),
                            "error": "account not found",
                        }
                    )
                continue

            try:
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
                    for scope in account_scopes:
                        names = org_names.get((scope.account_id, scope.organization_id), {})
                        try:
                            clouds = await clouds_api.list_clouds(scope.organization_id)
                            for cloud in clouds:
                                if cloud.deleting or cloud.state in {
                                    DbCloudState.BLOCKED,
                                    DbCloudState.DELETING,
                                    DbCloudState.DELETED,
                                }:
                                    continue
                                folders = await clouds_api.list_folders(cloud.id)
                                for folder in folders:
                                    addresses = await vpc_api.list_addresses(folder.id)
                                    for address in addresses:
                                        prefix = match_known_prefix(address.ip)
                                        if not prefix:
                                            continue
                                        existing_ips.append(
                                            {
                                                "account_id": scope.account_id,
                                                "account_name": names.get("account_name") or account.name,
                                                "organization_id": scope.organization_id,
                                                "organization_name": names.get("organization_name"),
                                                "cloud_id": cloud.id,
                                                "cloud_name": cloud.name,
                                                "folder_id": folder.id,
                                                "ip": address.ip,
                                                "prefix": prefix,
                                                "resource_type": "address",
                                                "address_id": address.id,
                                            }
                                        )
                                    instances = await compute_api.list_instances(folder.id)
                                    for instance in instances:
                                        prefix = match_known_prefix(instance.ip)
                                        if not prefix:
                                            continue
                                        existing_ips.append(
                                            {
                                                "account_id": scope.account_id,
                                                "account_name": names.get("account_name") or account.name,
                                                "organization_id": scope.organization_id,
                                                "organization_name": names.get("organization_name"),
                                                "cloud_id": cloud.id,
                                                "cloud_name": cloud.name,
                                                "folder_id": folder.id,
                                                "ip": instance.ip,
                                                "prefix": prefix,
                                                "resource_type": "vm",
                                                "instance_id": instance.id,
                                                "zone_id": instance.zone_id,
                                            }
                                        )
                        except Exception as exc:  # noqa: BLE001
                            log_error(
                                self.logger,
                                "hunt.preflight.scope.error",
                                exc,
                                account_id=scope.account_id,
                                org_id=scope.organization_id,
                            )
                            errors.append(
                                {
                                    "account_id": scope.account_id,
                                    "account_name": names.get("account_name") or account.name,
                                    "organization_id": scope.organization_id,
                                    "organization_name": names.get("organization_name"),
                                    "error": str(exc) or exc.__class__.__name__,
                                }
                            )
            except Exception as exc:  # noqa: BLE001
                for scope in account_scopes:
                    names = org_names.get((scope.account_id, scope.organization_id), {})
                    errors.append(
                        {
                            "account_id": scope.account_id,
                            "account_name": names.get("account_name") or account.name,
                            "organization_id": scope.organization_id,
                            "organization_name": names.get("organization_name"),
                            "error": str(exc) or exc.__class__.__name__,
                        }
                    )
                    log_error(
                        self.logger,
                        "hunt.preflight.account.error",
                        exc,
                        account_id=scope.account_id,
                        org_id=scope.organization_id,
                    )

        existing_ips.sort(key=lambda item: (item["prefix"], item["ip"], item["cloud_id"]))
        return {"existing_ips": existing_ips, "errors": errors}

    async def start_hunt(self, request: HuntStartRequest) -> str:
        if request.target_count < 1 or request.target_count > 5:
            raise ValueError("target_count must be between 1 and 5")
        prefixes = validate_hunt_prefixes(request.prefixes)
        vm_config = (
            request.vm_config
            if isinstance(request.vm_config, VmHuntConfig)
            else VmHuntConfig.from_dict(request.vm_config)
        )
        if not request.scopes:
            raise ValueError("at least one organization scope must be selected")

        scopes = [
            HuntScopeInput(account_id=item.account_id, organization_external_id=item.organization_id)
            for item in request.scopes
        ]

        account_ids = sorted({item.account_id for item in request.scopes})
        acquired_locks = [self._account_lock(account_id) for account_id in account_ids]
        async with contextlib.AsyncExitStack() as stack:
            for lock in acquired_locks:
                await stack.enter_async_context(lock)

            async with self.db.session() as session:
                repo = HuntRepository(session)
                job = await repo.create_job(
                    branch_id=request.branch_id,
                    requested_by_chat_id=request.requested_by_chat_id,
                    target_prefixes=prefixes,
                    requested_ip_count=request.target_count,
                    vm_config=vm_config.to_dict(),
                    scopes=scopes,
                )
                await session.commit()

            stop_event = asyncio.Event()
            task = asyncio.create_task(self._run_job(job.id, stop_event, vm_config))

            async with self._lock:
                self._tasks[job.id] = SchedulerTask(job_id=job.id, task=task, stop_event=stop_event)

            log_event(
                self.logger,
                "hunt.started",
                job_id=job.id,
                scopes=[asdict(item) for item in request.scopes],
            )
            return job.id

    async def stop_hunt(self, job_id: str, *, branch_id: str | None) -> bool:
        if not await self._job_in_branch(job_id, branch_id):
            return False
        async with self._lock:
            task_ref = self._tasks.get(job_id)
        if task_ref is None:
            return False
        task_ref.stop_event.set()
        task_ref.task.cancel()
        await asyncio.gather(task_ref.task, return_exceptions=True)
        return True

    async def list_hunts(self, *, branch_id: str | None) -> list[dict]:
        hunts = [
            hunt
            for hunt in await self.state.list_hunts()
            if hunt.status in ACTIVE_HUNT_STATUSES
        ]
        rows: list[dict] = []
        scope_counts: dict[str, int] = {}
        async with self.db.session() as session:
            repo = HuntRepository(session)
            for hunt in hunts:
                job = await repo.get_job_for_branch(hunt.job_id, branch_id)
                if job is None:
                    continue
                scope_counts[hunt.job_id] = len(await repo.list_job_scopes(hunt.job_id))

        for hunt in hunts:
            if hunt.job_id not in scope_counts:
                continue
            status_counts = Counter(cloud.lifecycle for cloud in hunt.cloud_states.values())
            rows.append(
                {
                    "job_id": hunt.job_id,
                    "status": hunt.status.value,
                    "prefixes": hunt.prefixes,
                    "target_count": hunt.target_count,
                    "target_total": hunt.target_count,
                    "match_count": len(hunt.matches),
                    "cloud_stats": {state.value: count for state, count in status_counts.items()},
                    "error": hunt.error,
                }
            )
        rows.sort(key=lambda item: item["job_id"], reverse=True)
        return rows

    async def hunt_details(self, job_id: str, *, branch_id: str | None) -> dict | None:
        hunt = await self.state.get_hunt(job_id)
        if hunt is None:
            async with self.db.session() as session:
                repo = HuntRepository(session)
                address_repo = AddressRepository(session)
                job = await repo.get_job_for_branch(job_id, branch_id)
                if job is None:
                    return None
                matches = await repo.list_matches(job_id)
                progress = await repo.list_cloud_progress(job_id)
                scopes = await repo.list_job_scopes(job_id)
                checked_ip_count = await address_repo.count_job_records(job_id)

            runtime_seconds = self._runtime_seconds(job.started_at, job.finished_at)
            return {
                "job_id": job.id,
                "status": job.status.value,
                "prefixes": list(job.target_prefixes),
                "target_count": job.requested_ip_count,
                "target_total": job.requested_ip_count,
                "match_count": job.match_count,
                "runtime_seconds": runtime_seconds,
                "checked_ip_count": checked_ip_count,
                "active_cloud_count": sum(1 for row in progress if row.status != HuntCloudStatus.FAILED),
                "error": job.error_text,
                "matches": [
                    {
                        "ip": item.ip_address,
                        "prefix": item.matched_prefix,
                        "cloud_id": item.cloud_external_id,
                        "preexisting": item.preexisting,
                    }
                    for item in matches
                ],
                "clouds": [
                    {
                        "cloud_id": row.cloud_external_id,
                        "status": row.status.value,
                        "attempts": row.cycles_attempted,
                        "notes": row.notes,
                        "error": row.last_error,
                    }
                for row in progress
                ],
            }

        async with self.db.session() as session:
            repo = HuntRepository(session)
            job = await repo.get_job_for_branch(job_id, branch_id)
            if job is None:
                return None
            scopes = await repo.list_job_scopes(job_id)

        active_cloud_count = sum(
            1
            for cloud in hunt.cloud_states.values()
            if cloud.lifecycle not in {CloudLifecycle.FAILED, CloudLifecycle.DELETING}
        )
        return {
            "job_id": hunt.job_id,
            "status": hunt.status.value,
            "prefixes": hunt.prefixes,
            "target_count": hunt.target_count,
            "target_total": hunt.target_count,
            "match_count": len(hunt.matches),
            "runtime_seconds": self._runtime_seconds(hunt.started_at, hunt.completed_at),
            "checked_ip_count": sum(len(cloud.checked_ips) for cloud in hunt.cloud_states.values()),
            "active_cloud_count": active_cloud_count,
            "error": hunt.error,
            "matches": [
                {
                    "ip": item.ip,
                    "prefix": item.prefix,
                    "cloud_id": item.cloud_id,
                    "preexisting": item.preexisting,
                }
                for item in hunt.matches
            ],
            "clouds": [
                {
                    "cloud_id": cloud.cloud_id,
                    "name": cloud.cloud_name,
                    "status": cloud.lifecycle.value,
                    "attempts": cloud.attempts,
                    "checked_ips": len(cloud.checked_ips),
                    "last_ip": cloud.last_ip,
                    "error": cloud.error,
                }
                for cloud in hunt.cloud_states.values()
            ],
        }

    async def _run_job(
        self,
        job_id: str,
        stop_event: asyncio.Event,
        vm_config: VmHuntConfig | None = None,
    ) -> None:
        try:
            await self.hunter.run_job(job_id, stop_event, vm_config)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self.state.set_task_status(job_id, TaskLifecycle.CANCELLED)
            with contextlib.suppress(Exception):
                async with self.db.session() as session:
                    repo = HuntRepository(session)
                    await repo.set_job_status(job_id, status=HuntStatus.CANCELLED, error_text=None)
                    await session.commit()
            raise
        except Exception as exc:  # noqa: BLE001
            log_error(self.logger, "hunt.execution.error", exc, job_id=job_id)
            with contextlib.suppress(Exception):
                await self.state.set_task_status(job_id, TaskLifecycle.FAILED, error=str(exc))
        finally:
            async with self._lock:
                self._tasks.pop(job_id, None)

    def _account_lock(self, account_id: str) -> asyncio.Lock:
        lock = self._account_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._account_locks[account_id] = lock
        return lock

    async def _job_in_branch(self, job_id: str, branch_id: str | None) -> bool:
        async with self.db.session() as session:
            repo = HuntRepository(session)
            return await repo.get_job_for_branch(job_id, branch_id) is not None

    async def _get_account_for_branch(self, account_id: str, branch_id: str | None):
        async with self.db.session() as session:
            repo = AccountRepository(session)
            return await repo.get_account_for_branch(account_id, branch_id)

    @staticmethod
    def _runtime_seconds(started_at: datetime | None, finished_at: datetime | None) -> int:
        if started_at is None:
            return 0
        end = finished_at or datetime.now(tz=timezone.utc)
        return max(0, int((end - started_at).total_seconds()))

    async def _account_has_active_hunt(self, account_id: str) -> bool:
        for hunt in await self.state.list_hunts():
            if hunt.status not in ACTIVE_HUNT_STATUSES:
                continue
            if any(cloud.account_id == account_id for cloud in hunt.cloud_states.values()):
                return True

        async with self.db.session() as session:
            repo = HuntRepository(session)
            jobs = await repo.list_active_jobs()
            for job in jobs:
                scopes = await repo.list_job_scopes(job.id)
                if any(scope.account_id == account_id for scope in scopes):
                    return True
        return False

    async def _normalize_organization_clouds(
        self,
        *,
        account_id: str,
        organization_id: str,
        clouds_api: CloudsApi,
        active_billing: list,
        billing_map: dict[str, str],
        org_billing_map: dict[str, str],
        ensure_capacity: bool,
    ) -> dict[str, int]:
        clouds = await clouds_api.list_clouds(organization_id)
        primary_billing = self._select_billing_for_organization(
            organization_id=organization_id,
            active_billing=active_billing,
            clouds=clouds,
            billing_map=billing_map,
            org_billing_map=org_billing_map,
        )
        managed: list[ManagedCloud] = []
        blocked_slots = 0
        protected_slots = 0
        deleted = 0

        for cloud in clouds:
            billing_id = billing_map.get(cloud.id)
            if cloud.deleting or cloud.state == DbCloudState.DELETING:
                blocked_slots += 1
                if await self._cloud_has_saved_match(account_id, cloud.id):
                    log_event(
                        self.logger,
                        "cloud.protected.already_deleting",
                        account_id=account_id,
                        cloud_id=cloud.id,
                    )
                else:
                    await self._remove_cloud(account_id, cloud.id)
                continue
            if cloud.state == DbCloudState.BLOCKED or not billing_id:
                if await self._cloud_has_saved_match(account_id, cloud.id):
                    protected_slots += 1
                    await self._skip_protected_cloud_delete(
                        account_id,
                        cloud.id,
                        reason="blocked" if cloud.state == DbCloudState.BLOCKED else "no_billing",
                    )
                    continue
                await clouds_api.delete_cloud(cloud.id, force_now=True)
                blocked_slots += 1
                await self._remove_cloud(account_id, cloud.id)
                deleted += 1
                continue

            if primary_billing and billing_id != primary_billing.id:
                await clouds_api.bind_cloud_to_billing(cloud.id, primary_billing.id)
                billing_id = primary_billing.id

            try:
                folder = await clouds_api.ensure_folder(cloud.id)
            except Exception as exc:  # noqa: BLE001
                log_error(self.logger, "folder.ensure.error", exc, cloud_id=cloud.id)
                if await self._cloud_has_saved_match(account_id, cloud.id):
                    protected_slots += 1
                    await self._skip_protected_cloud_delete(account_id, cloud.id, reason="folder_unavailable")
                    continue
                await clouds_api.delete_cloud(cloud.id, force_now=True)
                blocked_slots += 1
                await self._remove_cloud(account_id, cloud.id)
                deleted += 1
                continue
            managed_cloud = ManagedCloud(
                account_id=account_id,
                organization_id=organization_id,
                cloud_id=cloud.id,
                cloud_name=cloud.name,
                folder_id=folder.id,
                billing_account_id=billing_id,
            )
            managed.append(managed_cloud)
            await self._upsert_cloud(managed_cloud)

        if ensure_capacity:
            target = 1
            total_slots = len(managed) + blocked_slots + protected_slots
            if primary_billing and len(managed) < target and total_slots < target:
                missing = target - total_slots
                names = {item.cloud_name for item in managed}
                for index in range(missing):
                    name = self._cloud_name(organization_id, names, index)
                    try:
                        created, folder = await clouds_api.create_cloud_with_folder(
                            organization_id=organization_id,
                            name=name,
                            billing_account_id=primary_billing.id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        blocked_slots += 1
                        log_error(self.logger, "cloud.prepare.error", exc, org_id=organization_id)
                        break
                    row = ManagedCloud(
                        account_id=account_id,
                        organization_id=organization_id,
                        cloud_id=created.id,
                        cloud_name=created.name,
                        folder_id=folder.id,
                        billing_account_id=primary_billing.id,
                    )
                    managed.append(row)
                    names.add(created.name)
                    await self._upsert_cloud(row)
            elif len(managed) < target and blocked_slots:
                log_event(
                    self.logger,
                    "cloud.capacity.wait_deleting",
                    account_id=account_id,
                    org_id=organization_id,
                    active_clouds=len(managed),
                    deleting_slots=blocked_slots,
                    target=target,
                )

        return {"active_clouds": len(managed), "deleted_clouds": deleted}

    @staticmethod
    def _select_billing_for_organization(
        *,
        organization_id: str,
        active_billing: list,
        clouds: list,
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

    async def _cleanup_loop(self) -> None:
        while not self._cleanup_stop.is_set():
            try:
                await self._cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log_error(self.logger, "cleanup.error", exc)
            await asyncio.sleep(self.settings.cleanup_interval_seconds)

    async def _cleanup_once(self) -> None:
        accounts = await self._list_all_accounts()
        if not accounts:
            return

        # 1. Keep account directory fresh without touching clouds.
        for account in accounts:
            try:
                await self.refresh_account_directory(account["id"], branch_id=account["branch_id"])
            except Exception as exc:  # noqa: BLE001
                log_error(self.logger, "cleanup.directory_refresh.error", exc, account_id=account["id"])

        # 2. Remove orphan / stuck IP addresses.
        stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=20)
        async with self.db.session() as session:
            repo = AddressRepository(session)
            pending = await repo.list_non_terminal_records()

        if not pending:
            return

        pending_by_account: dict[str, list] = {}
        for item in pending:
            pending_by_account.setdefault(item.account_id, []).append(item)

        for account_id, items in pending_by_account.items():
            account = await self._get_account(account_id)
            if account is None:
                continue
            async with YcClient(
                settings=self.settings,
                oauth_token=account.oauth_token,
                proxy_url=account.proxy_url,
                semaphore=self.semaphore,
                logger=self.logger,
            ) as client:
                vpc = VpcApi(client=client, settings=self.settings, logger=self.logger)
                compute = ComputeApi(client=client, settings=self.settings, logger=self.logger)
                for item in items:
                    try:
                        if item.address_id.startswith(VM_RECORD_PREFIX):
                            existing = await compute.get_instance(strip_vm_record_id(item.address_id))
                        else:
                            existing = await vpc.get_address(item.address_id)
                        if existing is None:
                            await self._mark_deleted(account_id, item.address_id)
                            continue

                        if item.created_at < stale_cutoff:
                            await self._delete_tracked_resource(account_id, item.address_id, vpc, compute)
                    except Exception as exc:  # noqa: BLE001
                        log_error(self.logger, "cleanup.address.error", exc, account_id=account_id, address_id=item.address_id)
                        await self._mark_failed(account_id, item.address_id)

    async def _delete_tracked_resource(
        self,
        account_id: str,
        address_id: str,
        vpc_api: VpcApi,
        compute_api: ComputeApi,
    ) -> bool:
        if await self._address_is_matched(account_id, address_id):
            return False

        if address_id.startswith(VM_RECORD_PREFIX):
            await compute_api.stop_instance(strip_vm_record_id(address_id))
        else:
            await vpc_api.delete_address(address_id)
        await self._mark_deleted(account_id, address_id)
        return True

    async def _get_account(self, account_id: str):
        async with self.db.session() as session:
            repo = AccountRepository(session)
            return await repo.get_account(account_id)

    async def _upsert_cloud(self, cloud: ManagedCloud) -> None:
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

    async def _remove_cloud(self, account_id: str, cloud_id: str) -> None:
        async with self.db.session() as session:
            repo = AccountRepository(session)
            await repo.remove_cloud(account_id, cloud_id)
            await session.commit()

    async def _mark_deleted(self, account_id: str, address_id: str) -> None:
        async with self.db.session() as session:
            repo = AddressRepository(session)
            await repo.mark_deleted(account_id, address_id)
            await session.commit()

    async def _mark_failed(self, account_id: str, address_id: str) -> None:
        async with self.db.session() as session:
            repo = AddressRepository(session)
            await repo.mark_failed(account_id, address_id)
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

    @staticmethod
    def _cloud_name(organization_id: str, names: set[str], offset: int) -> str:
        import re

        suffix = re.sub(r"[^a-z0-9]", "", organization_id.lower())[-6:] or "org"
        seq = offset + 1
        for _ in range(200):
            candidate = f"hunter-{suffix}-{seq:03d}"
            if candidate not in names:
                return candidate
            seq += 1
        return f"hunter-{suffix}-{int(datetime.now(tz=timezone.utc).timestamp())}"
