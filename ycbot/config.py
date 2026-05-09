from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = Field(default="production", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    tg_token: SecretStr = Field(alias="TG_TOKEN")
    tg_chat_id: int | None = Field(default=None, alias="TG_CHAT_ID")
    tg_chat_ids_raw: str = Field(default="", alias="TG_CHAT_IDS")
    tg_match_message_effect_id: str | None = Field(default="5427168083074628963", alias="TG_MATCH_MESSAGE_EFFECT_ID")

    database_dsn: str = Field(
        default="postgresql+asyncpg://postgres:postgres@postgres:5432/ychunter",
        alias="DATABASE_DSN",
    )

    yc_iam_url: str = Field(
        default="https://iam.api.cloud.yandex.net/iam/v1/tokens",
        alias="YC_IAM_URL",
    )
    yc_org_url: str = Field(
        default=(
            "https://organization-manager.api.cloud.yandex.net/"
            "organization-manager/v1/organizations"
        ),
        alias="YC_ORGANIZATION_URL",
    )
    yc_cloud_url: str = Field(
        default="https://resource-manager.api.cloud.yandex.net/resource-manager/v1/clouds",
        alias="YC_CLOUD_URL",
    )
    yc_folder_url: str = Field(
        default="https://resource-manager.api.cloud.yandex.net/resource-manager/v1/folders",
        alias="YC_FOLDER_URL",
    )
    yc_billing_url: str = Field(
        default="https://billing.api.cloud.yandex.net/billing/v1/billingAccounts",
        alias="YC_BILLING_URL",
    )
    yc_vpc_address_url: str = Field(
        default="https://vpc.api.cloud.yandex.net/vpc/v1/addresses",
        alias="YC_VPC_ADDRESS_URL",
    )
    yc_vpc_subnet_url: str = Field(
        default="https://vpc.api.cloud.yandex.net/vpc/v1/subnets",
        alias="YC_VPC_SUBNET_URL",
    )
    yc_vpc_network_url: str = Field(
        default="https://vpc.api.cloud.yandex.net/vpc/v1/networks",
        alias="YC_VPC_NETWORK_URL",
    )
    yc_compute_instance_url: str = Field(
        default="https://compute.api.cloud.yandex.net/compute/v1/instances",
        alias="YC_COMPUTE_INSTANCE_URL",
    )
    yc_compute_image_url: str = Field(
        default="https://compute.api.cloud.yandex.net/compute/v1/images",
        alias="YC_COMPUTE_IMAGE_URL",
    )
    yc_operation_url: str = Field(
        default="https://operation.api.cloud.yandex.net/operations",
        alias="YC_OPERATION_URL",
    )

    yc_max_concurrency: int = Field(default=8, alias="YC_MAX_CONCURRENCY")
    yc_request_retries: int = Field(default=5, alias="YC_REQUEST_RETRIES")
    yc_retry_base_delay: float = Field(default=0.8, alias="YC_RETRY_BASE_DELAY")
    yc_retry_max_delay: float = Field(default=15.0, alias="YC_RETRY_MAX_DELAY")
    yc_cloud_prepare_retries: int = Field(default=8, alias="YC_CLOUD_PREPARE_RETRIES")
    yc_cloud_prepare_retry_base_delay: float = Field(default=3.0, alias="YC_CLOUD_PREPARE_RETRY_BASE_DELAY")
    yc_cloud_prepare_retry_max_delay: float = Field(default=30.0, alias="YC_CLOUD_PREPARE_RETRY_MAX_DELAY")
    yc_billing_code9_as_bound: bool = Field(default=False, alias="YC_BILLING_CODE9_AS_BOUND")
    yc_cloud_delete_poll_seconds: int = Field(default=300, alias="YC_CLOUD_DELETE_POLL_SECONDS")
    yc_cloud_delete_timeout_seconds: int = Field(default=259200, alias="YC_CLOUD_DELETE_TIMEOUT_SECONDS")
    yc_cloud_slot_wait_poll_seconds: int = Field(default=30, alias="YC_CLOUD_SLOT_WAIT_POLL_SECONDS")
    yc_cloud_slot_wait_timeout_seconds: int = Field(default=259200, alias="YC_CLOUD_SLOT_WAIT_TIMEOUT_SECONDS")

    hunt_cloud_target_count: int = Field(default=5, alias="HUNT_CLOUD_TARGET_COUNT")
    hunt_cycles_per_cloud: int = Field(default=4, alias="HUNT_CYCLES_PER_CLOUD")
    hunt_ips_per_cycle: int = Field(default=2, alias="HUNT_IPS_PER_CYCLE")
    hunt_poll_min_seconds: int = Field(default=3, alias="HUNT_POLL_MIN_SECONDS")
    hunt_poll_max_seconds: int = Field(default=5, alias="HUNT_POLL_MAX_SECONDS")
    hunt_poll_timeout_seconds: int = Field(default=30, alias="HUNT_POLL_TIMEOUT_SECONDS")
    hunt_worker_count: int = Field(default=8, alias="HUNT_WORKER_COUNT")
    hunt_vm_batch_size: int = Field(default=8, alias="HUNT_VM_BATCH_SIZE")
    hunt_vm_delete_delay_seconds: float = Field(default=1.0, alias="HUNT_VM_DELETE_DELAY_SECONDS")
    hunt_vm_poll_seconds: int = Field(default=5, alias="HUNT_VM_POLL_SECONDS")
    hunt_vm_poll_timeout_seconds: int = Field(default=180, alias="HUNT_VM_POLL_TIMEOUT_SECONDS")
    hunt_vm_zones_raw: str = Field(default="ru-central1-a,ru-central1-d", alias="HUNT_VM_ZONES")
    hunt_vm_subnet_cidr_blocks_raw: str = Field(
        default="10.10.0.0/24,10.20.0.0/24",
        alias="HUNT_VM_SUBNET_CIDR_BLOCKS",
    )
    hunt_vm_image_family: str = Field(default="debian-12", alias="HUNT_VM_IMAGE_FAMILY")
    hunt_vm_image_folder_id: str = Field(default="standard-images", alias="HUNT_VM_IMAGE_FOLDER_ID")
    hunt_vm_platform_id: str = Field(default="standard-v4a", alias="HUNT_VM_PLATFORM_ID")
    hunt_vm_disk_type_id: str = Field(default="network-hdd", alias="HUNT_VM_DISK_TYPE_ID")
    hunt_vm_disk_size_gb: int = Field(default=10, alias="HUNT_VM_DISK_SIZE_GB")
    hunt_vm_cores: int = Field(default=2, alias="HUNT_VM_CORES")
    hunt_vm_core_fraction: int = Field(default=20, alias="HUNT_VM_CORE_FRACTION")
    hunt_vm_memory_gb: int = Field(default=1, alias="HUNT_VM_MEMORY_GB")
    hunt_vm_username: str = Field(default="user", alias="HUNT_VM_USERNAME")

    cleanup_interval_seconds: int = Field(default=180, alias="CLEANUP_INTERVAL_SECONDS")
    default_zones_raw: str = Field(
        default="ru-central1-a,ru-central1-b,ru-central1-d",
        alias="YC_DEFAULT_ZONES",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return str(value).upper().strip()

    @property
    def allowed_chat_ids(self) -> tuple[int, ...]:
        parsed: list[int] = []
        if self.tg_chat_ids_raw.strip():
            for chunk in self.tg_chat_ids_raw.split(","):
                token = chunk.strip()
                if token:
                    parsed.append(int(token))
        elif self.tg_chat_id is not None:
            parsed.append(self.tg_chat_id)

        if not parsed:
            raise RuntimeError("Set TG_CHAT_ID or TG_CHAT_IDS")
        return tuple(dict.fromkeys(parsed))

    @property
    def default_zones(self) -> list[str]:
        return _split_csv(self.default_zones_raw)

    @property
    def hunt_vm_zones(self) -> list[str]:
        return _split_csv(self.hunt_vm_zones_raw)

    @property
    def hunt_vm_subnet_cidr_blocks(self) -> list[str]:
        return _split_csv(self.hunt_vm_subnet_cidr_blocks_raw)


def _split_csv(value: str) -> list[str]:
    chunks: Iterable[str] = (item.strip() for item in value.split(","))
    return [item for item in chunks if item]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
