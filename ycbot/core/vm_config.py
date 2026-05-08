from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


PLATFORM_LABELS = {
    "standard-v4a": "AMD Zen 4",
    "standard-v3": "Intel Ice Lake",
    "standard-v2": "Intel Cascade Lake",
}

DISK_TYPE_LABELS = {
    "network-hdd": "HDD",
    "network-ssd": "SSD",
    "network-ssd-nonreplicated": "Нереплицируемый SSD",
}

ALLOWED_CORES = (2, 4, 8)
ALLOWED_CORE_FRACTIONS = (20, 50, 100)
ALLOWED_MEMORY_GB = (1, 2, 4, 8)
ALLOWED_DISK_SIZE_GB = (10, 20, 40, 80)


@dataclass(frozen=True, slots=True)
class VmHuntConfig:
    platform_id: str
    cores: int
    core_fraction: int
    memory_gb: int
    disk_type_id: str
    disk_size_gb: int

    @classmethod
    def default(cls) -> VmHuntConfig:
        return cls(
            platform_id="standard-v4a",
            cores=2,
            core_fraction=20,
            memory_gb=1,
            disk_type_id="network-hdd",
            disk_size_gb=10,
        )

    @classmethod
    def from_settings(cls, settings: Any) -> VmHuntConfig:
        return cls(
            platform_id=settings.hunt_vm_platform_id,
            cores=settings.hunt_vm_cores,
            core_fraction=settings.hunt_vm_core_fraction,
            memory_gb=settings.hunt_vm_memory_gb,
            disk_type_id=settings.hunt_vm_disk_type_id,
            disk_size_gb=settings.hunt_vm_disk_size_gb,
        ).validated()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> VmHuntConfig:
        default = cls.default()
        if not data:
            return default
        return cls(
            platform_id=str(data.get("platform_id") or default.platform_id),
            cores=int(data.get("cores") or default.cores),
            core_fraction=int(data.get("core_fraction") or default.core_fraction),
            memory_gb=int(data.get("memory_gb") or default.memory_gb),
            disk_type_id=str(data.get("disk_type_id") or default.disk_type_id),
            disk_size_gb=int(data.get("disk_size_gb") or default.disk_size_gb),
        ).validated()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validated(self) -> VmHuntConfig:
        if self.platform_id not in PLATFORM_LABELS:
            raise ValueError(f"unsupported VM platform: {self.platform_id}")
        if self.cores not in ALLOWED_CORES:
            raise ValueError(f"unsupported VM cores: {self.cores}")
        if self.core_fraction not in ALLOWED_CORE_FRACTIONS:
            raise ValueError(f"unsupported VM core fraction: {self.core_fraction}")
        if self.memory_gb not in ALLOWED_MEMORY_GB:
            raise ValueError(f"unsupported VM memory: {self.memory_gb}")
        if self.disk_type_id not in DISK_TYPE_LABELS:
            raise ValueError(f"unsupported VM disk type: {self.disk_type_id}")
        if self.disk_size_gb not in ALLOWED_DISK_SIZE_GB:
            raise ValueError(f"unsupported VM disk size: {self.disk_size_gb}")
        return self

    @property
    def platform_label(self) -> str:
        return PLATFORM_LABELS[self.platform_id]

    @property
    def disk_type_label(self) -> str:
        return DISK_TYPE_LABELS[self.disk_type_id]
