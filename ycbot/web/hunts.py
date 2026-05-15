from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select

from ycbot.config import Settings
from ycbot.core.hunter import HunterEngine
from ycbot.core.scheduler import HuntScheduler, HuntStartRequest, HuntStartScope
from ycbot.core.state_manager import StateManager
from ycbot.db.models import Account, BillingAccount, Organization
from ycbot.db.session import Database
from ycbot.web.schemas import (
    HuntPreflightCapacity,
    HuntPreflightCheck,
    HuntPreflightResponse,
    HuntStartRequestPayload,
)


class HuntsService:
    def __init__(self, *, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.logger = logging.getLogger("ycbot.web.hunts")
        self.semaphore = asyncio.Semaphore(settings.yc_max_concurrency)
        self.state = StateManager()
        self.hunter = HunterEngine(
            settings=settings,
            db=db,
            state=self.state,
            semaphore=self.semaphore,
            logger=self.logger,
        )
        self.scheduler = HuntScheduler(
            settings=settings,
            db=db,
            state=self.state,
            hunter=self.hunter,
            semaphore=self.semaphore,
            logger=self.logger,
        )

    async def start(self, payload: HuntStartRequestPayload) -> str:
        scopes = [
            HuntStartScope(account_id=item.account_id, organization_id=item.organization_id)
            for item in payload.scopes
        ]
        for account_id in sorted({item.account_id for item in scopes}):
            await self.scheduler.refresh_account_directory(account_id, branch_id=None)

        return await self.scheduler.start_hunt(
            HuntStartRequest(
                requested_by_chat_id=self.settings.allowed_chat_ids[0],
                branch_id=None,
                prefixes=payload.prefixes,
                target_count=payload.target_count,
                scopes=scopes,
                vm_config=payload.vm_config,
            )
        )

    async def preflight(self, payload: HuntStartRequestPayload) -> HuntPreflightResponse:
        checks: list[HuntPreflightCheck] = []
        target_count = payload.target_count
        selected_org_count = len(payload.scopes)
        max_per_cloud = self.settings.hunt_cloud_target_count

        def add_check(key: str, label: str, status: str, detail: str) -> None:
            checks.append(HuntPreflightCheck(key=key, label=label, status=status, detail=detail))

        if payload.scopes:
            add_check("scope", "Область запуска выбрана", "ready", f"{selected_org_count} организаций выбрано")
        else:
            add_check("scope", "Область запуска выбрана", "error", "Выбери минимум одну организацию")

        if payload.prefixes:
            add_check("prefixes", "Целевые префиксы", "ready", " / ".join(payload.prefixes))
        else:
            add_check("prefixes", "Целевые префиксы", "error", "Выбери минимум один префикс")

        if 1 <= target_count <= max_per_cloud:
            add_check("target_count", "Нужных VM", "ready", f"Остановиться после {target_count} нужных VM")
        else:
            add_check("target_count", "Нужных VM", "error", f"Укажи 1-{max_per_cloud} нужных VM")

        async with self.db.session() as session:
            account_ids = sorted({scope.account_id for scope in payload.scopes})
            accounts = {}
            if account_ids:
                account_rows = await session.scalars(select(Account).where(Account.id.in_(account_ids)))
                accounts = {account.id: account for account in account_rows}

            org_pairs = {(scope.account_id, scope.organization_id) for scope in payload.scopes}
            organizations = set()
            if org_pairs:
                org_rows = await session.execute(
                    select(Organization.account_id, Organization.external_id).where(
                        Organization.account_id.in_([account_id for account_id, _ in org_pairs]),
                        Organization.external_id.in_([org_id for _, org_id in org_pairs]),
                    )
                )
                organizations = {(str(account_id), str(org_id)) for account_id, org_id in org_rows}

            billing_counts = {}
            if account_ids:
                billing_rows = await session.execute(
                    select(BillingAccount.account_id, func.count())
                    .select_from(BillingAccount)
                    .where(BillingAccount.account_id.in_(account_ids), BillingAccount.active.is_(True))
                    .group_by(BillingAccount.account_id)
                )
                billing_counts = {str(account_id): int(count or 0) for account_id, count in billing_rows}

        for account_id in account_ids:
            account = accounts.get(account_id)
            if account is None:
                add_check("account", "Готовность аккаунта", "error", f"Аккаунт {account_id} не найден")
            elif not account.is_active:
                add_check("account", "Готовность аккаунта", "error", f"{account.name} неактивен")
            else:
                add_check("account", "Готовность аккаунта", "ready", f"{account.name} активен")

            billing_count = billing_counts.get(account_id, 0)
            if billing_count > 0:
                add_check("billing", "Кеш биллинга", "ready", f"{billing_count} активных биллинг-аккаунтов")
            else:
                add_check(
                    "billing",
                    "Кеш биллинга",
                    "warning",
                    f"Для {account_id} нет активного биллинга в кеше; при необходимости синхронизируй",
                )

        for account_id, organization_id in sorted(org_pairs):
            if (account_id, organization_id) in organizations:
                add_check("organization", "Готовность организации", "ready", f"{organization_id} есть в кеше")
            else:
                add_check(
                    "organization",
                    "Готовность организации",
                    "error",
                    f"{organization_id} не найдена в {account_id}",
                )

        ready = all(check.status != "error" for check in checks)
        vm_config = payload.vm_config or {}
        vm_profile = self._vm_profile(vm_config)
        estimated_vm_limit = selected_org_count * max(0, min(target_count, max_per_cloud))
        summary = (
            f"Готово искать {estimated_vm_limit} нужные VM в {selected_org_count} организациях"
            if ready
            else "Проверка нашла блокеры запуска"
        )
        return HuntPreflightResponse(
            ready=ready,
            summary=summary,
            capacity=HuntPreflightCapacity(
                selected_organizations=selected_org_count,
                target_count=target_count,
                max_vm_per_cloud=max_per_cloud,
                estimated_vm_limit=estimated_vm_limit,
            ),
            checks=checks,
            vm_profile=vm_profile,
        )

    def _vm_profile(self, vm_config: dict) -> str:
        platform_names = {
            "standard-v2": "Intel Cascade Lake",
            "standard-v3": "Intel Ice Lake",
            "standard-v4a": "AMD Zen 4",
        }
        platform_id = str(vm_config.get("platform_id") or self.settings.hunt_vm_platform_id)
        platform = platform_names.get(platform_id, platform_id)
        cores = vm_config.get("cores", self.settings.hunt_vm_cores)
        memory = vm_config.get("memory_gb", self.settings.hunt_vm_memory_gb)
        disk_type = vm_config.get("disk_type_id", self.settings.hunt_vm_disk_type_id)
        disk_size = vm_config.get("disk_size_gb", self.settings.hunt_vm_disk_size_gb)
        image = vm_config.get("image_family", self.settings.hunt_vm_image_family)
        return f"{platform} · {cores} vCPU · {memory} GB RAM · {disk_type} {disk_size} GB · {image}"
