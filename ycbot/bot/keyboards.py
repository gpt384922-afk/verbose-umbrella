from __future__ import annotations

from aiogram.utils.keyboard import InlineKeyboardBuilder

from ycbot.bot.ui import CUSTOM_EMOJI_IDS
from ycbot.core.prefixes import KNOWN_PREFIXES


def menu_keyboard(*, can_manage_branches: bool = False) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Запустить хант", callback_data="menu:start_hunt", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"], style="success")
    kb.button(text="Аккаунты", callback_data="menu:accounts", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["key"])
    kb.button(text="Активные ханты", callback_data="menu:hunts", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["eyes"])
    kb.button(text="Добавить аккаунт", callback_data="menu:add_account", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["sparkles"], style="success")
    kb.button(text="Синхронизировать", callback_data="menu:sync_all", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])
    if can_manage_branches:
        kb.button(text="Филиалы", callback_data="menu:branches", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["crown"])
        kb.adjust(2, 2, 1, 1)
    else:
        kb.adjust(2, 2, 1)
    return kb


def back_keyboard(target: str = "menu:main") -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data=target)
    return kb


def account_detail_keyboard(account_id: str, *, secrets_visible: bool = False) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if secrets_visible:
        kb.button(text="Скрыть секреты", callback_data=f"accounts:view:{account_id}", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["eyes"])
    else:
        kb.button(
            text="Показать секреты",
            callback_data=f"accounts:secrets:{account_id}",
            icon_custom_emoji_id=CUSTOM_EMOJI_IDS["key"],
        )
    kb.button(text="Удалить аккаунт", callback_data=f"accounts:delete_ask:{account_id}", style="danger")
    kb.button(text="Назад", callback_data="menu:accounts")
    kb.adjust(1)
    return kb


def account_delete_confirm_keyboard(account_id: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, удалить", callback_data=f"accounts:delete_confirm:{account_id}", style="danger")
    kb.button(text="Назад", callback_data=f"accounts:view:{account_id}")
    kb.adjust(1)
    return kb


def account_list_keyboard(accounts: list[dict]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for account in accounts:
        kb.button(
            text=account["name"],
            callback_data=f"accounts:view:{account['id']}",
            icon_custom_emoji_id=CUSTOM_EMOJI_IDS["key"],
        )
    kb.button(text="Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb


def add_account_nav_keyboard(*, can_back: bool = True) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if can_back:
        kb.button(text="Назад", callback_data="add:back")
    kb.button(text="Отмена", callback_data="add:cancel", style="danger")
    kb.adjust(1)
    return kb


def add_account_confirm_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Сохранить аккаунт", callback_data="add:confirm", style="success")
    kb.button(text="Назад", callback_data="add:back")
    kb.button(text="Отмена", callback_data="add:cancel", style="danger")
    kb.adjust(1)
    return kb


def hunt_accounts_keyboard(accounts: list[dict], selected: set[str]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for account in accounts:
        marker = "🟢" if account["id"] in selected else "⚪"
        style = "success" if account["id"] in selected else None
        kb.button(
            text=f"{marker} {account['name']}",
            callback_data=f"hunt:acc:{account['id']}",
            icon_custom_emoji_id=CUSTOM_EMOJI_IDS["key"],
            style=style,
        )
    kb.button(text="Дальше", callback_data="hunt:acc_done", style="success")
    kb.button(text="Отмена", callback_data="hunt:cancel", style="danger")
    kb.adjust(1)
    return kb


def hunt_organizations_keyboard(options: list[dict], selected: set[str]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for item in options:
        key = item["key"]
        marker = "🟢" if key in selected else "⚪"
        style = "success" if key in selected else None
        kb.button(
            text=f"{marker} {item['account_name']} / {item['organization_name']}",
            callback_data=f"hunt:org:{key}",
            icon_custom_emoji_id=CUSTOM_EMOJI_IDS["crown"],
            style=style,
        )
    kb.button(text="Дальше", callback_data="hunt:org_done", style="success")
    kb.button(text="Назад", callback_data="hunt:back:accounts")
    kb.button(text="Отмена", callback_data="hunt:cancel", style="danger")
    kb.adjust(1)
    return kb


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


def hunt_text_step_keyboard(back_data: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data=back_data)
    kb.button(text="Отмена", callback_data="hunt:cancel", style="danger")
    kb.adjust(1)
    return kb


def target_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="1 IP", callback_data="hunt:target:1", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"], style="success")
    kb.button(text="2 IP", callback_data="hunt:target:2", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"], style="success")
    kb.button(text="Назад", callback_data="hunt:back:prefixes")
    kb.button(text="Отмена", callback_data="hunt:cancel", style="danger")
    kb.adjust(2, 1, 1)
    return kb


def hunt_confirm_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Запустить", callback_data="hunt:confirm", style="success")
    kb.button(text="Назад", callback_data="hunt:back:target")
    kb.button(text="Отмена", callback_data="hunt:cancel", style="danger")
    kb.adjust(1)
    return kb


def hunts_keyboard(hunts: list[dict]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for hunt in hunts:
        kb.button(
            text=f"{hunt['job_id'][:8]}  {hunt['match_count']}/{hunt.get('target_total', hunt['target_count'])}",
            callback_data=f"hunt:view:{hunt['job_id']}",
            icon_custom_emoji_id=CUSTOM_EMOJI_IDS["eyes"],
        )
    kb.button(text="Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb


def hunt_detail_keyboard(job_id: str, status: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if status in {"pending", "running"}:
        kb.button(text="Остановить", callback_data=f"hunt:stop:{job_id}", style="danger")
    kb.button(text="К хантам", callback_data="menu:hunts", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["eyes"])
    kb.adjust(1)
    return kb


def branch_list_keyboard(branches: list[dict]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for branch in branches:
        marker = "🟢" if branch["is_active"] else "⚪"
        kb.button(
            text=f"{marker} {branch['name']}",
            callback_data=f"branches:view:{branch['id']}",
            icon_custom_emoji_id=CUSTOM_EMOJI_IDS["crown"],
        )
    kb.button(text="Добавить филиал", callback_data="branches:add", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["sparkles"], style="success")
    kb.button(text="Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb


def branch_detail_keyboard(branch_id: str, *, is_active: bool) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if is_active:
        kb.button(text="Отключить филиал", callback_data=f"branches:disable_ask:{branch_id}", style="danger")
    kb.button(text="К филиалам", callback_data="menu:branches", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["crown"])
    kb.adjust(1)
    return kb


def branch_disable_confirm_keyboard(branch_id: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, отключить", callback_data=f"branches:disable_confirm:{branch_id}", style="danger")
    kb.button(text="Назад", callback_data=f"branches:view:{branch_id}")
    kb.adjust(1)
    return kb


def add_branch_nav_keyboard(*, can_back: bool = True) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if can_back:
        kb.button(text="Назад", callback_data="branch_add:back")
    kb.button(text="Отмена", callback_data="branch_add:cancel", style="danger")
    kb.adjust(1)
    return kb


def add_branch_confirm_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Создать филиал", callback_data="branch_add:confirm", style="success")
    kb.button(text="Назад", callback_data="branch_add:back")
    kb.button(text="Отмена", callback_data="branch_add:cancel", style="danger")
    kb.adjust(1)
    return kb
