from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ycbot.config import Settings
from ycbot.db.enums import HuntCloudStatus, HuntStatus
from ycbot.db.models import (
    Account,
    AddressRecord,
    BillingAccount,
    BotBranch,
    Cloud,
    HuntCloudProgress,
    HuntJob,
    HuntMatch,
    Organization,
)
from ycbot.web.schemas import (
    AccountCard,
    BranchCard,
    CloudCard,
    DashboardPayload,
    HuntCard,
    MatchCard,
    OrganizationCard,
    OverviewStats,
    VmSettings,
)


class DashboardService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def build(self, session: AsyncSession) -> DashboardPayload:
        accounts = await _all(session, select(Account).order_by(Account.created_at.desc()))
        organizations = await _all(session, select(Organization).order_by(Organization.name.asc()))
        clouds = await _all(session, select(Cloud).order_by(Cloud.last_seen_at.desc()).limit(100))
        hunts = await _all(session, select(HuntJob).order_by(HuntJob.created_at.desc()).limit(30))
        matches = await _all(session, select(HuntMatch).order_by(HuntMatch.found_at.desc()).limit(50))
        branches = await _all(session, select(BotBranch).order_by(BotBranch.created_at.desc()))

        account_names = {account.id: account.name for account in accounts}
        account_counts = await self._account_counts(session)
        organization_cloud_counts = await self._organization_cloud_counts(session)
        job_checked_counts = await self._job_address_counts(session, [job.id for job in hunts])
        job_cloud_counts = await self._job_active_cloud_counts(session, [job.id for job in hunts])
        branch_account_counts = await self._branch_account_counts(session)

        return DashboardPayload(
            overview=await self._overview(session),
            accounts=[
                AccountCard(
                    id=account.id,
                    name=account.name,
                    email=account.email,
                    branch_id=account.branch_id,
                    is_active=account.is_active,
                    has_proxy=bool(account.proxy_url),
                    organization_count=account_counts["organizations"].get(account.id, 0),
                    cloud_count=account_counts["clouds"].get(account.id, 0),
                    active_billing_count=account_counts["billing"].get(account.id, 0),
                    created_at=account.created_at,
                )
                for account in accounts
            ],
            organizations=[
                OrganizationCard(
                    id=organization.id,
                    external_id=organization.external_id,
                    name=organization.name,
                    state=organization.state,
                    account_id=organization.account_id,
                    account_name=account_names.get(organization.account_id, organization.account_id),
                    cloud_count=organization_cloud_counts.get(
                        (organization.account_id, organization.external_id),
                        0,
                    ),
                )
                for organization in organizations
            ],
            clouds=[
                CloudCard(
                    id=cloud.id,
                    external_id=cloud.external_id,
                    name=cloud.name,
                    state=str(cloud.state),
                    account_id=cloud.account_id,
                    account_name=account_names.get(cloud.account_id, cloud.account_id),
                    organization_external_id=cloud.organization_external_id,
                    folder_external_id=cloud.folder_external_id,
                    marked_for_deletion=cloud.marked_for_deletion,
                    last_seen_at=cloud.last_seen_at,
                )
                for cloud in clouds
            ],
            hunts=[
                HuntCard(
                    id=job.id,
                    status=str(job.status),
                    target_prefixes=list(job.target_prefixes),
                    requested_ip_count=job.requested_ip_count,
                    match_count=job.match_count,
                    checked_ip_count=job_checked_counts.get(job.id, 0),
                    active_cloud_count=job_cloud_counts.get(job.id, 0),
                    vm_config=job.vm_config,
                    branch_id=job.branch_id,
                    created_at=job.created_at,
                    started_at=job.started_at,
                    finished_at=job.finished_at,
                    error_text=job.error_text,
                )
                for job in hunts
            ],
            matches=[
                MatchCard(
                    id=match.id,
                    job_id=match.job_id,
                    account_id=match.account_id,
                    account_name=account_names.get(match.account_id, match.account_id),
                    cloud_external_id=match.cloud_external_id,
                    ip_address=match.ip_address,
                    matched_prefix=match.matched_prefix,
                    preexisting=match.preexisting,
                    found_at=match.found_at,
                )
                for match in matches
            ],
            branches=[
                BranchCard(
                    id=branch.id,
                    name=branch.name,
                    bot_username=branch.bot_username,
                    owner_chat_id=branch.owner_chat_id,
                    is_active=branch.is_active,
                    account_count=branch_account_counts.get(branch.id, 0),
                    created_at=branch.created_at,
                )
                for branch in branches
            ],
            settings=VmSettings(
                platform_id=self.settings.hunt_vm_platform_id,
                image_family=self.settings.hunt_vm_image_family,
                cores=self.settings.hunt_vm_cores,
                core_fraction=self.settings.hunt_vm_core_fraction,
                memory_gb=self.settings.hunt_vm_memory_gb,
                disk_type_id=self.settings.hunt_vm_disk_type_id,
                disk_size_gb=self.settings.hunt_vm_disk_size_gb,
                vm_batch_size=self.settings.hunt_vm_batch_size,
                cloud_target_count=self.settings.hunt_cloud_target_count,
                username=self.settings.hunt_vm_username,
                zones=self.settings.hunt_vm_zones,
            ),
        )

    async def _overview(self, session: AsyncSession) -> OverviewStats:
        active_hunts = await _count(
            session,
            select(func.count())
            .select_from(HuntJob)
            .where(HuntJob.status.in_([HuntStatus.PENDING, HuntStatus.RUNNING])),
        )
        matched_vm = await _count(session, select(func.count()).select_from(HuntMatch))
        managed_clouds = await _count(session, select(func.count()).select_from(Cloud))
        checked_ip = await _count(session, select(func.count()).select_from(AddressRecord))

        return OverviewStats(
            active_hunts=active_hunts,
            matched_vm=matched_vm,
            managed_clouds=managed_clouds,
            checked_ip=checked_ip,
        )

    async def _account_counts(self, session: AsyncSession) -> dict[str, dict[str, int]]:
        return {
            "organizations": await _count_by(session, Organization.account_id, Organization),
            "clouds": await _count_by(session, Cloud.account_id, Cloud),
            "billing": await _count_by(
                session,
                BillingAccount.account_id,
                BillingAccount,
                BillingAccount.active.is_(True),
            ),
        }

    async def _organization_cloud_counts(self, session: AsyncSession) -> dict[tuple[str, str], int]:
        rows = await session.execute(
            select(Cloud.account_id, Cloud.organization_external_id, func.count())
            .select_from(Cloud)
            .group_by(Cloud.account_id, Cloud.organization_external_id)
        )
        return {(str(account_id), str(org_id)): int(count or 0) for account_id, org_id, count in rows}

    async def _job_address_counts(self, session: AsyncSession, job_ids: list[str]) -> dict[str, int]:
        if not job_ids:
            return {}
        return await _count_by(
            session,
            AddressRecord.job_id,
            AddressRecord,
            AddressRecord.job_id.in_(job_ids),
        )

    async def _job_active_cloud_counts(self, session: AsyncSession, job_ids: list[str]) -> dict[str, int]:
        if not job_ids:
            return {}
        return await _count_by(
            session,
            HuntCloudProgress.job_id,
            HuntCloudProgress,
            HuntCloudProgress.job_id.in_(job_ids),
            HuntCloudProgress.status.in_([HuntCloudStatus.PENDING, HuntCloudStatus.RUNNING]),
        )

    async def _branch_account_counts(self, session: AsyncSession) -> dict[str, int]:
        return await _count_by(session, Account.branch_id, Account, Account.branch_id.is_not(None))


async def _all(session: AsyncSession, statement):
    rows = await session.scalars(statement)
    return list(rows)


async def _count(session: AsyncSession, statement) -> int:
    value = await session.scalar(statement)
    return int(value or 0)


async def _count_by(session: AsyncSession, key_column, model, *filters) -> dict[str, int]:
    statement = select(key_column, func.count()).select_from(model).group_by(key_column)
    for item in filters:
        statement = statement.where(item)
    rows: Iterable[tuple[str | None, int]] = (await session.execute(statement)).tuples()
    return {str(key): int(count or 0) for key, count in rows if key is not None}
