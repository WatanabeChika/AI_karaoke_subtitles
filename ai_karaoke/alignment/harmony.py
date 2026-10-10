"""Detect inline harmonies that overlap the primary vocal in time."""

from __future__ import annotations

import math

from ai_karaoke.alignment.core import align_sequence, build_alignment_sequence
from ai_karaoke.alignment.models import (
    AlignedToken,
    line_end,
    line_start,
    mean_token_confidence,
)
from ai_karaoke.alignment.text import build_alignment_units
from ai_karaoke.lyrics.parser import remove_inline_harmonies

HARMONY_PROBE_LEAD_SEC = 1.5
HARMONY_PROBE_END_PAD_SEC = 0.25
HARMONY_PROBE_MAX_SPAN_SEC = 15.0
HARMONY_MIN_CTC_CONFIDENCE = 0.15
HARMONY_MIN_TEMPORAL_OVERLAP = 0.60
HARMONY_BOUNDARY_LOCK_CONFIDENCE = 0.05
HARMONY_BOUNDARY_LOCK_OVERLAP = 0.80
HARMONY_BOUNDARY_TOLERANCE_SEC = 0.12
HARMONY_MAX_SERIAL_UNIT_SEC = 0.20
HARMONY_NEAR_TOTAL_OVERLAP = 0.98


def assess_harmony_overlap(
    main_tokens: list[AlignedToken],
    harmony_tokens: list[AlignedToken],
    serial_harmony_tokens: list[AlignedToken] | None = None,
) -> dict:
    """Classify harmony using only CTC confidence and aligned time ranges."""
    main_start = line_start(main_tokens)
    main_end = line_end(main_tokens)
    harmony_start = line_start(harmony_tokens)
    harmony_end = line_end(harmony_tokens)
    intersection = max(0.0, min(main_end, harmony_end) - max(main_start, harmony_start))
    harmony_duration = max(1e-6, harmony_end - harmony_start)
    overlap = intersection / harmony_duration
    main_confidence = mean_token_confidence(main_tokens)
    harmony_confidence = mean_token_confidence(harmony_tokens)
    confidently_overlapping = (
        main_confidence >= HARMONY_MIN_CTC_CONFIDENCE
        and harmony_confidence >= HARMONY_MIN_CTC_CONFIDENCE
        and overlap >= HARMONY_MIN_TEMPORAL_OVERLAP
    )
    boundary_locked = (
        main_confidence >= HARMONY_MIN_CTC_CONFIDENCE
        and harmony_confidence >= HARMONY_BOUNDARY_LOCK_CONFIDENCE
        and overlap >= HARMONY_BOUNDARY_LOCK_OVERLAP
        and abs(main_start - harmony_start) <= HARMONY_BOUNDARY_TOLERANCE_SEC
    )
    near_total_overlap = (
        main_confidence >= HARMONY_MIN_CTC_CONFIDENCE
        and overlap >= HARMONY_NEAR_TOTAL_OVERLAP
    )
    serial_unit_sec = None
    serial_squeezed = False
    if serial_harmony_tokens:
        serial_unit_sec = sum(
            max(0.0, token.end - token.start) for token in serial_harmony_tokens
        ) / len(serial_harmony_tokens)
        serial_squeezed = (
            main_confidence >= HARMONY_MIN_CTC_CONFIDENCE
            and overlap >= HARMONY_MIN_TEMPORAL_OVERLAP
            and serial_unit_sec <= HARMONY_MAX_SERIAL_UNIT_SEC
        )
    remove = confidently_overlapping or boundary_locked or near_total_overlap or serial_squeezed
    return {
        "main_start": main_start,
        "main_end": main_end,
        "harmony_start": harmony_start,
        "harmony_end": harmony_end,
        "main_confidence": main_confidence,
        "harmony_confidence": harmony_confidence,
        "temporal_overlap": overlap,
        "serial_unit_seconds": serial_unit_sec,
        "evidence": (
            "confident_overlap" if confidently_overlapping
            else "boundary_locked_overlap" if boundary_locked
            else "near_total_overlap" if near_total_overlap
            else "serial_squeeze_overlap" if serial_squeezed
            else "insufficient"
        ),
        "remove": remove,
    }


def _harmony_probe_window(line: dict, duration: float) -> tuple[float, float]:
    hint = line["start_time"]
    start = max(0.0, hint - HARMONY_PROBE_LEAD_SEC)
    end = min(
        duration,
        line.get("end_time", hint + HARMONY_PROBE_MAX_SPAN_SEC) + HARMONY_PROBE_END_PAD_SEC,
        hint + HARMONY_PROBE_MAX_SPAN_SEC,
    )
    return start, max(start, end)


def _align_probe_text(
    emissions,
    stride_ms: float,
    text: str,
    readings: dict[str, str],
    number_reading: str,
    window_start: float,
    window_end: float,
) -> list[AlignedToken]:
    frame_start = max(0, int(window_start * 1000 / stride_ms))
    frame_end = min(len(emissions), int(math.ceil(window_end * 1000 / stride_ms)))
    units = build_alignment_units(text, readings, number_reading)
    if not units or frame_end <= frame_start:
        raise ValueError(f"No alignable harmony probe for {text!r}")
    tokens, owners = build_alignment_sequence([0], [units])
    return align_sequence(
        emissions[frame_start:frame_end],
        stride_ms,
        tokens,
        owners,
        1,
        offset_sec=frame_start * stride_ms / 1000,
    )[0]


def _align_serial_harmony_parts(
    emissions,
    stride_ms: float,
    line: dict,
    number_reading: str,
    window_start: float,
    window_end: float,
) -> dict[int, list[AlignedToken]]:
    parts = line.get("inline_parts") or []
    if not parts:
        return {}
    units_per_part = [
        build_alignment_units(part["text"], line.get("readings", {}), number_reading)
        for part in parts
    ]
    indices = [index for index, units in enumerate(units_per_part) if units]
    if not indices:
        return {}
    frame_start = max(0, int(window_start * 1000 / stride_ms))
    frame_end = min(len(emissions), int(math.ceil(window_end * 1000 / stride_ms)))
    if frame_end <= frame_start:
        return {}
    tokens, owners = build_alignment_sequence(indices, units_per_part)
    aligned = align_sequence(
        emissions[frame_start:frame_end],
        stride_ms,
        tokens,
        owners,
        len(parts),
        offset_sec=frame_start * stride_ms / 1000,
    )
    return {
        part["index"]: aligned[index]
        for index, part in enumerate(parts)
        if part["kind"] == "harmony" and aligned[index]
    }


def filter_overlapping_inline_harmonies(
    emissions,
    stride_ms: float,
    lines: list[dict],
    duration: float,
    number_reading: str,
) -> list[dict]:
    filtered_lines = [dict(line) for line in lines]
    for line in filtered_lines:
        candidates = line.get("inline_harmonies") or []
        main_text = line.get("main_text") or line["text"]
        if not candidates or main_text == line["text"]:
            continue

        window_start, window_end = _harmony_probe_window(line, duration)
        decisions = []
        removed_indices: set[int] = set()
        try:
            main_tokens = _align_probe_text(
                emissions,
                stride_ms,
                main_text,
                line.get("readings", {}),
                number_reading,
                window_start,
                window_end,
            )
        except (RuntimeError, ValueError) as exc:
            line["harmony_decisions"] = [
                {"text": candidate["text"], "status": "kept_main_unalignable", "reason": str(exc)}
                for candidate in candidates
            ]
            continue

        try:
            serial_harmonies = _align_serial_harmony_parts(
                emissions, stride_ms, line, number_reading, window_start, window_end
            )
        except (RuntimeError, ValueError):
            serial_harmonies = {}

        for candidate in candidates:
            try:
                harmony_tokens = _align_probe_text(
                    emissions,
                    stride_ms,
                    candidate["text"],
                    {},
                    number_reading,
                    window_start,
                    window_end,
                )
                evidence = assess_harmony_overlap(
                    main_tokens,
                    harmony_tokens,
                    serial_harmonies.get(candidate["index"]),
                )
                status = "removed_temporal_overlap" if evidence.pop("remove") else "kept"
                if status == "removed_temporal_overlap":
                    removed_indices.add(candidate["index"])
                decisions.append({"text": candidate["text"], "status": status, **evidence})
            except (RuntimeError, ValueError) as exc:
                decisions.append({
                    "text": candidate["text"],
                    "status": "kept_unalignable",
                    "reason": str(exc),
                })

        if removed_indices:
            line["text"] = remove_inline_harmonies(line["text"], removed_indices)
        line["harmony_decisions"] = decisions
    return filtered_lines
