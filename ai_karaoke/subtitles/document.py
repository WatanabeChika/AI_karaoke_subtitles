"""Small ASS document helpers shared by subtitle styling modules."""

from __future__ import annotations


def parse_ass_time_to_ms(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, centiseconds = rest.split(".")
    return (
        (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000
        + int(centiseconds) * 10
    )


def format_ass_time_from_ms(milliseconds: int) -> str:
    milliseconds = max(0, milliseconds)
    total_centiseconds = milliseconds // 10
    hours = total_centiseconds // (3600 * 100)
    remainder = total_centiseconds % (3600 * 100)
    minutes = remainder // (60 * 100)
    remainder %= 60 * 100
    seconds = remainder // 100
    centiseconds = remainder % 100
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def find_section_bounds(lines: list[str], section_name: str) -> tuple[int, int]:
    start = next((index for index, line in enumerate(lines) if line.strip() == section_name), -1)
    if start < 0:
        return -1, -1
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("[") and lines[index].strip().endswith("]")
        ),
        len(lines),
    )
    return start, end


def is_stylable_dialogue(is_comment: bool, style: str, effect: str, handle_style: str) -> bool:
    if style != handle_style:
        return False
    if not is_comment:
        return effect in ("", "karaoke")
    return effect == "karaoke"
