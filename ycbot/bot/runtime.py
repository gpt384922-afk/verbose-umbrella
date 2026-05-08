from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BotRuntimeScope:
    branch_id: str | None
    allowed_chat_ids: tuple[int, ...]
    can_manage_branches: bool = False
