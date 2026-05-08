from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from ycbot.config import Settings
from ycbot.utils import log_event, retry_async


@dataclass(slots=True)
class YcApiError(Exception):
    status: int
    message: str
    payload: str | None = None

    def __str__(self) -> str:
        if self.payload:
            return f"{self.status} {self.message}: {self.payload}"
        return f"{self.status} {self.message}"

    @property
    def retryable(self) -> bool:
        return self.status in {403, 429, 500, 502, 503, 504}


class YcClient:
    def __init__(
        self,
        *,
        settings: Settings,
        oauth_token: str,
        proxy_url: str | None,
        semaphore: asyncio.Semaphore,
        logger,
    ) -> None:
        self.settings = settings
        self.oauth_token = oauth_token
        self.proxy_url = proxy_url
        self.semaphore = semaphore
        self.logger = logger

        self._session: aiohttp.ClientSession | None = None
        self._iam_token: str | None = None
        self._iam_expires_at: datetime | None = None

    async def __aenter__(self) -> "YcClient":
        timeout = aiohttp.ClientTimeout(total=40)
        connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        retries: int | None = None,
    ) -> dict[str, Any]:
        total_attempts = retries or self.settings.yc_request_retries

        async def _run() -> dict[str, Any]:
            token = await self._get_iam_token()
            return await self._raw_request(
                method,
                url,
                token=token,
                params=params,
                body=body,
            )

        try:
            return await retry_async(
                _run,
                attempts=total_attempts,
                base_delay=self.settings.yc_retry_base_delay,
                max_delay=self.settings.yc_retry_max_delay,
                retryable=self._retryable_exception,
            )
        except Exception as exc:  # noqa: BLE001
            log_event(
                self.logger,
                "yc.request.failed",
                method=method,
                url=url,
                params=params,
                error=str(exc),
            )
            raise

    async def request_iam(self) -> str:
        if self._session is None:
            raise RuntimeError("YcClient session is not initialized")

        async def _run() -> str:
            async with self.semaphore:
                async with self._session.post(
                    self.settings.yc_iam_url,
                    json={"yandexPassportOauthToken": self.oauth_token},
                    proxy=self.proxy_url,
                ) as response:
                    if response.status >= 400:
                        payload = await response.text()
                        raise YcApiError(
                            status=response.status,
                            message="IAM token request failed",
                            payload=payload,
                        )
                    data = await response.json(content_type=None)
                    token = data.get("iamToken")
                    if not token:
                        raise YcApiError(status=500, message="IAM response has no iamToken")
                    return token

        return await retry_async(
            _run,
            attempts=self.settings.yc_request_retries,
            base_delay=self.settings.yc_retry_base_delay,
            max_delay=self.settings.yc_retry_max_delay,
            retryable=self._retryable_exception,
        )

    async def poll_operation(
        self,
        operation_id: str,
        *,
        timeout_seconds: int = 240,
        min_delay: float = 1.0,
        max_delay: float = 2.5,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            data = await self.request_json("GET", f"{self.settings.yc_operation_url}/{operation_id}")
            if data.get("done"):
                if data.get("error"):
                    raise YcApiError(
                        status=500,
                        message=f"Operation failed: {json.dumps(data['error'])}",
                        payload=json.dumps(data, ensure_ascii=False),
                    )
                return data

            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"operation timeout: {operation_id}")
            await asyncio.sleep(min(max_delay, min_delay + (max_delay - min_delay) * 0.5))

    async def paginated(
        self,
        *,
        url: str,
        key: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page_token = ""
        base_params = dict(params or {})

        while True:
            query = dict(base_params)
            query.setdefault("pageSize", "100")
            if page_token:
                query["pageToken"] = page_token

            data = await self.request_json("GET", url, params=query)
            records.extend(data.get(key, []))
            page_token = data.get("nextPageToken") or ""
            if not page_token:
                return records

    async def _get_iam_token(self) -> str:
        now = datetime.now(tz=timezone.utc)
        if self._iam_token and self._iam_expires_at and now < self._iam_expires_at:
            return self._iam_token

        token = await self.request_iam()
        self._iam_token = token
        self._iam_expires_at = now + timedelta(hours=11)
        return token

    async def _raw_request(
        self,
        method: str,
        url: str,
        *,
        token: str,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("YcClient session is not initialized")

        headers = {"Authorization": f"Bearer {token}"}
        async with self.semaphore:
            async with self._session.request(
                method,
                url,
                params=params,
                json=body,
                headers=headers,
                proxy=self.proxy_url,
            ) as response:
                if response.status == 403:
                    # Force token refresh next attempt.
                    self._iam_token = None
                    self._iam_expires_at = None

                if response.status >= 400:
                    payload = await response.text()
                    raise YcApiError(
                        status=response.status,
                        message=f"request failed: {method} {url}",
                        payload=payload,
                    )
                return await response.json(content_type=None)

    @staticmethod
    def _retryable_exception(exc: Exception) -> bool:
        if isinstance(exc, YcApiError):
            return exc.retryable
        if isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError)):
            return True
        return False
