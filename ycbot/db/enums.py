from __future__ import annotations

from enum import StrEnum


class CloudState(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETING = "deleting"
    DELETED = "deleted"
    UNKNOWN = "unknown"


class HuntStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HuntCloudStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    REPLACED = "replaced"
    FAILED = "failed"
    SKIPPED = "skipped"


class AddressLifecycle(StrEnum):
    CREATED = "created"
    MATCHED = "matched"
    DELETED = "deleted"
    FAILED = "failed"
