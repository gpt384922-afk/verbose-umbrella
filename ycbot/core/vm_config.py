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
ALLOWED_CORE_FRACTIONS = (5, 20, 50, 100)
ALLOWED_MEMORY_GB = (0.5, 1, 2, 4, 8)
ALLOWED_DISK_SIZE_GB = (5, 10, 20, 40, 80)


@dataclass(frozen=True, slots=True)
class VmHuntConfig:
    platform_id: str
    cores: int
    core_fraction: int
    memory_gb: float
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
            memory_gb=float(data.get("memory_gb") or default.memory_gb),
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


@dataclass(frozen=True, slots=True)
class VmConfigPreset:
    id: str
    title: str
    config: VmHuntConfig

    @property
    def button_label(self) -> str:
        memory = f"{self.config.memory_gb:g}"
        return (
            f"CPU: {self.config.cores} | RAM: {memory} GB | "
            f"{self.config.disk_type_label} {self.config.disk_size_gb} GB | "
            f"{self.config.core_fraction}% vCPU"
        )


VM_CONFIG_PRESETS = (
    VmConfigPreset(
        id="amd-small",
        title="AMD Zen 4 старт",
        config=VmHuntConfig(
            platform_id="standard-v4a",
            cores=2,
            core_fraction=20,
            memory_gb=1,
            disk_type_id="network-hdd",
            disk_size_gb=10,
        ),
    ),
    VmConfigPreset(
        id="cascade-tiny",
        title="Минимальный Cascade Lake",
        config=VmHuntConfig(
            platform_id="standard-v2",
            cores=2,
            core_fraction=5,
            memory_gb=0.5,
            disk_type_id="network-hdd",
            disk_size_gb=5,
        ),
    ),
    VmConfigPreset(
        id="ice-balanced",
        title="Intel Ice Lake баланс",
        config=VmHuntConfig(
            platform_id="standard-v3",
            cores=4,
            core_fraction=50,
            memory_gb=2,
            disk_type_id="network-ssd",
            disk_size_gb=20,
        ),
    ),
    VmConfigPreset(
        id="amd-mid",
        title="AMD Zen 4 средний",
        config=VmHuntConfig(
            platform_id="standard-v4a",
            cores=4,
            core_fraction=20,
            memory_gb=4,
            disk_type_id="network-hdd",
            disk_size_gb=40,
        ),
    ),
    VmConfigPreset(
        id="ice-fast",
        title="Intel Ice Lake SSD",
        config=VmHuntConfig(
            platform_id="standard-v3",
            cores=4,
            core_fraction=100,
            memory_gb=4,
            disk_type_id="network-ssd",
            disk_size_gb=40,
        ),
    ),
    VmConfigPreset(
        id="amd-large",
        title="AMD Zen 4 максимум",
        config=VmHuntConfig(
            platform_id="standard-v4a",
            cores=8,
            core_fraction=100,
            memory_gb=8,
            disk_type_id="network-ssd",
            disk_size_gb=80,
        ),
    ),
)


def vm_config_preset(preset_id: str) -> VmConfigPreset | None:
    return next((item for item in VM_CONFIG_PRESETS if item.id == preset_id), None)


def matching_vm_config_preset(config: VmHuntConfig) -> VmConfigPreset | None:
    return next((item for item in VM_CONFIG_PRESETS if item.config == config), None)
