from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any


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
    payload = {**fields, "error": str(error), "error_type": type(error).__name__}
    log_event(logger, event, **payload)


def _safe(value: Any) -> str:
    if isinstance(value, Mapping):
        return "{" + ",".join(f"{k}:{_safe(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, set, tuple)):
        return "[" + ",".join(_safe(item) for item in value) + "]"
    return str(value)
