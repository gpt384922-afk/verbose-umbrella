from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class OverviewStats(ApiModel):
    active_hunts: int
    matched_vm: int
    managed_clouds: int
    checked_ip: int


class AccountCard(ApiModel):
    id: str
    name: str
    email: str | None
    branch_id: str | None
    is_active: bool
    has_proxy: bool
    organization_count: int
    cloud_count: int
    active_billing_count: int
    created_at: datetime | None


class AccountCreateRequest(ApiModel):
    name: str
    oauth_token: str
    email: str | None = None
    password: str | None = None
    secret: str | None = None
    proxy_url: str | None = None


class AccountCreateResponse(ApiModel):
    id: str


class AccountSyncResponse(ApiModel):
    organizations: int
    billing_accounts: int


class OrganizationCard(ApiModel):
    id: str
    external_id: str
    name: str
    state: str
    account_id: str
    account_name: str
    cloud_count: int


class CloudCard(ApiModel):
    id: str
    external_id: str
    name: str
    state: str
    account_id: str
    account_name: str
    organization_external_id: str
    folder_external_id: str | None
    marked_for_deletion: bool
    last_seen_at: datetime | None


class HuntCard(ApiModel):
    id: str
    status: str
    target_prefixes: list[str]
    requested_ip_count: int
    match_count: int
    checked_ip_count: int
    active_cloud_count: int
    vm_config: dict | None
    branch_id: str | None
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error_text: str | None


class HuntScopeRequest(ApiModel):
    account_id: str
    organization_id: str


class HuntStartRequestPayload(ApiModel):
    scopes: list[HuntScopeRequest]
    prefixes: list[str]
    target_count: int
    vm_config: dict | None = None


class HuntPreflightCapacity(ApiModel):
    selected_organizations: int
    target_count: int
    max_vm_per_cloud: int
    estimated_vm_limit: int


class HuntPreflightCheck(ApiModel):
    key: str
    label: str
    status: str
    detail: str


class HuntPreflightResponse(ApiModel):
    ready: bool
    summary: str
    capacity: HuntPreflightCapacity
    checks: list[HuntPreflightCheck]
    vm_profile: str


class HuntStartResponse(ApiModel):
    job_id: str


class MatchCard(ApiModel):
    id: str
    job_id: str
    account_id: str
    account_name: str
    cloud_external_id: str
    ip_address: str
    matched_prefix: str
    preexisting: bool
    found_at: datetime | None


class BranchCard(ApiModel):
    id: str
    name: str
    bot_username: str | None
    owner_chat_id: int
    is_active: bool
    account_count: int
    created_at: datetime | None


class VmSettings(ApiModel):
    platform_id: str
    image_family: str
    cores: int
    core_fraction: int
    memory_gb: float
    disk_type_id: str
    disk_size_gb: int
    vm_batch_size: int
    cloud_target_count: int
    username: str
    zones: list[str]


class DashboardPayload(ApiModel):
    overview: OverviewStats
    accounts: list[AccountCard]
    organizations: list[OrganizationCard]
    clouds: list[CloudCard]
    hunts: list[HuntCard]
    matches: list[MatchCard]
    branches: list[BranchCard]
    settings: VmSettings
