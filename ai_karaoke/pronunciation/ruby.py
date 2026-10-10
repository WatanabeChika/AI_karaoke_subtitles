"""Render dictionary or corrected Japanese readings into ASS karaoke ruby."""

from __future__ import annotations

import re

from ai_karaoke.pronunciation.reading import (
    build_converter,
    contains_kanji,
    is_pronounceable_japanese_segment,
    speech_text,
)

K_TAG_PATTERN = re.compile(r"\{\\(k(?:f|o)?)(\d+)\}([^{}]*)")


def _split_karaoke_text(text: str) -> list[dict]:
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
        })
        pos = match.end()
    if pos < len(text):
        parts.append({"type": "raw", "text": text[pos:]})
    return parts


def _render_karaoke_text(parts: list[dict]) -> str:
    result: list[str] = []
    for part in parts:
        if part["type"] == "raw":
            result.append(part["text"])
        else:
            result.append(f"{{\\{part['tag']}{part['duration']}}}{part['text']}")
    return "".join(result)


def _distribute_duration(duration: int, length: int) -> list[int]:
    if length <= 0:
        return []
    base = duration // length
    remainder = duration % length
    return [base + (1 if index < remainder else 0) for index in range(length)]


def _reading_morae(text: str) -> list[str]:
    small = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮヵヶ")
    morae: list[str] = []
    for char in text:
        if char in small or char == "ー":
            if morae:
                morae[-1] += char
            else:
                morae.append(char)
        elif "ぁ" <= char <= "ゖ" or "ァ" <= char <= "ヿ":
            morae.append(char)
    return morae or ([text] if text else [])


def _split_compound_reading(orig: str, hira: str, converter=None) -> list[dict]:
    if len(orig) <= 1 or not all(contains_kanji(char) for char in orig) or converter is None:
        return [{"orig": orig, "hira": hira}]
    baseline_parts: list[str] = []
    for char in orig:
        tokens = converter.convert(char)
        if len(tokens) != 1 or tokens[0].get("orig") != char:
            return [{"orig": orig, "hira": hira}]
        baseline_parts.append(tokens[0].get("hira", char))
    if "".join(baseline_parts) != hira:
        return [{"orig": orig, "hira": hira}]
    return [
        {"orig": char, "hira": reading}
        for char, reading in zip(orig, baseline_parts)
    ]


def _weighted_duration(duration: int, weights: list[int]) -> list[int]:
    if not weights or duration <= 0:
        return [0] * len(weights)
    total = sum(weights)
    values = [duration * weight // total for weight in weights]
    for index in range(duration - sum(values)):
        values[index % len(values)] += 1
    return values


def _build_ruby_text(orig: str, hira: str) -> str:
    return f"{orig}|<{hira}" if contains_kanji(orig) and hira and hira != orig else orig


def _merge_group_by_furigana(group: list[dict], converter) -> list[dict]:
    combined = "".join(segment["text"] for segment in group)
    if not combined:
        return group
    converted = converter.convert(combined)
    if not converted or "".join(item.get("orig", "") for item in converted) != combined:
        return group

    char_durations: list[int] = []
    for segment in group:
        char_durations.extend(_distribute_duration(segment["duration"], len(segment["text"])))
    if len(char_durations) != len(combined):
        return group

    merged: list[dict] = []
    offset = 0
    total_old = sum(segment["duration"] for segment in group)
    for item in converted:
        orig = item.get("orig", "")
        if not orig:
            continue
        span = len(orig)
        token_duration = sum(char_durations[offset : offset + span])
        offset += span
        split_items = _split_compound_reading(orig, item.get("hira", ""), converter)
        source_durations = char_durations[offset - span : offset]
        if len(split_items) == span and all(len(part["orig"]) == 1 for part in split_items):
            durations = source_durations
        else:
            durations = _weighted_duration(
                token_duration,
                [len(_reading_morae(part["hira"])) for part in split_items],
            )
        for part, part_duration in zip(split_items, durations):
            merged.append({
                "type": "k",
                "tag": group[0]["tag"],
                "duration": part_duration,
                "text": _build_ruby_text(part["orig"], part["hira"]),
            })

    if not merged or offset != len(char_durations):
        return group
    drift = total_old - sum(segment["duration"] for segment in merged)
    if drift:
        merged[-1]["duration"] += drift
    return merged


def annotate_karaoke_text_with_furigana(text: str, converter) -> str:
    parts = _split_karaoke_text(text)
    output: list[dict] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        if part["type"] != "k" or not is_pronounceable_japanese_segment(part["text"]):
            output.append(part)
            index += 1
            continue
        start = index
        while (
            index < len(parts)
            and parts[index]["type"] == "k"
            and is_pronounceable_japanese_segment(parts[index]["text"])
        ):
            index += 1
        output.extend(_merge_group_by_furigana(parts[start:index], converter))
    return _render_karaoke_text(output)


def _karaoke_display_text(text: str) -> str:
    visible = []
    for part in _split_karaoke_text(text):
        fragment = part["text"]
        if part["type"] == "raw":
            fragment = re.sub(r"\{[^}]*\}", "", fragment)
        visible.append(fragment.split("|<", 1)[0])
    return "".join(visible)


def _build_contextual_converter(text: str, readings=None):
    explicit = dict(readings or {})
    converter = build_converter(explicit)
    resolved: dict[str, str] = {}
    conflicts: set[str] = set()
    for item in converter.convert(speech_text(text)):
        original = item.get("orig", "")
        reading = item.get("hira", "")
        if not contains_kanji(original) or not reading:
            continue
        if original in resolved and resolved[original] != reading:
            conflicts.add(original)
        else:
            resolved[original] = reading
    for original in conflicts:
        if original not in explicit:
            resolved.pop(original, None)
    converter.readings = {**resolved, **explicit}
    return converter


def apply_furigana_to_ass(ass_path: str, readings_per_line=None) -> int:
    with open(ass_path, "r", encoding="utf-8") as stream:
        lines = stream.readlines()

    changed_lines = 0
    new_lines: list[str] = []
    dialogue_index = 0
    for line in lines:
        if not line.startswith("Dialogue:"):
            new_lines.append(line)
            continue
        readings = readings_per_line[dialogue_index] if readings_per_line is not None else None
        dialogue_index += 1
        fields = line.rstrip("\n").split(",", 9)
        if len(fields) < 10:
            new_lines.append(line)
            continue
        old_text = fields[9]
        converter = _build_contextual_converter(_karaoke_display_text(old_text), readings)
        new_text = annotate_karaoke_text_with_furigana(old_text, converter)
        if new_text != old_text:
            changed_lines += 1
            fields[9] = new_text
            new_lines.append(",".join(fields) + "\n")
        else:
            new_lines.append(line)

    with open(ass_path, "w", encoding="utf-8") as stream:
        stream.writelines(new_lines)
    return changed_lines
