# Prefix Picker Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual hunt prefix input with a fixed multi-select list, show read-only preflight findings on the confirmation screen, and protect existing IPs from all known prefixes during hunt preparation.

**Architecture:** Add one prefix catalog module used by UI, scheduler, and hunter. Keep preflight scan in `HuntScheduler` because it already owns branch-scoped account access and YC client creation. Keep final runtime protection in `HunterEngine` so known existing prefixes stay safe even if resources change after confirmation.

**Tech Stack:** Python 3.11+, aiogram 3.27 inline keyboards/FSM, async YC API wrappers, stdlib `unittest` with `AsyncMock`.

---

## File Structure

- Create `ycbot/core/prefixes.py`: known prefix catalog, validation, and IP matching helpers.
- Modify `ycbot/bot/keyboards.py`: add prefix multi-select keyboard.
- Modify `ycbot/bot/handlers.py`: replace text prefix input with callback multi-select, run preflight before confirmation, render findings.
- Modify `ycbot/core/scheduler.py`: validate selected prefixes and add read-only preflight scan.
- Modify `ycbot/core/hunter.py`: protect clouds with existing IPs from any known prefix, while still matching new IPs only against selected prefixes.
- Modify `README.md`: update launch instructions from manual prefix input to button selection and preflight warning.
- Create `tests/test_prefix_picker_preflight.py`: focused tests for catalog, UI, scheduler validation, confirmation text, and hunter protection.

## Task 1: Prefix Catalog And UI Contract

**Files:**
- Create: `ycbot/core/prefixes.py`
- Create: `tests/test_prefix_picker_preflight.py`
- Modify: `ycbot/bot/keyboards.py`
- Modify: `ycbot/bot/handlers.py`

- [ ] **Step 1: Write failing tests for known prefixes, keyboard, and confirmation text**

Create `tests/test_prefix_picker_preflight.py`:

```python
from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ycbot.bot.handlers import _hunt_confirm_text, _hunt_prefixes_text
from ycbot.bot.keyboards import hunt_prefixes_keyboard
from ycbot.core.hunter import HunterEngine, ManagedCloud
from ycbot.core.prefixes import KNOWN_PREFIX_VALUES, match_known_prefix, validate_hunt_prefixes
from ycbot.core.scheduler import HuntScheduler, HuntStartRequest, HuntStartScope
from ycbot.core.state_manager import CloudLifecycle
from ycbot.db.enums import CloudState as DbCloudState, HuntCloudStatus
from ycbot.yc import Address


class PrefixCatalogUiTests(unittest.TestCase):
    def test_known_prefixes_are_fixed_and_match_ip_addresses(self) -> None:
        self.assertEqual(["158.160", "84.201", "51.250"], KNOWN_PREFIX_VALUES)
        self.assertEqual("158.160", match_known_prefix("158.160.10.20"))
        self.assertEqual("84.201", match_known_prefix("84.201.1.2"))
        self.assertEqual("51.250", match_known_prefix("51.250.99.1"))
        self.assertIsNone(match_known_prefix("8.8.8.8"))

    def test_validate_hunt_prefixes_rejects_unknown_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported prefix"):
            validate_hunt_prefixes(["84.201", "1.2"])

    def test_hunt_prefixes_text_contains_operator_descriptions(self) -> None:
        text = _hunt_prefixes_text()

        self.assertIn("158.160", text)
        self.assertIn("84.201", text)
        self.assertIn("51.250", text)
        self.assertIn("для всех операторов", text)
        self.assertIn("только для Мегафон", text)

    def test_hunt_prefixes_keyboard_marks_selected_prefixes(self) -> None:
        markup = hunt_prefixes_keyboard({"84.201"}).as_markup()
        buttons = [button for row in markup.inline_keyboard for button in row]
        by_callback = {button.callback_data: button.text for button in buttons}

        self.assertIn("🟢", by_callback["hunt:prefix:84.201"])
        self.assertIn("⚪", by_callback["hunt:prefix:158.160"])
        self.assertIn("Дальше", by_callback["hunt:prefix_done"])

    def test_hunt_confirm_text_shows_preflight_existing_ips(self) -> None:
        text = _hunt_confirm_text(
            {
                "accounts": [{"id": "acc-1", "name": "Main"}],
                "selected_accounts": ["acc-1"],
                "organization_options": [
                    {
                        "key": "1",
                        "account_id": "acc-1",
                        "account_name": "Main",
                        "organization_id": "org-1",
                        "organization_name": "Org",
                    }
                ],
                "selected_orgs": ["1"],
                "prefixes": ["84.201", "51.250"],
                "target_count": 1,
                "preflight_existing_ips": [
                    {
                        "ip": "158.160.10.20",
                        "prefix": "158.160",
                        "account_name": "Main",
                        "organization_name": "Org",
                        "cloud_id": "cloud-abcdef",
                    }
                ],
                "preflight_errors": [],
            }
        )

        self.assertIn("Уже есть IP", text)
        self.assertIn("158.160.10.20", text)
        self.assertIn("cloud-a", text)
```

- [ ] **Step 2: Run tests to verify RED**

Run: `python -m unittest tests.test_prefix_picker_preflight -v`

Expected: FAIL because `ycbot.core.prefixes` and `hunt_prefixes_keyboard` do not exist, and `_hunt_confirm_text()` does not render preflight findings.

- [ ] **Step 3: Add the prefix catalog**

Create `ycbot/core/prefixes.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrefixOption:
    value: str
    description: str


KNOWN_PREFIXES: tuple[PrefixOption, ...] = (
    PrefixOption("158.160", "для всех операторов"),
    PrefixOption("84.201", "для всех операторов"),
    PrefixOption("51.250", "только для Мегафон"),
)
KNOWN_PREFIX_VALUES = [item.value for item in KNOWN_PREFIXES]
_KNOWN_PREFIX_SET = set(KNOWN_PREFIX_VALUES)


def validate_hunt_prefixes(prefixes: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in prefixes:
        prefix = raw.strip()
        if not prefix or prefix in seen:
            continue
        if prefix not in _KNOWN_PREFIX_SET:
            raise ValueError(f"unsupported prefix: {prefix}")
        cleaned.append(prefix)
        seen.add(prefix)
    if not cleaned:
        raise ValueError("prefixes are required")
    return cleaned


def match_prefix(ip: str | None, prefixes: list[str]) -> str | None:
    if not ip:
        return None
    for prefix in prefixes:
        if ip.startswith(prefix):
            return prefix
    return None


def match_known_prefix(ip: str | None) -> str | None:
    return match_prefix(ip, KNOWN_PREFIX_VALUES)
```

- [ ] **Step 4: Add prefix keyboard and update prefix text**

Modify imports in `ycbot/bot/keyboards.py`:

```python
from ycbot.core.prefixes import KNOWN_PREFIXES
```

Add below `hunt_organizations_keyboard()`:

```python
def hunt_prefixes_keyboard(selected: set[str]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for option in KNOWN_PREFIXES:
        marker = "🟢" if option.value in selected else "⚪"
        style = "success" if option.value in selected else None
        kb.button(
            text=f"{marker} {option.value} · {option.description}",
            callback_data=f"hunt:prefix:{option.value}",
            icon_custom_emoji_id=CUSTOM_EMOJI_IDS["eyes"],
            style=style,
        )
    kb.button(text="Дальше", callback_data="hunt:prefix_done", style="success")
    kb.button(text="Назад", callback_data="hunt:back:organizations")
    kb.button(text="Отмена", callback_data="hunt:cancel", style="danger")
    kb.adjust(1)
    return kb
```

Modify imports in `ycbot/bot/handlers.py`:

```python
from ycbot.bot.keyboards import (
    ...
    hunt_prefixes_keyboard,
    ...
)
from ycbot.core.prefixes import KNOWN_PREFIXES
```

Replace `_hunt_prefixes_text()` body with:

```python
def _hunt_prefixes_text() -> str:
    lines = "\n".join(f"{item.value} — {item.description}" for item in KNOWN_PREFIXES)
    return (
        f"{ce('bolt')} | <b>Запуск ханта</b> ▾\n\n"
        + quote(
            f"{ce('eyes')} <b>Шаг 3/4</b>\n"
            "Выбери один или несколько IP-префиксов.\n\n"
            f"{lines}"
        )
    )
```

- [ ] **Step 5: Render preflight findings in confirmation text**

Add helper in `ycbot/bot/handlers.py` near `_hunt_confirm_text()`:

```python
def _preflight_text(data: dict) -> str:
    rows = data.get("preflight_existing_ips", [])
    errors = data.get("preflight_errors", [])
    lines: list[str] = [f"{ce('eyes')} <b>Проверка существующих IP:</b>"]
    if rows:
        lines.append("Уже есть IP из известных префиксов:")
        for item in rows:
            lines.append(
                "- "
                f"{_code(item['ip'])} · {escape(item['prefix'])} · "
                f"{escape(item.get('account_name') or item['account_id'])} / "
                f"{escape(item.get('organization_name') or item['organization_id'])} · "
                f"cloud={_code(str(item['cloud_id'])[:7])}"
            )
    else:
        lines.append("Существующие IP из известных префиксов не найдены")
    for item in errors:
        lines.append(
            "- "
            f"⚠️ {escape(item.get('account_name') or item['account_id'])} / "
            f"{escape(item.get('organization_name') or item['organization_id'])}: "
            f"{escape(item['error'])}"
        )
    return "\n".join(lines)
```

Update `_hunt_confirm_text()` to insert preflight text before the target line:

```python
        f"{ce('eyes')} <b>Префиксы:</b> {_code(', '.join(data.get('prefixes', [])))}\n"
        + _preflight_text(data)
        + "\n"
        f"{ce('diamond')} <b>Цель:</b> {data.get('target_count')} IP на каждое облако"
```

- [ ] **Step 6: Run tests to verify Task 1 GREEN**

Run: `python -m unittest tests.test_prefix_picker_preflight.PrefixCatalogUiTests -v`

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add tests/test_prefix_picker_preflight.py ycbot/core/prefixes.py ycbot/bot/keyboards.py ycbot/bot/handlers.py
git commit -m "Add fixed hunt prefix picker UI"
```

## Task 2: FSM Prefix Selection And Preflight Hook

**Files:**
- Modify: `ycbot/bot/handlers.py`
- Test: `tests/test_prefix_picker_preflight.py`

- [ ] **Step 1: Add focused confirmation empty-state test**

Append to `PrefixCatalogUiTests` in `tests/test_prefix_picker_preflight.py`:

```python
    def test_hunt_confirm_text_shows_empty_preflight_result(self) -> None:
        text = _hunt_confirm_text(
            {
                "accounts": [{"id": "acc-1", "name": "Main"}],
                "selected_accounts": ["acc-1"],
                "organization_options": [
                    {
                        "key": "1",
                        "account_id": "acc-1",
                        "account_name": "Main",
                        "organization_id": "org-1",
                        "organization_name": "Org",
                    }
                ],
                "selected_orgs": ["1"],
                "prefixes": ["84.201"],
                "target_count": 1,
                "preflight_existing_ips": [],
                "preflight_errors": [],
            }
        )

        self.assertIn("Существующие IP из известных префиксов не найдены", text)
```

- [ ] **Step 2: Run the focused UI tests**

Run: `python -m unittest tests.test_prefix_picker_preflight.PrefixCatalogUiTests -v`

Expected: PASS.

- [ ] **Step 3: Replace the text prefix step with callback multi-select**

In `ycbot/bot/handlers.py`, update every prefix screen render to use `hunt_prefixes_keyboard`:

```python
reply_markup=hunt_prefixes_keyboard(set(data.get("prefixes", []))).as_markup()
```

Specifically update:

- `hunt_back_prefixes()`;
- `hunt_orgs_done()`.

Remove or replace `@router.message(StartHuntFlow.prefixes) async def hunt_set_prefixes(...)` with:

```python
@router.callback_query(StartHuntFlow.prefixes, F.data.startswith("hunt:prefix:"))
async def hunt_toggle_prefix(callback: CallbackQuery, state: FSMContext) -> None:
    prefix = callback.data.split(":", maxsplit=2)[2]
    data = await state.get_data()
    selected = set(data.get("prefixes", []))
    if prefix in selected:
        selected.remove(prefix)
    else:
        selected.add(prefix)
    await state.update_data(prefixes=list(selected))
    await _safe_edit_text(
        callback.message,
        _hunt_prefixes_text(),
        reply_markup=hunt_prefixes_keyboard(selected).as_markup(),
    )
    await callback.answer()


@router.callback_query(StartHuntFlow.prefixes, F.data == "hunt:prefix_done")
async def hunt_prefixes_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("prefixes"):
        await callback.answer("Выбери хотя бы один префикс", show_alert=True)
        return
    await state.set_state(StartHuntFlow.target)
    await _safe_edit_text(
        callback.message,
        _hunt_target_text(),
        reply_markup=target_keyboard().as_markup(),
    )
    await callback.answer()
```

Add a message fallback for users typing in the prefix step:

```python
@router.message(StartHuntFlow.prefixes)
async def hunt_prefixes_message_fallback(message: Message) -> None:
    await message.answer("Выбери префиксы кнопками ниже.")
```

- [ ] **Step 4: Run preflight after target selection**

Change `hunt_target_selected()` signature:

```python
async def hunt_target_selected(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
```

After building `selected_options`, build scopes and call preflight before confirmation:

```python
    scopes = [
        HuntStartScope(
            account_id=item["account_id"],
            organization_id=item["organization_id"],
        )
        for item in selected_options
    ]
    progress = await _safe_edit_text(
        callback.message,
        f"{ce('eyes')} <b>Проверяю существующие IP</b>\n\n"
        "Смотрю облака и публичные адреса из известных префиксов..."
    )
    preflight = await scheduler.scan_existing_prefix_ips(scopes, branch_id=bot_scope.branch_id)
    await state.update_data(
        preflight_existing_ips=preflight["existing_ips"],
        preflight_errors=preflight["errors"],
    )
    data = await state.get_data()
    await state.set_state(StartHuntFlow.confirm)
```

Then render confirmation with `progress`:

```python
    await _safe_edit_text(
        progress,
        _hunt_confirm_text(data),
        reply_markup=hunt_confirm_keyboard().as_markup(),
    )
```

- [ ] **Step 5: Run syntax check**

Run: `python -m compileall ycbot/bot`

Expected: compileall exits 0.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add tests/test_prefix_picker_preflight.py ycbot/bot/handlers.py
git commit -m "Wire hunt prefix selection flow"
```

## Task 3: Scheduler Validation And Read-Only Preflight

**Files:**
- Modify: `ycbot/core/scheduler.py`
- Test: `tests/test_prefix_picker_preflight.py`

- [ ] **Step 1: Add failing scheduler tests**

Append to `tests/test_prefix_picker_preflight.py`:

```python
class SchedulerPrefixTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_hunt_rejects_unknown_prefix_before_database_work(self) -> None:
        scheduler = HuntScheduler(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=SimpleNamespace(),
            logger=logging.getLogger("test"),
        )

        with self.assertRaisesRegex(ValueError, "unsupported prefix"):
            await scheduler.start_hunt(
                HuntStartRequest(
                    requested_by_chat_id=123,
                    branch_id=None,
                    prefixes=["84.201", "1.2"],
                    target_count=1,
                    scopes=[HuntStartScope(account_id="acc-1", organization_id="org-1")],
                )
            )
```

- [ ] **Step 2: Run test to verify RED**

Run: `python -m unittest tests.test_prefix_picker_preflight.SchedulerPrefixTests -v`

Expected: FAIL because `start_hunt()` does not reject unknown prefixes yet.

- [ ] **Step 3: Validate prefixes in scheduler**

Modify imports in `ycbot/core/scheduler.py`:

```python
from ycbot.core.prefixes import match_known_prefix, validate_hunt_prefixes
```

In `start_hunt()`, replace the empty-prefix check with:

```python
        prefixes = validate_hunt_prefixes(request.prefixes)
```

Use `prefixes` when creating the job:

```python
                    target_prefixes=prefixes,
```

- [ ] **Step 4: Run scheduler validation test**

Run: `python -m unittest tests.test_prefix_picker_preflight.SchedulerPrefixTests -v`

Expected: PASS for the unknown-prefix test.

- [ ] **Step 5: Add preflight method**

Add this method to `HuntScheduler` before `start_hunt()`:

```python
    async def scan_existing_prefix_ips(
        self,
        scopes: list[HuntStartScope],
        *,
        branch_id: str | None,
    ) -> dict[str, list[dict]]:
        if not scopes:
            return {"existing_ips": [], "errors": []}

        account_ids = sorted({item.account_id for item in scopes})
        org_names: dict[tuple[str, str], dict] = {}
        try:
            for row in await self.list_account_organizations(account_ids, branch_id=branch_id):
                org_names[(row["account_id"], row["organization_id"])] = row
        except Exception as exc:  # noqa: BLE001
            log_error(self.logger, "hunt.preflight.directory.error", exc)

        existing_ips: list[dict] = []
        errors: list[dict] = []
        scopes_by_account: dict[str, list[HuntStartScope]] = {}
        for scope in scopes:
            scopes_by_account.setdefault(scope.account_id, []).append(scope)

        for account_id, account_scopes in scopes_by_account.items():
            account = await self._get_account_for_branch(account_id, branch_id)
            if account is None:
                for scope in account_scopes:
                    names = org_names.get((scope.account_id, scope.organization_id), {})
                    errors.append(
                        {
                            "account_id": scope.account_id,
                            "account_name": names.get("account_name"),
                            "organization_id": scope.organization_id,
                            "organization_name": names.get("organization_name"),
                            "error": "account not found",
                        }
                    )
                continue

            try:
                async with YcClient(
                    settings=self.settings,
                    oauth_token=account.oauth_token,
                    proxy_url=account.proxy_url,
                    semaphore=self.semaphore,
                    logger=self.logger,
                ) as client:
                    clouds_api = CloudsApi(client=client, settings=self.settings, logger=self.logger)
                    vpc_api = VpcApi(client=client, settings=self.settings, logger=self.logger)
                    for scope in account_scopes:
                        names = org_names.get((scope.account_id, scope.organization_id), {})
                        try:
                            clouds = await clouds_api.list_clouds(scope.organization_id)
                            for cloud in clouds:
                                if cloud.deleting or cloud.state != DbCloudState.ACTIVE:
                                    continue
                                folders = await clouds_api.list_folders(cloud.id)
                                for folder in folders:
                                    for address in await vpc_api.list_addresses(folder.id):
                                        prefix = match_known_prefix(address.ip)
                                        if not prefix:
                                            continue
                                        existing_ips.append(
                                            {
                                                "account_id": scope.account_id,
                                                "account_name": names.get("account_name") or account.name,
                                                "organization_id": scope.organization_id,
                                                "organization_name": names.get("organization_name"),
                                                "cloud_id": cloud.id,
                                                "cloud_name": cloud.name,
                                                "folder_id": folder.id,
                                                "ip": address.ip,
                                                "prefix": prefix,
                                            }
                                        )
                        except Exception as exc:  # noqa: BLE001
                            log_error(self.logger, "hunt.preflight.scope.error", exc, account_id=scope.account_id, org_id=scope.organization_id)
                            errors.append(
                                {
                                    "account_id": scope.account_id,
                                    "account_name": names.get("account_name") or account.name,
                                    "organization_id": scope.organization_id,
                                    "organization_name": names.get("organization_name"),
                                    "error": str(exc) or exc.__class__.__name__,
                                }
                            )
            except Exception as exc:  # noqa: BLE001
                for scope in account_scopes:
                    names = org_names.get((scope.account_id, scope.organization_id), {})
                    errors.append(
                        {
                            "account_id": scope.account_id,
                            "account_name": names.get("account_name") or account.name,
                            "organization_id": scope.organization_id,
                            "organization_name": names.get("organization_name"),
                            "error": str(exc) or exc.__class__.__name__,
                        }
                    )
                    log_error(self.logger, "hunt.preflight.account.error", exc, account_id=scope.account_id, org_id=scope.organization_id)

        existing_ips.sort(key=lambda item: (item["prefix"], item["ip"], item["cloud_id"]))
        return {"existing_ips": existing_ips, "errors": errors}
```

- [ ] **Step 6: Add a focused preflight unit test**

Append to `SchedulerPrefixTests`:

```python
    async def test_scan_existing_prefix_ips_reports_known_prefixes(self) -> None:
        scheduler = HuntScheduler(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=SimpleNamespace(),
            hunter=SimpleNamespace(),
            semaphore=SimpleNamespace(),
            logger=logging.getLogger("test"),
        )
        scheduler._get_account_for_branch = AsyncMock(
            return_value=SimpleNamespace(
                id="acc-1",
                name="Main",
                oauth_token="token",
                proxy_url=None,
            )
        )
        scheduler.list_account_organizations = AsyncMock(
            return_value=[
                {
                    "account_id": "acc-1",
                    "account_name": "Main",
                    "organization_id": "org-1",
                    "organization_name": "Org",
                }
            ]
        )

        class FakeYcClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        class FakeCloudsApi:
            def __init__(self, **kwargs):
                pass

            async def list_clouds(self, organization_id):
                return [
                    SimpleNamespace(
                        id="cloud-1",
                        name="Cloud",
                        state=DbCloudState.ACTIVE,
                        deleting=False,
                    )
                ]

            async def list_folders(self, cloud_id):
                return [SimpleNamespace(id="folder-1", name="Folder")]

        class FakeVpcApi:
            def __init__(self, **kwargs):
                pass

            async def list_addresses(self, folder_id):
                return [Address(id="addr-1", ip="158.160.10.20"), Address(id="addr-2", ip="8.8.8.8")]

        with (
            patch("ycbot.core.scheduler.YcClient", FakeYcClient),
            patch("ycbot.core.scheduler.CloudsApi", FakeCloudsApi),
            patch("ycbot.core.scheduler.VpcApi", FakeVpcApi),
        ):
            result = await scheduler.scan_existing_prefix_ips(
                [HuntStartScope(account_id="acc-1", organization_id="org-1")],
                branch_id=None,
            )

        self.assertEqual([], result["errors"])
        self.assertEqual(1, len(result["existing_ips"]))
        self.assertEqual("158.160.10.20", result["existing_ips"][0]["ip"])
        self.assertEqual("158.160", result["existing_ips"][0]["prefix"])
```

- [ ] **Step 7: Run scheduler tests**

Run: `python -m unittest tests.test_prefix_picker_preflight.SchedulerPrefixTests -v`

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

Run:

```bash
git add tests/test_prefix_picker_preflight.py ycbot/core/scheduler.py
git commit -m "Add hunt prefix preflight scan"
```

## Task 4: Hunter Runtime Protection For Known Prefixes

**Files:**
- Modify: `ycbot/core/hunter.py`
- Test: `tests/test_prefix_picker_preflight.py`

- [ ] **Step 1: Add failing hunter protection test**

Append to `tests/test_prefix_picker_preflight.py`:

```python
class HunterKnownPrefixProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_known_prefix_protects_cloud_even_when_not_selected(self) -> None:
        state = SimpleNamespace()
        state.add_checked_ip = AsyncMock()
        state.set_cloud_lifecycle = AsyncMock()
        state.get_prefixes = AsyncMock(return_value=["84.201"])
        hunter = HunterEngine(
            settings=SimpleNamespace(),
            db=SimpleNamespace(),
            state=state,
            semaphore=SimpleNamespace(),
            logger=logging.getLogger("test"),
        )
        hunter._set_cloud_progress = AsyncMock()
        vpc_api = SimpleNamespace(list_addresses=AsyncMock(return_value=[Address(id="addr-1", ip="158.160.10.20")]))
        cloud = ManagedCloud(
            account_id="acc-1",
            organization_id="org-1",
            cloud_id="cloud-1",
            cloud_name="Cloud",
            folder_id="folder-1",
            billing_account_id="billing-1",
        )

        protected = await hunter._scan_existing_matches("job-1", cloud, vpc_api)

        self.assertTrue(protected)
        state.set_cloud_lifecycle.assert_awaited_once_with("job-1", "cloud-1", CloudLifecycle.IDLE)
        hunter._set_cloud_progress.assert_awaited_once()
        args = hunter._set_cloud_progress.await_args.args
        self.assertEqual(HuntCloudStatus.SKIPPED, args[3])
```

- [ ] **Step 2: Run test to verify RED**

Run: `python -m unittest tests.test_prefix_picker_preflight.HunterKnownPrefixProtectionTests -v`

Expected: FAIL because `_scan_existing_matches()` only checks selected prefixes.

- [ ] **Step 3: Use known prefix matching in hunter pre-scan**

Modify imports in `ycbot/core/hunter.py`:

```python
from ycbot.core.prefixes import match_known_prefix, match_prefix
```

Update `_scan_existing_matches()`:

```python
            selected_prefix = await self._matched_prefix(job_id, address.ip)
            known_prefix = match_known_prefix(address.ip)
            prefix = selected_prefix or known_prefix
            if not prefix:
                continue
```

Change notes and log event to mention known prefix:

```python
                notes=f"preexisting known prefix {prefix}; cloud untouched",
```

Leave `_accept_match()` untouched so existing IPs are not counted as current hunt matches.

Update `_matched_prefix()` to use the shared helper:

```python
    async def _matched_prefix(self, job_id: str, ip: str) -> str | None:
        prefixes = await self.state.get_prefixes(job_id)
        return match_prefix(ip, prefixes)
```

- [ ] **Step 4: Run hunter protection test**

Run: `python -m unittest tests.test_prefix_picker_preflight.HunterKnownPrefixProtectionTests -v`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add tests/test_prefix_picker_preflight.py ycbot/core/hunter.py
git commit -m "Protect known existing prefix IPs"
```

## Task 5: Docs And Full Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README launch instructions**

Modify the quick start section that currently says to type prefixes manually. Replace it with text equivalent to:

```markdown
10. Выберите IP-префиксы кнопками:
    - `158.160` и `84.201` — для всех операторов;
    - `51.250` — только для Мегафон.
11. Выберите, сколько подходящих IP нужно поймать на каждое подготовленное облако.
12. На экране подтверждения проверьте блок существующих IP. Если бот нашел IP из `158.160`, `84.201` или `51.250`, он покажет их перед запуском. Эти IP и их облака не удаляются автоматически, даже если префикс не выбран целью текущего ханта.
13. Нажмите `Запустить`.
```

Also update the behavior section to say:

```markdown
Если уже существующий IP входит в один из известных префиксов `158.160`, `84.201`, `51.250`, бот предупреждает об этом перед запуском и не трогает его облако. Такой IP не засчитывается как успешная находка текущего ханта, если его префикс не выбран пользователем.
```

- [ ] **Step 2: Run focused tests**

Run: `python -m unittest tests.test_prefix_picker_preflight -v`

Expected: PASS.

- [ ] **Step 3: Run existing tests**

Run: `python -m unittest tests.test_branch_scope -v`

Expected: PASS.

- [ ] **Step 4: Run full verification**

Run:

```bash
python -m unittest discover -v
python -m compileall ycbot
```

Expected: all tests pass and compileall exits 0.

- [ ] **Step 5: Commit docs and final verification state**

Run:

```bash
git add README.md
git commit -m "Document fixed hunt prefixes"
```

## Self-Review

- Spec coverage: Tasks cover fixed prefix catalog, prefix multi-select UI, preflight confirmation block, scheduler validation, read-only scan, runtime protection for non-selected known prefixes, docs, and verification.
- Placeholder scan: no TBD/TODO/fill-later language remains. Each code-changing step identifies exact files and concrete code.
- Type consistency: plan consistently uses `KNOWN_PREFIXES`, `KNOWN_PREFIX_VALUES`, `validate_hunt_prefixes()`, `match_known_prefix()`, `hunt_prefixes_keyboard()`, and `scan_existing_prefix_ips()`.
- Scope check: this is one coherent feature with UI, scheduler, and hunter changes that must ship together to satisfy the safety requirement.
