from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ycbot.db.base import Base, IdMixin, TimestampMixin
from ycbot.db.enums import AddressLifecycle, CloudState, HuntCloudStatus, HuntStatus


class Account(IdMixin, TimestampMixin, Base):
    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("bot_branches.id", ondelete="SET NULL"), nullable=True)
    oauth_token: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    center_proxy_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organizations: Mapped[list[Organization]] = relationship(back_populates="account")
    billing_accounts: Mapped[list[BillingAccount]] = relationship(back_populates="account")
    clouds: Mapped[list[Cloud]] = relationship(back_populates="account")
    branch: Mapped[BotBranch | None] = relationship(back_populates="accounts")


class BotBranch(IdMixin, TimestampMixin, Base):
    __tablename__ = "bot_branches"
    __table_args__ = (
        Index("ix_bot_branches_active", "is_active"),
        Index("ix_bot_branches_owner", "owner_chat_id"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    bot_token: Mapped[str] = mapped_column(Text, nullable=False)
    owner_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bot_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    accounts: Mapped[list[Account]] = relationship(back_populates="branch")


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_org_account_external"),
    )

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False, default="ACTIVE")

    account: Mapped[Account] = relationship(back_populates="organizations")


class BillingAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "billing_accounts"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_billing_account_external"),
    )

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    account: Mapped[Account] = relationship(back_populates="billing_accounts")


class Cloud(IdMixin, TimestampMixin, Base):
    __tablename__ = "clouds"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_cloud_account_external"),
        Index("ix_cloud_org", "account_id", "organization_external_id"),
    )

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    organization_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[CloudState] = mapped_column(
        Enum(CloudState, name="cloud_state"),
        nullable=False,
        default=CloudState.UNKNOWN,
    )
    billing_account_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    folder_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    marked_for_deletion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    account: Mapped[Account] = relationship(back_populates="clouds")


class HuntJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "hunt_jobs"
    __table_args__ = (Index("ix_hunt_jobs_status", "status"),)

    branch_id: Mapped[str | None] = mapped_column(ForeignKey("bot_branches.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[HuntStatus] = mapped_column(
        Enum(HuntStatus, name="hunt_status"),
        nullable=False,
        default=HuntStatus.PENDING,
    )
    requested_ip_count: Mapped[int] = mapped_column(Integer, nullable=False)
    target_prefixes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    vm_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    requested_by_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    progress_message_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progress_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    scopes: Mapped[list[HuntScope]] = relationship(back_populates="job", cascade="all, delete-orphan")
    matches: Mapped[list[HuntMatch]] = relationship(back_populates="job", cascade="all, delete-orphan")


class HuntScope(IdMixin, TimestampMixin, Base):
    __tablename__ = "hunt_scopes"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "account_id",
            "organization_external_id",
            name="uq_hunt_scope",
        ),
    )

    job_id: Mapped[str] = mapped_column(ForeignKey("hunt_jobs.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    organization_external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    job: Mapped[HuntJob] = relationship(back_populates="scopes")


class HuntCloudProgress(IdMixin, TimestampMixin, Base):
    __tablename__ = "hunt_cloud_progress"
    __table_args__ = (
        Index("ix_hunt_cloud_progress_job", "job_id"),
        UniqueConstraint(
            "job_id",
            "account_id",
            "cloud_external_id",
            name="uq_hunt_cloud_job_account_cloud",
        ),
    )

    job_id: Mapped[str] = mapped_column(ForeignKey("hunt_jobs.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    organization_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cloud_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[HuntCloudStatus] = mapped_column(
        Enum(HuntCloudStatus, name="hunt_cloud_status"),
        nullable=False,
        default=HuntCloudStatus.PENDING,
    )
    cycles_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HuntMatch(IdMixin, TimestampMixin, Base):
    __tablename__ = "hunt_matches"
    __table_args__ = (
        UniqueConstraint("job_id", "address_id", name="uq_hunt_match_address_per_job"),
        Index("ix_hunt_matches_job", "job_id"),
    )

    job_id: Mapped[str] = mapped_column(ForeignKey("hunt_jobs.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    organization_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cloud_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    address_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    matched_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    preexisting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    job: Mapped[HuntJob] = relationship(back_populates="matches")


class AddressRecord(IdMixin, TimestampMixin, Base):
    __tablename__ = "address_records"
    __table_args__ = (
        UniqueConstraint("account_id", "address_id", name="uq_account_address_id"),
        Index("ix_address_records_cloud", "account_id", "cloud_external_id"),
    )

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("hunt_jobs.id", ondelete="SET NULL"), nullable=True)
    cloud_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    folder_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    address_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lifecycle: Mapped[AddressLifecycle] = mapped_column(
        Enum(AddressLifecycle, name="address_lifecycle"),
        nullable=False,
        default=AddressLifecycle.CREATED,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
