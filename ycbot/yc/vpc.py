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
    network_id: str | None = None


@dataclass(slots=True)
class Network:
    id: str
    name: str | None = None


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
            self._to_subnet(item)
            for item in rows
            if item.get("id")
        ]

    async def list_networks(self, folder_id: str) -> list[Network]:
        rows = await self.client.paginated(
            url=self.settings.yc_vpc_network_url,
            key="networks",
            params={"folderId": folder_id},
        )
        return [self._to_network(item) for item in rows if item.get("id")]

    async def create_network(self, folder_id: str, name: str) -> Network:
        payload = {
            "folderId": folder_id,
            "name": name,
            "description": "Managed by YC Hunter",
            "labels": {"managed_by": "ychunter"},
        }
        data = await self.client.request_json("POST", self.settings.yc_vpc_network_url, body=payload)
        response = await self._resolve_operation(data)
        network = self._to_network(response)
        log_event(self.logger, "network.created", folder_id=folder_id, network_id=network.id, name=network.name)
        return network

    async def create_subnet(
        self,
        *,
        folder_id: str,
        network_id: str,
        zone_id: str,
        name: str,
        cidr_block: str,
    ) -> Subnet:
        payload = {
            "folderId": folder_id,
            "name": name,
            "description": "Managed by YC Hunter",
            "networkId": network_id,
            "zoneId": zone_id,
            "v4CidrBlocks": [cidr_block],
            "labels": {"managed_by": "ychunter"},
        }
        data = await self.client.request_json("POST", self.settings.yc_vpc_subnet_url, body=payload)
        response = await self._resolve_operation(data)
        subnet = self._to_subnet(response)
        log_event(
            self.logger,
            "subnet.created",
            folder_id=folder_id,
            network_id=network_id,
            subnet_id=subnet.id,
            zone=zone_id,
            cidr=cidr_block,
        )
        return subnet

    async def ensure_subnets(
        self,
        *,
        folder_id: str,
        zones: list[str],
        cidr_blocks: list[str],
    ) -> list[Subnet]:
        zones = [zone for zone in zones if zone]
        existing = [subnet for subnet in await self.list_subnets(folder_id) if subnet.zone_id in zones]
        by_zone: dict[str, Subnet] = {}
        for subnet in existing:
            by_zone.setdefault(subnet.zone_id, subnet)

        missing_zones = [zone for zone in zones if zone not in by_zone]
        if not missing_zones:
            return [by_zone[zone] for zone in zones]

        network_id = next((subnet.network_id for subnet in existing if subnet.network_id), None)
        if network_id is None:
            networks = await self.list_networks(folder_id)
            if networks:
                network_id = networks[0].id
            else:
                network_id = (await self.create_network(folder_id, self._resource_name("ycbot-net", folder_id))).id

        for zone in missing_zones:
            zone_index = zones.index(zone)
            subnet = await self.create_subnet(
                folder_id=folder_id,
                network_id=network_id,
                zone_id=zone,
                name=self._resource_name(f"ycbot-subnet-{zone[-1]}", folder_id),
                cidr_block=self._cidr_for_zone(cidr_blocks, zone_index),
            )
            by_zone[zone] = subnet

        return [by_zone[zone] for zone in zones if zone in by_zone]

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

    @staticmethod
    def _to_subnet(payload: dict[str, Any]) -> Subnet:
        return Subnet(
            id=payload["id"],
            zone_id=payload.get("zoneId") or "",
            network_id=payload.get("networkId"),
        )

    @staticmethod
    def _to_network(payload: dict[str, Any]) -> Network:
        return Network(id=payload["id"], name=payload.get("name"))

    @staticmethod
    def _resource_name(prefix: str, folder_id: str) -> str:
        cleaned = "".join(ch for ch in folder_id.lower() if ch.isalnum())
        suffix = (cleaned[-12:] or "folder").strip("-")
        return f"{prefix}-{suffix}"[:63].strip("-")

    @staticmethod
    def _cidr_for_zone(cidr_blocks: list[str], zone_index: int) -> str:
        if zone_index < len(cidr_blocks):
            return cidr_blocks[zone_index]
        return f"10.{10 + zone_index * 10}.0.0/24"
