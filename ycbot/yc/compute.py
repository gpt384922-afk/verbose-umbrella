from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from ycbot.config import Settings
from ycbot.utils import log_event
from ycbot.yc.client import YcApiError, YcClient


@dataclass(slots=True)
class Instance:
    id: str
    ip: str | None
    zone_id: str | None = None
    name: str | None = None


class ComputeApi:
    def __init__(self, *, client: YcClient, settings: Settings, logger) -> None:
        self.client = client
        self.settings = settings
        self.logger = logger

    async def get_latest_image_by_family(self, folder_id: str, family: str) -> str:
        data = await self.client.request_json(
            "GET",
            f"{self.settings.yc_compute_image_url}:latestByFamily",
            params={"folderId": folder_id, "family": family},
        )
        image_id = data.get("id")
        if not image_id:
            raise RuntimeError(f"latest image response has no id: family={family}")
        return image_id

    async def list_instances(self, folder_id: str) -> list[Instance]:
        rows = await self.client.paginated(
            url=self.settings.yc_compute_instance_url,
            key="instances",
            params={"folderId": folder_id},
        )
        return [self._to_instance(item) for item in rows if item.get("id")]

    async def create_instance(
        self,
        *,
        folder_id: str,
        name: str,
        zone_id: str,
        subnet_id: str,
        image_id: str,
        ssh_username: str,
        ssh_public_key: str,
    ) -> Instance:
        payload = {
            "folderId": folder_id,
            "name": name,
            "zoneId": zone_id,
            "platformId": self.settings.hunt_vm_platform_id,
            "resourcesSpec": {
                "cores": str(self.settings.hunt_vm_cores),
                "memory": str(self.settings.hunt_vm_memory_gb * 1024**3),
                "coreFraction": str(self.settings.hunt_vm_core_fraction),
            },
            "bootDiskSpec": {
                "autoDelete": True,
                "diskSpec": {
                    "typeId": self.settings.hunt_vm_disk_type_id,
                    "size": str(self.settings.hunt_vm_disk_size_gb * 1024**3),
                    "imageId": image_id,
                },
            },
            "networkInterfaceSpecs": [
                {
                    "subnetId": subnet_id,
                    "primaryV4AddressSpec": {
                        "oneToOneNatSpec": {
                            "ipVersion": "IPV4",
                        },
                    },
                }
            ],
            "schedulingPolicy": {"preemptible": True},
            "metadata": {"ssh-keys": f"{ssh_username}:{ssh_public_key}"},
            "labels": {"managed_by": "ychunter"},
        }
        data = await self.client.request_json("POST", self.settings.yc_compute_instance_url, body=payload)
        response = await self._resolve_operation(data)
        instance = self._to_instance(response)
        log_event(
            self.logger,
            "vm.created",
            instance_id=instance.id,
            folder_id=folder_id,
            zone=zone_id,
            ip=instance.ip,
        )
        return instance

    async def get_instance(self, instance_id: str) -> Instance | None:
        try:
            data = await self.client.request_json("GET", f"{self.settings.yc_compute_instance_url}/{instance_id}")
        except YcApiError as exc:
            if exc.status == 404:
                return None
            raise

        if not data.get("id"):
            return None
        return self._to_instance(data)

    async def wait_for_external_ip(
        self,
        instance_id: str,
        *,
        poll_seconds: int,
        timeout_seconds: int,
    ) -> Instance | None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            instance = await self.get_instance(instance_id)
            if instance is None or instance.ip:
                return instance
            if asyncio.get_running_loop().time() >= deadline:
                return instance
            await asyncio.sleep(poll_seconds)

    async def delete_instance(self, instance_id: str) -> None:
        try:
            data = await self.client.request_json("DELETE", f"{self.settings.yc_compute_instance_url}/{instance_id}")
        except YcApiError as exc:
            if exc.status == 404:
                return
            raise

        if data.get("id"):
            await self.client.poll_operation(data["id"], timeout_seconds=240)

        log_event(self.logger, "vm.deleted", instance_id=instance_id)

    async def _resolve_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("done"):
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            return payload.get("response") or {}

        operation_id = payload.get("id")
        if not operation_id:
            return payload.get("response") or {}

        done = await self.client.poll_operation(operation_id, timeout_seconds=240)
        return done.get("response") or {}

    @classmethod
    def _to_instance(cls, payload: dict[str, Any]) -> Instance:
        return Instance(
            id=payload["id"],
            ip=cls.extract_external_ip(payload),
            zone_id=payload.get("zoneId"),
            name=payload.get("name"),
        )

    @staticmethod
    def extract_external_ip(payload: dict[str, Any]) -> str | None:
        for interface in payload.get("networkInterfaces", []):
            primary = interface.get("primaryV4Address") or {}
            nat = primary.get("oneToOneNat") or {}
            if nat.get("address"):
                return nat["address"]
        return None
