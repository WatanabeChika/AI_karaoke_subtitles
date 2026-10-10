"""ASS color defaults and validation."""

from __future__ import annotations

import re

ASS_COLOR_PATTERN = re.compile(r"^&H[0-9A-F]{8}$")
DEFAULT_KARA_PRIMARY_COLOR = "&H00FFFFFF"
DEFAULT_KARA_ACCENT_COLOR = "&H002F1CD8"
NAMED_ASS_COLORS = {
    "white": "&H00FFFFFF",
    "black": "&H00000000",
    "red": "&H000000FF",
    "green": "&H0000FF00",
    "blue": "&H00FF0000",
    "yellow": "&H0000FFFF",
    "cyan": "&H00FFFF00",
    "magenta": "&H00FF00FF",
    "gray": "&H00808080",
    "grey": "&H00808080",
    "orange": "&H0000A5FF",
    "purple": "&H00800080",
    "pink": "&H00CBC0FF",
}


def normalize_kara_color(color: str, arg_name: str) -> str:
    token = color.strip()
    mapped = NAMED_ASS_COLORS.get(token.lower())
    if mapped:
        return mapped
    normalized = token.upper()
    if not ASS_COLOR_PATTERN.match(normalized):
        raise ValueError(
            f"{arg_name} must be ASS color (&H00FFFFFF) or named color "
            f"({', '.join(sorted(NAMED_ASS_COLORS))})"
        )
    return normalized


def validate_distinct_kara_colors(primary_color: str, accent_color: str) -> None:
    if primary_color[-6:] == accent_color[-6:]:
        raise ValueError("kara_primary_color and kara_accent_color must be different")


def ass_color_to_tag_color(color: str) -> str:
    return f"&H{color[-6:]}&"
