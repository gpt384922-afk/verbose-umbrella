from __future__ import annotations

from html import escape

CUSTOM_EMOJI_IDS = {
    "cat": "5307507558313915883",
    "crown": "5307954634344657117",
    "key": "5305473255644102663",
    "eyes": "5210956306952758910",
    "bolt": "5456140674028019486",
    "diamond": "5427168083074628963",
    "sparkles": "5312230970072530008",
}

CUSTOM_EMOJI_ALT = {
    "cat": "🐱",
    "crown": "👑",
    "key": "🗝",
    "eyes": "👀",
    "bolt": "⚡️",
    "diamond": "💎",
    "sparkles": "✨",
}


def ce(name: str) -> str:
    return f'<tg-emoji emoji-id="{CUSTOM_EMOJI_IDS[name]}">{CUSTOM_EMOJI_ALT[name]}</tg-emoji>'


def plain_emoji(name: str) -> str:
    return CUSTOM_EMOJI_ALT[name]


def quote(text: str) -> str:
    return f"<blockquote>{text}</blockquote>"


def code(value: object) -> str:
    return f"<code>{escape(str(value))}</code>"


def main_menu_text(*, can_manage_branches: bool = False) -> str:
    branch_line = f"\n{ce('sparkles')} Филиалы: управляет дополнительными ботами;" if can_manage_branches else ""
    return (
        f"{ce('cat')} | <b>Главное меню</b> ▾\n\n"
        f"{ce('crown')} <b>YC Hunter</b> — панель охоты за публичными IP в Yandex Cloud.\n\n"
        + quote(
            f"<b>Что делает бот:</b>\n"
            f"{ce('bolt')} готовит облака и каталоги;\n"
            f"{ce('eyes')} следит за активными хантами;\n"
            f"{ce('diamond')} присылает уведомления о пойманных IP;\n"
            f"{ce('key')} хранит аккаунты и организации;{branch_line}"
        )
        + "\n\n👉 <i>Выбери действие ниже.</i>"
    )
