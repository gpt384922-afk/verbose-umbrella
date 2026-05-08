from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol


class CloudLifecycle(StrEnum):
    IDLE = "idle"
    INIT = "init"
    HUNTING = "hunting"
    SUCCESS = "success"
    FAILED = "failed"
    DELETING = "deleting"
    RECREATED = "recreated"


class TaskLifecycle(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class CloudState:
    account_id: str
    organization_id: str
    cloud_id: str
    cloud_name: str
    folder_id: str
    billing_account_id: str
    lifecycle: CloudLifecycle = CloudLifecycle.INIT
    attempts: int = 0
    checked_ips: set[str] = field(default_factory=set)
    last_ip: str | None = None
    error: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass(slots=True)
class MatchState:
    account_id: str
    organization_id: str
    cloud_id: str
    address_id: str
    ip: str
    prefix: str
    preexisting: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass(slots=True)
class HuntState:
    job_id: str
    prefixes: list[str]
    target_count: int
    status: TaskLifecycle = TaskLifecycle.PENDING
    cloud_states: dict[str, CloudState] = field(default_factory=dict)
    matches: list[MatchState] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None


class StatePersistence(Protocol):
    async def persist_hunt(self, state: HuntState) -> None:  # pragma: no cover - interface only
        ...


class NullPersistence:
    async def persist_hunt(self, state: HuntState) -> None:
        return


class StateManager:
    def __init__(self, persistence: StatePersistence | None = None) -> None:
        self._hunts: dict[str, HuntState] = {}
        self._lock = asyncio.Lock()
        self._persistence = persistence or NullPersistence()

    async def create_hunt(self, job_id: str, prefixes: list[str], target_count: int) -> HuntState:
        async with self._lock:
            state = HuntState(
                job_id=job_id,
                prefixes=list(prefixes),
                target_count=target_count,
                status=TaskLifecycle.PENDING,
            )
            self._hunts[job_id] = state
            await self._persistence.persist_hunt(state)
            return self._copy_hunt(state)

    async def set_task_status(self, job_id: str, status: TaskLifecycle, *, error: str | None = None) -> None:
        async with self._lock:
            state = self._require(job_id)
            state.status = status
            if status == TaskLifecycle.RUNNING and state.started_at is None:
                state.started_at = datetime.now(tz=timezone.utc)
            if status in {TaskLifecycle.COMPLETED, TaskLifecycle.FAILED, TaskLifecycle.CANCELLED}:
                state.completed_at = datetime.now(tz=timezone.utc)
            state.error = error
            await self._persistence.persist_hunt(state)

    async def register_cloud(self, job_id: str, cloud_state: CloudState) -> None:
        async with self._lock:
            state = self._require(job_id)
            state.cloud_states[cloud_state.cloud_id] = cloud_state
            await self._persistence.persist_hunt(state)

    async def set_cloud_lifecycle(
        self,
        job_id: str,
        cloud_id: str,
        lifecycle: CloudLifecycle,
        *,
        error: str | None = None,
    ) -> None:
        async with self._lock:
            state = self._require(job_id)
            cloud = state.cloud_states[cloud_id]
            cloud.lifecycle = lifecycle
            cloud.error = error
            cloud.updated_at = datetime.now(tz=timezone.utc)
            await self._persistence.persist_hunt(state)

    async def increment_cloud_attempt(self, job_id: str, cloud_id: str) -> int:
        async with self._lock:
            state = self._require(job_id)
            cloud = state.cloud_states[cloud_id]
            cloud.attempts += 1
            cloud.updated_at = datetime.now(tz=timezone.utc)
            await self._persistence.persist_hunt(state)
            return cloud.attempts

    async def add_checked_ip(self, job_id: str, cloud_id: str, ip: str) -> bool:
        async with self._lock:
            state = self._require(job_id)
            cloud = state.cloud_states[cloud_id]
            is_new = ip not in cloud.checked_ips
            cloud.checked_ips.add(ip)
            cloud.last_ip = ip
            cloud.updated_at = datetime.now(tz=timezone.utc)
            if is_new:
                await self._persistence.persist_hunt(state)
            return is_new

    async def add_match(self, job_id: str, match: MatchState) -> bool:
        async with self._lock:
            state = self._require(job_id)
            if any(existing.address_id == match.address_id for existing in state.matches):
                return False
            state.matches.append(match)
            cloud = state.cloud_states.get(match.cloud_id)
            if cloud:
                cloud.lifecycle = CloudLifecycle.SUCCESS
                cloud.last_ip = match.ip
                cloud.updated_at = datetime.now(tz=timezone.utc)
            await self._persistence.persist_hunt(state)
            return True

    async def get_hunt(self, job_id: str) -> HuntState | None:
        async with self._lock:
            state = self._hunts.get(job_id)
            if state is None:
                return None
            return self._copy_hunt(state)

    async def list_hunts(self) -> list[HuntState]:
        async with self._lock:
            return [self._copy_hunt(item) for item in self._hunts.values()]

    async def remove_hunt(self, job_id: str) -> None:
        async with self._lock:
            self._hunts.pop(job_id, None)

    async def total_matches(self, job_id: str) -> int:
        async with self._lock:
            state = self._require(job_id)
            return len(state.matches)

    async def cloud_match_count(self, job_id: str, cloud_id: str) -> int:
        async with self._lock:
            state = self._require(job_id)
            return sum(1 for item in state.matches if item.cloud_id == cloud_id)

    async def cloud_target_reached(self, job_id: str, cloud_id: str) -> bool:
        async with self._lock:
            state = self._require(job_id)
            return sum(1 for item in state.matches if item.cloud_id == cloud_id) >= state.target_count

    async def all_cloud_targets_reached(self, job_id: str) -> bool:
        async with self._lock:
            state = self._require(job_id)
            if not state.cloud_states:
                return False
            considered = False
            for cloud_id, cloud in state.cloud_states.items():
                if cloud.lifecycle in {CloudLifecycle.FAILED, CloudLifecycle.DELETING}:
                    continue
                considered = True
                count = sum(1 for item in state.matches if item.cloud_id == cloud_id)
                if count < state.target_count:
                    return False
            return considered

    async def scope_targets_reached(
        self,
        job_id: str,
        *,
        account_id: str,
        organization_id: str,
        target_cloud_count: int,
    ) -> bool:
        async with self._lock:
            state = self._require(job_id)
            reached = 0
            for cloud_id, cloud in state.cloud_states.items():
                if cloud.account_id != account_id or cloud.organization_id != organization_id:
                    continue
                count = sum(1 for item in state.matches if item.cloud_id == cloud_id)
                if count >= state.target_count:
                    reached += 1
            return reached >= target_cloud_count

    async def get_prefixes(self, job_id: str) -> list[str]:
        async with self._lock:
            state = self._require(job_id)
            return list(state.prefixes)

    def _require(self, job_id: str) -> HuntState:
        state = self._hunts.get(job_id)
        if state is None:
            raise KeyError(f"hunt state not found: {job_id}")
        return state

    @staticmethod
    def _copy_hunt(source: HuntState) -> HuntState:
        return HuntState(
            job_id=source.job_id,
            prefixes=list(source.prefixes),
            target_count=source.target_count,
            status=source.status,
            cloud_states={
                cloud_id: CloudState(
                    account_id=cloud.account_id,
                    organization_id=cloud.organization_id,
                    cloud_id=cloud.cloud_id,
                    cloud_name=cloud.cloud_name,
                    folder_id=cloud.folder_id,
                    billing_account_id=cloud.billing_account_id,
                    lifecycle=cloud.lifecycle,
                    attempts=cloud.attempts,
                    checked_ips=set(cloud.checked_ips),
                    last_ip=cloud.last_ip,
                    error=cloud.error,
                    updated_at=cloud.updated_at,
                )
                for cloud_id, cloud in source.cloud_states.items()
            },
            matches=[
                MatchState(
                    account_id=item.account_id,
                    organization_id=item.organization_id,
                    cloud_id=item.cloud_id,
                    address_id=item.address_id,
                    ip=item.ip,
                    prefix=item.prefix,
                    preexisting=item.preexisting,
                    created_at=item.created_at,
                )
                for item in source.matches
            ],
            error=source.error,
            created_at=source.created_at,
            started_at=source.started_at,
            completed_at=source.completed_at,
        )
