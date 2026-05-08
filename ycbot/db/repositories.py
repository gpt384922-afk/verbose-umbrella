from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ycbot.db.enums import AddressLifecycle, CloudState, HuntCloudStatus, HuntStatus
from ycbot.db.models import (
    Account,
    AddressRecord,
    BillingAccount,
    BotBranch,
    Cloud,
    HuntCloudProgress,
    HuntJob,
    HuntMatch,
    HuntScope,
    Organization,
)


@dataclass(slots=True)
class HuntScopeInput:
    account_id: str
    organization_external_id: str


def _branch_filter(column, branch_id: str | None):
    if branch_id is None:
        return column.is_(None)
    return column == branch_id


class BranchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_branch(
        self,
        *,
        name: str,
        bot_token: str,
        owner_chat_id: int,
        bot_username: str | None,
    ) -> BotBranch:
        existing = await self.get_active_branch_by_token(bot_token)
        if existing is not None:
            raise ValueError("этот bot token уже подключен как активный филиал")

        branch = BotBranch(
            name=name,
            bot_token=bot_token,
            owner_chat_id=owner_chat_id,
            bot_username=bot_username,
            is_active=True,
        )
        self.session.add(branch)
        await self.session.flush()
        return branch

    async def list_branches(self, *, include_inactive: bool = True) -> list[BotBranch]:
        stmt = select(BotBranch).order_by(BotBranch.created_at.desc())
        if not include_inactive:
            stmt = stmt.where(BotBranch.is_active.is_(True))
        rows = await self.session.scalars(stmt)
        return list(rows)

    async def active_branches(self) -> list[BotBranch]:
        return await self.list_branches(include_inactive=False)

    async def get_branch(self, branch_id: str) -> BotBranch | None:
        return await self.session.get(BotBranch, branch_id)

    async def get_active_branch_by_token(self, bot_token: str) -> BotBranch | None:
        return await self.session.scalar(
            select(BotBranch)
            .where(
                BotBranch.bot_token == bot_token,
                BotBranch.is_active.is_(True),
            )
            .limit(1)
        )

    async def deactivate_branch(self, branch_id: str) -> bool:
        branch = await self.get_branch(branch_id)
        if branch is None or not branch.is_active:
            return False
        branch.is_active = False
        await self.session.flush()
        return True


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_account(
        self,
        *,
        branch_id: str | None,
        name: str,
        oauth_token: str,
        email: str | None,
        password: str | None,
        secret: str | None,
        proxy_url: str | None,
    ) -> Account:
        account = Account(
            branch_id=branch_id,
            name=name,
            oauth_token=oauth_token,
            email=email,
            password=password,
            secret=secret,
            proxy_url=proxy_url,
            is_active=True,
        )
        self.session.add(account)
        await self.session.flush()
        return account

    async def list_accounts(
        self,
        *,
        branch_id: str | None,
        include_inactive: bool = False,
    ) -> list[Account]:
        stmt = select(Account).order_by(Account.created_at.desc())
        stmt = stmt.where(_branch_filter(Account.branch_id, branch_id))
        if not include_inactive:
            stmt = stmt.where(Account.is_active.is_(True))
        rows = await self.session.scalars(stmt)
        return list(rows)

    async def list_all_accounts(self, *, include_inactive: bool = False) -> list[Account]:
        stmt = select(Account).order_by(Account.created_at.desc())
        if not include_inactive:
            stmt = stmt.where(Account.is_active.is_(True))
        rows = await self.session.scalars(stmt)
        return list(rows)

    async def get_account(self, account_id: str) -> Account | None:
        return await self.session.get(Account, account_id)

    async def get_account_for_branch(self, account_id: str, branch_id: str | None) -> Account | None:
        return await self.session.scalar(
            select(Account).where(
                Account.id == account_id,
                _branch_filter(Account.branch_id, branch_id),
            )
        )

    async def deactivate_account(self, account_id: str) -> None:
        account = await self.get_account(account_id)
        if account is None:
            return
        suffix = f" [deleted:{account_id[:8]}]"
        account.name = f"{account.name[:128 - len(suffix)]}{suffix}"
        account.is_active = False
        await self.session.flush()

    async def replace_organizations(self, account_id: str, organizations: list[dict]) -> None:
        await self.session.execute(delete(Organization).where(Organization.account_id == account_id))
        for item in organizations:
            ext_id = item.get("id")
            if not ext_id:
                continue
            self.session.add(
                Organization(
                    account_id=account_id,
                    external_id=ext_id,
                    name=item.get("name") or ext_id,
                    state=item.get("status") or item.get("state") or "UNKNOWN",
                )
            )
        await self.session.flush()

    async def replace_billing_accounts(self, account_id: str, billing_accounts: list[dict]) -> None:
        await self.session.execute(delete(BillingAccount).where(BillingAccount.account_id == account_id))
        for item in billing_accounts:
            ext_id = item.get("id")
            if not ext_id:
                continue
            self.session.add(
                BillingAccount(
                    account_id=account_id,
                    external_id=ext_id,
                    name=item.get("name") or ext_id,
                    active=bool(item.get("active")),
                )
            )
        await self.session.flush()

    async def upsert_cloud(
        self,
        *,
        account_id: str,
        organization_external_id: str,
        external_id: str,
        name: str,
        state: CloudState,
        billing_account_external_id: str | None,
        folder_external_id: str | None,
        marked_for_deletion: bool,
    ) -> Cloud:
        existing = await self.session.scalar(
            select(Cloud).where(
                Cloud.account_id == account_id,
                Cloud.external_id == external_id,
            )
        )
        if existing:
            existing.organization_external_id = organization_external_id
            existing.name = name
            existing.state = state
            existing.billing_account_external_id = billing_account_external_id
            existing.folder_external_id = folder_external_id
            existing.marked_for_deletion = marked_for_deletion
            existing.last_seen_at = datetime.now(tz=timezone.utc)
            await self.session.flush()
            return existing

        cloud = Cloud(
            account_id=account_id,
            organization_external_id=organization_external_id,
            external_id=external_id,
            name=name,
            state=state,
            billing_account_external_id=billing_account_external_id,
            folder_external_id=folder_external_id,
            marked_for_deletion=marked_for_deletion,
        )
        self.session.add(cloud)
        await self.session.flush()
        return cloud

    async def remove_cloud(self, account_id: str, cloud_external_id: str) -> None:
        await self.session.execute(
            delete(Cloud).where(
                Cloud.account_id == account_id,
                Cloud.external_id == cloud_external_id,
            )
        )

    async def list_organizations(self, account_id: str) -> list[Organization]:
        rows = await self.session.scalars(
            select(Organization)
            .where(Organization.account_id == account_id)
            .order_by(Organization.name.asc())
        )
        return list(rows)

    async def list_clouds(
        self,
        account_id: str,
        organization_external_id: str | None = None,
    ) -> list[Cloud]:
        stmt = select(Cloud).where(Cloud.account_id == account_id)
        if organization_external_id:
            stmt = stmt.where(Cloud.organization_external_id == organization_external_id)
        stmt = stmt.order_by(Cloud.name.asc())
        rows = await self.session.scalars(stmt)
        return list(rows)

    async def list_active_billing_accounts(self, account_id: str) -> list[BillingAccount]:
        rows = await self.session.scalars(
            select(BillingAccount)
            .where(
                BillingAccount.account_id == account_id,
                BillingAccount.active.is_(True),
            )
            .order_by(BillingAccount.created_at.asc())
        )
        return list(rows)

    async def get_cloud(self, account_id: str, cloud_external_id: str) -> Cloud | None:
        return await self.session.scalar(
            select(Cloud).where(
                Cloud.account_id == account_id,
                Cloud.external_id == cloud_external_id,
            )
        )

    async def list_account_organization_pairs(
        self,
        account_ids: list[str],
        *,
        branch_id: str | None,
    ) -> list[tuple[Account, Organization]]:
        stmt = (
            select(Account, Organization)
            .join(Organization, Organization.account_id == Account.id)
            .where(
                Account.id.in_(account_ids),
                Account.is_active.is_(True),
                _branch_filter(Account.branch_id, branch_id),
            )
            .order_by(Account.name.asc(), Organization.name.asc())
        )
        rows = await self.session.execute(stmt)
        return list(rows.tuples())


class HuntRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_job(
        self,
        *,
        branch_id: str | None,
        requested_by_chat_id: int,
        target_prefixes: list[str],
        requested_ip_count: int,
        scopes: list[HuntScopeInput],
    ) -> HuntJob:
        job = HuntJob(
            branch_id=branch_id,
            status=HuntStatus.PENDING,
            requested_by_chat_id=requested_by_chat_id,
            target_prefixes=target_prefixes,
            requested_ip_count=requested_ip_count,
        )
        self.session.add(job)
        await self.session.flush()

        for scope in scopes:
            self.session.add(
                HuntScope(
                    job_id=job.id,
                    account_id=scope.account_id,
                    organization_external_id=scope.organization_external_id,
                )
            )

        await self.session.flush()
        return job

    async def get_job(self, job_id: str) -> HuntJob | None:
        return await self.session.get(HuntJob, job_id)

    async def get_job_for_branch(self, job_id: str, branch_id: str | None) -> HuntJob | None:
        return await self.session.scalar(
            select(HuntJob).where(
                HuntJob.id == job_id,
                _branch_filter(HuntJob.branch_id, branch_id),
            )
        )

    async def list_active_jobs(self, *, branch_id: str | None = None, all_branches: bool = True) -> list[HuntJob]:
        stmt = (
            select(HuntJob)
            .where(HuntJob.status.in_([HuntStatus.PENDING, HuntStatus.RUNNING]))
            .order_by(HuntJob.created_at.desc())
        )
        if not all_branches:
            stmt = stmt.where(_branch_filter(HuntJob.branch_id, branch_id))
        rows = await self.session.scalars(stmt)
        return list(rows)

    async def list_job_scopes(self, job_id: str) -> list[HuntScope]:
        rows = await self.session.scalars(
            select(HuntScope)
            .where(HuntScope.job_id == job_id)
            .order_by(HuntScope.created_at.asc())
        )
        return list(rows)

    async def set_job_running(
        self,
        job_id: str,
        *,
        progress_message_chat_id: int | None,
        progress_message_id: int | None,
    ) -> None:
        await self.session.execute(
            update(HuntJob)
            .where(HuntJob.id == job_id)
            .values(
                status=HuntStatus.RUNNING,
                started_at=datetime.now(tz=timezone.utc),
                progress_message_chat_id=progress_message_chat_id,
                progress_message_id=progress_message_id,
            )
        )

    async def set_job_status(
        self,
        job_id: str,
        *,
        status: HuntStatus,
        error_text: str | None = None,
    ) -> None:
        payload = {
            "status": status,
            "error_text": error_text,
        }
        if status in {HuntStatus.COMPLETED, HuntStatus.FAILED, HuntStatus.CANCELLED}:
            payload["finished_at"] = datetime.now(tz=timezone.utc)
        await self.session.execute(
            update(HuntJob)
            .where(HuntJob.id == job_id)
            .values(**payload)
        )

    async def increment_match_count(self, job_id: str) -> int:
        job = await self.session.get(HuntJob, job_id, with_for_update=True)
        if job is None:
            return 0
        job.match_count += 1
        await self.session.flush()
        return job.match_count

    async def add_match(
        self,
        *,
        job_id: str,
        account_id: str,
        organization_external_id: str,
        cloud_external_id: str,
        address_id: str,
        ip_address: str,
        matched_prefix: str,
        preexisting: bool,
    ) -> tuple[HuntMatch, bool]:
        existing = await self.session.scalar(
            select(HuntMatch).where(
                HuntMatch.job_id == job_id,
                HuntMatch.address_id == address_id,
            )
        )
        if existing:
            return existing, False

        match = HuntMatch(
            job_id=job_id,
            account_id=account_id,
            organization_external_id=organization_external_id,
            cloud_external_id=cloud_external_id,
            address_id=address_id,
            ip_address=ip_address,
            matched_prefix=matched_prefix,
            preexisting=preexisting,
        )
        self.session.add(match)
        await self.session.flush()
        return match, True

    async def list_matches(self, job_id: str) -> list[HuntMatch]:
        rows = await self.session.scalars(
            select(HuntMatch)
            .where(HuntMatch.job_id == job_id)
            .order_by(HuntMatch.found_at.asc())
        )
        return list(rows)

    async def upsert_cloud_progress(
        self,
        *,
        job_id: str,
        account_id: str,
        organization_external_id: str,
        cloud_external_id: str,
        status: HuntCloudStatus,
        cycles_attempted: int,
        notes: str | None = None,
        last_error: str | None = None,
        completed: bool = False,
    ) -> HuntCloudProgress:
        progress = await self.session.scalar(
            select(HuntCloudProgress).where(
                HuntCloudProgress.job_id == job_id,
                HuntCloudProgress.account_id == account_id,
                HuntCloudProgress.cloud_external_id == cloud_external_id,
            )
        )
        if progress is None:
            progress = HuntCloudProgress(
                job_id=job_id,
                account_id=account_id,
                organization_external_id=organization_external_id,
                cloud_external_id=cloud_external_id,
                status=status,
                cycles_attempted=cycles_attempted,
                notes=notes,
                last_error=last_error,
                completed_at=datetime.now(tz=timezone.utc) if completed else None,
            )
            self.session.add(progress)
            await self.session.flush()
            return progress

        progress.status = status
        progress.cycles_attempted = cycles_attempted
        progress.notes = notes
        progress.last_error = last_error
        if completed:
            progress.completed_at = datetime.now(tz=timezone.utc)
        await self.session.flush()
        return progress

    async def list_cloud_progress(self, job_id: str) -> list[HuntCloudProgress]:
        rows = await self.session.scalars(
            select(HuntCloudProgress)
            .where(HuntCloudProgress.job_id == job_id)
            .order_by(HuntCloudProgress.updated_at.desc())
        )
        return list(rows)


class AddressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def mark_created(
        self,
        *,
        account_id: str,
        job_id: str | None,
        cloud_external_id: str,
        folder_external_id: str,
        address_id: str,
        ip_address: str | None,
    ) -> AddressRecord:
        existing = await self.session.scalar(
            select(AddressRecord).where(
                AddressRecord.account_id == account_id,
                AddressRecord.address_id == address_id,
            )
        )
        if existing:
            if existing.lifecycle == AddressLifecycle.MATCHED:
                return existing
            existing.job_id = job_id
            existing.cloud_external_id = cloud_external_id
            existing.folder_external_id = folder_external_id
            existing.ip_address = ip_address
            existing.lifecycle = AddressLifecycle.CREATED
            existing.deleted_at = None
            await self.session.flush()
            return existing

        row = AddressRecord(
            account_id=account_id,
            job_id=job_id,
            cloud_external_id=cloud_external_id,
            folder_external_id=folder_external_id,
            address_id=address_id,
            ip_address=ip_address,
            lifecycle=AddressLifecycle.CREATED,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def mark_match(self, account_id: str, address_id: str, ip_address: str) -> None:
        await self.session.execute(
            update(AddressRecord)
            .where(
                AddressRecord.account_id == account_id,
                AddressRecord.address_id == address_id,
            )
            .values(lifecycle=AddressLifecycle.MATCHED, ip_address=ip_address)
        )

    async def mark_deleted(self, account_id: str, address_id: str) -> None:
        if await self.address_is_matched(account_id, address_id):
            return
        await self.session.execute(
            update(AddressRecord)
            .where(
                AddressRecord.account_id == account_id,
                AddressRecord.address_id == address_id,
                AddressRecord.lifecycle != AddressLifecycle.MATCHED,
            )
            .values(
                lifecycle=AddressLifecycle.DELETED,
                deleted_at=datetime.now(tz=timezone.utc),
            )
        )

    async def mark_failed(self, account_id: str, address_id: str) -> None:
        if await self.address_is_matched(account_id, address_id):
            return
        await self.session.execute(
            update(AddressRecord)
            .where(
                AddressRecord.account_id == account_id,
                AddressRecord.address_id == address_id,
            )
            .values(lifecycle=AddressLifecycle.FAILED)
        )

    async def list_non_terminal_records(self, account_id: str | None = None) -> list[AddressRecord]:
        stmt = select(AddressRecord).where(
            AddressRecord.lifecycle.in_([AddressLifecycle.CREATED, AddressLifecycle.FAILED])
        )
        if account_id:
            stmt = stmt.where(AddressRecord.account_id == account_id)
        rows = await self.session.scalars(stmt.order_by(AddressRecord.created_at.asc()))
        return list(rows)

    async def count_job_records(self, job_id: str) -> int:
        value = await self.session.scalar(
            select(func.count()).select_from(AddressRecord).where(AddressRecord.job_id == job_id)
        )
        return int(value or 0)

    async def address_is_matched(self, account_id: str, address_id: str) -> bool:
        row_id = await self.session.scalar(
            select(AddressRecord.id)
            .where(
                AddressRecord.account_id == account_id,
                AddressRecord.address_id == address_id,
                AddressRecord.lifecycle == AddressLifecycle.MATCHED,
            )
            .limit(1)
        )
        if row_id is not None:
            return True

        match_id = await self.session.scalar(
            select(HuntMatch.id)
            .where(
                HuntMatch.account_id == account_id,
                HuntMatch.address_id == address_id,
            )
            .limit(1)
        )
        return match_id is not None

    async def cloud_has_matched_address(self, account_id: str, cloud_external_id: str) -> bool:
        row_id = await self.session.scalar(
            select(AddressRecord.id)
            .where(
                AddressRecord.account_id == account_id,
                AddressRecord.cloud_external_id == cloud_external_id,
                AddressRecord.lifecycle == AddressLifecycle.MATCHED,
            )
            .limit(1)
        )
        if row_id is not None:
            return True

        match_id = await self.session.scalar(
            select(HuntMatch.id)
            .where(
                HuntMatch.account_id == account_id,
                HuntMatch.cloud_external_id == cloud_external_id,
            )
            .limit(1)
        )
        return match_id is not None


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def account_overview(self, account_id: str) -> dict[str, int]:
        org_count = await self.session.scalar(
            select(func.count()).select_from(Organization).where(Organization.account_id == account_id)
        )
        cloud_count = await self.session.scalar(
            select(func.count()).select_from(Cloud).where(Cloud.account_id == account_id)
        )
        active_billing_count = await self.session.scalar(
            select(func.count())
            .select_from(BillingAccount)
            .where(and_(BillingAccount.account_id == account_id, BillingAccount.active.is_(True)))
        )
        return {
            "organizations": int(org_count or 0),
            "clouds": int(cloud_count or 0),
            "active_billing": int(active_billing_count or 0),
        }
