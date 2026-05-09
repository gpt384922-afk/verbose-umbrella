from __future__ import annotations

import logging
import json
import re
from collections.abc import Mapping
from typing import Any

MAX_LOG_VALUE_LENGTH = 300


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    if not fields:
        logger.info(event)
        return
    payload = " ".join(f"{key}={_safe(value)}" for key, value in sorted(fields.items()))
    logger.info("%s %s", event, payload)


def log_error(logger: logging.Logger, event: str, error: Exception, **fields: Any) -> None:
    payload = {**fields, "error": format_error(error), "error_type": type(error).__name__}
    log_event(logger, event, **payload)


def format_error(error: Exception) -> str:
    root = _root_cause(error)
    status = getattr(root, "status", None)
    message = getattr(root, "message", None)
    payload = getattr(root, "payload", None)

    payload_message = _payload_message(payload) if isinstance(payload, str) else None
    if status is not None and (message or payload_message):
        text = f"{status} {message or 'request failed'}"
        if payload_message and payload_message != message:
            text = f"{text}: {payload_message}"
        return _short_text(text)

    text = str(root if root is not error else error)
    extracted = _extract_json_message(text)
    if extracted:
        text = extracted
    return _short_text(text)


def _safe(value: Any) -> str:
    if isinstance(value, Mapping):
        return "{" + ",".join(f"{k}:{_safe(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, set, tuple)):
        return "[" + ",".join(_safe(item) for item in value) + "]"
    if isinstance(value, str):
        return _short_text(value)
    return _short_text(str(value))


def _root_cause(error: Exception) -> Exception:
    current = error
    seen: set[int] = set()
    while isinstance(getattr(current, "__cause__", None), Exception) and id(current) not in seen:
        seen.add(id(current))
        current = current.__cause__  # type: ignore[assignment]
    return current


def _payload_message(payload: str) -> str | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return _extract_json_message(payload)
    if isinstance(data, Mapping):
        message = data.get("message")
        if isinstance(message, str) and message:
            return message
        details = data.get("details")
        if isinstance(details, list):
            for item in details:
                if isinstance(item, Mapping) and isinstance(item.get("message"), str):
                    return item["message"]
    return None


def _extract_json_message(text: str) -> str | None:
    match = re.search(r'"message"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1)
    return None


def _short_text(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= MAX_LOG_VALUE_LENGTH:
        return compact
    return f"{compact[:MAX_LOG_VALUE_LENGTH - 1]}…"
