from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup

from ycbot.bot.keyboards import (
    account_delete_confirm_keyboard,
    account_detail_keyboard,
    account_list_keyboard,
    add_account_confirm_keyboard,
    add_account_nav_keyboard,
    add_branch_confirm_keyboard,
    add_branch_nav_keyboard,
    back_keyboard,
    branch_detail_keyboard,
    branch_disable_confirm_keyboard,
    branch_list_keyboard,
    hunt_confirm_keyboard,
    hunt_accounts_keyboard,
    hunt_detail_keyboard,
    hunt_organizations_keyboard,
    hunt_prefixes_keyboard,
    hunt_vm_config_keyboard,
    hunts_keyboard,
    menu_keyboard,
    target_keyboard,
)
from ycbot.bot.runtime import BotRuntimeScope
from ycbot.bot.ui import ce, code, main_menu_text, quote
from ycbot.core.prefixes import KNOWN_PREFIXES
from ycbot.core.scheduler import HuntStartRequest, HuntScheduler, HuntStartScope
from ycbot.core.vm_config import VmHuntConfig, matching_vm_config_preset, vm_config_preset

router = Router(name="main_handlers")


class AddAccountFlow(StatesGroup):
    name = State()
    oauth = State()
    email = State()
    password = State()
    secret = State()
    confirm = State()


class StartHuntFlow(StatesGroup):
    accounts = State()
    organizations = State()
    prefixes = State()
    target = State()
    vm_config = State()
    confirm = State()


class AddBranchFlow(StatesGroup):
    name = State()
    token = State()
    owner = State()
    confirm = State()


def _with_option_keys(options: list[dict]) -> list[dict]:
    return [{**item, "key": str(index)} for index, item in enumerate(options, start=1)]


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned in {"", "-", "skip", "/skip"}:
        return None
    return cleaned


def _mask_token(token: str) -> str:
    if len(token) < 12:
        return "***"
    return f"{token[:6]}...{token[-4:]}"


def _optional_display(value: str | None) -> str:
    return value or "-"


def _secret_status(value: str | None) -> str:
    return "задан" if value else "-"


def _account_text(details: dict, *, reveal_secrets: bool) -> str:
    org_lines = "\n".join(
        f"🏢 {escape(item['name'])}\n   {_code(item['id'])}"
        for item in details["organizations"]
    ) or "—"

    cloud_lines = "\n".join(
        f"{_status_emoji(item.get('runtime'), item.get('state'))} {escape(item['name'])} "
        f"· {escape(_status_label(item['state']))} · billing={escape(item['billing'] or '-')}"
        for item in details["clouds"]
    ) or "—"

    if reveal_secrets:
        access_lines = (
            f"Token: {_code(details['oauth_token'])}\n"
            f"Email: {_code(_optional_display(details.get('email')))}\n"
            f"Password: {_code(_optional_display(details.get('password')))}\n"
            f"Secret: {_code(_optional_display(details.get('secret')))}\n"
            f"Proxy: {_code(_optional_display(details.get('proxy_url')))}"
        )
    else:
        access_lines = (
            f"Token: {_code(_mask_token(details['oauth_token']))}\n"
            f"Email: {_code(_optional_display(details.get('email')))}\n"
            f"Password: {_code(_secret_status(details.get('password')))}\n"
            f"Secret: {_code(_secret_status(details.get('secret')))}\n"
            f"Proxy: {_code(_optional_display(details.get('proxy_url')))}"
        )

    return (
        f"{ce('key')} | <b>{escape(details['name'])}</b> ▾\n\n"
        + quote(f"<b>Доступ:</b>\n{access_lines}")
        + f"\n\n{ce('crown')} <b>Организации</b>\n{org_lines}\n\n"
        f"{ce('eyes')} <b>Облака</b>\n{cloud_lines}"
    )


def _add_account_prompt(step: str, data: dict) -> str:
    if step == "name":
        current = data.get("name")
        suffix = f"\n\nТекущее значение: {_code(current)}" if current else ""
        return (
            f"{ce('sparkles')} | <b>Добавление аккаунта</b> ▾\n\n"
            + quote(f"<b>Шаг 1/5</b>\nВведи понятное название аккаунта.{suffix}")
        )
    if step == "oauth":
        current = data.get("oauth")
        suffix = f"\n\nТекущее значение: {_code(_mask_token(current))}" if current else ""
        return f"{ce('key')} <b>Шаг 2/5</b>\nВставь OAuth token.{suffix}"
    if step == "email":
        current = data.get("email")
        suffix = f"\n\nТекущее значение: {_code(_optional_display(current))}" if current is not None else ""
        return f"{ce('eyes')} <b>Шаг 3/5</b>\nВведи email. Можно отправить <code>/skip</code>.{suffix}"
    if step == "password":
        current = data.get("password")
        suffix = f"\n\nТекущее значение: {_code(_secret_status(current))}" if current is not None else ""
        return f"{ce('key')} <b>Шаг 4/5</b>\nВведи password. Можно отправить <code>/skip</code>.{suffix}"
    current = data.get("secret")
    suffix = f"\n\nТекущее значение: {_code(_secret_status(current))}" if current is not None else ""
    return f"{ce('diamond')} <b>Шаг 5/5</b>\nВведи secret. Можно отправить <code>/skip</code>.{suffix}"


def _add_account_confirm_text(data: dict) -> str:
    return (
        f"{ce('sparkles')} | <b>Проверка аккаунта</b> ▾\n\n"
        + quote(
            "Проверь, все ли верно. Если нужно исправить последний шаг, нажми «Назад»."
        )
        + "\n\n"
        f"{ce('key')} <b>Название:</b> {_code(data.get('name'))}\n"
        f"{ce('key')} <b>OAuth:</b> {_code(_mask_token(data.get('oauth', '')))}\n"
        f"{ce('eyes')} <b>Email:</b> {_code(_optional_display(data.get('email')))}\n"
        f"{ce('key')} <b>Password:</b> {_code(_secret_status(data.get('password')))}\n"
        f"{ce('diamond')} <b>Secret:</b> {_code(_secret_status(data.get('secret')))}"
    )


def _hunt_accounts_text() -> str:
    return (
        f"{ce('bolt')} | <b>Запуск ханта</b> ▾\n\n"
        + quote(
            f"{ce('key')} <b>Шаг 1/5</b>\n"
            "Выбери один или несколько аккаунтов."
        )
    )


def _hunt_organizations_text() -> str:
    return (
        f"{ce('bolt')} | <b>Запуск ханта</b> ▾\n\n"
        + quote(
            f"{ce('crown')} <b>Шаг 2/5</b>\n"
            "Выбери организации для ханта.\n"
            "Облака и каталоги hunter проверит внутри организации."
        )
    )


def _hunt_prefixes_text() -> str:
    lines = "\n".join(f"{item.value} — {item.description}" for item in KNOWN_PREFIXES)
    return (
        f"{ce('bolt')} | <b>Запуск ханта</b> ▾\n\n"
        + quote(
            f"{ce('eyes')} <b>Шаг 3/5</b>\n"
            "Выбери один или несколько IP-префиксов.\n\n"
            f"{lines}"
        )
    )


def _hunt_target_text() -> str:
    return (
        f"{ce('bolt')} | <b>Запуск ханта</b> ▾\n\n"
        + quote(
            f"{ce('diamond')} <b>Шаг 4/5</b>\n"
            "Сколько VM с нужным префиксом нужно поймать за хант?"
        )
    )


def _hunt_vm_config_text(config: VmHuntConfig) -> str:
    preset = matching_vm_config_preset(config)
    preset_label = preset.title if preset else "собран вручную"
    memory = f"{config.memory_gb:g}"
    return (
        f"{ce('bolt')} | <b>Запуск ханта</b> ▾\n\n"
        + quote(
            f"{ce('diamond')} <b>Выберите тарифный план</b>\n"
            "🔎 <b>Найти решение</b> — готовые конфигурации под быстрый подбор IP.\n"
            "➕ <b>Создать конфиг</b> — ручная сборка ниже по CPU/RAM/DISK/% vCPU.\n\n"
            f"Тариф: {_code(preset_label)}\n"
            f"Платформа: {_code(config.platform_label)}\n"
            f"vCPU: {_code(str(config.cores) + ' vCPU')}\n"
            f"RAM: {_code(memory + ' GB')}\n"
            f"Диск: {_code(config.disk_type_label + ' ' + str(config.disk_size_gb) + ' GB')}\n"
            f"Гарантированная доля CPU: {_code(str(config.core_fraction) + '%')}\n"
            "SSH: ключ генерируется автоматически при найденной VM"
        )
    )


def _selected_hunt_options(data: dict) -> list[dict]:
    selected_set = set(data.get("selected_orgs", []))
    return [
        item
        for item in data.get("organization_options", [])
        if item["key"] in selected_set
    ]


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


def _hunt_confirm_text(data: dict) -> str:
    selected_accounts = set(data.get("selected_accounts", []))
    account_names = [
        item["name"]
        for item in data.get("accounts", [])
        if item["id"] in selected_accounts
    ]
    selected_options = _selected_hunt_options(data)
    vm_config = VmHuntConfig.from_dict(data.get("vm_config"))
    org_lines = "\n".join(
        f"- {escape(item['account_name'])} / {escape(item['organization_name'])}"
        for item in selected_options
    ) or "-"

    return (
        f"{ce('bolt')} | <b>Проверка ханта</b> ▾\n\n"
        + quote("Проверь, все ли верно. После подтверждения hunter начнет готовить облака и ловить IP.")
        + "\n\n"
        f"{ce('key')} <b>Аккаунты:</b> {_code(', '.join(account_names) or '-')}\n"
        f"{ce('crown')} <b>Организации:</b>\n{org_lines}\n"
        f"{ce('eyes')} <b>Префиксы:</b> {_code(', '.join(data.get('prefixes', [])))}\n"
        + _preflight_text(data)
        + "\n"
        f"{ce('diamond')} <b>Цель:</b> {data.get('target_count')} VM с нужным префиксом за хант\n"
        f"VM: {_code(vm_config.platform_label)}, {_code(str(vm_config.cores) + ' vCPU')}, "
        f"{_code(str(vm_config.memory_gb) + ' GB RAM')}, "
        f"{_code(vm_config.disk_type_label + ' ' + str(vm_config.disk_size_gb) + ' GB')}, "
        f"{_code(str(vm_config.core_fraction) + '%')}"
    )


def _format_duration(seconds: int | None) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}ч {minutes}м {secs}с"
    if minutes:
        return f"{minutes}м {secs}с"
    return f"{secs}с"


def _hunt_detail_text(details: dict) -> str:
    match_lines = "\n".join(
        f"✅ {_code(item['ip'])} · {escape(item['prefix'])} · cloud={_code(str(item['cloud_id'])[:8])}"
        for item in details.get("matches", [])
    ) or "—"

    return (
        f"{ce('eyes')} | <b>Хант</b> {_code(details['job_id'])} ▾\n\n"
        + quote(
            f"<b>Статус:</b> {escape(_status_label(details.get('status')))}\n"
            f"{ce('bolt')} <b>Время работы:</b> {_format_duration(details.get('runtime_seconds'))}\n"
            f"{ce('eyes')} <b>Перебрано IP:</b> {int(details.get('checked_ip_count') or 0)}\n"
            f"{ce('crown')} <b>Активных облаков:</b> {int(details.get('active_cloud_count') or 0)}\n"
            f"{ce('diamond')} <b>Цель:</b> {details.get('match_count', 0)}/{details.get('target_total', details.get('target_count', 0))} "
            "VM с нужным префиксом\n"
            f"{ce('bolt')} <b>Префиксы:</b> {_code(', '.join(details.get('prefixes', [])))}\n"
            f"⚠️ <b>Ошибка:</b> {_code(details.get('error') or '-')}"
        )
        + f"\n\n{ce('diamond')} <b>Поймано</b>\n{match_lines}"
    )


def _branch_list_text(branches: list[dict]) -> str:
    lines = "\n".join(
        f"{'🟢' if item['is_active'] else '⚪'} <b>{escape(item['name'])}</b> · "
        f"@{escape(item.get('bot_username') or '-')}"
        for item in branches
    ) or "—"
    return (
        f"{ce('crown')} | <b>Филиалы</b> ▾\n\n"
        + quote(
            "Филиалы запускаются как отдельные Telegram-боты в этом же процессе.\n"
            "Добавлять и отключать их может только владелец основного бота."
        )
        + f"\n\n{lines}"
    )


def _branch_detail_text(branch: dict) -> str:
    status = "активен" if branch["is_active"] else "отключен"
    username = f"@{branch['bot_username']}" if branch.get("bot_username") else "-"
    return (
        f"{ce('crown')} | <b>{escape(branch['name'])}</b> ▾\n\n"
        + quote(
            f"<b>Статус:</b> {escape(status)}\n"
            f"{ce('eyes')} <b>Бот:</b> {escape(username)}\n"
            f"{ce('key')} <b>Owner chat ID:</b> {_code(branch['owner_chat_id'])}\n"
            f"{ce('diamond')} <b>ID:</b> {_code(branch['id'])}"
        )
    )


def _add_branch_prompt(step: str, data: dict) -> str:
    if step == "name":
        current = data.get("name")
        suffix = f"\n\nТекущее значение: {_code(current)}" if current else ""
        return (
            f"{ce('sparkles')} | <b>Добавление филиала</b> ▾\n\n"
            + quote(f"<b>Шаг 1/3</b>\nВведи понятное название филиала.{suffix}")
        )
    if step == "token":
        current = data.get("token")
        suffix = f"\n\nТекущее значение: {_code(_mask_token(current))}" if current else ""
        return f"{ce('key')} <b>Шаг 2/3</b>\nВставь token Telegram-бота филиала.{suffix}"
    current = data.get("owner_chat_id")
    suffix = f"\n\nТекущее значение: {_code(current)}" if current else ""
    return f"{ce('eyes')} <b>Шаг 3/3</b>\nВведи Telegram chat ID владельца филиала.{suffix}"


def _add_branch_confirm_text(data: dict) -> str:
    username = f"@{data['bot_username']}" if data.get("bot_username") else "-"
    return (
        f"{ce('sparkles')} | <b>Проверка филиала</b> ▾\n\n"
        + quote("Проверь данные. После сохранения бот филиала запустится сразу.")
        + "\n\n"
        f"{ce('crown')} <b>Название:</b> {_code(data.get('name'))}\n"
        f"{ce('eyes')} <b>Бот:</b> {escape(username)}\n"
        f"{ce('key')} <b>Token:</b> {_code(_mask_token(data.get('token', '')))}\n"
        f"{ce('diamond')} <b>Owner chat ID:</b> {_code(data.get('owner_chat_id'))}"
    )


def _can_manage_branches(bot_scope: BotRuntimeScope) -> bool:
    return bot_scope.can_manage_branches


def _code(value: object) -> str:
    return code(value)


def _error_message(prefix: str, exc: Exception) -> str:
    raw = str(exc) or exc.__class__.__name__
    if len(raw) > 900:
        raw = raw[:900] + "..."
    return f"🔴 <b>{escape(prefix)}</b>\n{_code(raw)}"


async def _safe_callback_answer(callback: CallbackQuery, *args, **kwargs) -> None:
    try:
        await callback.answer(*args, **kwargs)
    except TelegramBadRequest as exc:
        text = str(exc)
        if "query is too old" in text or "query ID is invalid" in text:
            return
        raise


async def _safe_edit_text(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    try:
        return await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return message
        raise


def _status_label(value: str | None) -> str:
    labels = {
        "pending": "ожидает",
        "running": "в работе",
        "completed": "завершен",
        "failed": "ошибка",
        "cancelled": "остановлен",
        "idle": "ожидает",
        "init": "подготовка",
        "hunting": "ловит IP",
        "success": "успех",
        "deleting": "удаляется",
        "recreated": "пересоздано",
        "replaced": "заменено",
        "skipped": "пропущено",
        "active": "активно",
        "blocked": "заблокировано",
        "unknown": "неизвестно",
    }
    return labels.get((value or "").lower(), value or "-")


def _status_emoji(runtime: str | None, db_state: str | None) -> str:
    runtime_value = (runtime or "idle").lower()
    db_value = (db_state or "unknown").lower()

    if runtime_value in {"hunting", "deleting", "recreated", "init"}:
        return "🟡"
    if runtime_value in {"failed"} or db_value in {"blocked", "unknown"}:
        return "🔴"
    return "🟢"


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, bot_scope: BotRuntimeScope) -> None:
    await state.clear()
    await message.answer(
        main_menu_text(can_manage_branches=bot_scope.can_manage_branches),
        reply_markup=menu_keyboard(can_manage_branches=bot_scope.can_manage_branches).as_markup(),
    )


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery, state: FSMContext, bot_scope: BotRuntimeScope) -> None:
    await state.clear()
    await _safe_edit_text(
        callback.message,
        main_menu_text(can_manage_branches=bot_scope.can_manage_branches),
        reply_markup=menu_keyboard(can_manage_branches=bot_scope.can_manage_branches).as_markup(),
    )
    await _safe_callback_answer(callback)


@router.callback_query(F.data == "menu:accounts")
async def accounts_menu(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    await state.clear()
    accounts = await scheduler.list_accounts(branch_id=bot_scope.branch_id)
    if not accounts:
        await callback.message.edit_text(
            f"{ce('key')} | <b>Аккаунты</b> ▾\n\n"
            + quote(
                f"{ce('eyes')} Аккаунтов пока нет.\n"
                f"{ce('sparkles')} Добавь первый аккаунт, чтобы получить организации и запускать ханты."
            ),
            reply_markup=back_keyboard().as_markup(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"{ce('key')} | <b>Аккаунты</b> ▾\n\n"
        + quote(
            f"<b>Подключено:</b> {len(accounts)}\n"
            f"{ce('eyes')} Выбери аккаунт, чтобы посмотреть организации и облака."
        ),
        reply_markup=account_list_keyboard(accounts).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:view:"))
async def account_view(callback: CallbackQuery, scheduler: HuntScheduler, bot_scope: BotRuntimeScope) -> None:
    account_id = callback.data.split(":", maxsplit=2)[2]
    details = await scheduler.account_details(account_id, branch_id=bot_scope.branch_id)
    if details is None:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    await _safe_edit_text(
        callback.message,
        _account_text(details, reveal_secrets=False),
        reply_markup=account_detail_keyboard(account_id).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:secrets:"))
async def account_secrets(callback: CallbackQuery, scheduler: HuntScheduler, bot_scope: BotRuntimeScope) -> None:
    account_id = callback.data.split(":", maxsplit=2)[2]
    details = await scheduler.account_details(account_id, branch_id=bot_scope.branch_id)
    if details is None:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    await _safe_edit_text(
        callback.message,
        _account_text(details, reveal_secrets=True),
        reply_markup=account_detail_keyboard(account_id, secrets_visible=True).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:delete_ask:"))
async def account_delete_ask(callback: CallbackQuery, scheduler: HuntScheduler, bot_scope: BotRuntimeScope) -> None:
    account_id = callback.data.split(":", maxsplit=2)[2]
    details = await scheduler.account_details(account_id, branch_id=bot_scope.branch_id)
    if details is None:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    await _safe_edit_text(
        callback.message,
        f"🔴 <b>Удалить аккаунт?</b>\n\n"
        f"{ce('key')} Аккаунт: <b>{escape(details['name'])}</b>\n\n"
        "Он пропадет из списка и больше не будет использоваться для новых хантов. "
        "Активный хант по этому аккаунту удалить не даст.",
        reply_markup=account_delete_confirm_keyboard(account_id).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:delete_confirm:"))
async def account_delete_confirm(callback: CallbackQuery, scheduler: HuntScheduler, bot_scope: BotRuntimeScope) -> None:
    account_id = callback.data.split(":", maxsplit=2)[2]
    try:
        deleted = await scheduler.delete_account(account_id, branch_id=bot_scope.branch_id)
    except Exception as exc:  # noqa: BLE001
        await _safe_edit_text(
            callback.message,
            _error_message("Не удалось удалить аккаунт", exc),
            reply_markup=back_keyboard(f"accounts:view:{account_id}").as_markup(),
        )
        await callback.answer()
        return

    if not deleted:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    await _safe_edit_text(
        callback.message,
        f"✅ <b>Аккаунт удален</b>\n\nОн больше не будет отображаться в списке аккаунтов.",
        reply_markup=back_keyboard("menu:accounts").as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:branches")
async def branches_menu(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    await state.clear()
    if not _can_manage_branches(bot_scope):
        await callback.answer("Недоступно", show_alert=True)
        return

    branches = await scheduler.list_branches()
    await _safe_edit_text(
        callback.message,
        _branch_list_text(branches),
        reply_markup=branch_list_keyboard(branches).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("branches:view:"))
async def branch_view(callback: CallbackQuery, scheduler: HuntScheduler, bot_scope: BotRuntimeScope) -> None:
    if not _can_manage_branches(bot_scope):
        await callback.answer("Недоступно", show_alert=True)
        return
    branch_id = callback.data.split(":", maxsplit=2)[2]
    branch = await scheduler.branch_details(branch_id)
    if branch is None:
        await callback.answer("Филиал не найден", show_alert=True)
        return

    await _safe_edit_text(
        callback.message,
        _branch_detail_text(branch),
        reply_markup=branch_detail_keyboard(branch_id, is_active=branch["is_active"]).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "branches:add")
async def branch_add_start(callback: CallbackQuery, state: FSMContext, bot_scope: BotRuntimeScope) -> None:
    if not _can_manage_branches(bot_scope):
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.clear()
    await state.set_state(AddBranchFlow.name)
    await _safe_edit_text(
        callback.message,
        _add_branch_prompt("name", {}),
        reply_markup=add_branch_nav_keyboard(can_back=False).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "branch_add:cancel")
async def branch_add_cancel(callback: CallbackQuery, state: FSMContext, bot_scope: BotRuntimeScope) -> None:
    await state.clear()
    if not _can_manage_branches(bot_scope):
        await callback.answer("Недоступно", show_alert=True)
        return
    await _safe_edit_text(
        callback.message,
        f"{ce('sparkles')} <b>Добавление филиала отменено</b>",
        reply_markup=back_keyboard("menu:branches").as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "branch_add:back")
async def branch_add_back(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    if not _can_manage_branches(bot_scope):
        await callback.answer("Недоступно", show_alert=True)
        return
    current = await state.get_state()
    data = await state.get_data()
    previous = {
        AddBranchFlow.token.state: (AddBranchFlow.name, "name", False),
        AddBranchFlow.owner.state: (AddBranchFlow.token, "token", True),
        AddBranchFlow.confirm.state: (AddBranchFlow.owner, "owner", True),
    }
    if current not in previous:
        await state.clear()
        branches = await scheduler.list_branches()
        await _safe_edit_text(
            callback.message,
            _branch_list_text(branches),
            reply_markup=branch_list_keyboard(branches).as_markup(),
        )
        await callback.answer()
        return
    next_state, step, can_back = previous[current]
    await state.set_state(next_state)
    await _safe_edit_text(
        callback.message,
        _add_branch_prompt(step, data),
        reply_markup=add_branch_nav_keyboard(can_back=can_back).as_markup(),
    )
    await callback.answer()


@router.message(AddBranchFlow.name)
async def branch_add_name(message: Message, state: FSMContext, bot_scope: BotRuntimeScope) -> None:
    if not _can_manage_branches(bot_scope):
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("⚠️ Введи название филиала.")
        return
    await state.update_data(name=name)
    await state.set_state(AddBranchFlow.token)
    await message.answer(
        _add_branch_prompt("token", await state.get_data()),
        reply_markup=add_branch_nav_keyboard().as_markup(),
    )


async def _inspect_branch_bot(token: str) -> str | None:
    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        return me.username
    finally:
        await bot.session.close()


@router.message(AddBranchFlow.token)
async def branch_add_token(message: Message, state: FSMContext, bot_scope: BotRuntimeScope) -> None:
    if not _can_manage_branches(bot_scope):
        return
    token = (message.text or "").strip()
    if not token:
        await message.answer("⚠️ Вставь token филиального бота.")
        return
    try:
        username = await _inspect_branch_bot(token)
    except Exception as exc:  # noqa: BLE001
        await message.answer(_error_message("Не удалось проверить token бота", exc))
        return

    await state.update_data(token=token, bot_username=username)
    await state.set_state(AddBranchFlow.owner)
    await message.answer(
        _add_branch_prompt("owner", await state.get_data()),
        reply_markup=add_branch_nav_keyboard().as_markup(),
    )


@router.message(AddBranchFlow.owner)
async def branch_add_owner(message: Message, state: FSMContext, bot_scope: BotRuntimeScope) -> None:
    if not _can_manage_branches(bot_scope):
        return
    raw = (message.text or "").strip()
    try:
        owner_chat_id = int(raw)
    except ValueError:
        await message.answer("⚠️ Owner chat ID должен быть числом.")
        return
    await state.update_data(owner_chat_id=owner_chat_id)
    data = await state.get_data()
    await state.set_state(AddBranchFlow.confirm)
    await message.answer(
        _add_branch_confirm_text(data),
        reply_markup=add_branch_confirm_keyboard().as_markup(),
    )


@router.callback_query(AddBranchFlow.confirm, F.data == "branch_add:confirm")
async def branch_add_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
    branch_manager: object | None = None,
) -> None:
    if not _can_manage_branches(bot_scope):
        await callback.answer("Недоступно", show_alert=True)
        return
    data = await state.get_data()
    try:
        branch = await scheduler.add_branch(
            name=data["name"],
            bot_token=data["token"],
            owner_chat_id=int(data["owner_chat_id"]),
            bot_username=data.get("bot_username"),
        )
        if branch_manager is not None and hasattr(branch_manager, "start_branch"):
            await branch_manager.start_branch(branch)
    except Exception as exc:  # noqa: BLE001
        await _safe_edit_text(
            callback.message,
            _error_message("Не удалось создать филиал", exc),
            reply_markup=add_branch_confirm_keyboard().as_markup(),
        )
        await callback.answer()
        return

    await state.clear()
    await _safe_edit_text(
        callback.message,
        (
            "✅ <b>Филиал создан</b>\n\n"
            f"{ce('crown')} Название: <b>{escape(branch['name'])}</b>\n"
            f"{ce('eyes')} Бот: <b>@{escape(branch.get('bot_username') or '-')}</b>\n"
            f"{ce('key')} Owner chat ID: {_code(branch['owner_chat_id'])}"
        ),
        reply_markup=back_keyboard("menu:branches").as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("branches:disable_ask:"))
async def branch_disable_ask(callback: CallbackQuery, scheduler: HuntScheduler, bot_scope: BotRuntimeScope) -> None:
    if not _can_manage_branches(bot_scope):
        await callback.answer("Недоступно", show_alert=True)
        return
    branch_id = callback.data.split(":", maxsplit=2)[2]
    branch = await scheduler.branch_details(branch_id)
    if branch is None:
        await callback.answer("Филиал не найден", show_alert=True)
        return
    await _safe_edit_text(
        callback.message,
        f"🔴 <b>Отключить филиал?</b>\n\n"
        f"{ce('crown')} Филиал: <b>{escape(branch['name'])}</b>\n\n"
        "Бот перестанет принимать сообщения. Аккаунты и история хантов останутся в базе.",
        reply_markup=branch_disable_confirm_keyboard(branch_id).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("branches:disable_confirm:"))
async def branch_disable_confirm(
    callback: CallbackQuery,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
    branch_manager: object | None = None,
) -> None:
    if not _can_manage_branches(bot_scope):
        await callback.answer("Недоступно", show_alert=True)
        return
    branch_id = callback.data.split(":", maxsplit=2)[2]
    disabled = await scheduler.disable_branch(branch_id)
    if not disabled:
        await callback.answer("Филиал не найден или уже отключен", show_alert=True)
        return
    if branch_manager is not None and hasattr(branch_manager, "stop_branch"):
        await branch_manager.stop_branch(branch_id)
    await _safe_edit_text(
        callback.message,
        "✅ <b>Филиал отключен</b>",
        reply_markup=back_keyboard("menu:branches").as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:add_account")
async def add_account_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddAccountFlow.name)
    await _safe_edit_text(
        callback.message,
        _add_account_prompt("name", {}),
        reply_markup=add_account_nav_keyboard(can_back=False).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "add:cancel")
async def add_account_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit_text(
        callback.message,
        f"{ce('sparkles')} <b>Добавление аккаунта отменено</b>",
        reply_markup=back_keyboard().as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "add:back")
async def add_account_back(callback: CallbackQuery, state: FSMContext, bot_scope: BotRuntimeScope) -> None:
    current = await state.get_state()
    data = await state.get_data()
    previous = {
        AddAccountFlow.oauth.state: (AddAccountFlow.name, "name", False),
        AddAccountFlow.email.state: (AddAccountFlow.oauth, "oauth", True),
        AddAccountFlow.password.state: (AddAccountFlow.email, "email", True),
        AddAccountFlow.secret.state: (AddAccountFlow.password, "password", True),
        AddAccountFlow.confirm.state: (AddAccountFlow.secret, "secret", True),
    }

    if current not in previous:
        await state.clear()
        await _safe_edit_text(
            callback.message,
            main_menu_text(can_manage_branches=bot_scope.can_manage_branches),
            reply_markup=menu_keyboard(can_manage_branches=bot_scope.can_manage_branches).as_markup(),
        )
        await callback.answer()
        return

    next_state, step, can_back = previous[current]
    await state.set_state(next_state)
    await _safe_edit_text(
        callback.message,
        _add_account_prompt(step, data),
        reply_markup=add_account_nav_keyboard(can_back=can_back).as_markup(),
    )
    await callback.answer()


@router.message(AddAccountFlow.name)
async def add_account_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("⚠️ Введи название аккаунта.")
        return
    await state.update_data(name=name)
    await state.set_state(AddAccountFlow.oauth)
    await message.answer(
        _add_account_prompt("oauth", await state.get_data()),
        reply_markup=add_account_nav_keyboard().as_markup(),
    )


@router.message(AddAccountFlow.oauth)
async def add_account_oauth(message: Message, state: FSMContext) -> None:
    oauth = (message.text or "").strip()
    if not oauth:
        await message.answer("⚠️ Вставь OAuth token.")
        return
    await state.update_data(oauth=oauth)
    await state.set_state(AddAccountFlow.email)
    await message.answer(
        _add_account_prompt("email", await state.get_data()),
        reply_markup=add_account_nav_keyboard().as_markup(),
    )


@router.message(AddAccountFlow.email)
async def add_account_email(message: Message, state: FSMContext) -> None:
    await state.update_data(email=_optional(message.text))
    await state.set_state(AddAccountFlow.password)
    await message.answer(
        _add_account_prompt("password", await state.get_data()),
        reply_markup=add_account_nav_keyboard().as_markup(),
    )


@router.message(AddAccountFlow.password)
async def add_account_password(message: Message, state: FSMContext) -> None:
    await state.update_data(password=_optional(message.text))
    await state.set_state(AddAccountFlow.secret)
    await message.answer(
        _add_account_prompt("secret", await state.get_data()),
        reply_markup=add_account_nav_keyboard().as_markup(),
    )


@router.message(AddAccountFlow.secret)
async def add_account_secret(message: Message, state: FSMContext) -> None:
    await state.update_data(secret=_optional(message.text))
    data = await state.get_data()
    await state.set_state(AddAccountFlow.confirm)
    await message.answer(
        _add_account_confirm_text(data),
        reply_markup=add_account_confirm_keyboard().as_markup(),
    )


@router.callback_query(AddAccountFlow.confirm, F.data == "add:confirm")
async def add_account_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    data = await state.get_data()
    progress = await _safe_edit_text(
        callback.message,
        f"{ce('sparkles')} <b>Добавляю аккаунт</b>\n\n"
        "Сохраняю данные, подтягиваю организации и платежные аккаунты...",
    )
    await callback.answer()
    try:
        account_id = await scheduler.add_account(
            branch_id=bot_scope.branch_id,
            name=data["name"],
            oauth_token=data["oauth"],
            email=data.get("email"),
            password=data.get("password"),
            secret=data.get("secret"),
            proxy_url=None,
        )
        summary = await scheduler.refresh_account_directory(account_id, branch_id=bot_scope.branch_id)
    except Exception as exc:  # noqa: BLE001
        await _safe_edit_text(
            progress,
            _error_message("Не удалось добавить аккаунт", exc),
            reply_markup=add_account_confirm_keyboard().as_markup(),
        )
        return

    await state.clear()
    await _safe_edit_text(
        progress,
        (
            "✅ <b>Аккаунт добавлен</b>\n\n"
            f"🆔 ID: {_code(account_id)}\n"
            f"{ce('crown')} Организаций: <b>{summary['organizations']}</b>\n"
            f"{ce('diamond')} Платежных аккаунтов: <b>{summary['billing_accounts']}</b>\n"
            f"{ce('bolt')} Облака будут подготовлены при запуске ханта."
        ),
        reply_markup=back_keyboard().as_markup(),
    )


@router.callback_query(F.data == "menu:sync_all")
async def sync_all(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    await state.clear()
    await callback.answer()
    status = await callback.message.answer(f"{ce('diamond')} <b>Синхронизация</b>\n\nПроверяю все аккаунты...")
    accounts = await scheduler.list_accounts(branch_id=bot_scope.branch_id)
    if not accounts:
        await status.edit_text(f"{ce('diamond')} <b>Синхронизировать нечего</b>\n\nАккаунтов пока нет.")
        return

    lines: list[str] = []
    for account in accounts:
        try:
            result = await scheduler.sync_account(account["id"], ensure_capacity=True, branch_id=bot_scope.branch_id)
            lines.append(
                f"✅ <b>{escape(account['name'])}</b>: "
                f"{ce('crown')} {result['organizations']} · {ce('diamond')} {result['billing_accounts']} · {ce('eyes')} {result['active_clouds']}"
            )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"🔴 <b>{escape(account['name'])}</b>: {_code(str(exc) or exc.__class__.__name__)}")

    await status.edit_text(
        f"{ce('diamond')} <b>Синхронизация завершена</b>\n\n" + "\n".join(lines),
        reply_markup=back_keyboard().as_markup(),
    )


@router.callback_query(F.data == "menu:start_hunt")
async def hunt_start(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    accounts = await scheduler.list_accounts(branch_id=bot_scope.branch_id)
    if not accounts:
        await callback.answer("Сначала добавь аккаунт", show_alert=True)
        return

    await state.clear()
    await state.set_state(StartHuntFlow.accounts)
    await state.update_data(accounts=accounts, selected_accounts=[])

    await _safe_edit_text(
        callback.message,
        _hunt_accounts_text(),
        reply_markup=hunt_accounts_keyboard(accounts, set()).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "hunt:cancel")
async def hunt_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit_text(
        callback.message,
        f"{ce('bolt')} <b>Запуск ханта отменен</b>",
        reply_markup=back_keyboard().as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "hunt:back:accounts")
async def hunt_back_accounts(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    accounts = data.get("accounts", [])
    selected = set(data.get("selected_accounts", []))
    await state.set_state(StartHuntFlow.accounts)
    await _safe_edit_text(
        callback.message,
        _hunt_accounts_text(),
        reply_markup=hunt_accounts_keyboard(accounts, selected).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "hunt:back:organizations")
async def hunt_back_organizations(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    options = data.get("organization_options", [])
    selected = set(data.get("selected_orgs", []))
    await state.set_state(StartHuntFlow.organizations)
    await _safe_edit_text(
        callback.message,
        _hunt_organizations_text(),
        reply_markup=hunt_organizations_keyboard(options, selected).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "hunt:back:prefixes")
async def hunt_back_prefixes(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = set(data.get("prefixes", []))
    await state.set_state(StartHuntFlow.prefixes)
    await _safe_edit_text(
        callback.message,
        _hunt_prefixes_text(),
        reply_markup=hunt_prefixes_keyboard(selected).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "hunt:back:target")
async def hunt_back_target(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(StartHuntFlow.target)
    await _safe_edit_text(
        callback.message,
        _hunt_target_text(),
        reply_markup=target_keyboard().as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "hunt:back:vm_config")
async def hunt_back_vm_config(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    config = VmHuntConfig.from_dict(data.get("vm_config"))
    page = int(data.get("vm_config_page", 0) or 0)
    await state.set_state(StartHuntFlow.vm_config)
    await _safe_edit_text(
        callback.message,
        _hunt_vm_config_text(config),
        reply_markup=hunt_vm_config_keyboard(config, page=page).as_markup(),
    )
    await callback.answer()


@router.callback_query(StartHuntFlow.accounts, F.data.startswith("hunt:acc:"))
async def hunt_toggle_account(callback: CallbackQuery, state: FSMContext) -> None:
    account_id = callback.data.split(":", maxsplit=2)[2]
    data = await state.get_data()
    selected = set(data.get("selected_accounts", []))
    accounts = data.get("accounts", [])

    if account_id in selected:
        selected.remove(account_id)
    else:
        selected.add(account_id)

    await state.update_data(selected_accounts=list(selected))
    await _safe_edit_text(
        callback.message,
        _hunt_accounts_text(),
        reply_markup=hunt_accounts_keyboard(accounts, selected).as_markup(),
    )
    await callback.answer()


@router.callback_query(StartHuntFlow.accounts, F.data == "hunt:acc_done")
async def hunt_accounts_done(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    await callback.answer()
    data = await state.get_data()
    selected_accounts = data.get("selected_accounts", [])
    if not selected_accounts:
        await callback.message.answer("⚠️ Выбери хотя бы один аккаунт.")
        return

    await _safe_edit_text(callback.message, f"{ce('eyes')} <b>Обновляю организации</b>\n\nЗабираю свежий список из Yandex Cloud...")
    for account_id in selected_accounts:
        try:
            await scheduler.refresh_account_directory(account_id, branch_id=bot_scope.branch_id)
        except Exception as exc:  # noqa: BLE001
            await _safe_edit_text(
                callback.message,
                _error_message("Не удалось обновить организации", exc),
                reply_markup=back_keyboard("menu:start_hunt").as_markup(),
            )
            return

    options = await scheduler.list_account_organizations(selected_accounts, branch_id=bot_scope.branch_id)
    if not options:
        await _safe_edit_text(
            callback.message,
            "⚠️ <b>Организации не найдены</b>\n\n"
            "На выбранных аккаунтах нет доступных организаций.",
            reply_markup=back_keyboard("menu:start_hunt").as_markup(),
        )
        return

    keyed_options = _with_option_keys(options)
    await state.set_state(StartHuntFlow.organizations)
    await state.update_data(organization_options=keyed_options, selected_orgs=[])
    await _safe_edit_text(
        callback.message,
        _hunt_organizations_text(),
        reply_markup=hunt_organizations_keyboard(keyed_options, set()).as_markup(),
    )


@router.callback_query(StartHuntFlow.organizations, F.data.startswith("hunt:org:"))
async def hunt_toggle_org(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", maxsplit=2)[2]
    data = await state.get_data()
    options = data.get("organization_options", [])
    selected = set(data.get("selected_orgs", []))

    if key in selected:
        selected.remove(key)
    else:
        selected.add(key)

    await state.update_data(selected_orgs=list(selected))
    await _safe_edit_text(
        callback.message,
        _hunt_organizations_text(),
        reply_markup=hunt_organizations_keyboard(options, selected).as_markup(),
    )
    await callback.answer()


@router.callback_query(StartHuntFlow.organizations, F.data == "hunt:org_done")
async def hunt_orgs_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = data.get("selected_orgs", [])
    if not selected:
        await callback.answer("Выбери хотя бы одну организацию", show_alert=True)
        return

    await state.set_state(StartHuntFlow.prefixes)
    await _safe_edit_text(
        callback.message,
        _hunt_prefixes_text(),
        reply_markup=hunt_prefixes_keyboard(set(data.get("prefixes", []))).as_markup(),
    )
    await callback.answer()


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


@router.message(StartHuntFlow.prefixes)
async def hunt_prefixes_message_fallback(message: Message) -> None:
    await message.answer("Выбери префиксы кнопками ниже.")


@router.callback_query(StartHuntFlow.target, F.data.startswith("hunt:target:"))
async def hunt_target_selected(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    target_count = int(callback.data.split(":", maxsplit=2)[2])
    data = await state.get_data()
    config = VmHuntConfig.from_dict(data.get("vm_config"))
    await state.update_data(target_count=target_count, vm_config=config.to_dict(), vm_config_page=0)
    await state.set_state(StartHuntFlow.vm_config)
    await _safe_edit_text(
        callback.message,
        _hunt_vm_config_text(config),
        reply_markup=hunt_vm_config_keyboard(config, page=0).as_markup(),
    )


@router.callback_query(StartHuntFlow.vm_config, F.data.startswith("hunt:vm_page:"))
async def hunt_vm_config_page_selected(callback: CallbackQuery, state: FSMContext) -> None:
    page = int(callback.data.split(":", maxsplit=2)[2])
    data = await state.get_data()
    config = VmHuntConfig.from_dict(data.get("vm_config"))
    await state.update_data(vm_config_page=page)
    await _safe_edit_text(
        callback.message,
        _hunt_vm_config_text(config),
        reply_markup=hunt_vm_config_keyboard(config, page=page).as_markup(),
    )
    await callback.answer()


@router.callback_query(StartHuntFlow.vm_config, F.data.startswith("hunt:vm:"))
async def hunt_vm_config_selected(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, field, raw_value = callback.data.split(":", maxsplit=3)
    data = await state.get_data()
    config = VmHuntConfig.from_dict(data.get("vm_config")).to_dict()
    page = int(data.get("vm_config_page", 0) or 0)
    if field == "noop":
        await callback.answer()
        return
    if field == "preset":
        preset = vm_config_preset(raw_value)
        if preset is None:
            await callback.answer("Неизвестный тариф", show_alert=True)
            return
        config = preset.config.to_dict()
    elif field == "platform":
        config["platform_id"] = raw_value
    elif field == "cores":
        config["cores"] = int(raw_value)
    elif field == "memory":
        config["memory_gb"] = float(raw_value)
    elif field == "disk_type":
        config["disk_type_id"] = raw_value
    elif field == "disk_size":
        config["disk_size_gb"] = int(raw_value)
    elif field == "fraction":
        config["core_fraction"] = int(raw_value)
    selected = VmHuntConfig.from_dict(config)
    await state.update_data(vm_config=selected.to_dict())
    await _safe_edit_text(
        callback.message,
        _hunt_vm_config_text(selected),
        reply_markup=hunt_vm_config_keyboard(selected, page=page).as_markup(),
    )
    await callback.answer()


@router.callback_query(StartHuntFlow.vm_config, F.data == "hunt:vm_done")
async def hunt_vm_config_done(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    await callback.answer()
    data = await state.get_data()
    selected_options = _selected_hunt_options(data)
    if not selected_options:
        await callback.message.answer("⚠️ Организация не выбрана.")
        return

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
        "Смотрю облака и публичные адреса из известных префиксов...",
    )
    preflight = await scheduler.scan_existing_prefix_ips(scopes, branch_id=bot_scope.branch_id)
    await state.update_data(
        preflight_existing_ips=preflight["existing_ips"],
        preflight_errors=preflight["errors"],
    )
    data = await state.get_data()
    await state.set_state(StartHuntFlow.confirm)
    await _safe_edit_text(
        progress,
        _hunt_confirm_text(data),
        reply_markup=hunt_confirm_keyboard().as_markup(),
    )


@router.callback_query(StartHuntFlow.confirm, F.data == "hunt:confirm")
async def hunt_start_execute(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    await callback.answer()
    data = await state.get_data()

    prefixes = data.get("prefixes", [])
    target_count = int(data.get("target_count", 1))
    vm_config = VmHuntConfig.from_dict(data.get("vm_config"))
    selected_options = _selected_hunt_options(data)

    if not selected_options:
        await callback.message.answer("⚠️ Организация не выбрана.")
        return

    scopes = [
        HuntStartScope(
            account_id=item["account_id"],
            organization_id=item["organization_id"],
        )
        for item in selected_options
    ]

    progress = await _safe_edit_text(
        callback.message,
        f"{ce('bolt')} <b>Запускаю хант</b>\n\n"
        "Создаю задачу. Облака и каталоги будут подготовлены внутри выбранных организаций."
    )
    try:
        for account_id in sorted({item.account_id for item in scopes}):
            await scheduler.refresh_account_directory(account_id, branch_id=bot_scope.branch_id)
        job_id = await scheduler.start_hunt(
            HuntStartRequest(
                requested_by_chat_id=callback.message.chat.id,
                branch_id=bot_scope.branch_id,
                prefixes=prefixes,
                target_count=target_count,
                scopes=scopes,
                vm_config=vm_config.to_dict(),
            )
        )
    except Exception as exc:  # noqa: BLE001
        await _safe_edit_text(progress, _error_message("Не удалось запустить хант", exc), reply_markup=back_keyboard().as_markup())
        await state.clear()
        return

    await _safe_edit_text(
        progress,
        (
            f"{ce('bolt')} <b>Хант запущен</b>\n\n"
            f"🆔 Задача: {_code(job_id)}\n"
            f"{ce('crown')} Организаций: <b>{len(scopes)}</b>\n"
            f"{ce('key')} Аккаунтов: <b>{len({item.account_id for item in scopes})}</b>\n"
            f"{ce('eyes')} Префиксы: {_code(', '.join(prefixes))}\n"
            f"{ce('diamond')} Цель: <b>{target_count}</b> VM с нужным префиксом за хант\n"
            f"VM: {_code(vm_config.platform_label)}, {_code(str(vm_config.cores) + ' vCPU')}, "
            f"{_code(str(vm_config.memory_gb) + ' GB RAM')}"
        ),
        reply_markup=back_keyboard("menu:hunts").as_markup(),
    )
    await state.clear()


@router.callback_query(F.data == "menu:hunts")
async def hunts_list(
    callback: CallbackQuery,
    state: FSMContext,
    scheduler: HuntScheduler,
    bot_scope: BotRuntimeScope,
) -> None:
    await state.clear()
    await _safe_callback_answer(callback)
    hunts = await scheduler.list_hunts(branch_id=bot_scope.branch_id)
    if not hunts:
        await _safe_edit_text(
            callback.message,
            f"{ce('eyes')} | <b>Активные ханты</b> ▾\n\n"
            + quote(f"{ce('sparkles')} Активных хантов нет.\n{ce('bolt')} Запусти новый хант из главного меню."),
            reply_markup=back_keyboard().as_markup(),
        )
        return

    lines = [
        f"{ce('eyes')} {_code(item['job_id'][:8])} · {escape(_status_label(item['status']))} · "
        f"{ce('diamond')} {item['match_count']}/{item.get('target_total', item['target_count'])}"
        for item in hunts
    ]

    await _safe_edit_text(
        callback.message,
        f"{ce('eyes')} | <b>Активные ханты</b> ▾\n\n" + "\n".join(lines),
        reply_markup=hunts_keyboard(hunts).as_markup(),
    )


@router.callback_query(F.data.startswith("hunt:view:"))
async def hunt_view(callback: CallbackQuery, scheduler: HuntScheduler, bot_scope: BotRuntimeScope) -> None:
    job_id = callback.data.split(":", maxsplit=2)[2]
    details = await scheduler.hunt_details(job_id, branch_id=bot_scope.branch_id)
    if details is None:
        await callback.answer("Хант не найден", show_alert=True)
        return

    await callback.message.edit_text(
        _hunt_detail_text(details),
        reply_markup=hunt_detail_keyboard(job_id, details["status"]).as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hunt:stop:"))
async def hunt_stop(callback: CallbackQuery, scheduler: HuntScheduler, bot_scope: BotRuntimeScope) -> None:
    job_id = callback.data.split(":", maxsplit=2)[2]
    stopped = await scheduler.stop_hunt(job_id, branch_id=bot_scope.branch_id)
    if stopped:
        await callback.answer("Хант остановлен")
    else:
        await callback.answer("Хант сейчас не запущен", show_alert=True)

    details = await scheduler.hunt_details(job_id, branch_id=bot_scope.branch_id)
    if details:
        await callback.message.edit_text(
            f"🛑 <b>Хант остановлен</b>\n\nЗадача: {_code(job_id)}\nСтатус: <b>{escape(_status_label(details['status']))}</b>",
            reply_markup=back_keyboard("menu:hunts").as_markup(),
        )
