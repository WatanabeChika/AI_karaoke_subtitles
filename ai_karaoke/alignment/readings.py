"""Select acoustically supported alternatives for ambiguous kanji readings."""

from __future__ import annotations

import math

from ai_karaoke.alignment.core import align_sequence, build_alignment_sequence
from ai_karaoke.alignment.models import (
    AlignedToken,
    AlignmentUnit,
    line_end,
    line_start,
    mean_token_confidence,
)
from ai_karaoke.alignment.text import romanize_pronunciation
from ai_karaoke.pronunciation.reading import contains_kanji, katakana_to_hiragana

LOW_READING_CONFIDENCE = 0.08
MIN_TARGET_IMPROVEMENT = 0.15
MAX_MEAN_REGRESSION = 0.02
READING_WINDOW_LEAD_SEC = 2.0
READING_WINDOW_TAIL_SEC = 1.0


def _pykakasi_reading(text: str) -> str:
    from pykakasi import kakasi

    return katakana_to_hiragana(
        "".join(item.get("hira", "") for item in kakasi().convert(text))
    )


def alternate_reading(unit: AlignmentUnit) -> str | None:
    """Return an independent dictionary reading when it is genuinely different."""
    if not contains_kanji(unit.text):
        return None
    candidate = _pykakasi_reading(unit.text)
    if (
        not candidate
        or not romanize_pronunciation(candidate)
        or romanize_pronunciation(candidate) == romanize_pronunciation(unit.pronunciation)
    ):
        return None
    return candidate


def _line_window(
    lines: list[dict],
    index: int,
    tokens: list[AlignedToken],
    duration: float,
) -> tuple[float, float]:
    hint = lines[index]["start_time"]
    next_hint = (
        lines[index + 1]["start_time"]
        if index + 1 < len(lines)
        else min(duration, hint + 20.0)
    )
    start = max(0.0, min(hint - READING_WINDOW_LEAD_SEC, line_start(tokens) - 1.0))
    end = min(duration, max(next_hint + READING_WINDOW_TAIL_SEC, line_end(tokens) + 1.0))
    return start, end


def _reading_is_better(
    current: list[AlignedToken],
    candidate: list[AlignedToken],
    unit_index: int,
) -> bool:
    current_score = current[unit_index].score or 0.0
    candidate_score = candidate[unit_index].score or 0.0
    return (
        candidate_score >= current_score + MIN_TARGET_IMPROVEMENT
        and mean_token_confidence(candidate)
        >= mean_token_confidence(current) - MAX_MEAN_REGRESSION
    )


def refine_ambiguous_readings(
    emissions,
    stride_ms: float,
    lines: list[dict],
    units_per_line: list[list[AlignmentUnit]],
    aligned: list[list[AlignedToken]],
    duration: float,
) -> None:
    """Retry low-confidence kanji with an independent lexical reading."""
    for index, units in enumerate(units_per_line):
        if not aligned[index] or len(aligned[index]) != len(units):
            continue
        window_start, window_end = _line_window(lines, index, aligned[index], duration)
        frame_start = max(0, int(window_start * 1000 / stride_ms))
        frame_end = min(len(emissions), int(math.ceil(window_end * 1000 / stride_ms)))
        for unit_index, unit in enumerate(list(units)):
            # Reading overrides apply to a whole line, so one acoustic occurrence
            # must not change a repeated word elsewhere in that line.
            if lines[index]["text"].count(unit.text) != 1:
                continue
            explicit = lines[index].get("readings", {})
            if any(unit.text in base or base in unit.text for base in explicit):
                continue
            if (aligned[index][unit_index].score or 0.0) >= LOW_READING_CONFIDENCE:
                continue
            reading = alternate_reading(unit)
            if not reading:
                continue
            candidate_units = list(units_per_line[index])
            candidate_units[unit_index] = AlignmentUnit(unit.text, reading)
            tokens, owners = build_alignment_sequence([0], [candidate_units])
            candidate = align_sequence(
                emissions[frame_start:frame_end],
                stride_ms,
                tokens,
                owners,
                1,
                offset_sec=frame_start * stride_ms / 1000,
            )[0]
            if _reading_is_better(aligned[index], candidate, unit_index):
                units_per_line[index] = candidate_units
                units = candidate_units
                aligned[index] = candidate
                lines[index].setdefault("alignment_readings", {})[unit.text] = reading
