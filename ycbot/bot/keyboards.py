from __future__ import annotations

from aiogram.utils.keyboard import InlineKeyboardBuilder

from ycbot.bot.ui import CUSTOM_EMOJI_IDS
from ycbot.core.prefixes import KNOWN_PREFIXES
from ycbot.core.vm_config import (
    ALLOWED_CORE_FRACTIONS,
    ALLOWED_CORES,
    ALLOWED_DISK_SIZE_GB,
    ALLOWED_MEMORY_GB,
    DISK_TYPE_LABELS,
    PLATFORM_LABELS,
    VM_CONFIG_PRESETS,
    VmHuntConfig,
    matching_vm_config_preset,
)


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
    kb.button(text="Загрузить cookies", callback_data=f"accounts:cookies_ask:{account_id}")
    kb.button(text="Указать center proxy", callback_data=f"accounts:proxy_ask:{account_id}")
    kb.button(text="Тест: создать организацию", callback_data=f"accounts:org_test_ask:{account_id}", style="success")
    kb.button(text="Удалить аккаунт", callback_data=f"accounts:delete_ask:{account_id}", style="danger")
    kb.button(text="Назад", callback_data="menu:accounts")
    kb.adjust(1)
    return kb


def account_org_test_confirm_keyboard(account_id: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Создать тестовую организацию", callback_data=f"accounts:org_test_confirm:{account_id}", style="success")
    kb.button(text="Назад", callback_data=f"accounts:view:{account_id}")
    kb.adjust(1)
    return kb


def account_cookie_upload_keyboard(account_id: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data=f"accounts:view:{account_id}")
    kb.button(text="Отмена", callback_data="accounts:cookies_cancel", style="danger")
    kb.adjust(1)
    return kb


def account_proxy_update_keyboard(account_id: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data=f"accounts:view:{account_id}")
    kb.button(text="Отмена", callback_data="accounts:proxy_cancel", style="danger")
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
    for count in range(1, 6):
        kb.button(
            text=f"{count} VM",
            callback_data=f"hunt:target:{count}",
            icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"],
            style="success",
        )
    kb.button(text="Назад", callback_data="hunt:back:prefixes")
    kb.button(text="Отмена", callback_data="hunt:cancel", style="danger")
    kb.adjust(3, 2, 1, 1)
    return kb


VM_CONFIG_PRESET_PAGE_SIZE = 3


def hunt_vm_config_keyboard(config: VmHuntConfig, *, page: int = 0, manual: bool = False) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    page_count = max(1, (len(VM_CONFIG_PRESETS) + VM_CONFIG_PRESET_PAGE_SIZE - 1) // VM_CONFIG_PRESET_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    selected_preset = matching_vm_config_preset(config)

    for platform_id, label in PLATFORM_LABELS.items():
        marker = "🟢" if config.platform_id == platform_id else "⚪"
        kb.button(text=f"{marker} CPU: {label}", callback_data=f"hunt:vm:platform:{platform_id}")

    if manual:
        for cores in ALLOWED_CORES:
            marker = "🟢" if config.cores == cores else "⚪"
            kb.button(text=f"{marker} {cores} vCPU", callback_data=f"hunt:vm:cores:{cores}")
        for memory_gb in ALLOWED_MEMORY_GB:
            marker = "🟢" if config.memory_gb == memory_gb else "⚪"
            kb.button(text=f"{marker} {memory_gb:g} GB RAM", callback_data=f"hunt:vm:memory:{memory_gb}")
        for disk_type_id, label in DISK_TYPE_LABELS.items():
            marker = "🟢" if config.disk_type_id == disk_type_id else "⚪"
            kb.button(text=f"{marker} {label}", callback_data=f"hunt:vm:disk_type:{disk_type_id}")
        for disk_size_gb in ALLOWED_DISK_SIZE_GB:
            marker = "🟢" if config.disk_size_gb == disk_size_gb else "⚪"
            kb.button(text=f"{marker} {disk_size_gb} GB", callback_data=f"hunt:vm:disk_size:{disk_size_gb}")
        for fraction in ALLOWED_CORE_FRACTIONS:
            marker = "🟢" if config.core_fraction == fraction else "⚪"
            kb.button(text=f"{marker} {fraction}%", callback_data=f"hunt:vm:fraction:{fraction}")
        kb.button(text="К тарифам", callback_data="hunt:vm_mode:tariffs")
        adjust = (1, 1, 1, 3, 5, 3, 5, 4, 1, 1, 1, 1)
    else:
        start = page * VM_CONFIG_PRESET_PAGE_SIZE
        for preset in VM_CONFIG_PRESETS[start:start + VM_CONFIG_PRESET_PAGE_SIZE]:
            marker = "🟢" if selected_preset and selected_preset.id == preset.id else "⚪"
            kb.button(text=f"{marker} {preset.button_label}", callback_data=f"hunt:vm:preset:{preset.id}")

        prev_page = max(0, page - 1)
        next_page = min(page_count - 1, page + 1)
        kb.button(text="«", callback_data=f"hunt:vm_page:{prev_page}")
        kb.button(text=f"{page + 1}/{page_count}", callback_data="hunt:vm:noop:page")
        kb.button(text="»", callback_data=f"hunt:vm_page:{next_page}")
        kb.button(text="Создать конфиг", callback_data="hunt:vm_mode:manual")
        adjust = (1, 1, 1, 1, 1, 1, 3, 1, 1, 1, 1)

    kb.button(text="Дальше", callback_data="hunt:vm_done", style="success")
    kb.button(text="Назад", callback_data="hunt:back:target")
    kb.button(text="Отмена", callback_data="hunt:cancel", style="danger")
    kb.adjust(*adjust)
    return kb


def hunt_confirm_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Запустить", callback_data="hunt:confirm", style="success")
    kb.button(text="Назад", callback_data="hunt:back:vm_config")
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
