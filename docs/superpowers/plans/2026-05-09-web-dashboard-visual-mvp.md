# Web Dashboard Visual MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Vite React visual dashboard shell for YC Hunter using the approved black-glass design direction.

**Architecture:** Add a separate `frontend/` app that is independent from the current Python bot. The UI uses static TypeScript data and focused React components so the later FastAPI integration can replace the data source without redesigning the screen.

**Tech Stack:** Vite, React, TypeScript, Vitest, Testing Library, CSS custom properties.

---

## Files

- Create `frontend/package.json` for local scripts and dependencies.
- Create `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/App.css`.
- Create `frontend/src/App.test.tsx` to lock key visual sections.
- Create `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`.

## Tasks

- [ ] Add frontend test/config scaffold.
- [ ] Run the test and verify it fails because `App` does not exist.
- [ ] Implement the static dashboard UI.
- [ ] Run tests and build.
- [ ] Start the local dev server and provide the URL.
