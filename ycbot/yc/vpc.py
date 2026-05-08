from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any

from ycbot.config import Settings
from ycbot.utils import log_event
from ycbot.yc.client import YcApiError, YcClient


@dataclass(slots=True)
class Address:
    id: str
    ip: str | None


@dataclass(slots=True)
class Subnet:
    id: str
    zone_id: str


class VpcApi:
    def __init__(self, *, client: YcClient, settings: Settings, logger) -> None:
        self.client = client
        self.settings = settings
        self.logger = logger

    async def list_addresses(self, folder_id: str) -> list[Address]:
        data = await self.client.request_json(
            "GET",
            self.settings.yc_vpc_address_url,
            params={"folderId": folder_id},
        )
        rows = data.get("addresses", [])
        return [self._to_address(item) for item in rows if item.get("id")]

    async def list_subnets(self, folder_id: str) -> list[Subnet]:
        rows = await self.client.paginated(
            url=self.settings.yc_vpc_subnet_url,
            key="subnets",
            params={"folderId": folder_id},
        )
        return [
            Subnet(id=item["id"], zone_id=item.get("zoneId") or "")
            for item in rows
            if item.get("id")
        ]

    async def create_address(self, folder_id: str, zone: str) -> Address:
        payload = {
            "folderId": folder_id,
            "externalIpv4AddressSpec": {"zoneId": zone},
            "labels": {"managed_by": "ychunter"},
        }
        data = await self.client.request_json("POST", self.settings.yc_vpc_address_url, body=payload)
        response = await self._resolve_operation(data)

        address = self._to_address(response)
        log_event(self.logger, "ip.created", address_id=address.id, folder_id=folder_id, zone=zone, ip=address.ip)
        return address

    async def get_address(self, address_id: str) -> Address | None:
        try:
            data = await self.client.request_json("GET", f"{self.settings.yc_vpc_address_url}/{address_id}")
        except YcApiError as exc:
            if exc.status == 404:
                return None
            raise

        if not data.get("id"):
            return None
        return self._to_address(data)

    async def wait_for_ip(
        self,
        address_id: str,
        *,
        timeout_seconds: int,
        min_interval: int,
        max_interval: int,
    ) -> Address | None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            item = await self.get_address(address_id)
            if item and item.ip:
                return item
            if asyncio.get_running_loop().time() >= deadline:
                return item
            await asyncio.sleep(random.uniform(float(min_interval), float(max_interval)))

    async def delete_address(self, address_id: str) -> None:
        try:
            data = await self.client.request_json("DELETE", f"{self.settings.yc_vpc_address_url}/{address_id}")
        except YcApiError as exc:
            if exc.status == 404:
                return
            raise

        if data.get("id"):
            await self.client.poll_operation(data["id"], timeout_seconds=120)

        log_event(self.logger, "ip.deleted", address_id=address_id)

    async def _resolve_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("done"):
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            return payload.get("response") or {}

        operation_id = payload.get("id")
        if not operation_id:
            return payload.get("response") or {}

        done = await self.client.poll_operation(operation_id, timeout_seconds=120)
        return done.get("response") or {}

    @staticmethod
    def _to_address(payload: dict[str, Any]) -> Address:
        address_id = payload["id"]
        ip = payload.get("address") or (payload.get("externalIpv4Address") or {}).get("address")
        return Address(id=address_id, ip=ip)
