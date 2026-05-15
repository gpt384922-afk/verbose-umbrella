# Web Hunt Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-data preflight check before starting hunts from the web UI.

**Architecture:** Reuse the existing hunt request schema for preflight. Add a small backend response model and a `HuntsService.preflight()` method that reads accounts, organizations, and billing rows from the database. Render the response in the existing Hunts page without introducing a new route or state manager.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic, React/Vite, Vitest, unittest.

---

### Task 1: Backend Contract

**Files:**
- Modify: `tests/test_web_dashboard_api.py`
- Modify: `ycbot/web/schemas.py`
- Modify: `ycbot/web/app.py`

- [ ] Write a failing test for `POST /api/hunts/preflight`.
- [ ] Add Pydantic response models: `HuntPreflightCheck`, `HuntPreflightCapacity`, `HuntPreflightResponse`.
- [ ] Add `/api/hunts/preflight` to `create_app()`.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest tests.test_web_dashboard_api`.

### Task 2: Backend Preflight Logic

**Files:**
- Modify: `ycbot/web/hunts.py`

- [ ] Implement `HuntsService.preflight(payload)`.
- [ ] Query selected accounts, organizations, and active billing counts using the existing database session API.
- [ ] Return hard errors for missing scope, missing prefixes, invalid target count, inactive account, and unknown organization.
- [ ] Return warnings for missing active billing because local cache can be stale until sync.
- [ ] Run backend tests.

### Task 3: Frontend Operator Flow

**Files:**
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`

- [ ] Write a failing test that runs preflight from Hunts and expects readiness output.
- [ ] Add TypeScript types for the preflight response.
- [ ] Add a `Run preflight` action to the Hunts form.
- [ ] Render checks, capacity, and VM profile in a glass panel.
- [ ] Run `npm test -- --run` and `npm run build`.

### Task 4: Local Verification

**Files:**
- Runtime only.

- [ ] Run `PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover`.
- [ ] Restart backend on `127.0.0.1:8000`.
- [ ] Verify `/api/health` and `/api/hunts/preflight` contract without creating a real hunt.
