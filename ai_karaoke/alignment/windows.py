"""Build bounded lyric windows and recover CTC drift or overlap."""

from __future__ import annotations

import math
import statistics

from ai_karaoke.alignment.core import align_sequence, build_alignment_sequence
from ai_karaoke.alignment.models import (
    AlignedToken,
    AlignmentUnit,
    line_end,
    line_start,
)

PARALLEL_LINE_TOLERANCE_SEC = 0.05
MAX_BLOCK_LINES = 4
MAX_BLOCK_SPAN_SEC = 35.0
BLOCK_BREAK_GAP_SEC = 12.0


def split_primary_and_parallel(lines: list[dict]) -> tuple[list[int], list[int]]:
    primary: list[int] = []
    parallel: list[int] = []
    index = 0
    while index < len(lines):
        primary.append(index)
        hint = lines[index]["start_time"]
        index += 1
        while (
            index < len(lines)
            and abs(lines[index]["start_time"] - hint) <= PARALLEL_LINE_TOLERANCE_SEC
        ):
            parallel.append(index)
            index += 1
    return primary, parallel


def build_primary_blocks(lines: list[dict], primary_indices: list[int]) -> list[list[int]]:
    blocks: list[list[int]] = []
    current: list[int] = []
    for index in primary_indices:
        if current:
            first_hint = lines[current[0]]["start_time"]
            previous_hint = lines[current[-1]]["start_time"]
            hint = lines[index]["start_time"]
            should_break = (
                len(current) >= MAX_BLOCK_LINES
                or hint - first_hint > MAX_BLOCK_SPAN_SEC
                or hint - previous_hint > BLOCK_BREAK_GAP_SEC
            )
            if should_break:
                blocks.append(current)
                current = []
        current.append(index)
    if current:
        blocks.append(current)
    return blocks


def block_windows(
    lines: list[dict],
    blocks: list[list[int]],
    duration: float,
) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    for block in blocks:
        first_hint = lines[block[0]]["start_time"]
        last_hint = lines[block[-1]]["start_time"]
        next_hint = next(
            (
                line["start_time"]
                for line in lines[block[-1] + 1 :]
                if line["start_time"] > last_hint + 0.05
            ),
            last_hint + 20.0,
        )
        start = max(0.0, first_hint - 3.0)
        end = min(duration, next_hint + 1.0)
        if end <= start:
            raise ValueError(f"Invalid CTC block window: {start:.3f}s to {end:.3f}s")
        windows.append((start, end))
    return windows


def context_blocks(blocks: list[list[int]], primary_indices: list[int]) -> list[list[int]]:
    """Include complete neighbouring lines so the last owned line is never cropped."""
    positions = {index: position for position, index in enumerate(primary_indices)}
    return [
        primary_indices[max(0, positions[block[0]] - 1) : positions[block[-1]] + 2]
        for block in blocks
    ]


def parallel_window(lines: list[dict], index: int, duration: float) -> tuple[float, float]:
    hint = lines[index]["start_time"]
    next_hint = next(
        (
            line["start_time"]
            for line in lines[index + 1 :]
            if line["start_time"] > hint + 0.05
        ),
        min(duration, hint + 15.0),
    )
    return max(0.0, hint - 3.0), min(duration, max(hint + 2.0, next_hint + 1.0))


def recover_drifted_primary_lines(
    emissions,
    stride_ms: float,
    lines: list[dict],
    units_per_line: list[list[AlignmentUnit]],
    primary_indices: list[int],
    aligned: list[list[AlignedToken]],
    duration: float,
) -> None:
    stable_drifts = [
        line_start(aligned[index]) - lines[index]["start_time"]
        for index in primary_indices
        if aligned[index]
        and abs(line_start(aligned[index]) - lines[index]["start_time"]) <= 4.0
        and sum(token.score or 0.0 for token in aligned[index]) / len(aligned[index]) >= 0.15
    ]
    global_offset = statistics.median(stable_drifts) if stable_drifts else 0.0

    for position, index in enumerate(primary_indices):
        expected_start = lines[index]["start_time"] + global_offset
        next_expected = (
            lines[primary_indices[position + 1]]["start_time"] + global_offset
            if position + 1 < len(primary_indices)
            else min(duration, expected_start + 20.0)
        )
        current_start = line_start(aligned[index])
        current_end = line_end(aligned[index])
        if (
            expected_start - 2.0 <= current_start <= expected_start + 4.0
            and current_end <= next_expected + 1.0
        ):
            continue

        context = primary_indices[max(0, position - 1) : position + 2]
        window_start, window_end = block_windows(lines, [context], duration)[0]
        frame_start = max(0, int(window_start * 1000 / stride_ms))
        frame_end = min(len(emissions), int(math.ceil(window_end * 1000 / stride_ms)))
        tokens, owners = build_alignment_sequence(context, units_per_line)
        local = align_sequence(
            emissions[frame_start:frame_end],
            stride_ms,
            tokens,
            owners,
            len(lines),
            offset_sec=frame_start * stride_ms / 1000,
        )
        aligned[index] = local[index]


def resolve_primary_overlaps(
    emissions,
    stride_ms: float,
    primary_indices: list[int],
    units_per_line: list[list[AlignmentUnit]],
    aligned: list[list[AlignedToken]],
    duration: float,
) -> None:
    """Re-align conflicting lines jointly instead of shortening their edge tokens."""
    position = 0
    while position + 1 < len(primary_indices):
        last = position
        while (
            last + 1 < len(primary_indices)
            and line_end(aligned[primary_indices[last]])
            > line_start(aligned[primary_indices[last + 1]])
        ):
            last += 1
        if last == position:
            position += 1
            continue
        window_start = line_end(aligned[primary_indices[position - 1]]) if position else 0.0
        window_end = (
            line_start(aligned[primary_indices[last + 1]])
            if last + 1 < len(primary_indices)
            else duration
        )
        frame_start = math.ceil(window_start * 1000 / stride_ms)
        frame_end = int(window_end * 1000 / stride_ms)
        indices = primary_indices[position : last + 1]
        tokens, owners = build_alignment_sequence(indices, units_per_line)
        local = align_sequence(
            emissions[frame_start:frame_end],
            stride_ms,
            tokens,
            owners,
            len(aligned),
            frame_start * stride_ms / 1000,
        )
        for index in indices:
            aligned[index] = local[index]
        position = last + 1
