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
    PrefixOption("87.250.247-254", "для всех операторов"),
    PrefixOption("77.88.21", "для всех операторов"),
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
        if _prefix_matches(ip, prefix):
            return prefix
    return None


def match_known_prefix(ip: str | None) -> str | None:
    return match_prefix(ip, KNOWN_PREFIX_VALUES)


def _prefix_matches(ip: str, prefix: str) -> bool:
    prefix_parts = prefix.split(".")
    if not prefix_parts:
        return False

    last_part = prefix_parts[-1]
    if "-" in last_part:
        return _range_prefix_matches(ip, prefix_parts)

    return ip == prefix or ip.startswith(f"{prefix}.")


def _range_prefix_matches(ip: str, prefix_parts: list[str]) -> bool:
    ip_parts = ip.split(".")
    if len(ip_parts) < len(prefix_parts):
        return False

    fixed_parts = prefix_parts[:-1]
    if ip_parts[: len(fixed_parts)] != fixed_parts:
        return False

    range_text = prefix_parts[-1]
    start_text, end_text = range_text.split("-", maxsplit=1)
    try:
        octet = int(ip_parts[len(fixed_parts)])
        start = int(start_text)
        end = int(end_text)
    except ValueError:
        return False

    return start <= octet <= end
