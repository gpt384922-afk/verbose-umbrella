# Target Matches And Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the hunt launch form clearly ask how many matching VM/IP results are needed, and add smooth premium motion across the web dashboard.

**Architecture:** Keep the backend API field as `target_count` because it already drives scheduler behavior. Change user-facing labels and preflight wording to `Target matches`, then add CSS-only transitions/keyframes to the existing glass UI. Respect `prefers-reduced-motion`.

**Tech Stack:** FastAPI, Pydantic, React/Vite, CSS animations, Vitest, unittest.

---

### Task 1: Target Matches Copy

**Files:**
- Modify: `tests/test_web_dashboard_api.py`
- Modify: `frontend/src/App.test.tsx`
- Modify: `ycbot/web/hunts.py`
- Modify: `frontend/src/App.tsx`

- [ ] Write failing assertions for `Target matches` copy in backend preflight and frontend launch flow.
- [ ] Update backend preflight labels/details/summary to describe matching VM results.
- [ ] Rename the frontend numeric label from `Target VM` to `Target matches`.
- [ ] Keep request payload field `target_count`.

### Task 2: Premium Motion Layer

**Files:**
- Modify: `frontend/src/App.css`

- [ ] Add page entrance, panel hover, button/input/nav transitions, and staggered check animations.
- [ ] Add ambient slow drift on glow layers.
- [ ] Add `prefers-reduced-motion: reduce` override.

### Task 3: Verification

**Files:**
- Runtime only.

- [ ] Run `npm test -- --run`.
- [ ] Run `npm run build`.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover`.
- [ ] Restart backend on `127.0.0.1:8000` if backend code changed.
