"""Normalize ASS karaoke tags and construct KTV countdown leads."""

from __future__ import annotations

import re
import unicodedata

K_TAG_PATTERN = re.compile(r"\{\\(k(?:f|o)?)(\d+)\}([^{}]*)")
DEFAULT_KARA_ADVANCE_MS = 3000
DEFAULT_KARA_SEP_THRESHOLD_MS = 10000
KARA_COUNTDOWN_DOT = "●"


def _is_punctuation_text(text: str) -> bool:
    chars = [char for char in text if not char.isspace()]
    return bool(chars) and all(unicodedata.category(char).startswith("P") for char in chars)


def _has_ruby_marker(text: str) -> bool:
    return "|<" in text or "｜＜" in text


def _split_k_text(text: str) -> list[dict]:
    parts: list[dict] = []
    pos = 0
    for match in K_TAG_PATTERN.finditer(text):
        if match.start() > pos:
            parts.append({"type": "raw", "text": text[pos:match.start()]})
        parts.append({
            "type": "k",
            "tag": match.group(1),
            "duration": int(match.group(2)),
            "text": match.group(3),
            "drop": False,
        })
        pos = match.end()
    if pos < len(text):
        parts.append({"type": "raw", "text": text[pos:]})
    return parts


def _render_k_text(parts: list[dict]) -> str:
    output: list[str] = []
    for part in parts:
        if part["type"] == "raw":
            output.append(part["text"])
        elif not part.get("drop", False):
            output.append(f"{{\\{part['tag']}{part['duration']}}}{part['text']}")
    return "".join(output)


def _find_timed(parts: list[dict], start: int, step: int) -> int:
    index = start
    while 0 <= index < len(parts):
        part = parts[index]
        if part["type"] == "k" and not part.get("drop", False) and part["duration"] > 0:
            return index
        index += step
    return -1


def normalize_zero_duration_k_parts(text: str) -> str:
    parts = _split_k_text(text)
    for index, part in enumerate(parts):
        if part["type"] != "k" or part.get("drop", False):
            continue
        if part["duration"] != 0 or not part["text"] or part["text"].isspace():
            continue

        is_punctuation = _is_punctuation_text(part["text"])
        previous_index = _find_timed(parts, index - 1, -1)
        next_index = _find_timed(parts, index + 1, 1)
        if is_punctuation:
            previous_has_ruby = (
                previous_index >= 0 and _has_ruby_marker(parts[previous_index]["text"])
            )
            next_has_ruby = next_index >= 0 and _has_ruby_marker(parts[next_index]["text"])
            if previous_index >= 0 and not (previous_has_ruby or next_has_ruby):
                parts[previous_index]["text"] += part["text"]
                part["drop"] = True
            continue

        if next_index >= 0 and parts[next_index]["duration"] > 1:
            part["duration"] = 1
            parts[next_index]["duration"] -= 1
            continue
        if previous_index >= 0 and parts[previous_index]["duration"] > 1:
            part["duration"] = 1
            parts[previous_index]["duration"] -= 1
            continue
        if next_index >= 0:
            prefix = part["text"]
            for middle_index in range(index + 1, next_index):
                middle = parts[middle_index]
                if (
                    middle["type"] == "k"
                    and not middle.get("drop", False)
                    and middle["duration"] == 0
                ):
                    prefix += middle["text"]
                    middle["drop"] = True
            parts[next_index]["text"] = prefix + parts[next_index]["text"]
            part["drop"] = True
            continue
        if previous_index >= 0:
            parts[previous_index]["text"] += part["text"]
            part["drop"] = True
    return _render_k_text(parts)


def build_sequence_lead(actual_lead_ms: int, is_new_sequence: bool) -> str:
    """Render an exactly timed lead-in, with up to three one-second KTV dots."""
    lead_centiseconds = max(0, actual_lead_ms // 10)
    dot_count = min(3, actual_lead_ms // 1000) if is_new_sequence else 0
    if dot_count <= 0:
        return f"{{\\k{lead_centiseconds}}}"
    silent_centiseconds = max(0, lead_centiseconds - dot_count * 100)
    parts = [f"{{\\k{silent_centiseconds}}}"] if silent_centiseconds else []
    parts.extend(f"{{\\kf100}}{KARA_COUNTDOWN_DOT}" for _ in range(dot_count))
    return "".join(parts)
