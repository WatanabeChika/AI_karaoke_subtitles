"""Map Whisper observations to conservative Japanese reading corrections."""

from __future__ import annotations

import difflib

from ai_karaoke.pronunciation.reading import (
    JapaneseReadingConverter,
    contains_kanji,
    reading_text,
    speech_text,
)
from ai_karaoke.pronunciation.spelling import propose_spelling_readings


def propose_whisper_readings(
    lyric: str, heard: str, readings: dict[str, str] | None = None
) -> list[dict]:
    """Use unique kanji spellings or anchored local kana differences."""
    converter = JapaneseReadingConverter(readings)
    units = converter.convert(speech_text(lyric))
    target = reading_text(heard, converter)
    if not target:
        return []

    unit_readings = [reading_text(unit["hira"], converter) for unit in units]
    expected = "".join(unit_readings)
    spans = []
    offset = 0
    for reading in unit_readings:
        spans.append((offset, offset + len(reading)))
        offset += len(reading)

    matcher = difflib.SequenceMatcher(None, expected, target, autojunk=False)
    proposals = propose_spelling_readings(units, heard, converter)
    for tag, expected_start, expected_end, heard_start, heard_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        affected = [
            index
            for index, (start, end) in enumerate(spans)
            if start < expected_end and end > expected_start
        ]
        kanji_units = [index for index in affected if contains_kanji(units[index]["orig"])]
        if len(kanji_units) != 1:
            continue
        index = kanji_units[0]
        base = units[index]["orig"]
        if any(base in proposal["base"] for proposal in proposals):
            continue
        if sum(unit["orig"] == base for unit in units) != 1:
            continue
        unit_start, unit_end = spans[index]
        touches_edge = expected_start == 0 or expected_end == len(expected)
        if touches_edge and len(base) != 1:
            continue

        mapped = []
        for part_tag, left, right, target_left, target_right in matcher.get_opcodes():
            if right <= unit_start or left >= unit_end:
                continue
            if part_tag == "equal":
                overlap_start = max(left, unit_start)
                overlap_end = min(right, unit_end)
                mapped.append(
                    target[
                        target_left + overlap_start - left:
                        target_left + overlap_end - left
                    ]
                )
            else:
                mapped.append(target[target_left:target_right])
        reading = "".join(mapped)
        dictionary = unit_readings[index]
        if not reading or len(reading) > 16 or reading == dictionary:
            continue
        proposal = {
            "base": base,
            "dictionary": dictionary,
            "reading": reading,
            "accepted": True,
        }
        if proposal not in proposals:
            proposals.append(proposal)
    return proposals


def _lyric_edge_coverage(expected: str, heard: str) -> tuple[bool, bool, float]:
    matcher = difflib.SequenceMatcher(None, expected, heard, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    if not blocks:
        return False, False, 0.0
    prefix_complete = blocks[0].a == 0 and blocks[0].b == 0
    suffix_complete = (
        blocks[-1].a + blocks[-1].size == len(expected)
        and blocks[-1].b + blocks[-1].size == len(heard)
    )
    matched = sum(block.size for block in blocks)
    return prefix_complete, suffix_complete, matched / max(1, len(expected))


def select_whisper_evidence(
    words: list[dict],
    start: float,
    end: float,
    lyric: str | None = None,
    readings: dict[str, str] | None = None,
) -> dict:
    """Select the lyric-like contiguous ASR span from a generous time window."""
    candidates = [word for word in words if word["end"] >= start and word["start"] <= end]
    selected = candidates
    match_ratio = None
    prefix_complete = suffix_complete = False
    lyric_coverage = 0.0
    converter = JapaneseReadingConverter(readings) if lyric else None
    if lyric and candidates:
        target = reading_text(lyric, converter)
        best = None
        window_midpoint = (start + end) / 2
        for left in range(len(candidates)):
            text = ""
            for right in range(left, len(candidates)):
                text += candidates[right]["text"]
                reading = reading_text(text, converter)
                if not reading:
                    continue
                if len(reading) > max(12, len(target) * 2 + 4):
                    break
                ratio = difflib.SequenceMatcher(None, target, reading, autojunk=False).ratio()
                length_ratio = min(len(target), len(reading)) / max(len(target), len(reading))
                span_midpoint = (candidates[left]["start"] + candidates[right]["end"]) / 2
                time_score = max(
                    0.0,
                    1.0 - abs(span_midpoint - window_midpoint) / max(1.0, end - start),
                )
                score = ratio * 0.82 + length_ratio * 0.13 + time_score * 0.05
                key = (score, ratio, -abs(len(target) - len(reading)))
                if best is None or key > best[0]:
                    best = (key, left, right, ratio)
        if best is not None:
            _, left, right, match_ratio = best
            selected = candidates[left:right + 1]

    if not selected:
        return {
            "text": "",
            "confidence": 0.0,
            "match_ratio": match_ratio,
            "prefix_complete": False,
            "suffix_complete": False,
            "lyric_coverage": 0.0,
            "accepted": False,
            "reasons": ["no_asr_words"],
        }

    selected_text = "".join(word["text"] for word in selected).strip()
    if lyric:
        expected = reading_text(lyric, converter)
        heard = reading_text(selected_text, converter)
        prefix_complete, suffix_complete, lyric_coverage = _lyric_edge_coverage(expected, heard)

    probabilities = [
        word["probability"] for word in selected if word["probability"] is not None
    ]
    confidence = sum(probabilities) / len(probabilities) if probabilities else 0.0
    reasons = []
    if confidence < 0.55:
        reasons.append("low_word_probability")
    if match_ratio is not None and match_ratio < 0.62:
        reasons.append("lyric_mismatch")
    if lyric and lyric_coverage < 0.65:
        reasons.append("low_lyric_coverage")
    if lyric and not prefix_complete:
        reasons.append("missing_lyric_start")
    if lyric and not suffix_complete:
        reasons.append("missing_lyric_end")
    strong_lyric_match = match_ratio is not None and match_ratio >= 0.82 and confidence >= 0.70
    if any(not word["segment_reliable"] for word in selected) and not strong_lyric_match:
        reasons.append("unreliable_segment")
    return {
        "text": selected_text,
        "confidence": confidence,
        "match_ratio": match_ratio,
        "prefix_complete": prefix_complete,
        "suffix_complete": suffix_complete,
        "lyric_coverage": lyric_coverage,
        "start": min(word["start"] for word in selected),
        "end": max(word["end"] for word in selected),
        "accepted": not reasons,
        "reasons": reasons,
    }


def local_reading_evidence_is_usable(evidence: dict) -> bool:
    """Allow anchored local corrections even when an unrelated line edge is missing."""
    return (
        evidence["confidence"] >= 0.70
        and (evidence.get("match_ratio") or 0.0) >= 0.62
        and evidence.get("lyric_coverage", 0.0) >= 0.65
        and "low_word_probability" not in evidence["reasons"]
        and "lyric_mismatch" not in evidence["reasons"]
    )


def evidence_rank(evidence: dict) -> tuple:
    complete_edges = int(evidence.get("prefix_complete", False)) + int(
        evidence.get("suffix_complete", False)
    )
    return (
        int(local_reading_evidence_is_usable(evidence)),
        complete_edges,
        evidence.get("lyric_coverage", 0.0),
        evidence.get("match_ratio") or 0.0,
        evidence.get("confidence", 0.0),
    )
