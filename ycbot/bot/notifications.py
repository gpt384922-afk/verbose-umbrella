from __future__ import annotations

from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from ycbot.bot.ui import ce, code, plain_emoji, quote
from ycbot.config import Settings
from ycbot.core.hunter import MatchNotification


async def send_match_notification(bot: Bot, settings: Settings, notification: MatchNotification) -> None:
    effect_id = (settings.tg_match_message_effect_id or "").strip() or None
    if notification.chat_id < 0:
        effect_id = None

    text = _match_text(notification, custom_emoji=True)
    try:
        await bot.send_message(
            chat_id=notification.chat_id,
            text=text,
            message_effect_id=effect_id,
        )
        return
    except TelegramBadRequest:
        if effect_id is None:
            await bot.send_message(chat_id=notification.chat_id, text=_match_text(notification, custom_emoji=False))
            return

    try:
        await bot.send_message(chat_id=notification.chat_id, text=text)
        return
    except TelegramBadRequest:
        await bot.send_message(chat_id=notification.chat_id, text=_match_text(notification, custom_emoji=False))


def _match_text(notification: MatchNotification, *, custom_emoji: bool) -> str:
    diamond = ce("diamond") if custom_emoji else plain_emoji("diamond")
    crown = ce("crown") if custom_emoji else plain_emoji("crown")
    key = ce("key") if custom_emoji else plain_emoji("key")
    eyes = ce("eyes") if custom_emoji else plain_emoji("eyes")
    bolt = ce("bolt") if custom_emoji else plain_emoji("bolt")
    sparkles = ce("sparkles") if custom_emoji else plain_emoji("sparkles")

    preexisting = "да" if notification.preexisting else "нет"
    resource_line = f"address={code(notification.address_id)}\n"
    access_block = ""
    if notification.resource_type == "vm":
        resource_line = (
            f"vm={code(notification.resource_id or notification.address_id)}\n"
            f"zone={code(notification.zone_id or '-')}\n"
        )
        if notification.ssh_username or notification.ssh_private_key:
            access_block = (
                f"\n\n{key} <b>SSH</b>\n"
                f"login={code(notification.ssh_username or 'user')}\n"
            )
            if notification.ssh_private_key:
                access_block += f"<pre>{escape(notification.ssh_private_key)}</pre>"

    return (
        f"{diamond} | <b>IP выбит</b> ▾\n\n"
        + quote(
            f"{sparkles} <b>{escape(notification.ip)}</b>\n"
            f"{eyes} Префикс: {code(notification.prefix)}\n"
            f"{bolt} Задача: {code(notification.job_id)}"
        )
        + "\n\n"
        f"{key} <b>Аккаунт</b>\n"
        f"{escape(notification.account_name)}\n"
        f"{code(notification.account_id)}\n\n"
        f"{crown} <b>Организация</b>\n"
        f"{code(notification.organization_id)}\n\n"
        f"{eyes} <b>Облако</b>\n"
        f"{escape(notification.cloud_name)}\n"
        f"cloud={code(notification.cloud_id)}\n"
        f"folder={code(notification.folder_id)}\n"
        f"{resource_line}"
        f"preexisting={code(preexisting)}"
        f"{access_block}"
    )
