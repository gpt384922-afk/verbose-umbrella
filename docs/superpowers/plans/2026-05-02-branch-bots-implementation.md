# Branch Bots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build branch Telegram bots in one YC Hunter process and update hunt details so active hunts show runtime and IP iteration metrics without mentioning cloud deletion.

**Architecture:** Add branch ownership to database rows, pass a runtime scope through aiogram middleware into handlers and scheduler methods, and run one dispatcher per active branch token. Keep one shared `AppContext`, `HuntScheduler`, `HunterEngine`, and database connection pool.

**Tech Stack:** Python 3.11+, aiogram 3.27, SQLAlchemy 2 async ORM, PostgreSQL in production, stdlib `unittest` for focused tests.

---

## File Structure

- Modify `ycbot/db/models.py`: add `BotBranch`, `branch_id` columns.
- Modify `ycbot/db/repositories.py`: add `BranchRepository`, branch-scoped account and hunt queries.
- Modify `ycbot/db/session.py`: add lightweight schema upgrades for existing tables.
- Modify `ycbot/bot/app.py`: add runtime scope middleware and multi-bot polling manager.
- Modify `ycbot/bot/handlers.py`: accept runtime scope, branch management states, branch-scoped scheduler calls, updated hunt detail text.
- Modify `ycbot/bot/keyboards.py`: add conditional main menu and branch keyboards.
- Modify `ycbot/bot/ui.py`: allow main menu to hide or show branch management.
- Modify `ycbot/core/scheduler.py`: accept branch scope in account/hunt operations and add branch management methods.
- Modify `README.md` and `.env.example`: document branch behavior.
- Create `tests/`: focused tests for branch filtering, menu visibility, and hunt detail text formatting.

## Task 1: Test Harness

- [ ] **Step 1: Add stdlib test package**

Create `tests/__init__.py` as an empty package marker.

- [ ] **Step 2: Add first failing tests**

Create `tests/test_branch_scope.py` with tests that import the target APIs and assert:

```python
from ycbot.bot.ui import main_menu_text
from ycbot.bot.handlers import _hunt_detail_text
from ycbot.db.repositories import BranchRepository


def test_main_menu_hides_branches_for_branch_runtime():
    text = main_menu_text(can_manage_branches=False)
    assert "Филиалы" not in text


def test_main_menu_shows_branches_for_main_runtime():
    text = main_menu_text(can_manage_branches=True)
    assert "Филиалы" in text


def test_hunt_detail_text_shows_runtime_and_ip_metrics_without_deletion_words():
    text = _hunt_detail_text(
        {
            "job_id": "job-1",
            "status": "running",
            "prefixes": ["84.201"],
            "target_count": 1,
            "target_total": 5,
            "match_count": 2,
            "runtime_seconds": 125,
            "checked_ip_count": 17,
            "active_cloud_count": 3,
            "error": None,
            "matches": [{"ip": "84.201.1.2", "prefix": "84.201", "cloud_id": "cloud-1"}],
            "clouds": [],
        }
    )
    lowered = text.lower()
    assert "время работы" in lowered
    assert "перебрано ip" in lowered
    assert "удален" not in lowered
    assert "удал" not in lowered
```

- [ ] **Step 3: Run tests to verify RED**

Run: `python -m unittest tests.test_branch_scope -v`

Expected: FAIL because `main_menu_text` does not accept `can_manage_branches`, `_hunt_detail_text` does not exist, and `BranchRepository` does not exist.

## Task 2: Branch Data Model

- [ ] **Step 1: Add `BotBranch` and branch columns**

Update `ycbot/db/models.py` with `BotBranch`, `Account.branch_id`, and `HuntJob.branch_id`.

- [ ] **Step 2: Add schema upgrades**

Update `Database.init_models()` to add missing `branch_id` columns and indexes in PostgreSQL after `create_all()`.

- [ ] **Step 3: Add repository methods**

Add `BranchRepository` with `create_branch`, `list_branches`, `get_branch`, `get_active_branch_by_token`, `deactivate_branch`, and `active_branches`.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_branch_scope -v`

Expected: failures move to UI and hunt text APIs only.

## Task 3: Runtime Scope And Menu

- [ ] **Step 1: Add `BotRuntimeScope`**

Define a dataclass in `ycbot/bot/app.py` with `branch_id`, `allowed_chat_ids`, and `can_manage_branches`.

- [ ] **Step 2: Update middleware**

Make access middleware use `BotRuntimeScope.allowed_chat_ids` and inject `bot_scope` into handler data.

- [ ] **Step 3: Update main menu text and keyboard**

Add `can_manage_branches` arguments to `main_menu_text()` and `menu_keyboard()`. Show `Филиалы` only when true.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_branch_scope -v`

Expected: menu tests pass; hunt detail test still fails.

## Task 4: Scheduler Branch Scope

- [ ] **Step 1: Scope account creation/listing/details/deletion**

Add `branch_id` parameters to scheduler account methods and repository account queries.

- [ ] **Step 2: Scope hunt creation/listing/details/stopping**

Add `branch_id` to `HuntStartRequest`, store it in `HuntJob`, and filter hunt lists/details by scope.

- [ ] **Step 3: Preserve main workspace behavior**

Treat `branch_id=None` as `IS NULL` in SQL filters.

- [ ] **Step 4: Run syntax and unit checks**

Run: `python -m unittest tests.test_branch_scope -v`

Expected: remaining failures are only from unimplemented branch UI or runtime manager if imports expose them.

## Task 5: Multi-Bot Runtime

- [ ] **Step 1: Extract dispatcher factory**

Create a helper that builds a dispatcher, registers middlewares, and includes the existing router for a given `BotRuntimeScope`.

- [ ] **Step 2: Add branch runtime manager**

Add a small manager that starts polling tasks for the main bot and active branch bots, tracks tasks by branch ID, and can start/stop a branch at runtime.

- [ ] **Step 3: Wire `run_bot()`**

Build one shared app context, start cleanup once, load active branches, start all polling tasks, and shut down all bot sessions in `finally`.

- [ ] **Step 4: Run compile check**

Run: `python -m compileall ycbot`

Expected: all files compile.

## Task 6: Branch Management UI

- [ ] **Step 1: Add branch keyboards**

Create keyboards for branch list, branch detail, add confirmation, and disable confirmation.

- [ ] **Step 2: Add branch states and handlers**

Add flow: list branches, add name, add token, validate with `getMe`, add owner chat ID, confirm, save branch, start runtime, disable branch.

- [ ] **Step 3: Guard branch handlers**

Every branch-management handler must reject `bot_scope.can_manage_branches=False`.

- [ ] **Step 4: Run compile check**

Run: `python -m compileall ycbot`

Expected: all files compile.

## Task 7: Hunt Detail Metrics

- [ ] **Step 1: Add detail metrics in scheduler**

Return `runtime_seconds`, `checked_ip_count`, and `active_cloud_count` from `hunt_details()`.

- [ ] **Step 2: Extract `_hunt_detail_text()`**

Move hunt detail message formatting into a helper in `handlers.py` and make it display status, runtime, checked IP count, active cloud count, target progress, prefixes, errors, and matches.

- [ ] **Step 3: Remove cloud status list from active hunt detail**

Do not render per-cloud lifecycle rows in the active hunt details screen.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_branch_scope -v`

Expected: PASS.

## Task 8: Docs And Verification

- [ ] **Step 1: Update `.env.example`**

Add a short note that branches are managed from the main bot and stored in the database; no extra env token is needed per branch.

- [ ] **Step 2: Update `README.md`**

Document how the main owner creates a branch and what branch owners can access.

- [ ] **Step 3: Full verification**

Run:

```bash
python -m unittest discover -v
python -m compileall ycbot
```

Expected: tests pass and compileall exits 0.

## Self-Review

- Spec coverage: tasks cover branch data model, access, runtime startup, UI, scheduler filtering, errors, tests, and manual behavior.
- Additional user request coverage: Task 7 removes the cloud lifecycle list from hunt details and replaces it with runtime/IP metrics without deletion wording.
- Placeholder scan: no TBD/TODO/fill-later steps.
- Type consistency: plan uses `BotBranch`, `BranchRepository`, `BotRuntimeScope`, and `branch_id` consistently.
