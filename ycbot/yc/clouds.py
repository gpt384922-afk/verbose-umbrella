from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ycbot.config import Settings
from ycbot.db.enums import CloudState
from ycbot.utils import log_event
from ycbot.utils.retry import RetryError
from ycbot.yc.client import YcApiError, YcClient


@dataclass(slots=True)
class Organization:
    id: str
    name: str
    state: str


@dataclass(slots=True)
class BillingAccount:
    id: str
    name: str | None
    active: bool
    organization_id: str | None = None


@dataclass(slots=True)
class Cloud:
    id: str
    name: str
    organization_id: str
    state: CloudState
    deleting: bool


@dataclass(slots=True)
class Folder:
    id: str
    name: str


class CloudsApi:
    def __init__(self, *, client: YcClient, settings: Settings, logger) -> None:
        self.client = client
        self.settings = settings
        self.logger = logger

    async def list_organizations(self) -> list[Organization]:
        rows = await self.client.paginated(url=self.settings.yc_org_url, key="organizations")
        return [
            Organization(
                id=item["id"],
                name=self._display_name(item),
                state=item.get("status") or item.get("state") or "UNKNOWN",
            )
            for item in rows
            if item.get("id")
            and not self._is_deleted_or_deleting(item)
        ]

    async def list_billing_accounts(self) -> list[BillingAccount]:
        rows = await self.client.paginated(url=self.settings.yc_billing_url, key="billingAccounts")
        return [
            BillingAccount(
                id=item["id"],
                name=item.get("name"),
                active=bool(item.get("active")),
                organization_id=(
                    item.get("organizationId")
                    or (item.get("organization") or {}).get("id")
                    or item.get("organization_id")
                ),
            )
            for item in rows
            if item.get("id")
        ]

    async def list_billing_bindings(self, billing_account_id: str) -> list[dict[str, Any]]:
        return await self.client.paginated(
            url=f"{self.settings.yc_billing_url}/{billing_account_id}/billableObjectBindings",
            key="billableObjectBindings",
        )

    async def list_clouds(self, organization_id: str) -> list[Cloud]:
        rows = await self.client.paginated(
            url=self.settings.yc_cloud_url,
            key="clouds",
            params={"organizationId": organization_id},
        )
        return [
            Cloud(
                id=item["id"],
                name=self._display_name(item),
                organization_id=organization_id,
                state=self._parse_cloud_state(item),
                deleting=self._is_deleting(item),
            )
            for item in rows
            if item.get("id")
        ]

    async def create_cloud(self, organization_id: str, name: str) -> Cloud:
        payload = {
            "organizationId": organization_id,
            "name": name,
            "labels": {"managed_by": "ycbot"},
            "description": "Managed by YCBot",
        }
        data = await self.client.request_json("POST", self.settings.yc_cloud_url, body=payload)
        response = await self._resolve_operation(data)
        cloud_id = response.get("id")
        if not cloud_id:
            raise RuntimeError(f"Cloud create response has no id: {response}")

        cloud = Cloud(
            id=cloud_id,
            name=response.get("name") or name,
            organization_id=organization_id,
            state=CloudState.ACTIVE,
            deleting=False,
        )
        log_event(self.logger, "cloud.created", cloud_id=cloud.id, org_id=organization_id, name=cloud.name)
        return cloud

    async def create_cloud_with_folder(
        self,
        *,
        organization_id: str,
        name: str,
        billing_account_id: str,
    ) -> tuple[Cloud, Folder]:
        cloud = await self.create_cloud(organization_id, name)
        last_error: Exception | None = None
        prepare_attempts = max(1, self.settings.yc_cloud_prepare_retries)
        await self.wait_cloud_active(cloud.id)
        for attempt in range(1, prepare_attempts + 1):
            try:
                await self.bind_cloud_to_billing(cloud.id, billing_account_id)
                if not await self._cloud_has_billing_binding(cloud.id, billing_account_id):
                    raise self._billing_binding_missing_error(cloud.id)
                folder = await self._ensure_folder_once(cloud.id)
                log_event(
                    self.logger,
                    "cloud.ready",
                    cloud_id=cloud.id,
                    folder_id=folder.id,
                    billing_id=billing_account_id,
                    org_id=organization_id,
                )
                return cloud, folder
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= prepare_attempts or not self._is_retryable_prepare_error(exc):
                    break
                delay = self._prepare_retry_delay(attempt)
                log_event(
                    self.logger,
                    "cloud.prepare.retry",
                    cloud_id=cloud.id,
                    billing_id=billing_account_id,
                    org_id=organization_id,
                    attempt=attempt,
                    delay_seconds=round(delay, 2),
                    error=str(exc),
                )
                await asyncio.sleep(delay)

        assert last_error is not None
        log_event(
            self.logger,
            "cloud.prepare.failed",
            cloud_id=cloud.id,
            billing_id=billing_account_id,
            org_id=organization_id,
            error=str(last_error),
        )
        try:
            await self.delete_cloud(cloud.id, force_now=True)
        except Exception as cleanup_error:  # noqa: BLE001
            log_event(
                self.logger,
                "cloud.prepare.cleanup_failed",
                cloud_id=cloud.id,
                error=str(cleanup_error),
            )
        raise last_error

    async def delete_cloud(self, cloud_id: str, *, force_now: bool = True) -> None:
        params: dict[str, str] = {}
        if force_now:
            delete_after = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            params["deleteAfter"] = delete_after

        try:
            data = await self.client.request_json(
                "DELETE",
                f"{self.settings.yc_cloud_url}/{cloud_id}",
                params=params,
                retries=1,
            )
        except (YcApiError, RetryError) as exc:
            api_error = exc if isinstance(exc, YcApiError) else exc.__cause__
            if not isinstance(api_error, YcApiError):
                raise
            if api_error.status == 404:
                return
            if api_error.status in {400, 409, 423}:
                # already deleting / locked for deletion.
                log_event(self.logger, "cloud.delete.pending", cloud_id=cloud_id, status=api_error.status)
                return
            raise

        log_event(self.logger, "cloud.delete.started", cloud_id=cloud_id, operation_id=data.get("id"))

    async def wait_cloud_deleted(
        self,
        cloud_id: str,
        *,
        timeout_seconds: int | None = None,
        poll_interval_seconds: int | None = None,
    ) -> None:
        timeout = timeout_seconds or self.settings.yc_cloud_delete_timeout_seconds
        poll_interval = poll_interval_seconds or self.settings.yc_cloud_delete_poll_seconds
        poll_interval = max(5, poll_interval)
        deadline = self._deadline(timeout)
        while True:
            try:
                await self.client.request_json("GET", f"{self.settings.yc_cloud_url}/{cloud_id}")
            except YcApiError as exc:
                if exc.status == 404:
                    log_event(self.logger, "cloud.deleted", cloud_id=cloud_id)
                    return
                raise

            if self._expired(deadline):
                raise TimeoutError(f"cloud deletion timeout: {cloud_id}")
            log_event(self.logger, "cloud.delete.wait", cloud_id=cloud_id, next_check_seconds=poll_interval)
            await asyncio.sleep(poll_interval)

    async def wait_cloud_active(
        self,
        cloud_id: str,
        *,
        timeout_seconds: int = 120,
        poll_interval_seconds: int = 5,
    ) -> None:
        poll_interval = max(1, poll_interval_seconds)
        deadline = self._deadline(timeout_seconds)
        while True:
            data = await self.client.request_json("GET", f"{self.settings.yc_cloud_url}/{cloud_id}")
            state = self._parse_cloud_state(data)
            if state == CloudState.ACTIVE and not self._is_deleting(data):
                return
            if state == CloudState.UNKNOWN and data.get("id") == cloud_id and not self._is_deleting(data):
                log_event(self.logger, "cloud.active.assumed", cloud_id=cloud_id)
                return
            if state in {CloudState.BLOCKED, CloudState.DELETED} or self._is_deleting(data):
                raise RuntimeError(f"cloud is not usable: {cloud_id} state={state.value}")
            if self._expired(deadline):
                raise TimeoutError(f"cloud active wait timeout: {cloud_id}")
            log_event(self.logger, "cloud.active.wait", cloud_id=cloud_id, state=state.value, next_check_seconds=poll_interval)
            await asyncio.sleep(poll_interval)

    async def bind_cloud_to_billing(self, cloud_id: str, billing_account_id: str) -> None:
        if await self._cloud_has_billing_binding(cloud_id, billing_account_id):
            log_event(self.logger, "cloud.billing.already_bound", cloud_id=cloud_id, billing_id=billing_account_id)
            return

        payload = {
            "billableObject": {
                "id": cloud_id,
                "type": "cloud",
            }
        }
        data = await self.client.request_json(
            "POST",
            f"{self.settings.yc_billing_url}/{billing_account_id}/billableObjectBindings",
            body=payload,
        )
        if data.get("done") and data.get("error"):
            exc = YcApiError(
                status=500,
                message=f"Operation failed: {json.dumps(data['error'])}",
                payload=json.dumps(data, ensure_ascii=False),
            )
            if self._is_retryable_prepare_error(exc) and await self._cloud_has_billing_binding(cloud_id, billing_account_id):
                log_event(
                    self.logger,
                    "cloud.billing.bound_after_operation_error",
                    cloud_id=cloud_id,
                    billing_id=billing_account_id,
                    operation_id=data.get("id"),
                    error=str(exc),
                )
                return
            if self._is_ignorable_billing_code9(exc, cloud_id):
                log_event(
                    self.logger,
                    "cloud.billing.code9_ignored",
                    cloud_id=cloud_id,
                    billing_id=billing_account_id,
                    operation_id=data.get("id"),
                    error=str(exc),
                )
                return
            raise exc

        if data.get("id"):
            try:
                await self.client.poll_operation(data["id"], timeout_seconds=240)
            except YcApiError as exc:
                if self._is_retryable_prepare_error(exc) and await self._cloud_has_billing_binding(cloud_id, billing_account_id):
                    log_event(
                        self.logger,
                        "cloud.billing.bound_after_operation_error",
                        cloud_id=cloud_id,
                        billing_id=billing_account_id,
                        operation_id=data["id"],
                        error=str(exc),
                    )
                    return
                if self._is_ignorable_billing_code9(exc, cloud_id):
                    log_event(
                        self.logger,
                        "cloud.billing.code9_ignored",
                        cloud_id=cloud_id,
                        billing_id=billing_account_id,
                        operation_id=data["id"],
                        error=str(exc),
                    )
                    return
                raise

        if not await self._cloud_has_billing_binding(cloud_id, billing_account_id):
            raise self._billing_binding_missing_error(cloud_id)

        log_event(self.logger, "cloud.billing.bound", cloud_id=cloud_id, billing_id=billing_account_id)

    async def _cloud_has_billing_binding(self, cloud_id: str, billing_account_id: str) -> bool:
        try:
            bindings = await self.list_billing_bindings(billing_account_id)
        except Exception as exc:  # noqa: BLE001
            log_event(
                self.logger,
                "cloud.billing.binding_check_failed",
                cloud_id=cloud_id,
                billing_id=billing_account_id,
                error=str(exc),
            )
            return False

        for binding in bindings:
            billable = binding.get("billableObject") or {}
            if str(billable.get("type") or "").lower() == "cloud" and billable.get("id") == cloud_id:
                return True
        return False

    async def list_folders(self, cloud_id: str) -> list[Folder]:
        rows = await self.client.paginated(
            url=self.settings.yc_folder_url,
            key="folders",
            params={"cloudId": cloud_id},
        )
        return [
            Folder(id=item["id"], name=item.get("name") or item["id"])
            for item in rows
            if item.get("id") and not self._is_deleted_or_deleting(item)
        ]

    async def ensure_folder(self, cloud_id: str) -> Folder:
        last_error: Exception | None = None
        prepare_attempts = max(1, self.settings.yc_cloud_prepare_retries)
        for attempt in range(1, prepare_attempts + 1):
            try:
                return await self._ensure_folder_once(cloud_id)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= prepare_attempts or not self._is_retryable_prepare_error(exc):
                    break
                delay = self._prepare_retry_delay(attempt)
                log_event(
                    self.logger,
                    "folder.ensure.retry",
                    cloud_id=cloud_id,
                    attempt=attempt,
                    delay_seconds=round(delay, 2),
                    error=str(exc),
                )
                await asyncio.sleep(delay)

        assert last_error is not None
        raise last_error

    async def _ensure_folder_once(self, cloud_id: str) -> Folder:
        folders = await self.list_folders(cloud_id)
        if folders:
            log_event(self.logger, "folder.reused", cloud_id=cloud_id, folder_id=folders[0].id)
            return folders[0]

        suffix = re.sub(r"[^a-z0-9]", "", cloud_id.lower())[-6:] or "cloud"
        folder_name = f"ycbot-folder-{suffix}"
        log_event(self.logger, "folder.missing", cloud_id=cloud_id, folder_name=folder_name)
        payload = {
            "cloudId": cloud_id,
            "name": folder_name,
            "labels": {"managed_by": "ycbot"},
            "description": "Managed by YCBot",
        }
        data = await self.client.request_json("POST", self.settings.yc_folder_url, body=payload)
        response = await self._resolve_operation(data)
        folder_id = response.get("id")
        if not folder_id:
            raise RuntimeError(f"Folder create response has no id: {response}")

        folder = Folder(id=folder_id, name=response.get("name") or folder_name)
        log_event(self.logger, "folder.created", cloud_id=cloud_id, folder_id=folder.id)
        return folder

    async def resolve_cloud_billing_map(self, billing_accounts: list[BillingAccount]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for billing in billing_accounts:
            if not billing.active:
                continue
            bindings = await self.list_billing_bindings(billing.id)
            for binding in bindings:
                billable = binding.get("billableObject") or {}
                if billable.get("type") == "cloud" and billable.get("id"):
                    mapping[billable["id"]] = billing.id
        return mapping

    async def resolve_organization_billing_map(self, billing_accounts: list[BillingAccount]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for billing in billing_accounts:
            if not billing.active:
                continue
            if billing.organization_id:
                mapping[billing.organization_id] = billing.id

            bindings = await self.list_billing_bindings(billing.id)
            for binding in bindings:
                billable = binding.get("billableObject") or {}
                billable_type = str(billable.get("type") or "").lower()
                billable_id = billable.get("id")
                if billable_type in {"organization", "organisation"} and billable_id:
                    mapping[billable_id] = billing.id
        return mapping

    async def _resolve_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("done"):
            if payload.get("error"):
                raise YcApiError(
                    status=500,
                    message=f"Operation failed: {payload['error']}",
                    payload=str(payload),
                )
            return payload.get("response") or {}

        operation_id = payload.get("id")
        if not operation_id:
            return payload.get("response") or {}

        data = await self.client.poll_operation(operation_id, timeout_seconds=240)
        return data.get("response") or {}

    @staticmethod
    def _is_retryable_prepare_error(exc: Exception) -> bool:
        if not isinstance(exc, YcApiError):
            cause = exc.__cause__
            if isinstance(cause, YcApiError):
                exc = cause

        text = str(exc)
        if "Operation failed" not in text:
            return False

        if isinstance(exc, YcApiError):
            for raw in (exc.message, exc.payload):
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if CloudsApi._operation_error_code(payload) == 9:
                    return True

        return bool(re.search(r"['\"]code['\"]\s*:\s*9\b", text))

    @staticmethod
    def _operation_error_code(payload: dict[str, Any]) -> int | None:
        error = payload.get("error") if isinstance(payload, dict) else None
        raw_code = None
        if isinstance(error, dict):
            raw_code = error.get("code")
        if raw_code is None:
            raw_code = payload.get("code")
        if raw_code is not None:
            try:
                return int(raw_code)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _billing_binding_missing_error(cloud_id: str) -> YcApiError:
        return YcApiError(
            status=500,
            message='Operation failed: {"code": 9}',
            payload=json.dumps(
                {
                    "error": {"code": 9, "message": "billing binding is not visible yet"},
                    "metadata": {"billableObjectId": cloud_id},
                },
                ensure_ascii=False,
            ),
        )

    def _is_ignorable_billing_code9(self, exc: Exception, cloud_id: str) -> bool:
        if not self.settings.yc_billing_code9_as_bound:
            return False
        operation = self._operation_payload(exc)
        if not operation:
            return False
        metadata = operation.get("metadata") or {}
        return (
            self._operation_error_code(operation) == 9
            and metadata.get("billableObjectId") == cloud_id
            and not ((operation.get("error") or {}).get("message"))
        )

    @staticmethod
    def _operation_payload(exc: Exception) -> dict[str, Any] | None:
        if not isinstance(exc, YcApiError):
            cause = exc.__cause__
            if isinstance(cause, YcApiError):
                exc = cause
        if not isinstance(exc, YcApiError):
            return None

        for raw in (exc.payload, exc.message):
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def _prepare_retry_delay(self, attempt: int) -> float:
        base_delay = max(0.1, self.settings.yc_cloud_prepare_retry_base_delay)
        max_delay = max(base_delay, self.settings.yc_cloud_prepare_retry_max_delay)
        return min(max_delay, base_delay * (2 ** (attempt - 1)))

    @staticmethod
    def _parse_cloud_state(cloud: dict[str, Any]) -> CloudState:
        state = CloudsApi._state_text(cloud)
        if "active" in state:
            return CloudState.ACTIVE
        if "block" in state:
            return CloudState.BLOCKED
        if "delet" in state:
            return CloudState.DELETING
        if state == "deleted":
            return CloudState.DELETED
        return CloudState.UNKNOWN

    @staticmethod
    def _is_deleting(cloud: dict[str, Any]) -> bool:
        state = CloudsApi._state_text(cloud)
        if "delet" in state:
            return True
        deletion_markers = (
            "deleteAfter",
            "deleteAt",
            "deletedAt",
            "deletingAt",
            "delete_after",
            "delete_at",
            "deleted_at",
            "deleting_at",
        )
        return any(cloud.get(marker) for marker in deletion_markers)

    @staticmethod
    def _is_deleted_or_deleting(payload: dict[str, Any]) -> bool:
        state = CloudsApi._state_text(payload)
        if "delet" in state or state in {"deleted", "inactive"}:
            return True
        deletion_markers = (
            "deleteAfter",
            "deleteAt",
            "deletedAt",
            "deletingAt",
            "delete_after",
            "delete_at",
            "deleted_at",
            "deleting_at",
        )
        return any(payload.get(marker) for marker in deletion_markers)

    @staticmethod
    def _state_text(payload: dict[str, Any]) -> str:
        return str(
            payload.get("status")
            or payload.get("state")
            or payload.get("lifecycleState")
            or payload.get("lifecycle_state")
            or payload.get("lifecycleStatus")
            or payload.get("lifecycle_status")
            or ""
        ).lower()

    @staticmethod
    def _display_name(payload: dict[str, Any]) -> str:
        for key in ("title", "displayName", "display_name", "name"):
            value = payload.get(key)
            if value:
                return str(value)
        return str(payload["id"])

    @staticmethod
    def _deadline(timeout_seconds: int) -> float:
        import asyncio

        return asyncio.get_running_loop().time() + timeout_seconds

    @staticmethod
    def _expired(deadline: float) -> bool:
        import asyncio

        if asyncio.get_running_loop().time() > deadline:
            return True
        return False
