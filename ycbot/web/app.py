from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from ycbot.config import Settings, get_settings
from ycbot.db.session import Database, create_database
from ycbot.web.accounts import AccountsService
from ycbot.web.dashboard import DashboardService
from ycbot.web.hunts import HuntsService
from ycbot.web.schemas import (
    AccountCreateRequest,
    AccountCreateResponse,
    AccountSyncResponse,
    DashboardPayload,
    HuntPreflightResponse,
    HuntStartRequestPayload,
    HuntStartResponse,
)
from ycbot.utils.logger import format_error


def create_app(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    dashboard_service: DashboardService | None = None,
    accounts_service: AccountsService | None = None,
    hunts_service: HuntsService | None = None,
) -> FastAPI:
    owns_db = db is None
    app_settings = settings
    if app_settings is None and (db is None or dashboard_service is None):
        app_settings = get_settings()
    app_db = db or create_database(app_settings)
    service = dashboard_service or DashboardService(app_settings)
    account_service = accounts_service or AccountsService(settings=app_settings, db=app_db)
    hunt_service = hunts_service or (HuntsService(settings=app_settings, db=app_db) if app_settings is not None else None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if owns_db:
            await app_db.init_models()
        app.state.db = app_db
        app.state.dashboard_service = service
        app.state.accounts_service = account_service
        app.state.hunts_service = hunt_service
        try:
            yield
        finally:
            if owns_db:
                await app_db.close()

    app = FastAPI(title="YC Hunter Web API", lifespan=lifespan)
    app.state.db = app_db
    app.state.dashboard_service = service
    app.state.accounts_service = account_service
    app.state.hunts_service = hunt_service

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "name": "YC Hunter Web API",
            "health": "/api/health",
            "dashboard": "/api/dashboard",
            "docs": "/docs",
        }

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/dashboard", response_model=DashboardPayload)
    async def dashboard(request: Request) -> Any:
        async with request.app.state.db.session() as session:
            return await request.app.state.dashboard_service.build(session)

    @app.post("/api/accounts", response_model=AccountCreateResponse, status_code=201)
    async def create_account(payload: AccountCreateRequest, request: Request) -> AccountCreateResponse:
        async with request.app.state.db.session() as session:
            account_id = await request.app.state.accounts_service.create(session, payload)
        return AccountCreateResponse(id=account_id)

    @app.post("/api/accounts/{account_id}/sync", response_model=AccountSyncResponse)
    async def sync_account(account_id: str, request: Request) -> dict[str, int]:
        try:
            return await request.app.state.accounts_service.sync(account_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=format_error(exc)) from exc

    @app.post("/api/hunts", response_model=HuntStartResponse, status_code=201)
    async def start_hunt(payload: HuntStartRequestPayload, request: Request) -> HuntStartResponse:
        if request.app.state.hunts_service is None:
            raise HTTPException(status_code=503, detail="Hunt service is not configured")
        try:
            job_id = await request.app.state.hunts_service.start(payload)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=format_error(exc)) from exc
        return HuntStartResponse(job_id=job_id)

    @app.post("/api/hunts/preflight", response_model=HuntPreflightResponse)
    async def preflight_hunt(payload: HuntStartRequestPayload, request: Request) -> HuntPreflightResponse:
        if request.app.state.hunts_service is None:
            raise HTTPException(status_code=503, detail="Hunt service is not configured")
        try:
            return await request.app.state.hunts_service.preflight(payload)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=format_error(exc)) from exc

    return app


def build_app() -> FastAPI:
    return create_app()
